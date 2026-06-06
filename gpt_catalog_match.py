#!/usr/bin/env python3
"""
Match basket brand items to saved store catalogs using OpenAI, then write URL master rows.

Standalone CLI — run catalog_building.py first, then this when adding a store or basket item.

Config (project root ``.env`` or environment):
  OPENAI_API_KEY   required
  OPENAI_MODEL     optional (default: gpt-5.4-mini)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from viva_tracker.db import connect_db, init_db  # noqa: E402
from viva_tracker.match_engine import match_all_stores, match_store  # noqa: E402
from viva_tracker.match_progress import cli_match_progress_callback  # noqa: E402
from viva_tracker.repository import list_stores  # noqa: E402
from viva_tracker.settings import OPENAI_DEFAULT_MODEL  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="GPT match basket items to saved store catalogs.")
    parser.add_argument("--store-id", type=int, help="Store id from DB")
    parser.add_argument("--store-label", help="Store label (must match catalog filename)")
    parser.add_argument("--all-stores", action="store_true", help="Match every store with a catalog JSON")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", OPENAI_DEFAULT_MODEL))
    parser.add_argument("--batch-size", type=int, default=8, help="Basket lines per GPT call")
    parser.add_argument("--dry-run", action="store_true", help="Print matches without writing DB")
    parser.add_argument(
        "--all-items",
        action="store_true",
        help="Re-match every basket line, including rows that already have URLs",
    )
    args = parser.parse_args()
    skip_existing = not args.all_items

    if not args.all_stores and not args.store_id and not args.store_label:
        parser.error("Provide --store-id, --store-label, or --all-stores")

    if not os.environ.get("OPENAI_API_KEY"):
        parser.error(
            "OPENAI_API_KEY is required. Set it in a .env file at the project root or in your environment."
        )

    conn = connect_db()
    init_db(conn)

    if args.all_stores:
        results = match_all_stores(
            conn,
            model=args.model,
            batch_size=max(1, args.batch_size),
            dry_run=args.dry_run,
            skip_existing=skip_existing,
            progress_callback=cli_match_progress_callback,
        )
        for r in results:
            print(r)
        return

    stores = list_stores(conn)
    store = None
    if args.store_id:
        store = next((s for s in stores if int(s["store_id"]) == args.store_id), None)
    elif args.store_label:
        lbl = args.store_label.strip().lower()
        store = next(
            (s for s in stores if str(s["store_label"]).strip().lower() == lbl),
            None,
        )
    if store is None:
        parser.error("Store not found in database")

    label = str(store["store_label"])
    brand = str(store["brand_name"])
    print(f"Matching store: {label} (brand {brand}) model={args.model}")
    stats = match_store(
        conn,
        store_id=int(store["store_id"]),
        store_label=label,
        brand_name=brand,
        model=args.model,
        batch_size=max(1, args.batch_size),
        dry_run=args.dry_run,
        skip_existing=skip_existing,
        basket_item_ids=None,
        progress_callback=cli_match_progress_callback,
    )
    print(
        f"\nDone: ok={stats['ok']} pack_mismatch={stats['pack_mismatch']} "
        f"missing={stats['missing']} skipped={stats['skipped']}"
    )


if __name__ == "__main__":
    main()
