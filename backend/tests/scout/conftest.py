"""Scout fixtures: a fresh Postgres schema per test (never the cluster), FakeLLM, fake Slack client."""

from __future__ import annotations

import itertools
import os
from typing import Any

import psycopg
import pytest

from scout import db as scout_db
from scout.config import get_scout_settings

_counter = itertools.count()


def _url() -> str:
    url = os.environ.get("SCOUT_TEST_DATABASE_URL") or get_scout_settings().database_url
    if "localhost" not in url and "127.0.0.1" not in url:
        pytest.skip("Scout tests only run against a local Postgres (set SCOUT_TEST_DATABASE_URL)")
    return url


@pytest.fixture
def schema() -> Any:
    url = _url()
    name = f"scout_test_{os.getpid()}_{next(_counter)}"
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f"CREATE SCHEMA {name}")
    conn = scout_db.connect(url, name)
    scout_db.apply_schema(conn)
    conn.close()
    yield url, name
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA {name} CASCADE")


@pytest.fixture
def conn_factory(schema):  # noqa: ANN001
    url, name = schema
    return scout_db.factory(url, name)


@pytest.fixture
def db(conn_factory):  # noqa: ANN001
    conn = conn_factory()
    yield conn
    conn.close()


class FakeSlack:
    """Records Web API calls the way slack_sdk's WebClient is used by Scout."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._ts = itertools.count(1)
        self.history: dict[str, list[dict[str, Any]]] = {}
        self.channels = [{"id": "C_FIN", "name": "finance", "is_member": True}]
        self.files: dict[str, bytes] = {}

    def _rec(self, _call: str, **kw: Any) -> dict[str, Any]:
        self.calls.append((_call, kw))
        return {"ok": True}

    def chat_postMessage(self, **kw: Any) -> dict[str, Any]:  # noqa: N802
        self._rec("chat_postMessage", **kw)
        return {"ok": True, "channel": kw.get("channel", "D1"), "ts": f"100.{next(self._ts):04d}"}

    def chat_update(self, **kw: Any) -> dict[str, Any]:  # noqa: N802
        return self._rec("chat_update", **kw)

    def chat_postEphemeral(self, **kw: Any) -> dict[str, Any]:  # noqa: N802
        return self._rec("chat_postEphemeral", **kw)

    def reactions_add(self, **kw: Any) -> dict[str, Any]:  # noqa: N802
        return self._rec("reactions_add", **kw)

    def conversations_open(self, **kw: Any) -> dict[str, Any]:  # noqa: N802
        self._rec("conversations_open", **kw)
        return {"ok": True, "channel": {"id": "D_" + str(kw.get("users"))}}

    def conversations_list(self, **kw: Any) -> dict[str, Any]:  # noqa: N802
        self._rec("conversations_list", **kw)
        return {"ok": True, "channels": self.channels, "response_metadata": {"next_cursor": ""}}

    def conversations_history(self, **kw: Any) -> dict[str, Any]:  # noqa: N802
        self._rec("conversations_history", **kw)
        return {"ok": True, "messages": self.history.get(kw["channel"], []), "has_more": False}

    def chat_getPermalink(self, **kw: Any) -> dict[str, Any]:  # noqa: N802
        return {"ok": True, "permalink": f"https://slack.test/{kw['channel']}/{kw['message_ts']}"}

    def users_info(self, **kw: Any) -> dict[str, Any]:  # noqa: N802
        return {"ok": True, "user": {"id": kw["user"], "real_name": kw["user"]}}

    def named(self, name: str) -> list[dict[str, Any]]:
        return [kw for n, kw in self.calls if n == name]


@pytest.fixture
def fake_slack() -> FakeSlack:
    return FakeSlack()
