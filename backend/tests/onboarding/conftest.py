"""Real disposable Postgres fixtures for the onboarding API. External providers are faked only at the HTTP boundary."""
from __future__ import annotations

import os

import httpx
import pytest
from fastapi.testclient import TestClient

from onboarding_helpers import APP_ORIGIN, MASTER_KEY, Org, ProviderFakes, _drop_org, _make_org


@pytest.fixture(scope="session")
def db_url():
    url = os.getenv("TENANT_TEST_DATABASE_URL")
    if not url or not ("127.0.0.1" in url or "localhost" in url):
        pytest.skip("set TENANT_TEST_DATABASE_URL to a disposable local DB")
    from cfo.db.migrate import migrate_auth
    migrate_auth(url)
    return url


@pytest.fixture
def make_org(db_url):
    made: list[Org] = []

    def factory(*, with_setup_row: bool = True, with_member: bool = True) -> Org:
        org = _make_org(db_url, with_setup_row=with_setup_row, with_member=with_member)
        made.append(org)
        return org

    yield factory
    for org in made:
        _drop_org(db_url, org)


@pytest.fixture
def org(make_org) -> Org:
    return make_org()


@pytest.fixture
def other_org(make_org) -> Org:
    return make_org()


@pytest.fixture
def fakes(monkeypatch) -> ProviderFakes:
    from scout.onboarding import http as onboarding_http
    fake = ProviderFakes()
    monkeypatch.setattr(onboarding_http, "TRANSPORT", httpx.MockTransport(fake.handler))
    return fake


@pytest.fixture
def settings_factory():
    from scout.config import ScoutSettings

    def make(**overrides) -> ScoutSettings:
        base = dict(database_url="postgresql://unused@localhost/unused", scout_app_origin=APP_ORIGIN,
                    connections_key=MASTER_KEY, google_client_id="google-client-id",
                    google_client_secret="google-client-secret", slack_client_id="slack-client-id",
                    slack_client_secret="slack-client-secret")
        base.update(overrides)
        return ScoutSettings(**base)

    return make


@pytest.fixture
def api(db_url, monkeypatch, settings_factory):
    """The real FastAPI app with real session verification against the disposable database."""
    from cfo.api import auth
    from cfo.api.app import create_app
    from scout import api as scout_api
    from scout import db as scout_db
    from scout.config import get_scout_settings

    monkeypatch.setattr(auth, "database_url", lambda: db_url)
    monkeypatch.delenv("SCOUT_LEGACY_CONNECTORS_ORGANIZATION", raising=False)
    app = create_app()
    state = {"settings": settings_factory(database_url=db_url)}

    def connection():
        conn = scout_db.connect(db_url)
        try:
            yield conn
        finally:
            conn.close()

    app.dependency_overrides[scout_api.get_conn] = connection
    app.dependency_overrides[get_scout_settings] = lambda: state["settings"]
    client = TestClient(app, raise_server_exceptions=True)
    client.state = state  # type: ignore[attr-defined]
    return client
