"""
Run the full pipeline for all stores in config/talabat_stores.json:

  0. (optional) Regenerate config/key_items_prepared_gemini.json when key_items.txt changed
  1. EXTRACT: fetch raw product data per store (talabat_extract.py)
  2. CLEANUP: score, rank, write CSV, append consolidated (cleanup_and_rank.py)
  3. DASHBOARD: rebuild consolidated_dashboard.csv (or defer until after Gemini when rerank is on)
  4. Gemini reranking (default on) and consolidated append from reranked CSV when enabled

Use --parallel-stores N and/or --fast to reduce wall time.
Re-run anytime; consolidated file grows with each run (new extraction_date).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from cleanup_and_rank import (
    append_consolidated,
    load_stores,
    rebuild_dashboard_slice,
    rerank_file,
    score_and_rank_store,
    store_safe_label,
    _write_reranked_csv,
)
from prepared_key_items_sync import ensure_key_items_gemini_json, load_dotenv_if_present
from talabat_extract import resolve_fetch_delays
from talabat_extract import run as run_extract

CONSOLIDATED_CSV = Path("output/consolidated_pricing.csv")
DASHBOARD_CSV = Path("output/consolidated_dashboard.csv")
STORES_JSON = Path("config/talabat_stores.json")
DEFAULT_KEY_ITEMS = Path("config/key_items_prepared_gemini.json")
DEFAULT_OUT_DIR = Path("output/stores")


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch, score, and consolidate all Talabat stores")
    ap.add_argument(
        "--key-items",
        type=Path,
        default=DEFAULT_KEY_ITEMS,
        help="Prepared JSON from gemini_key_items_builder.py (default: config/key_items_prepared_gemini.json)",
    )
    ap.add_argument("--stores-json", type=Path, default=STORES_JSON)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--consolidated", type=Path, default=CONSOLIDATED_CSV)
    ap.add_argument("--dashboard-csv", type=Path, default=DASHBOARD_CSV)
    ap.add_argument("--extraction-date", default="", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--min-ratio", type=float, default=0.55)
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument(
        "--no-gemini-after",
        dest="gemini_after",
        action="store_false",
        help="Skip Gemini reranking after scoring (default: run rerank when API key is set)",
    )
    ap.set_defaults(gemini_after=True)
    ap.add_argument("--gemini-rerank-all", action="store_true")
    ap.add_argument(
        "--skip-key-items-prep",
        action="store_true",
        help="Do not auto-regenerate key_items_prepared_gemini.json from key_items.txt",
    )
    ap.add_argument(
        "--force-key-items-prep",
        action="store_true",
        help="Always regenerate key_items_prepared_gemini.json before extract",
    )
    ap.add_argument(
        "--parallel-stores",
        type=int,
        default=1,
        metavar="N",
        help="N store fetches concurrently (default 1 = sequential)",
    )
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--query-delay", type=float, default=None)
    ap.add_argument("--line-delay", type=float, default=None)
    args = ap.parse_args()

    load_dotenv_if_present()

    ensure_key_items_gemini_json(skip=args.skip_key_items_prep, force=args.force_key_items_prep)

    if not args.key_items.is_file():
        print(f"File not found: {args.key_items}", file=sys.stderr)
        sys.exit(1)
    if not args.stores_json.is_file():
        print(f"File not found: {args.stores_json}", file=sys.stderr)
        sys.exit(1)

    extraction_date = args.extraction_date.strip() or date.today().isoformat()
    stores = load_stores(args.stores_json)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    args.consolidated.parent.mkdir(parents=True, exist_ok=True)

    query_d, line_d = resolve_fetch_delays(args.fast, args.query_delay, args.line_delay)

    # ── Step 1: Extract raw data ───────────────────────────────────

    def extract_one(entry: dict) -> tuple[str, Path]:
        label = entry["label"]
        store_uuid = entry["store_uuid"]
        safe = store_safe_label(label)
        raw_json = args.out_dir / f"{safe}.raw.json"
        print(f"[{label}] extract start", flush=True)
        run_extract(
            args.key_items,
            raw_json,
            store_uuid,
            query_delay_s=query_d,
            line_delay_s=line_d,
        )
        print(f"[{label}] extract done -> {raw_json}", flush=True)
        return label, raw_json

    n_workers = max(1, args.parallel_stores)
    extract_results: dict[str, Path] = {}
    if n_workers == 1:
        for entry in stores:
            label, raw_json = extract_one(entry)
            extract_results[label] = raw_json
    else:
        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            futures = {ex.submit(extract_one, e): e for e in stores}
            for fut in as_completed(futures):
                label, raw_json = fut.result()
                extract_results[label] = raw_json

    # ── Step 2: Score, rank, write CSV ─────────────────────────────

    for entry in stores:
        label = entry["label"]
        safe = store_safe_label(label)
        raw_json = extract_results.get(label)
        if raw_json is None or not raw_json.is_file():
            print(f"[{label}] raw JSON missing, skipping cleanup", flush=True)
            continue
        scored_json = args.out_dir / f"{safe}.json"
        scored_csv = args.out_dir / f"{safe}.csv"
        score_and_rank_store(
            raw_json,
            scored_json,
            scored_csv,
            min_ratio=args.min_ratio,
            top_k=max(1, args.top_k),
        )
        if not args.gemini_after:
            append_consolidated(args.consolidated, label, extraction_date, scored_csv)
        print(f"[{label}] scored -> {scored_csv}", flush=True)

    # ── Step 3: Dashboard (skip intermediate rebuild if Gemini rerank will refresh) ─

    if not args.gemini_after:
        n_dash = rebuild_dashboard_slice(
            args.out_dir, args.stores_json, args.dashboard_csv, extraction_date
        )
        print(f"Dashboard {args.dashboard_csv}: {n_dash} rows for {extraction_date}.", flush=True)

    # ── Step 4: Gemini rerank (default) ────────────────────────────

    if args.gemini_after:
        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print(
                "Gemini rerank is on by default but no GOOGLE_API_KEY or GEMINI_API_KEY is set. "
                "Use --no-gemini-after to skip reranking, or set an API key.",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            from google import genai
        except ImportError:
            print("pip install google-genai", file=sys.stderr)
            sys.exit(1)
        client = genai.Client(api_key=api_key)
        model = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")

        for entry in stores:
            safe = store_safe_label(entry["label"])
            scored_json = args.out_dir / f"{safe}.json"
            if not scored_json.is_file():
                continue
            data = rerank_file(
                scored_json,
                client,
                model,
                score_below=0.75,
                chunk_size=25,
                dry_run=False,
                rerank_all=args.gemini_rerank_all,
            )
            rr_json = scored_json.with_name(f"{safe}.reranked.json")
            rr_json.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            rr_csv = scored_json.with_name(f"{safe}.reranked.csv")
            _write_reranked_csv(data, rr_csv)
            print(f"  Wrote {rr_json} and {rr_csv}", flush=True)

        for entry in stores:
            label = entry["label"]
            safe = store_safe_label(label)
            rr_csv = args.out_dir / f"{safe}.reranked.csv"
            if rr_csv.is_file():
                append_consolidated(args.consolidated, label, extraction_date, rr_csv)

        n_dash = rebuild_dashboard_slice(
            args.out_dir, args.stores_json, args.dashboard_csv, extraction_date
        )
        print(f"Dashboard {args.dashboard_csv}: {n_dash} rows for {extraction_date} (after Gemini rerank).", flush=True)

    print(f"Done. Extraction date: {extraction_date}", flush=True)


if __name__ == "__main__":
    main()
