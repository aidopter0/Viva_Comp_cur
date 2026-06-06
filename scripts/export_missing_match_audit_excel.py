#!/usr/bin/env python3
"""Export missing match audit CSV to a categorized Excel workbook."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from viva_tracker.missing_match_audit import export_missing_match_audit_excel
from viva_tracker.settings import EXPORTS_DIR


def main() -> int:
    csv_path = EXPORTS_DIR / "missing_match_audit.csv"
    out_path = EXPORTS_DIR / "missing_match_audit.xlsx"
    if not csv_path.is_file():
        print(f"Missing {csv_path} — run scripts/audit_missing_matches.py first.")
        return 1

    written = export_missing_match_audit_excel(csv_path, out_path)
    if written != out_path:
        print("(target file was locked/open; wrote a timestamped copy instead)")

    print(f"Wrote {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
