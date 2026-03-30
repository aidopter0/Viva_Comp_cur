"""
Extract raw product data from Talabat via the public nextApi search endpoint.

Reads config/key_items_prepared.json (from prepare_key_items.py), runs first-page
HTTP search for each line's search_query, merges results by product id, and
writes per-store raw JSON.

No scoring, no ranking, no CSV — cleanup_and_rank.py handles that.
No imports from prepare_key_items or cleanup_and_rank.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_STORE_UUID = "ec0d3c95-f616-44fd-a196-a5da80616341"
STORE_UUID_VIVA_MOTOR_CITY_730466 = "c9283f38-5945-4940-816c-e273bda7188f"
COUNTRY_ID_UAE = "4"


# ── JSON loader ────────────────────────────────────────────────────

def load_prepared_key_items(path: Path) -> list[dict]:
    """Load config/key_items_prepared.json."""
    if path.suffix.lower() == ".txt":
        raise ValueError(
            "Extract expects prepared JSON, not raw key_items.txt. "
            "Run: python prepare_key_items.py"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError(f"Prepared key items must be a non-empty JSON array: {path}")
    for row in data:
        if not isinstance(row, dict):
            raise ValueError(f"Each prepared row must be an object: {path}")
        for k in ("line", "raw", "search_query", "label"):
            if k not in row:
                raise ValueError(f"Prepared row missing key {k!r} (run prepare_key_items.py): {path}")
        sq = row["search_query"]
        if not isinstance(sq, str) or not sq.strip():
            raise ValueError(f"Prepared row search_query must be a non-empty string: {path}")
    return data


# ── HTTP helpers ───────────────────────────────────────────────────

def api_request(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            "accept": "application/json",
            "accept-language": "en-US",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "sourceapp": "web",
            "appbrand": "1",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_search_first_page(
    store_uuid: str,
    query: str,
    limit: int = 50,
) -> list[dict]:
    """Single GET for /products search (offset=0 only)."""
    params = urllib.parse.urlencode(
        {
            "countryId": COUNTRY_ID_UAE,
            "query": query,
            "limit": str(limit),
            "offset": "0",
            "isDarkstore": "false",
            "isMigrated": "true",
        }
    )
    base = f"https://www.talabat.com/nextApi/groceries/stores/{store_uuid}/products"
    url = f"{base}?{params}"
    try:
        data = api_request(url)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} for {url}") from e
    return list(data.get("items") or [])


# ── Delay helpers ──────────────────────────────────────────────────

def resolve_fetch_delays(
    fast: bool,
    query_delay: float | None,
    line_delay: float | None,
) -> tuple[float, float]:
    if fast:
        return (
            0.05 if query_delay is None else query_delay,
            0.1 if line_delay is None else line_delay,
        )
    return (
        0.2 if query_delay is None else query_delay,
        0.3 if line_delay is None else line_delay,
    )


# ── Extract run ────────────────────────────────────────────────────

def run(
    key_items_path: Path,
    out_json: Path,
    store_uuid: str,
    *,
    query_delay_s: float = 0.2,
    line_delay_s: float = 0.3,
) -> None:
    """Fetch raw products for each prepared line and write a single JSON file."""
    out_json.parent.mkdir(parents=True, exist_ok=True)
    prepared = load_prepared_key_items(key_items_path)
    results: list[dict] = []
    for prep in prepared:
        i = int(prep["line"])
        raw = str(prep["raw"])
        label = str(prep["label"])
        search_query = str(prep["search_query"]).strip()
        syn_raw = prep.get("search_synonym")
        search_synonym = str(syn_raw).strip() if syn_raw not in (None, "") else None
        merged_by_id: dict[str, dict] = {}
        fetch_error: str | None = None
        try:
            batch = fetch_search_first_page(store_uuid, search_query)
        except Exception as e:
            fetch_error = str(e)
        else:
            for it in batch:
                merged_by_id[it["id"]] = it
            time.sleep(query_delay_s)
        row_out: dict = {
            "line": i,
            "raw": raw,
            "label": label,
            "search_query": search_query,
            "products": list(merged_by_id.values()),
            "error": fetch_error,
        }
        if search_synonym:
            row_out["search_synonym"] = search_synonym
        results.append(row_out)
        time.sleep(line_delay_s)

    out_json.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")


# ── CLI ────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Extract raw Talabat products for key items.")
    ap.add_argument(
        "--key-items",
        type=Path,
        default=Path("config/key_items_prepared.json"),
    )
    ap.add_argument("--out-json", type=Path, default=Path("output/key_items_raw.json"))
    ap.add_argument("--store-uuid", default=DEFAULT_STORE_UUID)
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--query-delay", type=float, default=None)
    ap.add_argument("--line-delay", type=float, default=None)
    args = ap.parse_args()
    if not args.key_items.is_file():
        print(f"File not found: {args.key_items}", file=sys.stderr)
        sys.exit(1)
    query_d, line_d = resolve_fetch_delays(args.fast, args.query_delay, args.line_delay)
    run(
        args.key_items,
        args.out_json,
        args.store_uuid,
        query_delay_s=query_d,
        line_delay_s=line_d,
    )
    print(f"Wrote {args.out_json}", flush=True)


if __name__ == "__main__":
    main()
