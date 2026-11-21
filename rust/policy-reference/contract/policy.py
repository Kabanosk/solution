"""Read-only characterization adapter for Scout pure policy operations.

The JSON-string boundary makes Pydantic input/output types recordable without
changing any production module. Invalid adapter payloads return a stable sentinel;
valid payloads call the unmodified original, including its Decimal formatting.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))
from cfo.domain import Rule, Transaction
# The source bundle contains exactly the pure part being characterized. Validate
# its provenance on every original run so changes cannot leave recordings stale.
_original = Path(__file__).resolve().parent / "original_policy.py"
_live = Path(__file__).resolve().parents[3] / "backend" / "scout" / "policy.py"
if _live.read_bytes().split(b"\n_RULE_TOOL =", 1)[0] != _original.read_bytes().split(b"\n# Characterization-private", 1)[0]:
    raise RuntimeError("Pure policy reference is stale; refresh from the original and record again")
from .original_policy import PolicyAction, PolicyContext, classify, context_from_transactions


def evaluate(payload: str) -> str:
    try:
        body = json.loads(payload)
    except (ValueError, TypeError):
        return "invalid JSON"
    if not isinstance(body, dict) or body.get("op") not in ("classify", "context"):
        return "unknown operation"
    try:
        if body["op"] == "classify":
            result = classify(PolicyAction.model_validate(body["action"]), PolicyContext.model_validate(body["context"]))
        else:
            result = context_from_transactions([Transaction.model_validate(t) for t in body["transactions"]],
                                               [Rule.model_validate(r) for r in body.get("rules", [])])
        return json.dumps(result.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (ValueError, TypeError, KeyError, ArithmeticError):
        return "invalid policy input"
