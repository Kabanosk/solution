"""Start a credential-free source snapshot for public HTTP characterization only."""
import os
from pathlib import Path
import shutil
import sys
import tempfile

root = Path(__file__).resolve().parents[2]
test_database = os.environ.get("SCOUT_RUST_TEST_DATABASE_URL")
if test_database:
    from urllib.parse import urlparse
    if urlparse(test_database).hostname not in ("127.0.0.1", "localhost"):
        raise ValueError("recording database must be local")
with tempfile.TemporaryDirectory(prefix="scout-rust-reference-") as temporary:
    backend = Path(temporary) / "backend"
    for package in ("cfo", "scout"):
        shutil.copytree(root / "backend" / package, backend / package,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".env*"))
    # No inherited credentials, .env files, model endpoints or production database.
    allowed = {key: value for key, value in os.environ.items()
               if key in {"PATH", "HOME", "LANG", "LC_ALL", "PORT"}}
    os.environ.clear()
    os.environ.update(allowed)
    os.environ["DATABASE_URL"] = test_database or "postgresql://invalid:invalid@127.0.0.1:1/invalid"
    os.environ["LLM_BASE_URL"] = "http://127.0.0.1:1/v1"
    os.environ["LLM_API_KEY"] = "recording-no-credentials"
    os.chdir(temporary)
    sys.path.insert(0, str(backend))
    import uvicorn
    from cfo.api.app import app
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8000")), log_level="error")
