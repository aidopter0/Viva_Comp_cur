from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from viva_tracker.db import connect_db, init_db
from viva_tracker.jobs import export_csv
from viva_tracker.settings import BASKET_CSV_PATH, EXPORTS_DIR


def main() -> None:
    parser = argparse.ArgumentParser(description="Export latest basket comparison data.")
    parser.add_argument(
        "--format",
        choices=["wide", "long", "summary", "audit", "history", "excel"],
        default="wide",
        help="Export shape (default: wide)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output path (default depends on format)",
    )
    parser.add_argument(
        "--include-pack-mismatch",
        action="store_true",
        help="Include pack_mismatch rows in summary competitiveness metrics",
    )
    args = parser.parse_args()

    default_names = {
        "wide": "latest_comparison.csv",
        "long": "latest_comparison_long.csv",
        "summary": "weekly_summary.csv",
        "audit": "extraction_audit.csv",
        "history": "price_history_long.csv",
        "excel": "price_basket.xlsx",
    }
    out_path = args.output or (EXPORTS_DIR / default_names[args.format])

    conn = connect_db()
    init_db(conn)
    saved = export_csv(
        conn,
        BASKET_CSV_PATH,
        out_path,
        fmt=args.format,
        include_pack_mismatch=args.include_pack_mismatch,
    )
    print(f"Exported ({args.format}): {saved}")


if __name__ == "__main__":
    main()
