"""Build and export missing URL-master match audit (CSV + Excel)."""

from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Any

import pandas as pd

from .basket_match_spec import BasketMatchSpec, parse_pack_from_basket_label
from .basket_matcher import (
    cheapest_row,
    near_match_pool_rows,
    shortlist_rows,
    top_near_match_rows,
)
from .catalog_index import CatalogIndex, catalog_exists, load_catalog_file, slug_from_row
from .match_overrides import load_match_overrides, lookup_override
from .repository import list_url_master_grid
from .settings import EXPORTS_DIR

BUCKET_LABELS = {
    "override_missing": "1. Intentional override (missing)",
    "no_token_shortlist": "2. Basket tokens not all in catalog titles",
    "no_pack_shortlist": "3. Exact pack not in catalog",
    "gpt_rejected": "4. GPT rejected auto-pick",
    "fixable_slug": "5. Fixable slug (shortlist exists — add override or rematch)",
    "gate_reject": "6. Gate reject (wrong subtype or form)",
    "true_absence": "7. True absence (no SKU / data gap)",
    "near_pool_empty": "8. No near-match candidates (Round 2 pool empty)",
    "gpt_near_rejected": "9. GPT near-match rejected (Round 2)",
}
BUCKET_NOTES = {
    "override_missing": (
        "Explicit missing in match_overrides.json — SKU not stocked or intentional gap."
    ),
    "no_token_shortlist": (
        "No catalog row contains every basket_label token in the product title/slug."
    ),
    "no_pack_shortlist": (
        "Token-compatible rows exist but none with exact pack parsed from basket_label."
    ),
    "gpt_rejected": (
        "Auto shortlist pick was rejected by GPT verify — review fallback pool or override."
    ),
    "fixable_slug": (
        "Basket shortlist finds a viable row — re-run match or add slug override."
    ),
    "gate_reject": (
        "Catalog hit blocked by form guards (e.g. carrot drink vs fresh carrot, wrong bread type)."
    ),
    "true_absence": (
        "No plausible catalog hit — empty basket label, thin catalog, or data gap."
    ),
    "near_pool_empty": (
        "Round 2 ran but no catalog row shared a distinctive token within the pack band."
    ),
    "gpt_near_rejected": (
        "Round 2 near-match candidates existed but GPT picked none (or below confidence)."
    ),
}

AUDIT_CSV_FIELDS = [
    "line_no",
    "store_label",
    "brand_name",
    "basket_label",
    "chain_item_name",
    "brand_token",
    "error",
    "bucket",
    "top_catalog_hit",
]


def _slug_from_url(url: str) -> str:
    if "/product/" not in url:
        return ""
    return url.split("/product/", 1)[1].split("?", 1)[0]


def _build_spec(row: dict) -> BasketMatchSpec:
    brand_name = str(row.get("grocery_chain_name") or row.get("brand_name") or "")
    store_label = str(row.get("store_label") or "")
    label = str(row.get("basket_label") or "").strip()
    pack_qty = str(row.get("pack_qty") or "").strip()
    pack_unit = str(row.get("pack_unit") or "").strip()
    if not pack_qty and label:
        pack_qty, pack_unit = parse_pack_from_basket_label(label)
    return BasketMatchSpec.from_basket_row(
        line_no=int(row.get("line_no") or 0),
        basket_item_id=int(row.get("basket_item_id") or 0),
        basket_label=label,
        category=str(row.get("category") or ""),
        match_group=str(row.get("match_group") or "packaged"),
        line_role=str(row.get("line_role") or ""),
        store_label=store_label,
        store_brand_name=brand_name,
    )


def _token_hits_in_catalog(index: CatalogIndex, spec: BasketMatchSpec) -> list[dict[str, str]]:
    if not spec.basket_tokens:
        return []
    out: list[dict[str, str]] = []
    for row in index.rows:
        if spec.all_tokens_in_title(row["product_name"], str(row.get("url") or "")):
            out.append(row)
    return out


def _top_near_suggestion(index: CatalogIndex, spec: BasketMatchSpec) -> str:
    near = top_near_match_rows(near_match_pool_rows(index, spec), spec, limit=1)
    if not near:
        return ""
    top = near[0]
    return f"{top.get('product_name', '')} | {slug_from_row(top)}"


def _classify_row(row: dict, index: CatalogIndex | None, spec: BasketMatchSpec) -> tuple[str, str, str]:
    brand_name = str(row.get("grocery_chain_name") or row.get("brand_name") or "")
    store_label = str(row.get("store_label") or "")
    line_no = int(row.get("line_no") or 0)
    error = str(row.get("error") or row.get("match_reason") or "")

    override = lookup_override(brand_name, store_label, line_no)
    if override and override.action == "missing":
        return "override_missing", error, ""

    if not spec.basket_label.strip():
        return "true_absence", error, ""

    if index is None:
        return "true_absence", error, ""

    shortlist = shortlist_rows(index, spec)
    if shortlist:
        best = cheapest_row(shortlist) or shortlist[0]
        return "fixable_slug", error, f"{best.get('product_name', '')} | {slug_from_row(best)}"

    if "gpt rejected" in error.lower() or "gpt reject" in error.lower():
        pool = _token_hits_in_catalog(index, spec)
        top = pool[0] if pool else None
        suggestion = ""
        if top:
            suggestion = f"{top.get('product_name', '')} | {_slug_from_url(str(top.get('url') or ''))}"
        return "gpt_rejected", error, suggestion

    err_l = error.lower()
    if "near-match candidates" in err_l:
        return "near_pool_empty", error, _top_near_suggestion(index, spec)
    if "near pick" in err_l or "near-match found" in err_l:
        return "gpt_near_rejected", error, _top_near_suggestion(index, spec)

    token_hits = _token_hits_in_catalog(index, spec)
    if not token_hits:
        return "no_token_shortlist", error, _top_near_suggestion(index, spec)

    if spec.pack_qty or spec.pack_unit:
        return "no_pack_shortlist", error, f"{token_hits[0].get('product_name', '')} | {slug_from_row(token_hits[0])}"

    top = token_hits[0]
    return "gate_reject", error, f"{top.get('product_name', '')} | {slug_from_row(top)}"


def build_missing_match_audit_rows(conn) -> tuple[list[dict[str, str]], int]:
    """Return (audit_rows, missing_count) for all URL-master rows with status=missing."""
    load_match_overrides()
    rows = [dict(r) for r in list_url_master_grid(conn)]
    missing = [r for r in rows if str(r.get("status") or "").lower() == "missing"]

    index_cache: dict[str, CatalogIndex | None] = {}
    audit_rows: list[dict[str, str]] = []

    for row in missing:
        store_label = str(row.get("store_label") or "")
        brand_name = str(row.get("grocery_chain_name") or "")
        error = str(row.get("error") or row.get("match_reason") or "")
        if store_label not in index_cache:
            if catalog_exists(store_label):
                index_cache[store_label] = CatalogIndex(load_catalog_file(store_label))
            else:
                index_cache[store_label] = None
        index = index_cache[store_label]
        spec = _build_spec(row)

        bucket, _, suggestion = _classify_row(row, index, spec)
        audit_rows.append(
            {
                "line_no": str(spec.line_no),
                "store_label": store_label,
                "brand_name": brand_name,
                "basket_label": spec.basket_label,
                "chain_item_name": str(row.get("chain_item_name") or ""),
                "brand_token": str(row.get("brand_token") or ""),
                "error": error,
                "bucket": bucket,
                "top_catalog_hit": suggestion,
            }
        )

    return audit_rows, len(missing)


def write_missing_match_audit_csv(
    audit_rows: list[dict[str, str]],
    csv_path: Path | None = None,
) -> Path:
    path = csv_path or (EXPORTS_DIR / "missing_match_audit.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=AUDIT_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(audit_rows)
    return path


def _split_hit(val: object) -> tuple[str, str]:
    s = str(val or "").strip()
    if not s or s == "nan":
        return "", ""
    if " | " in s:
        title, slug = s.split(" | ", 1)
        return title.strip(), slug.strip()
    return s, ""


def export_missing_match_audit_excel(
    csv_path: Path,
    out_path: Path | None = None,
) -> Path:
    """Write categorized Excel workbook from audit CSV. Returns path actually written."""
    target = out_path or (EXPORTS_DIR / "missing_match_audit.xlsx")
    df = pd.read_csv(csv_path)
    df["line_no"] = pd.to_numeric(df["line_no"], errors="coerce").astype("Int64")
    df["category"] = df["bucket"].map(BUCKET_LABELS)
    df["category_note"] = df["bucket"].map(BUCKET_NOTES)

    hits = df["top_catalog_hit"].map(_split_hit)
    df["nearest_catalog_product"] = [h[0] for h in hits]
    df["nearest_catalog_slug"] = [h[1] for h in hits]

    cols = [
        "category",
        "line_no",
        "store_label",
        "brand_name",
        "basket_label",
        "chain_item_name",
        "brand_token",
        "error",
        "nearest_catalog_product",
        "nearest_catalog_slug",
        "category_note",
    ]
    df = df.sort_values(["category", "line_no", "store_label"]).reset_index(drop=True)
    detail = df[cols]

    summary = (
        detail.groupby("category", as_index=False)
        .agg(count=("line_no", "count"))
        .merge(
            pd.DataFrame(
                [
                    {"category": v, "bucket_key": k, "category_note": BUCKET_NOTES[k]}
                    for k, v in BUCKET_LABELS.items()
                ]
            ),
            on="category",
        )
        .sort_values("category")
    )

    def _write(path: Path) -> None:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            summary[["category", "count", "category_note"]].to_excel(
                writer, sheet_name="Summary", index=False
            )
            detail.to_excel(writer, sheet_name="All missing", index=False)
            for bucket_key, sheet_name in [
                ("override_missing", "1 Override missing"),
                ("no_token_shortlist", "2 No token shortlist"),
                ("no_pack_shortlist", "3 No pack shortlist"),
                ("gpt_rejected", "4 GPT rejected"),
                ("fixable_slug", "5 Fixable slug"),
                ("gate_reject", "6 Gate reject"),
                ("true_absence", "7 True absence"),
                ("near_pool_empty", "8 Near pool empty"),
                ("gpt_near_rejected", "9 GPT near rejected"),
            ]:
                part = detail[df["bucket"] == bucket_key].drop(columns=["category_note"])
                part.to_excel(writer, sheet_name=sheet_name[:31], index=False)

    try:
        _write(target)
        return target
    except PermissionError:
        fallback = target.with_name(f"missing_match_audit_{time.strftime('%Y%m%d_%H%M%S')}.xlsx")
        _write(fallback)
        return fallback


def bucket_summary(audit_rows: list[dict[str, str]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for row in audit_rows:
        bucket = str(row.get("bucket") or "")
        summary[bucket] = summary.get(bucket, 0) + 1
    return summary


def refresh_missing_match_audit(
    conn,
    *,
    csv_path: Path | None = None,
    xlsx_path: Path | None = None,
) -> dict[str, Any]:
    """
    Rebuild missing-match audit CSV and Excel after a matching run.

    Returns dict with keys: csv_path, xlsx_path, missing_count, bucket_summary.
    """
    audit_rows, missing_count = build_missing_match_audit_rows(conn)
    csv_out = write_missing_match_audit_csv(audit_rows, csv_path)
    xlsx_out = export_missing_match_audit_excel(csv_out, xlsx_path)
    return {
        "csv_path": csv_out,
        "xlsx_path": xlsx_out,
        "missing_count": missing_count,
        "bucket_summary": bucket_summary(audit_rows),
    }
