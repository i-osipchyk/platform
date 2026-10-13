"""Rotates the account's one cTrader token pair in SSM /platform/shared/ctrader-*.

A refresh spends the old refresh token, so exactly one thing may refresh: this function. Every
project only reads the pair (SHARED_SECRETS_PATH, CTRADER_ACCESS_TOKEN etc.), so they all hold
the same, current token. Runs daily and refreshes when the access token (~30 days) has less than
REFRESH_WINDOW left, or when it's invoked with {"force": true}. A failure raises, which the
Errors alarm sends to platform-alarms.
"""

import json
import os
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta

TOKEN_URL = "https://openapi.ctrader.com/apps/token"
REFRESH_WINDOW = timedelta(days=7)

# Parameter name (under SSM_PREFIX) -> what it holds.
CLIENT_ID = "ctrader-client-id"
CLIENT_SECRET = "ctrader-client-secret"
ACCESS_TOKEN = "ctrader-access-token"
REFRESH_TOKEN = "ctrader-refresh-token"
EXPIRES_AT = "ctrader-access-token-expires-at"


class TokenRefreshError(Exception):
    pass


class SsmStore:
    def __init__(self, client, prefix: str) -> None:
        self._client = client
        self._prefix = prefix.rstrip("/")

    def read(self) -> dict[str, str]:
        names = [CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN, EXPIRES_AT]
        res = self._client.get_parameters(
            Names=[f"{self._prefix}/{n}" for n in names], WithDecryption=True
        )
        return {p["Name"].rsplit("/", 1)[-1]: p["Value"] for p in res["Parameters"]}

    def write(self, name: str, value: str) -> None:
        self._client.put_parameter(
            Name=f"{self._prefix}/{name}", Value=value, Type="SecureString", Overwrite=True
        )


def refresh_if_due(store, token_endpoint, *, now: datetime, force: bool = False) -> str:
    """Refresh the pair when it expires within REFRESH_WINDOW, or if forced. Returns the outcome."""
    current = store.read()
    missing = [n for n in (CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN) if not current.get(n)]
    if missing:
        raise TokenRefreshError(f"missing SSM parameters: {', '.join(missing)}")
    expires_at = current.get(EXPIRES_AT)
    if not force and expires_at and datetime.fromisoformat(expires_at) - now > REFRESH_WINDOW:
        return "not_due"
    try:
        response = token_endpoint({
            "grant_type": "refresh_token",
            "refresh_token": current[REFRESH_TOKEN],
            "client_id": current[CLIENT_ID],
            "client_secret": current[CLIENT_SECRET],
        })
    except Exception as exc:
        raise TokenRefreshError(f"{type(exc).__name__}: {exc}") from exc
    if response.get("errorCode") or not response.get("refreshToken"):
        raise TokenRefreshError(f"{response.get('errorCode')}: {response.get('description')}")
    # The old refresh token is spent now, so the new one is written first: if a later write
    # fails, the next run can still refresh.
    store.write(REFRESH_TOKEN, response["refreshToken"])
    store.write(ACCESS_TOKEN, response["accessToken"])
    store.write(EXPIRES_AT, (now + timedelta(seconds=response["expiresIn"])).isoformat())
    return "refreshed"


def token_endpoint(params: dict[str, str]) -> dict:
    url = f"{TOKEN_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read())


def handler(event, context):
    import boto3

    store = SsmStore(boto3.client("ssm"), os.environ["SSM_PREFIX"])
    force = bool((event or {}).get("force"))
    outcome = refresh_if_due(store, token_endpoint, now=datetime.now(UTC), force=force)
    print(json.dumps({"outcome": outcome}))
    return {"outcome": outcome}
