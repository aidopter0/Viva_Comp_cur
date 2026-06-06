"""Worst-case Viva reference pricing (max price_per_base across Viva branches per line)."""

from __future__ import annotations

import pandas as pd

from .pack_normalize import (
    compute_normalized_price,
    format_title_pack_display,
    resolve_price_normalization_pack,
)

VIVA_REFERENCE_LABEL = "Viva (worst-of-branches)"


def comparison_row_normalized(r: pd.Series) -> dict[str, object]:
    """Normalized unit price fields; prefer catalog pack when available."""
    catalog_title = str(r.get("item_title") or "").strip()
    catalog_pack = str(r.get("catalog_pack_text") or "").strip()
    if not catalog_pack and catalog_title:
        catalog_pack = format_title_pack_display(catalog_title)
    norm_pack_qty, norm_pack_unit = resolve_price_normalization_pack(
        pack_qty=str(r.get("pack_qty") or ""),
        pack_unit=str(r.get("pack_unit") or ""),
        pack_text=str(r.get("pack_text") or ""),
        catalog_title=catalog_title,
    )
    shelf = r.get("price")
    if shelf is None or (isinstance(shelf, float) and pd.isna(shelf)):
        shelf = r.get("discounted_price")
    norm_unit, norm_qty, ppb = compute_normalized_price(
        float(shelf) if shelf is not None else None,
        norm_pack_qty,
        norm_pack_unit,
    )
    if ppb is None and r.get("price_per_base") is not None:
        ppb = float(r["price_per_base"])
    if norm_qty is None and r.get("normalized_qty") is not None:
        norm_qty = float(r["normalized_qty"])
    if norm_unit is None and r.get("normalized_unit") is not None:
        norm_unit = str(r["normalized_unit"])
    return {
        "catalog_item_name": catalog_title,
        "catalog_item_pack": catalog_pack,
        "normalized_qty": norm_qty,
        "price_per_base": ppb,
        "normalized_unit": norm_unit,
    }


def format_extraction_status(status: object, error: object = None) -> str:
    val = str(status or "").strip()
    err = str(error or "").strip()
    if err and err.lower() not in {"none", "nan"}:
        return f"{val}: {err}" if val else err
    return val


def _viva_mask(comp: pd.DataFrame) -> pd.Series:
    return comp["brand_name"].astype(str).str.lower().eq("viva")


def viva_reference_by_line(comp: pd.DataFrame) -> pd.DataFrame:
    """Per basket line, pick the Viva branch with the highest price_per_base (worst case)."""
    viva = comp.loc[_viva_mask(comp)].copy()
    if viva.empty:
        return pd.DataFrame(
            columns=[
                "line_no",
                "viva_reference_ppb",
                "viva_reference_store",
                "viva_store_count",
            ]
        )

    scored: list[dict[str, object]] = []
    for _, row in viva.iterrows():
        ppb = comparison_row_normalized(row).get("price_per_base")
        if ppb is None or (isinstance(ppb, float) and pd.isna(ppb)):
            continue
        scored.append(
            {
                "line_no": int(row["line_no"]),
                "store_label": str(row["store_label"]),
                "viva_reference_ppb": float(ppb),
            }
        )
    if not scored:
        return pd.DataFrame(
            columns=[
                "line_no",
                "viva_reference_ppb",
                "viva_reference_store",
                "viva_store_count",
            ]
        )

    df = pd.DataFrame(scored)
    counts = df.groupby("line_no").size().rename("viva_store_count")
    worst = df.loc[df.groupby("line_no")["viva_reference_ppb"].idxmax()].copy()
    worst = worst.rename(columns={"store_label": "viva_reference_store"})
    worst = worst.merge(counts, on="line_no", how="left")
    return worst[
        ["line_no", "viva_reference_ppb", "viva_reference_store", "viva_store_count"]
    ]


def viva_worst_basket_total(comp: pd.DataFrame) -> float | None:
    refs = viva_reference_by_line(comp)
    if refs.empty:
        return None
    return float(refs["viva_reference_ppb"].sum())


def gap_vs_viva_pct(store_ppb: float | None, viva_ref_ppb: float | None) -> float | None:
    if viva_ref_ppb is None or store_ppb is None:
        return None
    ref = float(viva_ref_ppb)
    if ref <= 0:
        return None
    return (float(store_ppb) - ref) / ref * 100.0
