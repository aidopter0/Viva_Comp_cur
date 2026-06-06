#!/usr/bin/env python3
"""Identity verification for URL master across all stores (pack_mismatch is informational)."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import pandas as pd

from viva_tracker.db import connect_db, init_db
from viva_tracker.jobs import pack_mismatch_register
from viva_tracker.repository import list_url_master_grid
from viva_tracker.settings import CONFIG_DIR


def _load_expectations() -> dict:
    path = CONFIG_DIR / "match_expectations.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _rules_for_line(expectations: dict, brand_name: str, line_no: int) -> dict:
    brand_lines = (expectations.get("brand_lines") or {}).get(brand_name) or {}
    if str(line_no) in brand_lines:
        base = dict((expectations.get("lines") or {}).get(str(line_no)) or {})
        base.update(brand_lines[str(line_no)])
        return base
    if str(line_no) in (expectations.get("lines") or {}):
        return dict(expectations["lines"][str(line_no)])
    return {}


def _check_identity(row: dict, rules: dict) -> list[str]:
    failures: list[str] = []
    title = str(row.get("item_title") or "").lower()
    url = str(row.get("source_url") or "").strip()
    status = str(row.get("status") or "").lower()
    slug = str(row.get("slug") or url).lower()

    if rules.get("allow_missing"):
        if url:
            failures.append("expected missing but URL is set")
        return failures

    if not url:
        if status == "missing":
            if str(row.get("match_method") or "").lower() == "override":
                return failures
            failures.append("missing URL (no catalog match)")
        else:
            failures.append("no URL assigned")
        return failures

    for tok in rules.get("forbid_tokens") or []:
        if str(tok).lower() in title or str(tok).lower() in slug:
            failures.append(f"forbidden token {tok!r} in title/slug")

    tokens_any = rules.get("require_tokens_any") or []
    if tokens_any and not any(
        str(tok).lower() in title or str(tok).lower() in slug for tok in tokens_any
    ):
        failures.append(f"missing any required token from {tokens_any!r}")

    for tok in rules.get("require_tokens") or []:
        t = str(tok).lower()
        if t not in title and t not in slug:
            failures.append(f"missing required token {tok!r}")

    brand_token = str(row.get("brand_token") or "").strip()
    if rules.get("require_brand_in_title") and brand_token:
        bt = brand_token.lower()
        compact = "".join(ch for ch in (title + slug) if ch.isalnum())
        if bt not in title and bt not in compact:
            failures.append(f"brand {brand_token!r} not in product title/slug")

    return failures


def main() -> int:
    conn = connect_db()
    init_db(conn)
    grid = pd.DataFrame([dict(r) for r in list_url_master_grid(conn)])
    expectations = _load_expectations()

    if grid.empty:
        print("No URL master rows.")
        return 1

    identity_failures: list[str] = []
    checked = 0
    pack_ok_identity = 0

    line_rules = expectations.get("lines") or {}
    brand_rules = expectations.get("brand_lines") or {}
    all_lines = set(int(k) for k in line_rules.keys())
    for specs in brand_rules.values():
        all_lines.update(int(k) for k in specs.keys())

    url_by_store: dict[tuple[str, str], list[int]] = defaultdict(list)
    for _, r in grid.iterrows():
        store = str(r.get("store_label") or "")
        url = str(r.get("source_url") or "").strip()
        if url:
            url_by_store[(store, url)].append(int(r.get("line_no") or 0))

    for (store, url), lines in url_by_store.items():
        uniq = sorted(set(lines))
        if len(uniq) > 1:
            identity_failures.append(
                f"Duplicate URL in {store!r} across lines {uniq}: {url[:80]}"
            )

    for line_no in sorted(all_lines):
        subset = grid[grid["line_no"].astype(int) == line_no]
        for _, r in subset.iterrows():
            brand = str(r.get("brand_name") or r.get("grocery_chain_name") or "")
            rules = _rules_for_line(expectations, brand, line_no)
            if not rules:
                continue
            checked += 1
            fails = _check_identity(dict(r), rules)
            store = str(r.get("store_label") or "")
            st = str(r.get("status") or "")
            pm = str(r.get("pack_match") or "")
            if fails:
                identity_failures.append(
                    f"L{line_no} {brand} {store}: {'; '.join(fails)} "
                    f"(status={st} title={str(r.get('item_title') or '')[:50]!r})"
                )
            elif st == "pack_mismatch":
                pack_ok_identity += 1

    pm_df = pack_mismatch_register(conn)
    pm_count = len(pm_df) if not pm_df.empty else 0

    print("=== Identity verification (all stores) ===\n")
    print(f"Rows checked against expectations: {checked}")
    print(f"Identity-correct with pack_mismatch: {pack_ok_identity} (informational)")
    print(f"Pack mismatch register rows: {pm_count}")

    if identity_failures:
        print(f"\nIDENTITY FAILURES ({len(identity_failures)}):")
        for msg in identity_failures[:50]:
            print(f"  - {msg}")
        if len(identity_failures) > 50:
            print(f"  ... and {len(identity_failures) - 50} more")
        return 1

    print("\nAll identity checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
