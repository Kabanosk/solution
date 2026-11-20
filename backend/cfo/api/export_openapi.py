"""Write the OpenAPI document: `python -m cfo.api.export_openapi docs/openapi.json`."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from cfo.api.app import create_app


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m cfo.api.export_openapi <output.json>", file=sys.stderr)
        return 2
    out = Path(argv[0])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(create_app().openapi(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
