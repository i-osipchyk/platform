"""SNS (platform-alarms) -> Telegram. Formats CloudWatch alarm messages; passes anything else on.

Alarm names follow <slug>-<env>-<function>-<kind>, so the project is readable from the name.
"""

import json
import os
import urllib.parse
import urllib.request

import boto3

_ssm = boto3.client("ssm")
_cache: dict[str, str] = {}


def _param(name: str) -> str:
    if name not in _cache:
        path = f"{os.environ['SSM_PREFIX']}/{name}"
        _cache[name] = _ssm.get_parameter(Name=path, WithDecryption=True)["Parameter"]["Value"]
    return _cache[name]


def format_message(subject: str | None, message: str) -> str:
    try:
        alarm = json.loads(message)
    except json.JSONDecodeError:
        return f"{subject or 'platform'}\n{message}"
    if not isinstance(alarm, dict) or "AlarmName" not in alarm:
        return f"{subject or 'platform'}\n{message}"
    state = alarm.get("NewStateValue", "?")
    icon = {"ALARM": "🔴", "OK": "✅"}.get(state, "⚠️")
    return (
        f"{icon} {alarm['AlarmName']} → {state}\n"
        f"{alarm.get('AlarmDescription') or ''}\n"
        f"{alarm.get('NewStateReason', '')}\n"
        f"{alarm.get('StateChangeTime', '')}"
    ).strip()


def send(text: str) -> None:
    url = f"https://api.telegram.org/bot{_param('telegram-bot-token')}/sendMessage"
    body = urllib.parse.urlencode({"chat_id": _param("telegram-chat-id"), "text": text[:4000]})
    with urllib.request.urlopen(url, data=body.encode(), timeout=10) as response:
        response.read()


def handler(event, context):
    for record in event.get("Records", []):
        sns = record.get("Sns", {})
        send(format_message(sns.get("Subject"), sns.get("Message", "")))
