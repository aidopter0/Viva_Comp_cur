"""
Cleanup, scoring, ranking, optional Gemini reranking, and dashboard consolidation.

Reads per-store raw JSON from talabat_extract.py, applies similarity scoring,
narrow filters, writes per-store CSV/JSON, and rebuilds the consolidated dashboard.

Imports match_catalog_hints from prepare_key_items (for Gemini re-scoring context).
No imports from talabat_extract.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd

# ── Text helpers (scoring side) ────────────────────────────────────

def normalize_for_compare(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def token_coverage(expected_norm: str, title_norm: str) -> float:
    exp_tokens = [t for t in expected_norm.split() if len(t) > 2]
    if not exp_tokens:
        return 1.0
    hits = sum(1 for t in exp_tokens if t in title_norm)
    return hits / len(exp_tokens)


_WEIGHT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(kg|g|ml|l)\b",
    re.IGNORECASE,
)


def _normalize_unit_typos(s: str) -> str:
    s = re.sub(r"(\d+(?:\.\d+)?)\s*lt\b", r"\1 l", s, flags=re.IGNORECASE)
    s = re.sub(r"(\d+(?:\.\d+)?)\s*ltr\b", r"\1 l", s, flags=re.IGNORECASE)
    s = re.sub(r"(\d+(?:\.\d+)?)\s*litre\b", r"\1 l", s, flags=re.IGNORECASE)
    return s


def _sizes_in_text(text: str) -> list[tuple[float, str]]:
    text = _normalize_unit_typos(text)
    out: list[tuple[float, str]] = []
    for m in _WEIGHT_RE.finditer(text.lower()):
        val = float(m.group(1))
        u = m.group(2).lower()
        if u == "kg":
            out.append((val * 1000.0, "mass"))
        elif u == "g":
            out.append((val, "mass"))
        elif u == "l":
            out.append((val * 1000.0, "vol"))
        elif u == "ml":
            out.append((val, "vol"))
    return out


def _primary_pack_size(text: str) -> tuple[float, str] | None:
    sizes = _sizes_in_text(text)
    return sizes[-1] if sizes else None


def size_consistency_multiplier(expected: str, title: str) -> float:
    e = _primary_pack_size(expected)
    t = _primary_pack_size(title)
    if not e or not t:
        return 1.0
    if e[1] != t[1]:
        em, ev = e[0], t[0]
        if e[1] == "mass" and t[1] == "vol" and em >= 800 and ev >= 800:
            return 0.88
        if e[1] == "vol" and t[1] == "mass" and em >= 800 and ev >= 800:
            return 0.88
        return 0.72
    ratio = min(e[0], t[0]) / max(e[0], t[0])
    if ratio >= 0.92:
        return 1.0
    if ratio >= 0.82:
        return 0.72
    if ratio >= 0.55:
        return 0.42
    return 0.12


def item_match_score(expected: str, title: str) -> float:
    """Similarity score in [0, 1] for one product title vs expected phrase."""
    exp = normalize_for_compare(expected)
    if not exp:
        return 0.0
    cand = normalize_for_compare(title)
    r = difflib.SequenceMatcher(None, exp, cand).ratio()
    cov = token_coverage(exp, cand)
    combined = 0.35 * r + 0.65 * cov
    if cov < 0.45:
        combined *= 0.5
    if (
        any(p in exp for p in ("spaghetti", "penne", "fusilli", "elbow"))
        and "sauce" not in exp
        and "sauce" in cand
    ):
        combined *= 0.35
    _fresh_banana_plu = (
        "banana premium" in exp
        or ("banana" in exp and "premium" in exp)
        or re.match(r"^banana\s+1\s*kg$", exp) is not None
    )
    if _fresh_banana_plu:
        if "chip" in cand or "chips" in cand:
            combined *= 0.12
        if "juice" in cand and "juice" not in exp:
            combined *= 0.15
    combined *= size_consistency_multiplier(expected, title)
    return combined


def match_score_for_row(title: str, search_query: str, search_synonym: str | None) -> float:
    """Max similarity vs search_query and optional search_synonym (e.g. griller chicken)."""
    s1 = item_match_score(search_query, title)
    if not search_synonym or not str(search_synonym).strip():
        return s1
    s2 = item_match_score(search_synonym.strip(), title)
    return max(s1, s2)


# ── Ranking helpers ────────────────────────────────────────────────

def _price_sort_key(it: dict) -> float:
    p = it.get("price")
    try:
        return float(p) if p is not None else float("inf")
    except (TypeError, ValueError):
        return float("inf")


def top_candidates_for_llm(
    search_query: str,
    items: list[dict],
    top_k: int = 12,
    search_synonym: str | None = None,
) -> list[tuple[dict, float]]:
    if not items or top_k < 1:
        return []
    scored: list[tuple[dict, float]] = []
    for it in items:
        title = it.get("title") or ""
        s = match_score_for_row(title, search_query, search_synonym)
        scored.append((it, s))
    scored.sort(key=lambda x: -x[1])
    seen: set[str] = set()
    out: list[tuple[dict, float]] = []
    for it, s in scored:
        iid = str(it.get("id", ""))
        if iid and iid in seen:
            continue
        if iid:
            seen.add(iid)
        out.append((it, s))
        if len(out) >= top_k:
            break
    return out


def top_matches(
    search_query: str,
    items: list[dict],
    min_ratio: float,
    top_k: int = 3,
    search_synonym: str | None = None,
) -> list[tuple[dict, float]]:
    if not items or top_k < 1:
        return []
    scored: list[tuple[dict, float]] = []
    for it in items:
        title = it.get("title") or ""
        s = match_score_for_row(title, search_query, search_synonym)
        if s >= min_ratio:
            scored.append((it, s))
    scored.sort(key=lambda x: -x[1])
    top = scored[:top_k]
    top.sort(key=lambda x: _price_sort_key(x[0]))
    return top


def best_match(
    search_query: str,
    items: list[dict],
    min_ratio: float = 0.55,
    search_synonym: str | None = None,
) -> tuple[dict | None, float]:
    exp = normalize_for_compare(search_query)
    if not exp:
        return None, 0.0
    best_item = None
    best_score = 0.0
    for it in items:
        title = it.get("title") or ""
        s = match_score_for_row(title, search_query, search_synonym)
        if s > best_score:
            best_score = s
            best_item = it
    if best_item is None or best_score < min_ratio:
        return None, best_score
    return best_item, best_score


# ── Narrow filters ─────────────────────────────────────────────────

def narrow_griller_synonym(search_synonym: str | None, items: list[dict]) -> list[dict]:
    if not search_synonym or "frozen chicken griller" not in normalize_for_compare(search_synonym):
        return items
    gr = [it for it in items if "griller" in (it.get("title") or "").lower()]
    return gr if gr else items


def narrow_banana_premium_raw(raw: str, items: list[dict]) -> list[dict]:
    if "banana premium" not in normalize_for_compare(raw):
        return items
    out = [it for it in items if "banana" in (it.get("title") or "").lower()]
    return out if out else items


# ── Product slice ──────────────────────────────────────────────────

def _product_slice(it: dict) -> dict:
    return {
        "title": it.get("title"),
        "slug": it.get("slug"),
        "sku": it.get("sku"),
        "price": it.get("price"),
        "originalPrice": it.get("originalPrice"),
        "discountPercentage": it.get("discountPercentage"),
        "stockAmount": it.get("stockAmount"),
        "image": it.get("image"),
    }


# ── Score and rank one store's raw JSON ────────────────────────────

CSV_FIELDNAMES = [
    "line", "raw", "expected", "match_score_best",
    "match_1_title", "match_1_price", "match_1_score", "match_1_sku", "match_1_stock",
    "match_2_title", "match_2_price", "match_2_score", "match_2_sku", "match_2_stock",
    "match_3_title", "match_3_price", "match_3_score", "match_3_sku", "match_3_stock",
]


def score_and_rank_store(
    raw_json_path: Path,
    out_json: Path | None,
    out_csv: Path | None,
    min_ratio: float = 0.55,
    top_k: int = 3,
) -> list[dict]:
    """Read raw extract JSON, score/rank, write scored JSON and/or CSV."""
    raw_data = json.loads(raw_json_path.read_text(encoding="utf-8"))
    results: list[dict] = []
    for entry in raw_data:
        i = int(entry["line"])
        raw = str(entry["raw"])
        label = str(entry["label"])
        search_query = str(entry["search_query"]).strip()
        syn_raw = entry.get("search_synonym")
        search_synonym = str(syn_raw).strip() if syn_raw not in (None, "") else None
        queries = [search_query]
        items = list(entry.get("products") or [])
        fetch_error = entry.get("error")

        items = narrow_griller_synonym(search_synonym, items)
        items = narrow_banana_premium_raw(raw, items)
        ranked = top_matches(
            search_query, items, min_ratio=min_ratio, top_k=top_k, search_synonym=search_synonym
        )
        best_sim = max((s for _, s in ranked), default=None)
        pool = top_candidates_for_llm(
            search_query, items, top_k=12, search_synonym=search_synonym
        )
        out_row: dict = {
            "line": i,
            "raw": raw,
            "label": label,
            "search_query": search_query,
            "expected_match": label,
            "search_queries_tried": queries,
            "match_score": round(best_sim, 4) if best_sim is not None else None,
            "top_k": top_k,
            "candidate_pool": [
                {**_product_slice(it), "match_score": round(s, 4)} for it, s in pool
            ],
        }
        if search_synonym:
            out_row["search_synonym"] = search_synonym
            out_row["scored_against"] = search_synonym
        if fetch_error:
            out_row["error"] = fetch_error
            out_row["matches"] = []
            out_row["product"] = None
        elif ranked:
            out_row["matches"] = [
                {**_product_slice(it), "match_score": round(s, 4)} for it, s in ranked
            ]
            out_row["product"] = _product_slice(ranked[0][0])
        else:
            out_row["matches"] = []
            out_row["product"] = None
            out_row["note"] = "No API product above similarity threshold; try manual search or lower min_ratio."
        results.append(out_row)

    if out_json is not None:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    if out_csv is not None:
        _write_scored_csv(results, out_csv)
    return results


def _write_scored_csv(results: list[dict], out_csv: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    flat: list[dict] = []
    for r in results:
        mrow = list(r.get("matches") or [])[:3]
        while len(mrow) < 3:
            mrow.append({})
        row_csv: dict = {
            "line": r.get("line"),
            "raw": r.get("raw"),
            "expected": r.get("expected_match"),
            "match_score_best": r.get("match_score"),
        }
        for idx in range(3):
            m = mrow[idx]
            n = idx + 1
            row_csv[f"match_{n}_title"] = m.get("title", "")
            row_csv[f"match_{n}_price"] = m.get("price", "")
            row_csv[f"match_{n}_score"] = m.get("match_score", "")
            row_csv[f"match_{n}_sku"] = m.get("sku", "")
            row_csv[f"match_{n}_stock"] = m.get("stockAmount", "")
        flat.append(row_csv)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        w.writerows(flat)


# ── Dashboard consolidation ────────────────────────────────────────

def store_safe_label(label: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in label)


def load_stores(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("talabat_stores.json must be a JSON array")
    return data


def pick_store_csv(out_dir: Path, safe: str) -> Path | None:
    rr = out_dir / f"{safe}.reranked.csv"
    raw = out_dir / f"{safe}.csv"
    if rr.is_file():
        return rr
    if raw.is_file():
        return raw
    return None


def rows_for_store_csv(
    path: Path,
    extraction_date: str,
    store_label: str,
) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        for row in reader:
            rows.append(
                {
                    "extraction_date": extraction_date,
                    "store_name": store_label,
                    **{k: row.get(k, "") for k in fieldnames},
                }
            )
    return rows


def rebuild_dashboard_slice(
    out_dir: Path,
    stores_json: Path,
    dashboard_csv: Path,
    extraction_date: str,
) -> int:
    stores = load_stores(stores_json)
    new_rows: list[dict] = []
    for entry in stores:
        label = entry["label"]
        safe = store_safe_label(label)
        path = pick_store_csv(out_dir, safe)
        if path is None:
            continue
        new_rows.extend(rows_for_store_csv(path, extraction_date, label))

    dashboard_csv.parent.mkdir(parents=True, exist_ok=True)
    if dashboard_csv.is_file() and dashboard_csv.stat().st_size > 0:
        old = pd.read_csv(dashboard_csv)
        if "extraction_date" in old.columns:
            old = old[old["extraction_date"].astype(str) != str(extraction_date)]
        else:
            old = pd.DataFrame()
    else:
        old = pd.DataFrame()

    fresh = pd.DataFrame(new_rows) if new_rows else pd.DataFrame()
    out = pd.concat([old, fresh], ignore_index=True)
    out.to_csv(dashboard_csv, index=False, encoding="utf-8")
    return len(fresh)


# ── Consolidated pricing append ────────────────────────────────────

def append_consolidated(
    consolidated_path: Path,
    store_label: str,
    extraction_date: str,
    per_store_csv: Path,
) -> None:
    if not per_store_csv.is_file():
        return
    with per_store_csv.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    if not fieldnames:
        return
    out_fields = ["extraction_date", "store_name"] + fieldnames
    file_exists = consolidated_path.is_file()
    write_header = not file_exists or consolidated_path.stat().st_size == 0
    with consolidated_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        if write_header:
            w.writeheader()
        for row in rows:
            w.writerow(
                {
                    "extraction_date": extraction_date,
                    "store_name": store_label,
                    **{k: row.get(k, "") for k in fieldnames},
                }
            )


# ── Gemini reranking ───────────────────────────────────────────────

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


def _candidates_for_row(row: dict) -> list[dict]:
    pool = row.get("candidate_pool") or []
    matches = row.get("matches") or []
    return pool if pool else matches


def _needs_rerank(row: dict, score_below: float, rerank_all: bool) -> bool:
    if row.get("error"):
        return False
    if not _candidates_for_row(row):
        return False
    if rerank_all:
        return True
    ms = row.get("match_score")
    if ms is None:
        return True
    try:
        return float(ms) < score_below
    except (TypeError, ValueError):
        return True


def _similarity_phrase_for_prompt(row: dict) -> str:
    sq = (row.get("search_query") or "").strip()
    syn = (row.get("search_synonym") or "").strip()
    if syn and sq:
        return f"{sq} / {syn}"
    return sq or syn or row.get("expected_match", "")


def _build_prompt_chunk(rows_chunk: list[dict], scored_against_map: dict[int, str]) -> str:
    payload = []
    for row in rows_chunk:
        line = row["line"]
        exp = row.get("expected_match", "")
        label = row.get("label") or exp
        cats = _similarity_phrase_for_prompt(row) or scored_against_map.get(line, exp)
        cands = _candidates_for_row(row)
        items = []
        for i, c in enumerate(cands, start=1):
            items.append(
                {
                    "index": i,
                    "sku": c.get("sku"),
                    "title": c.get("title"),
                    "price_aed": c.get("price"),
                    "algorithm_similarity": c.get("match_score"),
                }
            )
        payload.append(
            {
                "line": line,
                "key_item_label": label,
                "similarity_scoring_phrase": cats,
                "candidates": items,
            }
        )
    schema = (
        '{"decisions":[{"line":<int>,"choice":<int>,"reason":"<brief>"}]} '
        "where choice is the candidate index (1-based) or 0 if none."
    )
    return (
        "You match grocery key-line items to Talabat product listings.\n"
        "For each line, pick the ONE candidate that best matches the key shopping intent "
        "(correct product category, compatible pack size/weight). Reject wrong categories "
        "(e.g. beef mince is not yoghurt). If all candidates are wrong, return choice 0.\n\n"
        f"Return ONLY valid JSON with this shape: {schema}\n\n"
        f"DATA:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _parse_json_response(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError("No JSON object in model response")
    return json.loads(m.group(0))


def _apply_gemini_decisions(
    rows: list[dict],
    decisions: list[dict],
    by_line: dict[int, dict],
) -> None:
    for d in decisions:
        line = int(d["line"])
        choice = int(d.get("choice", 0))
        reason = str(d.get("reason", "")).strip()
        row = by_line.get(line)
        if not row:
            continue
        cands = _candidates_for_row(row)
        if not cands or choice < 1 or choice > len(cands):
            row["gemini"] = {
                "choice_index": choice,
                "reason": reason,
                "product": None,
                "skipped": True,
            }
            continue
        picked = cands[choice - 1]
        title = picked.get("title") or ""
        sq = (row.get("search_query") or row.get("expected_match", "")).strip()
        syn = row.get("search_synonym")
        syn = str(syn).strip() if syn else None
        algo_score = match_score_for_row(title, sq, syn)
        row["gemini"] = {
            "choice_index": choice,
            "reason": reason,
            "product": {
                "title": picked.get("title"),
                "slug": picked.get("slug"),
                "sku": picked.get("sku"),
                "price": picked.get("price"),
                "originalPrice": picked.get("originalPrice"),
                "discountPercentage": picked.get("discountPercentage"),
                "stockAmount": picked.get("stockAmount"),
                "image": picked.get("image"),
                "match_score": round(algo_score, 4),
                "source": "gemini_rerank",
            },
        }


def _gemini_call(client, model: str, prompt: str) -> str:
    response = client.models.generate_content(model=model, contents=prompt)
    return (response.text or "").strip()


def rerank_file(
    path: Path,
    client,
    model: str,
    score_below: float,
    chunk_size: int,
    dry_run: bool,
    rerank_all: bool = False,
) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Expected JSON array of rows")
    by_line = {int(r["line"]): r for r in data if "line" in r}
    scored_against: dict[int, str] = {}
    for r in data:
        line = int(r["line"])
        if r.get("search_query"):
            scored_against[line] = _similarity_phrase_for_prompt(r)
        elif "scored_against" in r:
            scored_against[line] = r["scored_against"]
        else:
            scored_against[line] = r.get("expected_match", "")

    todo = [r for r in data if _needs_rerank(r, score_below, rerank_all)]
    if not todo:
        if rerank_all:
            print(f"  {path.name}: no rows with candidates (empty candidate_pool/matches).")
        else:
            print(f"  {path.name}: nothing to rerank (all scores >= {score_below} or no candidates).")
        return data

    if dry_run:
        print(f"  {path.name}: would rerank {len(todo)} line(s) (dry-run).", flush=True)
        return data

    print(f"  {path.name}: reranking {len(todo)} line(s) in chunk(s) of {chunk_size}...", flush=True)

    for i in range(0, len(todo), chunk_size):
        chunk = todo[i : i + chunk_size]
        prompt = _build_prompt_chunk(chunk, scored_against)
        raw = _gemini_call(client, model, prompt)
        try:
            parsed = _parse_json_response(raw)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"  ERROR parsing Gemini response for {path.name}: {e}\n---\n{raw[:2000]}\n---", file=sys.stderr)
            raise
        decisions = parsed.get("decisions") or []
        _apply_gemini_decisions(data, decisions, by_line)

    return data


def _write_reranked_csv(rows: list[dict], out_path: Path) -> None:
    flat: list[dict] = []
    for r in rows:
        mrow: list[dict] = []
        g = (r.get("gemini") or {}).get("product")
        base_matches = list(r.get("matches") or [])[:3]
        if g:
            primary = {
                "title": g.get("title"),
                "price": g.get("price"),
                "match_score": g.get("match_score"),
                "sku": g.get("sku"),
                "stockAmount": g.get("stockAmount"),
            }
            rest = [x for x in base_matches if x.get("sku") != g.get("sku")][:2]
            mrow = [primary] + rest
        else:
            mrow = base_matches
        while len(mrow) < 3:
            mrow.append({})
        row_csv: dict = {
            "line": r.get("line"),
            "raw": r.get("raw"),
            "expected": r.get("expected_match"),
            "match_score_best": (
                mrow[0].get("match_score") if mrow and mrow[0] else r.get("match_score")
            ),
        }
        for idx in range(3):
            m = mrow[idx]
            n = idx + 1
            row_csv[f"match_{n}_title"] = m.get("title", "")
            row_csv[f"match_{n}_price"] = m.get("price", "")
            row_csv[f"match_{n}_score"] = m.get("match_score", "")
            row_csv[f"match_{n}_sku"] = m.get("sku", "")
            row_csv[f"match_{n}_stock"] = m.get("stockAmount", "")
        flat.append(row_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        w.writerows(flat)


# ── CLI ────────────────────────────────────────────────────────────

def main() -> None:
    _load_dotenv_if_present()

    ap = argparse.ArgumentParser(description="Score, rank, optionally Gemini-rerank, and build dashboard")
    ap.add_argument("--input-dir", type=Path, default=Path("output/stores"))
    ap.add_argument("--stores-json", type=Path, default=Path("config/talabat_stores.json"))
    ap.add_argument("--consolidated", type=Path, default=Path("output/consolidated_pricing.csv"))
    ap.add_argument("--dashboard-csv", type=Path, default=Path("output/consolidated_dashboard.csv"))
    ap.add_argument("--extraction-date", default="", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--min-ratio", type=float, default=0.55)
    ap.add_argument("--top-k", type=int, default=3)

    g = ap.add_argument_group("Gemini reranking")
    g.add_argument("--gemini", action="store_true", help="Run Gemini reranking on scored JSONs")
    g.add_argument("--rerank-all", action="store_true")
    g.add_argument("--model", default=os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview"))
    g.add_argument("--chunk-size", type=int, default=25)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--score-below", type=float, default=0.75)
    g.add_argument("--suffix", default=".reranked")

    ap.add_argument("--dashboard-only", action="store_true", help="Only rebuild dashboard from existing CSVs")
    args = ap.parse_args()

    extraction_date = args.extraction_date.strip() or date.today().isoformat()

    if args.dashboard_only:
        n = rebuild_dashboard_slice(args.input_dir, args.stores_json, args.dashboard_csv, extraction_date)
        print(f"Dashboard {args.dashboard_csv}: {n} rows for {extraction_date}.", flush=True)
        return

    stores = load_stores(args.stores_json)
    for entry in stores:
        label = entry["label"]
        safe = store_safe_label(label)
        raw_json_path = args.input_dir / f"{safe}.raw.json"
        if not raw_json_path.is_file():
            print(f"[{label}] raw JSON not found: {raw_json_path}, skipping", flush=True)
            continue

        scored_json = args.input_dir / f"{safe}.json"
        scored_csv = args.input_dir / f"{safe}.csv"
        score_and_rank_store(
            raw_json_path,
            scored_json,
            scored_csv,
            min_ratio=args.min_ratio,
            top_k=max(1, args.top_k),
        )
        append_consolidated(args.consolidated, label, extraction_date, scored_csv)
        print(f"[{label}] scored -> {scored_csv}", flush=True)

    if args.gemini:
        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key and not args.dry_run:
            print("Missing Gemini API key (GOOGLE_API_KEY or GEMINI_API_KEY).", file=sys.stderr)
            sys.exit(1)
        try:
            from google import genai
        except ImportError:
            print("pip install google-genai", file=sys.stderr)
            sys.exit(1)
        client = genai.Client(api_key=api_key) if api_key else None

        for entry in stores:
            safe = store_safe_label(entry["label"])
            scored_json = args.input_dir / f"{safe}.json"
            if not scored_json.is_file():
                continue
            data = rerank_file(
                scored_json, client, args.model,
                args.score_below, args.chunk_size,
                dry_run=args.dry_run, rerank_all=args.rerank_all,
            )
            if not args.dry_run:
                rr_json = scored_json.with_name(safe + args.suffix + ".json")
                rr_json.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                rr_csv = scored_json.with_name(safe + args.suffix + ".csv")
                _write_reranked_csv(data, rr_csv)
                print(f"  Wrote {rr_json} and {rr_csv}", flush=True)

    n = rebuild_dashboard_slice(args.input_dir, args.stores_json, args.dashboard_csv, extraction_date)
    print(f"Dashboard {args.dashboard_csv}: {n} rows for {extraction_date}.", flush=True)
    print("Done.", flush=True)


if __name__ == "__main__":
    main()
