"""Apply `schema.sql` to `DATABASE_URL`.

    python -m cfo.db.migrate          # apply
    python -m cfo.db.migrate --check  # dry run: apply in a rolled-back transaction, exit 0 if clean
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

from cfo.config import get_settings

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def apply_schema(database_url: str, *, check: bool = False) -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with psycopg.connect(database_url) as conn:
        conn.execute(sql)
        if check:
            conn.rollback()
        else:
            conn.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cfo.db.migrate")
    parser.add_argument("--check", action="store_true", help="verify the schema applies, change nothing")
    args = parser.parse_args(argv)
    try:
        apply_schema(get_settings().database_url, check=args.check)
    except psycopg.Error as exc:
        print(f"schema failed: {exc}", file=sys.stderr)
        return 1
    print("schema OK (dry run)" if args.check else "schema applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
