"""Record pure tenant/schema/identity behavior from unchanged original modules."""
import dataclasses
import itertools
import json
import os
import re
import sys
from contextlib import nullcontext
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from cfo import tenancy
from cfo.api.auth import require_write_token
from cfo.api.errors import ApiError


def evaluate(body):
    try:
        raw = body.get("tenant")
        tenant = tenancy.Tenant(**raw) if raw is not None else None
        with tenancy.bind_tenant(tenant) if tenant else nullcontext():
            match body["op"]:
                case "tenant": result = dataclasses.asdict(tenant)
                case "schema": result = tenancy.scoped_schema(body.get("schema"))
                case "legacy":
                    os.environ["SCOUT_LEGACY_CONNECTORS_ORGANIZATION"] = body.get("configured", "")
                    result = tenancy.legacy_connectors_enabled(tenant)
                case "write": result = require_write_token()
                case "session_input":
                    result = bool(re.fullmatch(r"[A-Za-z0-9_-]{40,256}", body["token"]) and
                                  re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", body["slug"]))
        return {"ok": result}
    except ApiError as error:
        return {"status": error.status_code, "error": {"code": error.code, "message": error.message}}
    except (PermissionError, ValueError) as error:
        return {"error_type": type(error).__name__, "message": str(error)}


def corpus():
    normal = dict(id="11111111-2222-4333-8444-555555555555", slug="normal", schema_name="tenant_11111111222243338444555555555555", employee_id="owner", role="owner", legacy=False)
    legacy = dict(id=tenancy.LEGACY_ID, slug=tenancy.LEGACY_SLUG, schema_name="public", employee_id="ceo", role="owner", legacy=True)
    for tenant in [normal, legacy, {**normal, "id": "11111111222243338444555555555555"}, {**normal, "id": "{11111111-2222-4333-8444-555555555555}"}, {**normal, "id": "urn:uuid:11111111-2222-4333-8444-555555555555"}, {**normal, "id": "bad"}, {**normal, "schema_name": "public"}, {**normal, "legacy": True}, {**normal, "slug": tenancy.LEGACY_SLUG}, {**legacy, "legacy": False}, {**legacy, "schema_name": normal["schema_name"]}]:
        yield {"op": "tenant", "tenant": tenant}
    for tenant, schema in itertools.product([None, normal, legacy], [None, "public", "tenant_11111111222243338444555555555555", "safe_01", "BAD", "a;drop table x", "", "1bad", "a" * 64, "a" * 63, "á"]):
        yield {"op": "schema", "tenant": tenant, "schema": schema}
    for tenant, configured in itertools.product([None, normal, legacy], ["", tenancy.LEGACY_ID, normal["id"], " " + tenancy.LEGACY_ID]):
        yield {"op": "legacy", "tenant": tenant, "configured": configured}
    for tenant in [None, normal, {**normal, "employee_id": None}, {**normal, "employee_id": ""}, {**normal, "employee_id": " "}]:
        yield {"op": "write", "tenant": tenant}
    for token, slug in itertools.product(["x" * 39, "x" * 40, "x" * 256, "x" * 257, "x" * 39 + "_", "x" * 39 + "-", "x" * 39 + "é", "x" * 40 + "\n", " x" * 40], ["a", "a" * 80, "a" * 81, "A", "a-b", "-a", "a_", "a\n", ""]):
        yield {"op": "session_input", "token": token, "slug": slug}


if __name__ == "__main__":
    output = Path(__file__).resolve().parents[1] / "platform" / "characterization.jsonl"
    cases = [{"input": body, "output": evaluate(body)} for body in corpus()]
    output.write_text("".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases))
    print(f"Recorded {len(cases)} pure platform cases: {output}")
