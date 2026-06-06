#!/usr/bin/env python3
"""Read-only verification report for P0/P1 Viva basket line matches."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import pandas as pd

from viva_tracker.db import connect_db, init_db
from viva_tracker.jobs import viva_store_spread
from viva_tracker.repository import list_url_master_grid

ARJAN_LABEL = "VIVA Supermarket, Arjan 2"
MURA_LABEL = "Viva Supermarket, Muraqqabat"

P0_P1_LINES = {
    12: {"rule": "Milba UHT 1L", "forbid_brand": None},
    13: {"rule": "missing (Hayatna UHT not in catalog)", "forbid_brand": "milba"},
    21: {"rule": "Qualiko frozen whole chicken", "forbid_brand": None},
    28: {"rule": "missing (watermelon)", "forbid_brand": None},
    31: {"rule": "D'Pollo chilled whole chicken", "forbid_brand": None},
    32: {"rule": "Beef mince Brazil", "forbid_brand": None},
}


def main() -> int:
    conn = connect_db()
    init_db(conn)
    grid = pd.DataFrame([dict(r) for r in list_url_master_grid(conn)])
    if grid.empty:
        print("No URL master rows.")
        return 1

    viva = grid[
        grid["store_label"].astype(str).str.strip().isin([ARJAN_LABEL, MURA_LABEL])
    ].copy()
    failures: list[str] = []

    print("=== P0/P1 Viva match verification ===\n")
    for line_no, spec in sorted(P0_P1_LINES.items()):
        rows = viva[viva["line_no"].astype(int) == line_no]
        print(f"Line {line_no}: expected {spec['rule']}")
        if rows.empty:
            print("  (no rows for Viva stores)\n")
            continue
        for _, r in rows.iterrows():
            store = str(r.get("store_label") or "")
            title = str(r.get("item_title") or "")
            status = str(r.get("status") or "")
            pack_match = str(r.get("pack_match") or "")
            url = str(r.get("source_url") or "")
            brand_token = str(r.get("brand_token") or "")
            print(
                f"  {store}: status={status} pack_match={pack_match} "
                f"brand_token={brand_token!r} title={title!r}"
            )
            if line_no == 13:
                if url.strip():
                    failures.append(f"Line 13 has URL on {store}")
                if "milba" in title.lower():
                    failures.append(f"Line 13 catalog title contains Milba on {store}")
            forbid = spec.get("forbid_brand")
            if forbid and forbid.lower() in title.lower():
                failures.append(f"Line {line_no} forbidden brand {forbid!r} in title on {store}")
        print()

    spread = viva_store_spread(conn)
    if spread.empty:
        spread_count = 0
    else:
        spread_count = int((spread["spread_ppb"].astype(float) > 0).sum())
    print(f"Cross-store price_per_base spreads (matched lines): {spread_count}")
    if not spread.empty and spread_count:
        print(spread[["line_no", "basket_item", "spread_ppb", "spread_pct"]].head(10).to_string(index=False))

    if failures:
        print("\nFAILURES:")
        for msg in failures:
            print(f"  - {msg}")
        return 1

    print("\nAll P0/P1 checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
