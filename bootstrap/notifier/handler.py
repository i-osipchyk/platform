"""SNS (platform-alarms) -> Telegram. Formats CloudWatch alarm messages; passes anything else on.

Alarm names follow <slug>-<env>-<function>-<kind>, so the project is readable from the name.

On ALARM, the message also says what went wrong, looked up from the alarm's own dimensions:
- a Lambda `Errors` alarm (FunctionName): the latest error lines the function logged in the
  alarm's window (unhandled exceptions, `logger.error` lines, timeouts, runtime crashes), each
  cut to its first line plus the last few traceback frames, and a link to the log group;
- a Step Functions alarm (StateMachineArn): the newest failed execution's error and cause, and a
  link to it.
A lookup that fails or finds nothing never stops the alert: the plain message goes out instead.
"""

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

# Lines that mean a failed invocation: the runtime's unhandled-exception and logger.error lines
# ([ERROR]), a timeout, and a crashed runtime (out of memory, killed process).
LOG_FILTER = (
    '?"[ERROR]" ?"Task timed out" ?"Runtime.ExitError" ?"Status: timeout" ?"Status: error"'
)
MAX_LOG_EVENTS = 3  # most recent matching lines shown
MAX_FRAMES = 3  # traceback frames kept per error, from the end (where it was raised)
MAX_LINE = 500  # chars per line; exception messages can carry request details
SLACK = timedelta(minutes=1)  # log ingestion lag and clock skew around the alarm's window

_cache: dict[str, str] = {}
_clients: dict[str, object] = {}


def _client(service: str):
    if service not in _clients:
        import boto3

        _clients[service] = boto3.client(service)
    return _clients[service]


def _param(name: str) -> str:
    if name not in _cache:
        path = f"{os.environ['SSM_PREFIX']}/{name}"
        response = _client("ssm").get_parameter(Name=path, WithDecryption=True)
        _cache[name] = response["Parameter"]["Value"]
    return _cache[name]


def format_message(subject: str | None, message: str, context: list[str] | None = None) -> str:
    try:
        alarm = json.loads(message)
    except json.JSONDecodeError:
        return f"{subject or 'platform'}\n{message}"
    if not isinstance(alarm, dict) or "AlarmName" not in alarm:
        return f"{subject or 'platform'}\n{message}"
    state = alarm.get("NewStateValue", "?")
    icon = {"ALARM": "🔴", "OK": "✅"}.get(state, "⚠️")
    text = (
        f"{icon} {alarm['AlarmName']} → {state}\n"
        f"{alarm.get('AlarmDescription') or ''}\n"
        f"{alarm.get('NewStateReason', '')}\n"
        f"{alarm.get('StateChangeTime', '')}"
    ).strip()
    if context:
        text += "\n\n" + "\n".join(context)
    return text


def _dimensions(alarm: dict) -> dict[str, str]:
    trigger = alarm.get("Trigger") or {}
    return {d["name"]: d["value"] for d in trigger.get("Dimensions") or [] if "name" in d}


def _window(alarm: dict) -> tuple[datetime, datetime]:
    """The span the alarm evaluated, widened by SLACK on both sides."""
    # CloudWatch sends e.g. 2026-10-26T20:05:12.345+0000.
    changed = datetime.strptime(alarm["StateChangeTime"], "%Y-%m-%dT%H:%M:%S.%f%z")
    trigger = alarm.get("Trigger") or {}
    periods = int(trigger.get("Period", 300)) * int(trigger.get("EvaluationPeriods", 1))
    return changed - timedelta(seconds=periods) - SLACK, changed + SLACK


def summarize_error(text: str) -> str:
    """First line of an error plus the last MAX_FRAMES `File "..."` lines of its traceback."""
    lines = [line.rstrip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return ""
    head = lines[0]
    # logger.error lines arrive as "[ERROR]\t<time>\t<request id>\t<message>": keep the message.
    if head.startswith("[ERROR]\t") and head.count("\t") >= 3:
        head = "[ERROR] " + head.split("\t", 3)[3]
    frames = [line.strip() for line in lines[1:] if line.strip().startswith('File "')]
    out = [head[:MAX_LINE]] + [f"  {frame[:MAX_LINE]}" for frame in frames[-MAX_FRAMES:]]
    return "\n".join(out)


def _console(region: str) -> str:
    return f"https://{region}.console.aws.amazon.com"


def lambda_context(function: str, start: datetime, end: datetime, logs, region: str) -> list[str]:
    group = f"/aws/lambda/{function}"
    events: list[dict] = []
    kwargs = {
        "logGroupName": group,
        "startTime": int(start.timestamp() * 1000),
        "endTime": int(end.timestamp() * 1000),
        "filterPattern": LOG_FILTER,
    }
    for _ in range(5):  # a handful of pages is plenty for one alarm window
        page = logs.filter_log_events(**kwargs)
        events += page.get("events", [])
        if not page.get("nextToken"):
            break
        kwargs["nextToken"] = page["nextToken"]
    if not events:
        return []
    events.sort(key=lambda e: e["timestamp"])
    latest = [summarize_error(e["message"]) for e in events[-MAX_LOG_EVENTS:]]
    # The console wants the group name URL-encoded twice, with "%" written as "$".
    encoded = urllib.parse.quote(urllib.parse.quote(group, safe=""), safe="").replace("%", "$")
    link = (
        f"{_console(region)}/cloudwatch/home?region={region}"
        f"#logsV2:log-groups/log-group/{encoded}"
    )
    return [f"{len(events)} error line(s) in the window, latest:", *filter(None, latest), link]


def _cause(cause: str) -> str:
    """A Lambda failure's cause is its error JSON; anything else is shown as is."""
    try:
        parsed = json.loads(cause)
    except (json.JSONDecodeError, TypeError):
        return summarize_error(cause or "")
    if not isinstance(parsed, dict) or "errorMessage" not in parsed:
        return summarize_error(cause)
    trace = "".join(parsed.get("stackTrace") or [])
    return summarize_error(f"{parsed.get('errorType', 'Error')}: {parsed['errorMessage']}\n{trace}")


_FAILED_EVENTS = {
    "ExecutionFailed": "executionFailedEventDetails",
    "ExecutionTimedOut": "executionTimedOutEventDetails",
    "ExecutionAborted": "executionAbortedEventDetails",
}


def states_context(state_machine_arn: str, states, region: str) -> list[str]:
    executions = states.list_executions(
        stateMachineArn=state_machine_arn, statusFilter="FAILED", maxResults=1
    ).get("executions", [])
    if not executions:
        return []
    execution = executions[0]
    history = states.get_execution_history(
        executionArn=execution["executionArn"], reverseOrder=True, maxResults=50
    ).get("events", [])
    details: dict = {}
    for event in history:
        key = _FAILED_EVENTS.get(event["type"])
        if key:
            details = event.get(key) or {}
            break
    link = (
        f"{_console(region)}/states/home?region={region}"
        f"#/v2/executions/details/{execution['executionArn']}"
    )
    lines = [f"execution {execution['name']}: {details.get('error') or 'failed'}"]
    if details.get("cause"):
        lines.append(_cause(details["cause"]))
    return [*lines, link]


def alarm_context(message: str, *, logs=None, states=None, region: str | None = None) -> list[str]:
    """What went wrong, for an alarm entering ALARM; [] for anything else or when unknown."""
    try:
        alarm = json.loads(message)
    except json.JSONDecodeError:
        return []
    if not isinstance(alarm, dict) or alarm.get("NewStateValue") != "ALARM":
        return []
    region = region or os.environ.get("AWS_REGION", "eu-west-1")
    dims = _dimensions(alarm)
    try:
        if "FunctionName" in dims:
            start, end = _window(alarm)
            return lambda_context(
                dims["FunctionName"], start, end, logs or _client("logs"), region
            )
        if "StateMachineArn" in dims:
            return states_context(
                dims["StateMachineArn"], states or _client("stepfunctions"), region
            )
    except Exception as exc:  # the alert itself must still go out
        return [f"(no error details: {type(exc).__name__}: {str(exc)[:200]})"]
    return []


def send(text: str) -> None:
    url = f"https://api.telegram.org/bot{_param('telegram-bot-token')}/sendMessage"
    body = urllib.parse.urlencode({"chat_id": _param("telegram-chat-id"), "text": text[:4000]})
    with urllib.request.urlopen(url, data=body.encode(), timeout=10) as response:
        response.read()


def handler(event, context):
    for record in event.get("Records", []):
        sns = record.get("Sns", {})
        message = sns.get("Message", "")
        send(format_message(sns.get("Subject"), message, alarm_context(message)))
