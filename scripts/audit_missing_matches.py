#!/usr/bin/env python3
"""Classify remaining missing URL-master rows by root cause."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from viva_tracker.db import connect_db, init_db
from viva_tracker.missing_match_audit import refresh_missing_match_audit


def main() -> int:
    conn = connect_db()
    init_db(conn)
    result = refresh_missing_match_audit(conn)

    print(f"Missing rows: {result['missing_count']}")
    print("By bucket:")
    for bucket, count in sorted(
        result["bucket_summary"].items(), key=lambda x: (-x[1], x[0])
    ):
        print(f"  {count:3d}  {bucket}")
    print(f"\nWrote {result['csv_path']}")
    print(f"Wrote {result['xlsx_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
