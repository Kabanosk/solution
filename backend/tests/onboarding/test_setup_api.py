"""Cycle 2: durable tenant-scoped setup state, optimistic updates, owner-only writes, built-in invoicing."""
from __future__ import annotations

import pytest

from onboarding_helpers import dump, setup_row, step, tenant_rows

STEP_IDS = ["bank", "invoicing", "ksef", "email", "slack", "network"]
NOT_STARTED = {"version": 1, "status": "not_started", "revision": 0, "network": None}


def get(api, org, **kw):
    return api.get("/v1/onboarding", headers=org.headers(**kw))


def patch(api, org, body, **kw):
    return api.patch("/v1/onboarding", headers=org.headers(**kw), json=body)


def test_missing_row_is_not_started_and_a_get_never_writes(api, make_org, db_url):
    org = make_org(with_setup_row=False)
    response = get(api, org)
    assert response.status_code == 200
    body = response.json()
    assert {k: body[k] for k in NOT_STARTED} == NOT_STARTED
    assert [s["id"] for s in body["steps"]] == STEP_IDS
    assert {s["state"] for s in body["steps"]} == {"not_started"}
    assert setup_row(db_url, org.id) is None
    assert response.headers.get("cache-control", "").startswith("no-store")


def test_pending_row_after_signup_is_returned_with_stable_steps(api, org):
    body = get(api, org).json()
    assert body["status"] == "pending" and body["revision"] == 0 and body["network"] is None
    assert [s["id"] for s in body["steps"]] == STEP_IDS
    for s in body["steps"]:
        assert set(s) == {"id", "state", "provider", "message", "display"}
        assert s["state"] == "not_started"
    assert [s["provider"] for s in body["steps"]] == ["bunq", "builtin", "ksef", "google", "slack", None]


def test_requires_a_session_and_a_member_may_read_but_not_write(api, org):
    assert api.get("/v1/onboarding").status_code == 401
    assert get(api, org, member=True).status_code == 200
    denied = patch(api, org, {"revision": 0, "action": "skip"}, member=True)
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "owner_required"
    assert api.post("/v1/onboarding/connect/builtin", headers=org.headers(member=True), json={}).status_code == 403
    assert api.delete("/v1/onboarding/connect/builtin", headers=org.headers(member=True)).status_code == 403


def test_organization_comes_from_the_session_never_the_request(api, org, other_org, db_url):
    assert patch(api, org, {"revision": 0, "action": "skip", "organization_id": other_org.id}).status_code == 422
    forged = {"Authorization": org.headers()["Authorization"], "X-Scout-Organization": other_org.slug}
    assert api.get("/v1/onboarding", headers=forged).status_code == 401
    assert patch(api, org, {"revision": 0, "action": "skip"}).status_code == 200
    assert setup_row(db_url, other_org.id)["revision"] == 0 and setup_row(db_url, other_org.id)["status"] == "pending"
    assert get(api, other_org).json()["status"] == "pending"


def test_skip_resume_and_optimistic_conflicts(api, org):
    dismissed = patch(api, org, {"revision": 0, "action": "skip"}).json()
    assert dismissed["status"] == "dismissed" and dismissed["revision"] == 1
    stale = patch(api, org, {"revision": 0, "action": "resume"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "conflict"
    resumed = patch(api, org, {"revision": 1, "action": "resume"}).json()
    assert resumed["status"] == "in_progress" and resumed["revision"] == 2
    assert get(api, org).json()["revision"] == 2


def test_resume_on_an_existing_organization_without_a_row_creates_it(api, make_org, db_url):
    org = make_org(with_setup_row=False)
    assert patch(api, org, {"revision": 3, "action": "resume"}).status_code == 409
    assert setup_row(db_url, org.id) is None
    body = patch(api, org, {"revision": 0, "action": "resume"}).json()
    assert body["status"] == "in_progress" and body["revision"] == 1
    assert setup_row(db_url, org.id)["status"] == "in_progress"


def test_step_decisions_do_not_fake_connector_status(api, org):
    body = patch(api, org, {"revision": 0, "action": "step", "step": "bank", "state": "skipped"}).json()
    assert step(body, "bank")["state"] == "skipped" and body["status"] == "in_progress"
    assert step(body, "email")["state"] == "not_started"
    back = patch(api, org, {"revision": 1, "action": "step", "step": "bank", "state": "not_started"}).json()
    assert step(back, "bank")["state"] == "not_started"
    for bad in ({"step": "bank"}, {"state": "skipped"}, {"step": "bank", "state": "connected"},
                {"step": "nope", "state": "skipped"}):
        assert patch(api, org, {"revision": 2, "action": "step", **bad}).status_code == 422


def test_network_answers_record_intent_and_require_administrator_setup(api, org):
    answers = {"required": True, "provider": "WireGuard", "protocol": "udp", "systems": ["ERP", "NAS"],
               "administrator": "it@acme.example", "company_only": True}
    body = patch(api, org, {"revision": 0, "action": "network", "network": answers}).json()
    assert body["network"] == answers
    network = step(body, "network")
    assert network["state"] == "needs_configuration" and "administrator" in network["message"].lower()
    assert network["display"]["required"] is True and network["display"]["systems"] == "ERP, NAS"
    none_needed = patch(api, org, {"revision": 1, "action": "network", "network": {**answers, "required": False}}).json()
    assert step(none_needed, "network")["state"] == "skipped"
    assert none_needed["revision"] == 2


@pytest.mark.parametrize("answers", [
    {"required": True, "provider": "x" * 200, "protocol": "udp", "systems": [], "administrator": "a", "company_only": True},
    {"required": True, "provider": "WG", "protocol": "udp", "systems": ["s"] * 40, "administrator": "a", "company_only": True},
    {"required": True, "provider": "-----BEGIN PRIVATE KEY-----", "protocol": "udp", "systems": [], "administrator": "a", "company_only": True},
    {"required": True, "provider": "WG", "protocol": "udp", "systems": [], "administrator": "a", "company_only": True, "password": "x"},
    {"required": True, "provider": "WG", "protocol": "udp", "systems": [], "administrator": "a\x00", "company_only": True},
])
def test_network_answers_are_bounded_and_secret_free(api, org, answers):
    assert patch(api, org, {"revision": 0, "action": "network", "network": answers}).status_code == 422


def test_complete_requires_every_step_configured_or_skipped(api, org):
    refused = patch(api, org, {"revision": 0, "action": "complete"})
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "setup_incomplete"
    revision = 0
    for sid in STEP_IDS:
        body = patch(api, org, {"revision": revision, "action": "step", "step": sid, "state": "skipped"}).json()
        revision = body["revision"]
    assert patch(api, org, {"revision": revision - 1, "action": "complete"}).status_code == 409
    done = patch(api, org, {"revision": revision, "action": "complete"}).json()
    assert done["status"] == "completed" and done["revision"] == revision + 1


def test_builtin_invoicing_connects_the_tenant_local_system_and_can_be_removed(api, org, db_url):
    body = api.post("/v1/onboarding/connect/builtin", headers=org.headers(), json={}).json()
    invoicing = step(body, "invoicing")
    assert invoicing["state"] == "connected" and invoicing["provider"] == "builtin"
    assert invoicing["display"] == {"system": "built-in", "documents": "0"}
    assert body["revision"] == 1 and body["status"] == "in_progress"
    [row] = tenant_rows(db_url, org, "SELECT id, kind, provider, status, config FROM connections")
    assert (row["id"], row["kind"], row["provider"], row["status"]) == ("invoicing", "invoicing", "builtin", "connected")
    assert row["config"]["managed_by"] == "onboarding"
    removed = api.delete("/v1/onboarding/connect/builtin", headers=org.headers()).json()
    assert step(removed, "invoicing")["state"] == "not_started" and removed["revision"] == 2
    assert tenant_rows(db_url, org, "SELECT id FROM connections") == []


def test_connections_are_never_visible_across_organizations(api, org, other_org, db_url):
    api.post("/v1/onboarding/connect/builtin", headers=org.headers(), json={})
    assert step(get(api, other_org).json(), "invoicing")["state"] == "not_started"
    assert tenant_rows(db_url, other_org, "SELECT id FROM connections") == []


def test_unknown_provider_and_extra_body_fields_are_rejected(api, org):
    assert api.post("/v1/onboarding/connect/dropbox", headers=org.headers(), json={}).status_code == 422
    assert api.post("/v1/onboarding/connect/builtin", headers=org.headers(), json={"x": 1}).status_code == 422


def test_missing_server_configuration_is_needs_configuration_never_connected(api, org, settings_factory, db_url):
    api.state["settings"] = settings_factory(database_url=db_url, google_client_id="", slack_client_secret="", connections_key="")
    body = get(api, org).json()
    for sid, missing in (("email", "GOOGLE_CLIENT_ID"), ("slack", "SLACK_CLIENT_SECRET"), ("bank", "CONNECTIONS_KEY")):
        s = step(body, sid)
        assert s["state"] == "needs_configuration" and missing in s["message"], sid
    assert step(body, "invoicing")["state"] == "not_started"
    assert "google-client" not in dump(body)
