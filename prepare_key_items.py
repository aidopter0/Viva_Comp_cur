"""
One-time setup (re-run when config/key_items.txt changes):

  Reads raw category-prefixed lines, normalises text, builds search queries
  (hints, alternates, glue canonicalization), and writes
  config/key_items_prepared.json.

  Extract and cleanup scripts load only the prepared JSON.
  No imports from talabat_extract or cleanup_and_rank.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ── Category prefix strip ──────────────────────────────────────────

CATEGORIES = sorted(
    [
        "CLEANING TOOLS & DISPOSABLES",
        "TOMATO & PEPPER",
        "FRESH JUICES",
        "FROZEN VEGETABLES",
        "PASTA & INSTANT FOOD",
        "CABBAGE & GOURDS",
        "ONION & GARLIC",
        "APPLE & PEAR",
        "POTATO & ROOT",
        "MILK & YOGURT",
        "CHEESE & BUTTER",
        "SAUCES & SEASONING",
        "MILK LONGLIFE",
        "PROTEINS",
        "SUGARS",
        "COFFEE",
        "WATER",
        "BREAD",
        "FLOUR",
        "RICE",
        "OIL",
        "EGGS",
        "BANANA",
    ],
    key=len,
    reverse=True,
)


def strip_category(line: str) -> str:
    s = line.strip()
    for cat in CATEGORIES:
        if s.startswith(cat):
            return s[len(cat):].lstrip()
    return s


# ── Text normalisation helpers ─────────────────────────────────────

def split_letter_digit_glue(s: str) -> str:
    """Insert spaces between glued letters/digits (e.g. Tomato1kg -> Tomato 1 kg)."""
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"([a-zA-Z])(\d)", r"\1 \2", s)
        s = re.sub(r"(\d)([a-zA-Z])", r"\1 \2", s)
    return re.sub(r"\s+", " ", s).strip()


def collapse_repeated_weight(s: str) -> str:
    """Collapse duplicated units from source lines like 800g800g -> 800 g once."""
    return re.sub(
        r"(\d+(?:\.\d+)?\s*(?:kg|g|L|ml|pc))(?:\s+\1)+",
        r"\1",
        s,
        flags=re.IGNORECASE,
    )


def normalize_for_compare(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def glue_weights_in_text(s: str) -> str:
    """Canonicalize spaced weights: '5 kg' -> '5kg', '500 ml' -> '500ml', etc."""
    return re.sub(
        r"(\d+(?:\.\d+)?)\s+(kg|g|ml|l|pc|pack|bag|gal)\b",
        r"\1\2",
        s,
        flags=re.IGNORECASE,
    )


# ── Parse raw key-items line ───────────────────────────────────────

def parse_line(line: str) -> tuple[str, str]:
    """Returns (expected_label_for_matching, primary search_query)."""
    rest = strip_category(line)
    rest = re.sub(r"^\d+", "", rest).lstrip()
    rest = split_letter_digit_glue(rest)
    rest = collapse_repeated_weight(rest)
    expected = rest.strip()
    q = re.sub(r"\([^)]*\)", " ", expected)
    q = re.sub(r"\s+", " ", q).strip()
    words = q.split()
    if len(words) > 8:
        q = " ".join(words[:8])
    if not q:
        q = expected
    return expected, q


# ── Search-query expansion ─────────────────────────────────────────

def alternate_queries(primary: str) -> list[str]:
    """Full phrase plus shorter word-prefix queries."""
    words = primary.split()
    out: list[str] = []
    if primary:
        out.append(primary)
    if len(words) >= 4:
        out.append(" ".join(words[:4]))
    if len(words) >= 3:
        out.append(" ".join(words[:3]))
    if len(words) >= 2:
        out.append(" ".join(words[:2]))
    seen: set[str] = set()
    uniq: list[str] = []
    for q in out:
        k = q.lower()
        if k not in seen and q:
            seen.add(k)
            uniq.append(q)
    return uniq


def dedupe_queries(queries: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        k = q.lower().strip()
        if k and k not in seen:
            seen.add(k)
            out.append(q.strip())
    return out


# ── Catalog hint overrides ─────────────────────────────────────────

def search_synonym_for_row(expected: str) -> str | None:
    """Optional Talabat-style phrase for dual scoring (griller chicken 900g only)."""
    ex = normalize_for_compare(expected)
    if "whole chicken" in ex and re.search(r"\b900\b", expected):
        return glue_weights_in_text("frozen chicken griller 900g")
    return None


def match_catalog_hints(expected: str) -> tuple[str, list[str]]:
    """
    When the key-list label differs from Talabat's typical title, return
    an adjusted scoring string and extra search queries.
    """
    ex = normalize_for_compare(expected)
    if "whole chicken" in ex and re.search(r"\b900\b", expected):
        return (
            "frozen chicken griller 900g",
            ["chicken 900g", "frozen chicken griller 900", "frozen chicken griller"],
        )
    if "banana premium" in ex:
        return (
            "banana 1kg",
            ["banana premium 1kg", "banana 1kg", "banana"],
        )
    return expected, []


# ── Build prepared fields ──────────────────────────────────────────

def build_prepared_query_fields(expected: str, q: str) -> tuple[str, list[str]]:
    """Build ``match_against`` and ``search_queries`` with glue canonicalization."""
    q = glue_weights_in_text(q)
    match_against, hint_queries = match_catalog_hints(expected)
    hint_queries = [glue_weights_in_text(h) for h in hint_queries]
    queries = dedupe_queries(hint_queries + alternate_queries(q))
    return match_against, queries


# ── CLI ────────────────────────────────────────────────────────────

DEFAULT_INPUT = Path("config/key_items.txt")
DEFAULT_OUTPUT = Path("config/key_items_prepared.json")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build key_items_prepared.json from key_items.txt")
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
