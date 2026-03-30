"""
Build config/key_items_prepared_gemini.json: LLM-cleaned search_query + label per line
via one Gemini batch call (deterministic JSON).

Uses parse_line + search_synonym_for_row from prepare_key_items.py (no circular imports).

Examples:
  python prepare_key_items_gemini.py
  python prepare_key_items_gemini.py --input config/key_items.txt --output config/key_items_prepared_gemini.json
  python prepare_key_items_gemini.py --dry-run   # no API; heuristic fallback from parse_line
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from prepare_key_items import glue_weights_in_text, normalize_for_compare, parse_line, search_synonym_for_row

DEFAULT_INPUT = Path("config/key_items.txt")
DEFAULT_OUTPUT = Path("config/key_items_prepared_gemini.json")


def _load_dotenv_if_present() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = Path(__file__).resolve().parent
    for name in (".env", "env"):
        p = root / name
        if p.is_file():
            load_dotenv(p)
            break


def _parse_json_object(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError("No JSON object in model response")
    return json.loads(m.group(0))


SYSTEM_INSTRUCTION = """
You are a data cleaning assistant for grocery product key lines used on Talabat UAE.

For each numbered raw string, output TWO fields:
1) search_query: lowercase, normalized, compact — good for an in-app product search box (SEO-style).
2) label: clean human-readable title case for reports and dashboards (not all-caps).

Rules:
- Strip category prefixes entirely (e.g. EGGS, BANANA, PROTEINS, MILK & YOGURT, OIL, RICE, WATER,
  CLEANING TOOLS & DISPOSABLES, PASTA & INSTANT FOOD, etc.) — they are not part of the product name.
- Remove stray leading digits from legacy formats (e.g. "2White" -> treat as product text after strip).
- Normalize units: use glued forms where natural for search: 5kg, 500g, 1.5L, 500ml, 1L (no duplicate units like 1L1L or 900g900g — keep one).
- Expand size codes: (S) -> Small, (L) -> Large when they refer to pack size.
- Packs: "30's" / 30pc style -> prefer "30 pack" in label; search_query can use "30 pack" or "30pc" consistently.
- Phrases like "1 x 5pack" / "1 x 4pack" -> simplify to "5 pack" / "4 pack" in label; search_query similar.
- Keep quantity and pack structure (e.g. "6 x 1.5L" water) — do not drop counts.
- Do NOT invent brands; keep text faithful to the raw line.
- Banana Premium ~1 kg: search_query must be "banana 1kg"; label e.g. "Banana Premium 1 kg".
- Whole chicken 900g: search_query should help find griller/chicken products (e.g. "chicken 900g"); label clear e.g. "Whole Chicken 900 g".

Output must be valid JSON only (no markdown).
"""


def _build_prompt(lines: list[str]) -> str:
    numbered = "\n".join(f"{i}. {raw}" for i, raw in enumerate(lines, start=1))
    return (
        SYSTEM_INSTRUCTION.strip()
        + "\n\nFew-shot (raw -> search_query / label):\n"
        + '- "EGGS 2White eggs 30\'s (S)30pc" -> search_query: "white eggs small 30 pack", '
        + 'label: "White Eggs Small 30 Pack"\n'
        + '- "BANANABanana Premium1kg" -> search_query: "banana 1kg", label: "Banana Premium 1 kg"\n'
        + '- "RICEJasmine Rice5kg" -> search_query: "jasmine rice 5kg", label: "Jasmine Rice 5 kg"\n'
        + '- "WATERBottle water6 x 1.5L" -> search_query: "bottle water 6 x 1.5l", label: "Bottle Water 6 x 1.5 L"\n'
        + '- "MILK & YOGURTFull cream milk 2l2L" -> search_query: "full cream milk 2l", label: "Full Cream Milk 2 L"\n'
        + '- "PROTEINSWhole chicken 1kg1000g" -> search_query: "whole chicken 1kg", label: "Whole Chicken 1 kg"\n'
        + '- "APPLE & PEARApple Royal Gala1kg" -> search_query: "apple royal gala 1kg", label: "Apple Royal Gala 1 kg"\n\n'
        + "Input lines (numbered):\n"
        + numbered
        + "\n\nReturn ONLY this JSON shape (exactly "
        + str(len(lines))
        + " items, lines 1.."
        + str(len(lines))
        + "):\n"
        + '{"items":[{"line":1,"search_query":"...","label":"..."},...]}'
    )


def _validate_llm_item(sq: str, lbl: str, line_no: int) -> None:
    if not sq or len(sq.strip()) < 2:
        raise ValueError(f"Line {line_no}: empty or too short search_query")
    if not lbl or len(lbl.strip()) < 2:
        raise ValueError(f"Line {line_no}: empty or too short label")


def _fallback_dry_run_row(expected: str, q: str) -> tuple[str, str]:
    """Deterministic placeholder when --dry-run (no API)."""
    sq = glue_weights_in_text(q)
    if "banana premium" in normalize_for_compare(expected):
        sq = glue_weights_in_text("banana 1kg")
    sq = sq.lower()
    label = " ".join(w.capitalize() for w in expected.split()) if expected.strip() else sq
    return sq, label


def _gemini_generate(client, model: str, prompt: str, *, max_retries: int = 4) -> str:
    try:
        from google.genai import types as genai_types

        gen_config = genai_types.GenerateContentConfig(
            temperature=0.0,
            response_mime_type="application/json",
        )
    except Exception:
        gen_config = None

    for attempt in range(max_retries):
        try:
            if gen_config is not None:
                try:
                    response = client.models.generate_content(
                        model=model, contents=prompt, config=gen_config
                    )
                except Exception:
                    response = client.models.generate_content(model=model, contents=prompt)
            else:
                response = client.models.generate_content(model=model, contents=prompt)
            return (response.text or "").strip()
        except Exception as e:
            msg = str(e).lower()
            retryable = "503" in msg or "unavailable" in msg or "429" in msg or "resource exhausted" in msg
            if not retryable or attempt == max_retries - 1:
                raise
            wait = 5 * (2**attempt)
            print(f"Gemini transient error ({e!r}); retry in {wait}s...", flush=True)
            time.sleep(wait)
    raise RuntimeError("Gemini call failed after retries")  # pragma: no cover


def run_gemini_prepare(
    input_path: Path,
    output_path: Path,
    *,
    model: str,
    dry_run: bool,
) -> list[dict]:
    if not input_path.is_file():
        raise FileNotFoundError(input_path)

    lines = [ln.strip() for ln in input_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    n = len(lines)
    if n == 0:
        raise ValueError("No non-empty lines in input")

    prepared_rows: list[tuple[int, str, str, str]] = []
    for i, raw in enumerate(lines, start=1):
        expected, q = parse_line(raw)
        prepared_rows.append((i, raw, expected, q))

    pair_by_line: dict[int, tuple[str, str]] = {}

    if dry_run:
        for i, raw, expected, q in prepared_rows:
            pair_by_line[i] = _fallback_dry_run_row(expected, q)
    else:
        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print(
                "Missing API key. Set GOOGLE_API_KEY or GEMINI_API_KEY, "
                "or use --dry-run.",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            from google import genai
        except ImportError:
            print("pip install google-genai", file=sys.stderr)
            sys.exit(1)

        client = genai.Client(api_key=api_key)
        prompt = _build_prompt(lines)
        raw_text = _gemini_generate(client, model, prompt)
        try:
            parsed = _parse_json_object(raw_text)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Failed to parse Gemini JSON: {e}\n---\n{raw_text[:4000]}\n---", file=sys.stderr)
            raise

        arr = parsed.get("items")
        if not isinstance(arr, list):
            raise ValueError("Response JSON must contain 'items' array")

        for item in arr:
            if not isinstance(item, dict):
                continue
            line_no = int(item["line"])
            sq = str(item.get("search_query", "")).strip()
            lbl = str(item.get("label", "")).strip()
            _validate_llm_item(sq, lbl, line_no)
            pair_by_line[line_no] = (sq, lbl)

        if len(pair_by_line) != n:
            raise ValueError(
                f"Expected {n} items, got {len(pair_by_line)} distinct lines"
            )
        for i in range(1, n + 1):
            if i not in pair_by_line:
                raise ValueError(f"Missing item for line {i}")

    out: list[dict] = []
    for i, raw, expected, q in prepared_rows:
        sq_in, lbl_in = pair_by_line[i]
        search_query = glue_weights_in_text(sq_in.lower().strip())
        if "banana premium" in normalize_for_compare(expected):
            search_query = glue_weights_in_text("banana 1kg")
        label = lbl_in.strip()
        row: dict = {
            "line": i,
            "raw": raw,
            "search_query": search_query,
            "label": label,
        }
        syn = search_synonym_for_row(expected)
        if syn:
            row["search_synonym"] = syn
        out.append(row)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def main() -> None:
    _load_dotenv_if_present()

    ap = argparse.ArgumentParser(
        description="Build key_items_prepared_gemini.json (one search query per item via Gemini)",
    )
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument(
        "--model",
        default=os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"),
        help="Gemini model id (default: env GEMINI_MODEL or gemini-3-flash-preview)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not call Gemini; heuristic search_query + label from parse_line",
    )
    args = ap.parse_args()

    try:
        rows = run_gemini_prepare(args.input, args.output, model=args.model, dry_run=args.dry_run)
    except FileNotFoundError as e:
        print(f"File not found: {e}", file=sys.stderr)
        sys.exit(1)
    except (ValueError, RuntimeError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    print(f"Wrote {len(rows)} rows to {args.output}", flush=True)


if __name__ == "__main__":
    main()
