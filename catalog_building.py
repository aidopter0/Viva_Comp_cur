#!/usr/bin/env python3
"""
Build and save per-store Talabat grocery catalogs as JSON under ``catalogs/``.

Standalone CLI — not used by the Streamlit app.
Overwrites the catalog file for the store on each run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from viva_tracker.catalog_build import (  # noqa: E402
    build_and_save_catalog_for_store,
    cli_catalog_progress_callback,
)
from viva_tracker.catalog_index import catalog_path_for_store  # noqa: E402
from viva_tracker.db import connect_db, init_db  # noqa: E402
from viva_tracker.repository import list_stores  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Talabat store catalog JSON files.")
    parser.add_argument(
        "--store-id",
        type=int,
        action="append",
        dest="store_ids",
        help="Store id from DB (repeatable).",
    )
    parser.add_argument(
        "--store-label",
        action="append",
        dest="store_labels",
        help="Store label exact match (repeatable).",
    )
    parser.add_argument("--all-stores", action="store_true", help="Build for every active store.")
    parser.add_argument(
        "--page-delay",
        type=float,
        default=0.85,
        help="Seconds between category page requests (default 0.85).",
    )
    args = parser.parse_args()

    if not args.all_stores and not args.store_ids and not args.store_labels:
        parser.error("Specify --store-id, --store-label, and/or --all-stores")

    conn = connect_db()
    init_db(conn)
    stores = list_stores(conn)
    if not stores:
        print("No active stores in database.")
        return

    selected = []
    if args.all_stores:
        selected = list(stores)
    else:
        want_ids = set(args.store_ids or [])
        want_labels = {s.strip().lower() for s in (args.store_labels or [])}
        for s in stores:
            if int(s["store_id"]) in want_ids:
                selected.append(s)
            elif str(s["store_label"]).strip().lower() in want_labels:
                selected.append(s)
        seen: set[int] = set()
        uniq = []
        for s in selected:
            sid = int(s["store_id"])
            if sid not in seen:
                seen.add(sid)
                uniq.append(s)
        selected = uniq

    if not selected:
        print("No stores matched your filters.")
        return

    for s in selected:
        label = str(s["store_label"])
        url = str(s["talabat_url"] or "").strip()
        if not url:
            print(f"Skip {label}: no talabat_url")
            continue
        out_path = catalog_path_for_store(label)
        print(f"\nBuilding catalog: {label} ({s['brand_name']})")
        print(f"  -> {out_path}")
        result = build_and_save_catalog_for_store(
            store_id=int(s["store_id"]),
            store_label=label,
            brand_name=str(s["brand_name"]),
            talabat_url=url,
            page_delay_s=args.page_delay,
            log=True,
            progress_callback=cli_catalog_progress_callback,
        )
        print(f"  saved ({result['product_count']} products)")


if __name__ == "__main__":
    main()
