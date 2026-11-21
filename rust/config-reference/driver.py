"""Characterize original settings without ever reading repository credentials."""
import json
import math
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from cfo.config import Settings
from cfo.api.settings import ApiSettings
from scout.config import ScoutSettings, tenant_settings
from cfo.tenancy import Tenant, bind_tenant


def clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN" if math.isnan(value) else "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clean(item) for item in value]
    return value


try:
    data = json.load(sys.stdin)
    if not isinstance(data, dict) or data.get("profile") not in ["cfo", "api", "scout"]:
        raise ValueError()
except (ValueError, TypeError):
    print('{"error":"invalid_input"}')
    sys.exit()
try:
    os.environ.clear()
    os.environ.update(data.get("env", {}))
    with tempfile.TemporaryDirectory() as directory:
        repo = Path(directory) / "repo.env"
        cwd = Path(directory) / "cwd.env"
        repo.write_text(data.get("repo", ""))
        cwd.write_text(data.get("cwd", ""))
        settings = {"cfo": Settings, "api": ApiSettings, "scout": ScoutSettings}[data["profile"]](_env_file=(repo, cwd))
        if "tenant" in data:
            with bind_tenant(Tenant(**data["tenant"])):
                settings = tenant_settings(settings)
        result = clean(settings.model_dump(mode="json"))
except Exception:
    result = {"error": "invalid_settings"}
print(json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(",", ":")))
