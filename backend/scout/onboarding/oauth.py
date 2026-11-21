"""Provider OAuth state: random, hashed at rest, single-use, short-lived, and bound to the organization, the initiating
owner session, the browser proof, the provider and the exact redirect URI."""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

from scout.config import ScoutSettings
from scout.connectors.crypto import derive_key, open_, seal

TTL = "10 minutes"


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _verifier_key(s: ScoutSettings, organization_id: str) -> str:
    return derive_key(s.connections_key, organization_id, "oauth-state")


def issue(conn: Any, s: ScoutSettings, organization_id: str, session_hash: str, provider: str, redirect_uri: str,
          browser_proof: str, label: str | None = None, verifier: str | None = None) -> str:
    """Create the state (one in flight per organization and provider) and return the raw value for the redirect."""
    state = secrets.token_urlsafe(32)
    conn.execute("DELETE FROM scout_auth.setup_oauth_states WHERE expires_at <= now() "
                 "OR (organization_id = %s AND provider = %s)", (organization_id, provider))
    conn.execute(
        "INSERT INTO scout_auth.setup_oauth_states (state_hash, organization_id, session_hash, browser_hash, provider, "
        f"redirect_uri, label, verifier_ciphertext, expires_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now() + interval '{TTL}')",
        (sha(state), organization_id, session_hash, sha(browser_proof), provider, redirect_uri, label,
         seal(verifier, _verifier_key(s, organization_id)) if verifier else None))
    return state


def consume(conn: Any, s: ScoutSettings, organization_id: str, session_hash: str, provider: str, state: str,
            redirect_uri: str, browser_proof: str) -> dict[str, Any] | None:
    """Atomically delete and return the state only if EVERY binding matches. A mismatching attempt consumes nothing, so
    only the rightful browser can finish (and only once). Expired rows are removed on the way."""
    row = conn.execute(
        "DELETE FROM scout_auth.setup_oauth_states WHERE state_hash = %s AND organization_id = %s AND provider = %s "
        "AND session_hash = %s AND browser_hash = %s AND redirect_uri = %s AND expires_at > now() "
        "RETURNING label, verifier_ciphertext",
        (sha(state), organization_id, provider, session_hash, sha(browser_proof), redirect_uri)).fetchone()
    conn.execute("DELETE FROM scout_auth.setup_oauth_states WHERE expires_at <= now()")
    if row is None:
        return None
    verifier = None
    if row["verifier_ciphertext"]:
        verifier = open_(row["verifier_ciphertext"], _verifier_key(s, organization_id))
    return {"label": row["label"], "verifier": verifier}
