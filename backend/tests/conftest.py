"""Shared fixtures."""

import pytest

from cfo.usage import recorder


@pytest.fixture(autouse=True)
def established_session_for_route_unit_tests(request, monkeypatch):
    """Only modules explicitly importing our authenticated client opt into the identity stub."""
    from api_test_helpers import TestClient
    if getattr(request.module, "TestClient", None) is TestClient:
        from cfo.api import auth
        from cfo.tenancy import LEGACY_ID, LEGACY_SLUG, Tenant
        identity = Tenant(id=LEGACY_ID, slug=LEGACY_SLUG, schema_name="public", employee_id="vincenzo", role="owner", legacy=True)
        monkeypatch.setattr(auth, "verify_session", lambda token, slug: identity if token == "tok" and slug == LEGACY_SLUG else None)
        monkeypatch.setenv("SCOUT_LEGACY_CONNECTORS_ORGANIZATION", LEGACY_ID)
    yield


class _NullSink:
    def write(self, record) -> None:  # noqa: ANN001
        pass


@pytest.fixture(autouse=True)
def no_llm_usage_database_writes():
    """FakeLLM calls in unrelated tests must not write `llm_usage` rows into the shared database (T032).
    Tests of the recording itself install their own sink with `recorder.set_sink`."""
    recorder.set_sink(_NullSink())
    yield
    recorder.set_sink(None)
