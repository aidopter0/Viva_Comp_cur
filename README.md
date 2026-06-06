# Viva Basket Tracker

Greenfield Streamlit + CLI application to manage a dynamic retail basket, stores, UUIDs, item URL master, and historical price extraction.

## Quick Start

1. Install dependencies:
   - `python -m pip install -r requirements.txt`
2. Copy env and set OpenAI key (needed only for URL matching):
   - copy `env.example` to `.env` and set `OPENAI_API_KEY`
3. Initialize DB from basket CSV:
   - `python scripts/init_from_basket.py`
4. Launch app:
   - `streamlit run app.py`

## Workflow

### One-time / rare (new store or basket item)

1. **Setup** — add store Talabat URLs
2. **Setup** — select store → **Build catalog** (saves JSON under `catalogs/`)
3. **Setup** — **Run GPT URL matching** (uses GPT-5.5 + full catalog search → URL master; by default skips items that already have URLs)
4. **URL Master** — review matches, manually fix any rows

### Day-to-day

1. **Run extraction now** (sidebar) or `python scripts/extract_prices.py`
2. **Export latest CSV** or view **Analytics** tab (normalized price vs Viva)

## CLI Jobs

- Refresh store UUIDs:
  - `python scripts/refresh_store_uuids.py`
- Build store catalog JSON:
  - `python catalog_building.py --store-label "Store Name"`
- GPT URL matching (requires catalog JSON + `OPENAI_API_KEY`):
  - `python gpt_catalog_match.py --store-label "Store Name"`
  - `python gpt_catalog_match.py --store-label "Store Name" --all-items` (re-match rows that already have URLs)
  - For selected items only, use **URL Master** (select rows → Get URL GPT) in Streamlit
  - `python gpt_catalog_match.py --all-stores` (all stores with catalogs)
- Extract latest prices:
  - `python scripts/extract_prices.py`
- Export latest comparison CSV:
  - `python scripts/export_latest_csv.py`

## Matching model (v2)

Catalog matching is **basket-first** and runs in rounds:

1. **Round 1 (strict):** every `basket_label` token in the catalog title/slug **and** exact pack; cheapest row is GPT-verified. Loose fresh (`produce` / `meat`) is pack-agnostic, picked per kg.
2. **Round 1b (fallback):** same all-token gate with close pack; top 10% pool → GPT pick.
3. **Round 2 (near match):** when Round 1b is empty, retrieve rows sharing a distinctive token within +/-12% pack, then GPT picks by customer intent (same category/need). Recorded as `gpt_near_pick`.

Chain `mapped_name`, `brand_token`, and `generic_description` are **reference only** and do not affect matching.

Full details: [docs/MATCHING.md](docs/MATCHING.md)

Re-match all items after upgrading: `python gpt_catalog_match.py --all-stores --all-items`

Audit missing rows: `python scripts/audit_missing_matches.py`

## Match statuses

| Status | Meaning |
|--------|---------|
| `ok` | URL found with exact, close, or normalized (per-kg loose fresh) pack |
| `pack_mismatch` | Product matched but target pack outside the close band — normalized price still computed |
| `missing` | No suitable catalog product after all rounds |

## Normalized pricing

Prices are converted to a comparable base unit from basket pack mapping:

- weight → price per kg
- volume → price per L
- count → price per item

Export CSV and Analytics include `price_per_base` and gap vs Viva (%).
