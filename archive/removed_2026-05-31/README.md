# Archived modules/scripts — 2026-05-31

Removed during the basket-first matching v2 cleanup. Kept here for reference; not on the import path.

## src/viva_tracker
- `basket_name_split.py` — GPT chain-name → brand_token/generic splitter. Dead after v2 (matching uses `basket_label` + pack only; `app.py` no longer calls `apply_name_splits_to_line`).

## scripts
- `refresh_item_urls.py` — duplicate of `match_item_urls.py`; both equal `gpt_catalog_match.py --all-stores`.
- `match_item_urls.py` — thin wrapper around `match_all_stores()`; superseded by `gpt_catalog_match.py --all-stores`.
- `backfill_name_splits.py` — deprecation stub for the removed name-split match path.
- `audit_match_overrides.py` — one-off seeder of `config/match_overrides.json` (hardcoded v1 slug/missing corrections).
- `apply_viva_url_corrections.py` — one-off Viva URL-master correction script (hardcoded slugs); superseded by `config/match_overrides.json`.
