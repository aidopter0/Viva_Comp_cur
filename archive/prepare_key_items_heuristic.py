"""
Unsupported reference: heuristic key_items_prepared.json from key_items.txt.

The supported pipeline uses gemini_key_items_builder.py -> key_items_prepared_gemini.json.
Run from repo root: python archive/prepare_key_items_heuristic.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from key_item_line_parse import (
    build_prepared_query_fields,
    glue_weights_in_text,
    parse_line,
    search_synonym_for_row,
)

DEFAULT_INPUT = Path("config/key_items.txt")
DEFAULT_OUTPUT = Path("archive/config/key_items_prepared.json")


def main() -> None:
    ap = argparse.ArgumentParser(description="(Reference) Build heuristic key_items_prepared.json")
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = ap.parse_args()

    if not args.input.is_file():
        print(f"File not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    lines = [ln.strip() for ln in args.input.read_text(encoding="utf-8").splitlines() if ln.strip()]
    out: list[dict] = []
    for i, raw in enumerate(lines, 1):
        expected, q = parse_line(raw)
        _, search_queries = build_prepared_query_fields(expected, q)
        search_query = search_queries[0] if search_queries else glue_weights_in_text(q)
        row: dict = {
            "line": i,
            "raw": raw,
            "search_query": search_query,
            "label": search_query,
        }
        syn = search_synonym_for_row(expected)
        if syn:
            row["search_synonym"] = syn
        out.append(row)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(out)} rows to {args.output}", flush=True)


if __name__ == "__main__":
    main()
