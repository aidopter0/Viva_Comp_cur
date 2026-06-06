#!/usr/bin/env python3
"""Merge catalog-backed slug overrides for rows with a basket shortlist match."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

AUTO_NOTE_PREFIX = "Auto basket slug"

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from viva_tracker.basket_match_spec import BasketMatchSpec, parse_pack_from_basket_label
from viva_tracker.basket_matcher import cheapest_row, shortlist_rows
from viva_tracker.catalog_index import CatalogIndex, catalog_exists, load_catalog_file, slug_from_row
from viva_tracker.match_overrides import load_match_overrides
from viva_tracker.repository import list_url_master_grid
from viva_tracker.settings import CONFIG_DIR


def _override_key(entry: dict) -> tuple[str, str, int]:
    return (
        str(entry.get("brand_name") or "").strip().lower(),
        str(entry.get("store_label") or "").strip().lower(),
        int(entry.get("line_no") or 0),
    )


def _build_spec(row: dict) -> BasketMatchSpec | None:
    label = str(row.get("basket_label") or "").strip()
    if not label:
        return None
    brand_name = str(row.get("grocery_chain_name") or "")
    store_label = str(row.get("store_label") or "")
    pack_qty, pack_unit = parse_pack_from_basket_label(label)
    return BasketMatchSpec.from_basket_row(
        line_no=int(row.get("line_no") or 0),
        basket_item_id=int(row.get("basket_item_id") or 0),
        basket_label=label,
        category=str(row.get("category") or ""),
        match_group=str(row.get("match_group") or "packaged"),
        line_role=str(row.get("line_role") or ""),
        store_label=store_label,
        store_brand_name=brand_name,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prune-auto",
        action="store_true",
        help="Remove previously auto-generated slug overrides and exit.",
    )
    args = parser.parse_args()

    cfg_path = CONFIG_DIR / "match_overrides.json"
    data = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {"version": 1, "entries": []}
    entries: list[dict] = list(data.get("entries") or [])

    if args.prune_auto:
        before = len(entries)
        entries = [e for e in entries if not str(e.get("note") or "").startswith(AUTO_NOTE_PREFIX)]
        data["entries"] = entries
        cfg_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Pruned {before - len(entries)} auto-generated slug overrides; {len(entries)} remain.")
        return 0

    conn_path = ROOT / "data" / "viva_tracker.db"
    from viva_tracker.db import connect_db, init_db

    conn = connect_db()
    init_db(conn)
    load_match_overrides()
    by_key = {_override_key(e): e for e in entries}

    added = 0
    updated = 0
    skipped = 0
    index_cache: dict[str, CatalogIndex | None] = {}

    for raw in list_url_master_grid(conn):
        row = dict(raw)
        if str(row.get("status") or "").lower() != "missing":
            continue

        brand_name = str(row.get("grocery_chain_name") or "")
        store_label = str(row.get("store_label") or "")
        line_no = int(row.get("line_no") or 0)
        key = (brand_name.lower(), store_label.lower(), line_no)
        existing = by_key.get(key)
        if existing and str(existing.get("action") or "").lower() == "missing":
            skipped += 1
            continue
        if existing and str(existing.get("action") or "").lower() == "slug" and existing.get("product_slug"):
            skipped += 1
            continue

        spec = _build_spec(row)
        if spec is None:
            skipped += 1
            continue

        if store_label not in index_cache:
            index_cache[store_label] = (
                CatalogIndex(load_catalog_file(store_label)) if catalog_exists(store_label) else None
            )
        index = index_cache[store_label]
        if index is None:
            continue

        shortlist = shortlist_rows(index, spec)
        best = cheapest_row(shortlist)
        if best is None:
            continue
        slug = slug_from_row(best)
        if not slug:
            continue

        entry = {
            "brand_name": brand_name,
            "store_label": store_label,
            "line_no": line_no,
            "action": "slug",
            "product_slug": slug,
            "note": f"{AUTO_NOTE_PREFIX} ({best.get('product_name', '')})",
        }
        if existing:
            existing.update(entry)
            updated += 1
        else:
            entries.append(entry)
            by_key[key] = entry
            added += 1

    data["entries"] = entries
    cfg_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Added {added}, updated {updated}, skipped {skipped}; {len(entries)} overrides total.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
