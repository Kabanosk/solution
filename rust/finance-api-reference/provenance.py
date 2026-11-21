import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
for path,digest in json.loads((ROOT/"rust/finance-api/source-provenance.json").read_text())["sources"].items():
    if hashlib.sha256((ROOT/path).read_bytes()).hexdigest()!=digest:
        raise RuntimeError("Financial source reference is stale; review and record again")
