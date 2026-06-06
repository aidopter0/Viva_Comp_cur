#!/usr/bin/env python3
"""Re-match a single basket line across all stores (overwrites existing URLs)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from viva_tracker.catalog_index import catalog_exists
from viva_tracker.db import connect_db, init_db
from viva_tracker.match_engine import match_store
from viva_tracker.repository import list_basket_items, list_stores


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--line-no", type=int, required=True)
    args = parser.parse_args()

    conn = connect_db()
    init_db(conn)
    bid = next(
        int(r["basket_item_id"])
        for r in list_basket_items(conn)
        if int(r["line_no"]) == args.line_no
    )
    for s in list_stores(conn):
        label = str(s["store_label"])
        if not catalog_exists(label):
            print(f"{label}: no catalog")
            continue
        stats = match_store(
            conn,
            store_id=int(s["store_id"]),
            store_label=label,
            brand_name=str(s["brand_name"]),
            skip_existing=False,
            basket_item_ids=[bid],
        )
        print(
            f"{label}: ok={stats['ok']} pack_mismatch={stats['pack_mismatch']} "
            f"missing={stats['missing']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
