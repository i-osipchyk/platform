from datetime import UTC, datetime, timedelta

import pytest
from handler import (
    ACCESS_TOKEN,
    CLIENT_ID,
    CLIENT_SECRET,
    EXPIRES_AT,
    REFRESH_TOKEN,
    TokenRefreshError,
    refresh_if_due,
)

NOW = datetime(2026, 10, 13, tzinfo=UTC)
OK = {"accessToken": "a2", "refreshToken": "r2", "expiresIn": 2628000}


class FakeStore:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = dict(values)
        self.writes: list[str] = []

    def read(self) -> dict[str, str]:
        return dict(self.values)

    def write(self, name: str, value: str) -> None:
        self.writes.append(name)
        self.values[name] = value


def seeded(**extra: str) -> FakeStore:
    return FakeStore({CLIENT_ID: "id", CLIENT_SECRET: "secret", REFRESH_TOKEN: "r1", **extra})


def test_not_due_leaves_tokens_alone():
    store = seeded(**{EXPIRES_AT: (NOW + timedelta(days=8)).isoformat()})
    assert refresh_if_due(store, lambda p: pytest.fail("called"), now=NOW) == "not_due"
    assert store.writes == []


def test_refreshes_inside_window_refresh_token_first():
    store = seeded(**{EXPIRES_AT: (NOW + timedelta(days=6)).isoformat()})
    sent = {}
    assert refresh_if_due(store, lambda p: sent.update(p) or OK, now=NOW) == "refreshed"
    assert sent["refresh_token"] == "r1" and sent["client_id"] == "id"
    assert store.writes == [REFRESH_TOKEN, ACCESS_TOKEN, EXPIRES_AT]
    assert store.values[ACCESS_TOKEN] == "a2"
    assert store.values[EXPIRES_AT] == (NOW + timedelta(seconds=2628000)).isoformat()


def test_refreshes_when_expiry_unknown_or_forced():
    assert refresh_if_due(seeded(), lambda p: OK, now=NOW) == "refreshed"
    later = seeded(**{EXPIRES_AT: (NOW + timedelta(days=20)).isoformat()})
    assert refresh_if_due(later, lambda p: OK, now=NOW, force=True) == "refreshed"


def test_api_error_raises_and_writes_nothing():
    store = seeded()
    err = {"errorCode": "ACCESS_DENIED", "description": "bad refresh token"}
    with pytest.raises(TokenRefreshError, match="ACCESS_DENIED"):
        refresh_if_due(store, lambda p: err, now=NOW)
    assert store.writes == []


def test_transport_error_raises():
    def boom(p):
        raise OSError("timeout")

    with pytest.raises(TokenRefreshError, match="OSError"):
        refresh_if_due(seeded(), boom, now=NOW)


def test_missing_seed_raises():
    with pytest.raises(TokenRefreshError, match=REFRESH_TOKEN):
        refresh_if_due(FakeStore({CLIENT_ID: "id", CLIENT_SECRET: "s"}), lambda p: OK, now=NOW)
