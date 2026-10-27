import json
from datetime import UTC, datetime

from handler import LOG_FILTER, alarm_context, format_message, summarize_error

TRACEBACK = (
    "[ERROR] APIStatusError: Error code: 402 - Insufficient Balance\n"
    "Traceback (most recent call last):\n"
    '  File "/var/runtime/bootstrap.py", line 60, in handle\n'
    "    return fn(event)\n"
    '  File "/var/task/ags/handlers/portfolio.py", line 15, in handle\n'
    "    cycle = run_portfolio(...)\n"
    '  File "/var/task/ags/portfolio/stage.py", line 180, in _run\n'
    "    manager_out = manager(...)\n"
    '  File "/var/task/ags/llm/client.py", line 50, in complete\n'
    "    result = self._llm.chat(...)\n"
)


def alarm(state="ALARM", dimensions=None, **extra) -> str:
    return json.dumps(
        {
            "AlarmName": "ags-live-portfolio-errors",
            "AlarmDescription": "ags (live): portfolio failed (error or timeout)",
            "NewStateValue": state,
            "NewStateReason": "Threshold Crossed: 1 datapoint [1.0] was >= 1.0",
            "StateChangeTime": "2026-10-26T20:05:12.345+0000",
            "Trigger": {
                "Period": 300,
                "EvaluationPeriods": 1,
                "Dimensions": dimensions
                if dimensions is not None
                else [{"name": "FunctionName", "value": "ags-live-portfolio"}],
            },
            **extra,
        }
    )


class FakeLogs:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def filter_log_events(self, **kwargs):
        self.calls.append(kwargs)
        return self.pages.pop(0)


class FakeStates:
    def __init__(self, executions, history):
        self.executions = executions
        self.history = history

    def list_executions(self, **kwargs):
        assert kwargs["statusFilter"] == "FAILED"
        return {"executions": self.executions}

    def get_execution_history(self, **kwargs):
        assert kwargs["reverseOrder"] is True
        return {"events": self.history}


def test_summary_keeps_the_error_line_and_the_last_frames():
    summary = summarize_error(TRACEBACK).splitlines()
    assert summary[0] == "[ERROR] APIStatusError: Error code: 402 - Insufficient Balance"
    assert len(summary) == 4
    assert "llm/client.py" in summary[-1] and "bootstrap.py" not in "\n".join(summary)


def test_summary_strips_the_logger_prefix():
    line = "[ERROR]\t2026-10-26T20:04:01.000Z\treq-1\tags-error commodity=coffee error=X: y"
    assert summarize_error(line) == "[ERROR] ags-error commodity=coffee error=X: y"


def test_lambda_alarm_quotes_the_latest_errors_from_the_alarm_window():
    logs = FakeLogs(
        [
            {"events": [{"timestamp": 2, "message": TRACEBACK}], "nextToken": "n"},
            {"events": [{"timestamp": 1, "message": "[ERROR]\tt\tr\tags-error cycle_id=c1"}]},
        ]
    )
    context = alarm_context(alarm(), logs=logs, region="eu-west-1")

    call = logs.calls[0]
    assert call["logGroupName"] == "/aws/lambda/ags-live-portfolio"
    assert call["filterPattern"] == LOG_FILTER
    def ms(*args: int) -> int:
        return int(datetime(*args, 345000, UTC).timestamp() * 1000)

    assert call["startTime"] == ms(2026, 10, 26, 19, 59, 12)
    assert call["endTime"] == ms(2026, 10, 26, 20, 6, 12)
    assert logs.calls[1]["nextToken"] == "n"
    assert context[0].startswith("2 error line(s)")
    # Oldest first, so the line nearest the link is the one that tripped the alarm.
    assert context[1] == "[ERROR] ags-error cycle_id=c1"
    assert context[2].startswith("[ERROR] APIStatusError: Error code: 402")
    assert context[-1].endswith("log-group/$252Faws$252Flambda$252Fags-live-portfolio")


def test_lambda_alarm_with_no_matching_lines_adds_nothing():
    assert alarm_context(alarm(), logs=FakeLogs([{"events": []}]), region="eu-west-1") == []


def test_state_machine_alarm_shows_the_failed_executions_error_and_cause():
    arn = "arn:aws:states:eu-west-1:1:stateMachine:ags-live-cycle"
    cause = {
        "errorType": "LateStartError",
        "errorMessage": "started 1:30:00 after scheduled_time",
        "stackTrace": ['  File "/var/task/ags/handlers/runtime.py", line 44, in check\n'],
    }
    states = FakeStates(
        [{"executionArn": f"{arn}:x1", "name": "x1"}],
        [
            {"type": "ExecutionFailed", "executionFailedEventDetails": {
                "error": "LateStartError", "cause": json.dumps(cause)}},
            {"type": "TaskFailed"},
        ],
    )
    message = alarm(dimensions=[{"name": "StateMachineArn", "value": arn}])

    context = alarm_context(message, states=states, region="eu-west-1")

    assert context[0] == "execution x1: LateStartError"
    assert context[1].startswith("LateStartError: started 1:30:00")
    assert "runtime.py" in context[1]
    assert context[2].endswith(f"#/v2/executions/details/{arn}:x1")


def test_ok_and_non_alarm_messages_look_nothing_up():
    def explode(**kwargs):
        raise AssertionError("looked up")

    logs = type("L", (), {"filter_log_events": staticmethod(explode)})()
    assert alarm_context(alarm(state="OK"), logs=logs) == []
    assert alarm_context("plain text", logs=logs) == []


def test_a_failed_lookup_still_lets_the_alert_go_out():
    class Denied:
        def filter_log_events(self, **kwargs):
            raise PermissionError("AccessDenied")

    context = alarm_context(alarm(), logs=Denied(), region="eu-west-1")
    assert context == ["(no error details: PermissionError: AccessDenied)"]


def test_context_is_appended_to_the_alarm_text():
    text = format_message(None, alarm(), ["1 error line(s) in the window, latest:", "boom"])
    assert text.startswith("🔴 ags-live-portfolio-errors → ALARM")
    assert text.endswith("\n\n1 error line(s) in the window, latest:\nboom")
    assert format_message("s", "not json", ["x"]) == "s\nnot json"
