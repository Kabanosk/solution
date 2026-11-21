"""Cycle 1: additive control-plane schema, per-organization sealing key, tenant settings shaping."""
from __future__ import annotations

import psycopg
import pytest
from psycopg.errors import CheckViolation, InvalidTextRepresentation

from onboarding_helpers import MASTER_KEY  # noqa: F401  (tests dir is on sys.path via rootdir conftest)


def test_workspace_setup_matches_the_signup_insert_of_organization_id_only(org, db_url):
    from onboarding_helpers import setup_row
    row = setup_row(db_url, org.id)  # org fixture inserted ONLY organization_id, like dashboard/lib/auth-store.ts
    assert row["revision"] == 0 and row["status"] == "pending" and row["steps"] == {} and row["network"] is None


def test_migration_is_idempotent_and_additive(db_url, org):
    from cfo.db.migrate import migrate_auth
    migrate_auth(db_url)
    migrate_auth(db_url)
    with psycopg.connect(db_url) as conn:
        assert conn.execute("SELECT count(*) FROM scout_auth.workspace_setup WHERE organization_id=%s", (org.id,)).fetchone()[0] == 1


def test_setup_rows_cascade_with_the_organization_and_reject_bad_status(db_url, make_org):
    victim = make_org()
    with psycopg.connect(db_url) as conn:
        with pytest.raises((CheckViolation, InvalidTextRepresentation)):
            conn.execute("UPDATE scout_auth.workspace_setup SET status='bogus' WHERE organization_id=%s", (victim.id,))
    with psycopg.connect(db_url) as conn:
        with pytest.raises(CheckViolation):
            conn.execute("UPDATE scout_auth.workspace_setup SET steps='[]'::jsonb WHERE organization_id=%s", (victim.id,))


def test_oauth_state_table_is_hashed_and_provider_bound(db_url):
    with psycopg.connect(db_url) as conn:
        cols = {r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='scout_auth' "
            "AND table_name='setup_oauth_states'").fetchall()}
    assert {"state_hash", "organization_id", "session_hash", "browser_hash", "provider", "redirect_uri",
            "expires_at"} <= cols
    assert "state" not in cols and "browser_proof" not in cols  # only hashes are persisted


def test_derived_keys_are_bound_to_organization_and_connection():
    from scout.connectors.crypto import derive_key, open_, seal
    a, b = "00000000-0000-4000-8000-0000000000aa", "00000000-0000-4000-8000-0000000000bb"
    key_a, key_b, key_a_slack = derive_key(MASTER_KEY, a, "gmail"), derive_key(MASTER_KEY, b, "gmail"), derive_key(MASTER_KEY, a, "slack")
    assert len({key_a, key_b, key_a_slack}) == 3
    sealed = seal("refresh-token-secret", key_a)
    assert b"refresh-token-secret" not in sealed
    assert open_(sealed, key_a) == "refresh-token-secret"
    for other in (key_b, key_a_slack):
        with pytest.raises(Exception):
            open_(sealed, other)  # ciphertext copied between organizations or connections is useless


def test_short_master_secret_cannot_derive_keys():
    from scout.connectors.crypto import derive_key
    with pytest.raises(ValueError):
        derive_key("short", "00000000-0000-4000-8000-0000000000aa", "gmail")


def test_tenant_settings_keep_app_registration_but_never_global_tenant_credentials(org):
    from cfo.tenancy import Tenant, bind_tenant
    from scout.config import ScoutSettings, tenant_settings
    raw = ScoutSettings(connections_key=MASTER_KEY, google_client_id="gid", google_client_secret="gsecret",
                        slack_client_id="sid", slack_client_secret="ssecret", scout_app_origin="https://scout.example.test",
                        slack_bot_token="xoxb-global", slack_app_token="xapp-global", slack_user_map="a=B",
                        chat_channels=["finance"], workstations_root="/srv/laptops", bank_api_url="http://bank",
                        oauth_state_secret="state-secret", google_mail_label="Amalfi")
    with bind_tenant(Tenant(id=org.id, slug=org.slug, schema_name=org.schema, employee_id=org.id, role="owner")):
        shaped = tenant_settings(raw)
    assert (shaped.google_client_id, shaped.google_client_secret) == ("gid", "gsecret")
    assert (shaped.slack_client_id, shaped.slack_client_secret) == ("sid", "ssecret")
    assert shaped.connections_key == MASTER_KEY and shaped.scout_app_origin == "https://scout.example.test"
    assert shaped.slack_bot_token == "" and shaped.slack_app_token == "" and shaped.slack_user_map == ""
    assert shaped.chat_channels == [] and shaped.workstations_root == "" and shaped.bank_api_url == ""
    assert shaped.oauth_state_secret == ""
