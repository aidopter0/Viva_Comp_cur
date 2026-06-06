from __future__ import annotations

# Business logic for extraction, analytics, and exports.
#
# Numeric policy:
# - Persist and compute prices at full precision (SQLite REAL, pandas floats).
# - Apply two-decimal presentation formatting only at export/UI boundaries via
#   presentation_format — never inside extract_prices or analytics builders.

import csv
from collections.abc import Iterable
from datetime import date
from pathlib import Path

import pandas as pd

from .basket import parse_basket_csv
from .presentation_format import (
    round_kpi_export_values,
    write_presentation_csv,
    write_presentation_excel,
)
from .catalog_match import best_catalog_match
from .pack_normalize import compute_normalized_price, resolve_price_normalization_pack
from .viva_reference import (
    VIVA_REFERENCE_LABEL,
    comparison_row_normalized,
    format_extraction_status,
    gap_vs_viva_pct,
    viva_reference_by_line,
    viva_worst_basket_total,
)
from .repository import (
    create_run,
    latest_prices_frame,
    list_basket_brand_maps,
    list_basket_items,
    list_basket_store_grid,
    list_brands,
    list_item_url_master,
    list_stores,
    list_url_master_grid,
    save_price_observation,
    upsert_basket_brand_map,
    upsert_basket_item,
    upsert_brand,
    upsert_item_url_master,
)
from .storefront import (
    FetchItemsError,
    collect_catalog,
    fetch_items_from_source_url,
    resolve_store_uuid,
)


def initialize_from_csv(conn, basket_csv: Path) -> None:
    brands, rows = parse_basket_csv(basket_csv)
    conn.execute("UPDATE brands SET is_active = 0")
    conn.execute("UPDATE basket_items SET is_active = 0")
    conn.execute("DELETE FROM basket_item_brand_map")
    conn.execute("DELETE FROM item_url_master")
    conn.commit()

    brand_ids: dict[str, int] = {}
    for b in brands:
        brand_ids[b] = upsert_brand(conn, b, is_viva=(b.lower() == "viva"))

    for row in rows:
        basket_item_id = upsert_basket_item(
            conn,
            line_no=row["line_no"],
            category=row["category"],
            product_id=row["product_id"],
            viva_name=row["viva_name"],
            basket_label=str(row.get("basket_label") or ""),
        )
        for brand in brands:
            m = row["brands"].get(brand) or {}
            name = (m.get("name") or "").strip()
            if not name:
                continue
            upsert_basket_brand_map(
                conn,
                basket_item_id=basket_item_id,
                brand_id=brand_ids[brand],
                mapped_name=name,
                pack_qty=(m.get("qty") or "").strip(),
                pack_unit=(m.get("unit") or "").strip(),
                search_query=(m.get("search_query") or "").strip(),
            )


def refresh_store_uuids(conn) -> list[dict]:
    out: list[dict] = []
    stores = list_stores(conn)
    for s in stores:
        status = "ok"
        error = None
        store_uuid = s["store_uuid"]
        if s["talabat_url"]:
            try:
                store_uuid = resolve_store_uuid(s["talabat_url"])
                conn.execute(
                    "UPDATE stores SET store_uuid = ?, updated_at = datetime('now') WHERE store_id = ?",
                    (store_uuid, s["store_id"]),
                )
                conn.commit()
            except Exception as e:  # noqa: BLE001
                status = "error"
                error = str(e)
        out.append(
            {
                "store_id": s["store_id"],
                "store_label": s["store_label"],
                "store_uuid": store_uuid,
                "status": status,
                "error": error,
            }
        )
    return out


def refresh_item_url_master(
    conn,
    *,
    page_delay_s: float = 0.85,
    store_ids: Iterable[int] | None = None,
) -> list[dict]:
    stores = list_stores(conn)
    if store_ids is not None:
        want = {int(x) for x in store_ids}
        stores = [s for s in stores if int(s["store_id"]) in want]
    mapping_rows = list_basket_brand_maps(conn)
    by_brand: dict[str, list] = {}
    for m in mapping_rows:
        by_brand.setdefault(m["brand_name"], []).append(m)

    results: list[dict] = []
    for s in stores:
        maps = by_brand.get(s["brand_name"], [])
        if not maps or not s["talabat_url"]:
            continue
        catalog = collect_catalog(s["talabat_url"], page_delay_s=page_delay_s)
        catalog_list = list(catalog.values())
        for m in maps:
            mapped_name = str(m["mapped_name"] or "").strip()
            sq = str(m["search_query"] or "").strip()
            pack_qty = str(m["pack_qty"] or "").strip()
            pack_unit = str(m["pack_unit"] or "").strip()

            picked, err_detail = best_catalog_match(
                catalog_list,
                mapped_name=mapped_name,
                pack_qty=pack_qty,
                pack_unit=pack_unit,
                search_query=sq or mapped_name,
            )

            if picked:
                upsert_item_url_master(
                    conn,
                    store_id=s["store_id"],
                    basket_item_id=m["basket_item_id"],
                    item_id=str(picked.get("id") or ""),
                    source_url=str(picked.get("__source_url") or ""),
                    slug=str(picked.get("slug") or ""),
                    item_title=str(picked.get("title") or ""),
                    status="ok",
                    error=None,
                )
                results.append({"store_label": s["store_label"], "line_no": m["line_no"], "status": "ok"})
            else:
                upsert_item_url_master(
                    conn,
                    store_id=s["store_id"],
                    basket_item_id=m["basket_item_id"],
                    item_id=None,
                    source_url=None,
                    slug=None,
                    item_title=None,
                    status="missing",
                    error=err_detail or "No catalog item matched",
                )
                results.append({"store_label": s["store_label"], "line_no": m["line_no"], "status": "missing"})
    return results


def extract_prices(
    conn,
    *,
    triggered_by: str = "cli",
    fetch_delay_s: float = 0.4,
    export_artifacts: bool = True,
):
    """Fetch shelf prices and persist full-precision observations to SQLite."""
    import time

    run_id = create_run(conn, triggered_by=triggered_by)
    grid = list_basket_store_grid(conn)

    for row in grid:
        store_id = int(row["store_id"])
        basket_item_id = int(row["basket_item_id"])
        url_status = str(row["status"] or "").strip().lower()
        source_url = row["source_url"]
        item_id = row["item_id"]
        pack_qty = str(row["pack_qty"] or "").strip()
        pack_unit = str(row["pack_unit"] or "").strip()
        pack_text = str(row["pack_text"] or "").strip()
        catalog_title = str(row["item_title"] or "").strip()
        norm_pack_qty, norm_pack_unit = resolve_price_normalization_pack(
            pack_qty=pack_qty,
            pack_unit=pack_unit,
            pack_text=pack_text,
            catalog_title=catalog_title,
        )

        has_url = (
            url_status in {"ok", "pack_mismatch"}
            and str(source_url or "").strip()
            and str(item_id or "").strip()
        )
        if not has_url:
            save_price_observation(
                conn,
                run_id=run_id,
                store_id=store_id,
                basket_item_id=basket_item_id,
                price=None,
                original_price=None,
                discount_percentage=None,
                discounted_price=None,
                status="missing_url",
                error="URL master missing or invalid",
                item_json=None,
            )
            continue

        try:
            items = fetch_items_from_source_url(str(source_url))
            item = items.get(str(item_id))
            if not item:
                save_price_observation(
                    conn,
                    run_id=run_id,
                    store_id=store_id,
                    basket_item_id=basket_item_id,
                    price=None,
                    original_price=None,
                    discount_percentage=None,
                    discounted_price=None,
                    status="not_found",
                    error=f"Item id {item_id} not found on source URL",
                    item_json=None,
                )
                time.sleep(fetch_delay_s)
                continue
            price = item.get("price")
            original = item.get("originalPrice")
            discount_pct = item.get("discountPercentage")
            shelf = float(price) if price is not None else None
            norm_unit, norm_qty, price_per_base = compute_normalized_price(
                shelf, norm_pack_qty, norm_pack_unit
            )
            save_price_observation(
                conn,
                run_id=run_id,
                store_id=store_id,
                basket_item_id=basket_item_id,
                price=shelf,
                original_price=float(original) if original is not None else None,
                discount_percentage=float(discount_pct) if discount_pct is not None else None,
                discounted_price=None,
                status="ok",
                error=None,
                item_json=item,
                normalized_unit=norm_unit,
                normalized_qty=norm_qty,
                price_per_base=price_per_base,
            )
        except FetchItemsError as e:
            save_price_observation(
                conn,
                run_id=run_id,
                store_id=store_id,
                basket_item_id=basket_item_id,
                price=None,
                original_price=None,
                discount_percentage=None,
                discounted_price=None,
                status="error",
                error=str(e),
                item_json=None,
            )
        except Exception as e:  # noqa: BLE001
            save_price_observation(
                conn,
                run_id=run_id,
                store_id=store_id,
                basket_item_id=basket_item_id,
                price=None,
                original_price=None,
                discount_percentage=None,
                discounted_price=None,
                status="error",
                error=str(e),
                item_json=None,
            )
        time.sleep(fetch_delay_s)

    if not export_artifacts:
        from .extraction_exports import ExtractionResult

        return ExtractionResult(run_id=run_id)

    from .extraction_exports import export_extraction_artifacts, ExtractionResult

    bundle = export_extraction_artifacts(conn, run_id)
    return ExtractionResult(
        run_id=run_id,
        export_label=bundle.export_label,
        export_dir=bundle.export_dir,
        exported_files=bundle.exported_files,
        pruned_dirs=bundle.pruned_dirs,
    )


def export_latest_csv(conn, basket_csv: Path, out_path: Path) -> Path:
    base_df = pd.DataFrame([dict(r) for r in list_basket_items(conn)])
    if base_df.empty:
        write_presentation_csv(base_df, out_path)
        return out_path
    base_df = base_df.rename(
        columns={
            "line_no": "line",
            "viva_name": "viva_product",
            "basket_label": "basket_item",
        }
    )
    base_df = base_df[["line", "category", "product_id", "basket_item", "viva_product"]]
    latest = pd.DataFrame([dict(r) for r in latest_prices_frame(conn)])
    url_master = pd.DataFrame([dict(r) for r in list_item_url_master(conn)])
    if latest.empty:
        write_presentation_csv(base_df, out_path)
        return out_path

    pack_lookup: dict[tuple[int, str], tuple[str, str, str]] = {}
    if not url_master.empty:
        for _, r in url_master.iterrows():
            pack_lookup[(int(r["line_no"]), str(r["store_label"]))] = (
                str(r.get("pack_qty") or ""),
                str(r.get("pack_unit") or ""),
                str(r.get("pack_match") or ""),
            )

    pivot_cols = []
    for _, r in latest.iterrows():
        line = int(r["line_no"])
        store = str(r["store_label"])
        pq, pu, pm = pack_lookup.get((line, store), ("", "", ""))
        row = {
            "line": line,
            f"{store}_price": r["price"],
            f"{store}_original_price": r["original_price"],
            f"{store}_discount_pct": r["discount_percentage"],
            f"{store}_price_per_base": r.get("price_per_base"),
            f"{store}_normalized_unit": r.get("normalized_unit"),
            f"{store}_pack_match": pm,
        }
        pivot_cols.append(row)
    latest_wide = pd.DataFrame(pivot_cols).groupby("line", as_index=False).first()
    final = base_df.merge(latest_wide, on="line", how="left")
    write_presentation_csv(final, out_path, quoting=csv.QUOTE_MINIMAL)
    return out_path


def viva_comparison_frame(conn, *, include_pack_mismatch: bool = False) -> pd.DataFrame:
    """Latest normalized prices pivoted by line with gap vs worst-case Viva reference."""
    detail = latest_prices_detail(conn)
    comp = _competitive_prices(detail, include_pack_mismatch=include_pack_mismatch)
    if comp.empty:
        return pd.DataFrame()

    url_master = pd.DataFrame([dict(r) for r in list_item_url_master(conn)])
    pack_flags: dict[tuple[int, str], str] = {}
    if not url_master.empty:
        for _, r in url_master.iterrows():
            pack_flags[(int(r["line_no"]), str(r["store_label"]))] = str(r.get("pack_match") or "")

    refs = viva_reference_by_line(comp).set_index("line_no")
    rows = []
    for line_no, grp in comp.groupby("line_no"):
        ref = refs.loc[int(line_no)] if int(line_no) in refs.index else None
        viva_ppb = float(ref["viva_reference_ppb"]) if ref is not None else None
        viva_store = str(ref["viva_reference_store"]) if ref is not None else None
        row: dict = {
            "line_no": int(line_no),
            "viva_price_per_base": viva_ppb,
            "viva_reference_store": viva_store,
        }
        for _, r in grp.iterrows():
            store = str(r["store_label"])
            norm = comparison_row_normalized(r)
            ppb = norm.get("price_per_base")
            row[f"{store}_price_per_base"] = ppb
            row[f"{store}_pack_match"] = pack_flags.get((int(line_no), store), "")
            row[f"{store}_gap_vs_viva_pct"] = gap_vs_viva_pct(
                float(ppb) if ppb is not None else None,
                viva_ppb,
            )
        rows.append(row)
    return pd.DataFrame(rows)


def backfill_basket_labels_from_csv(conn, basket_csv: Path) -> int:
    """Fill empty basket_label values from CSV Basket column (by line_no)."""
    _, rows = parse_basket_csv(basket_csv)
    updated = 0
    for row in rows:
        label = str(row.get("basket_label") or "").strip()
        if not label:
            continue
        cur = conn.execute(
            "SELECT basket_label FROM basket_items WHERE line_no = ? AND is_active = 1",
            (int(row["line_no"]),),
        ).fetchone()
        if cur is None:
            continue
        existing = str(cur["basket_label"] or "").strip()
        if existing:
            continue
        conn.execute(
            "UPDATE basket_items SET basket_label = ? WHERE line_no = ?",
            (label, int(row["line_no"])),
        )
        updated += 1
    if updated:
        conn.commit()
    return updated


def grid_integrity_report(conn) -> pd.DataFrame:
    """Data-quality counts from the unified store × basket grid."""
    grid = pd.DataFrame([dict(r) for r in list_basket_store_grid(conn)])
    if grid.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    for store_label, grp in grid.groupby("store_label"):
        total = len(grp)
        rows.append(
            {
                "store_label": store_label,
                "grocery_chain": grp["grocery_chain_name"].iloc[0],
                "grid_rows": total,
                "missing_basket_label": int(
                    grp["basket_label"].fillna("").astype(str).str.strip().eq("").sum()
                ),
                "missing_chain_name": int(
                    grp["chain_item_name"].fillna("").astype(str).str.strip().eq("").sum()
                ),
                "missing_url": int((grp["has_url"] != 1).sum()),
                "missing_generic_desc": int(
                    grp["generic_description"].fillna("").astype(str).str.strip().eq("").sum()
                ),
                "extraction_ok": int(
                    grp["extraction_status"].fillna("").astype(str).str.lower().eq("ok").sum()
                ),
                "extraction_missing_url": int(
                    grp["extraction_status"].fillna("").astype(str).str.lower().eq("missing_url").sum()
                ),
            }
        )
    return pd.DataFrame(rows).sort_values("store_label")


def analytics_timeseries(conn) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT
          r.run_ts,
          r.run_date,
          r.run_id,
          b.brand_name,
          s.store_label,
          bi.line_no,
          bi.category,
          IFNULL(bi.basket_label, '') AS basket_label,
          bi.viva_name,
          rp.price,
          rp.original_price,
          rp.discount_percentage,
          rp.discounted_price,
          rp.status AS extraction_status,
          rp.price_per_base,
          rp.normalized_unit
        FROM run_item_prices rp
        JOIN runs r ON r.run_id = rp.run_id
        JOIN stores s ON s.store_id = rp.store_id
        JOIN brands b ON b.brand_id = s.brand_id
        JOIN basket_items bi ON bi.basket_item_id = rp.basket_item_id
        ORDER BY r.run_ts, s.store_label, bi.line_no
        """,
        conn,
    )


def _pack_ok_for_competitive(pack_match: object) -> bool:
    val = str(pack_match or "").strip().lower()
    if not val:
        return False
    # "normalized" = loose fresh produce/meat matched per kg/L (pack-agnostic but comparable).
    return val in {"exact", "normalized"}


def _basket_line_meta(conn) -> dict[int, dict[str, str]]:
    return {
        int(r["line_no"]): {
            "category": str(r["category"] or ""),
            "basket_label": str(r["basket_label"] or r["viva_name"] or ""),
            "viva_name": str(r["viva_name"] or ""),
            "product_id": str(r["product_id"] or ""),
        }
        for r in list_basket_items(conn)
    }


def latest_prices_detail(conn) -> pd.DataFrame:
    """Latest extraction joined to unified basket store grid."""
    grid = pd.DataFrame([dict(r) for r in list_basket_store_grid(conn)])
    if grid.empty:
        return pd.DataFrame()
    latest = pd.read_sql_query(
        """
        WITH latest AS (SELECT MAX(run_id) AS run_id FROM runs)
        SELECT
          rp.store_id,
          rp.basket_item_id,
          r.run_ts,
          r.run_date,
          rp.price,
          rp.original_price,
          rp.discount_percentage,
          rp.discounted_price,
          rp.status AS extraction_status,
          rp.price_per_base,
          rp.normalized_unit,
          rp.normalized_qty
        FROM latest l
        JOIN run_item_prices rp ON rp.run_id = l.run_id
        JOIN runs r ON r.run_id = rp.run_id
        """,
        conn,
    )
    if latest.empty:
        grid["extraction_status"] = grid.get("extraction_status", "")
        return grid
    merged = grid.merge(
        latest,
        on=["store_id", "basket_item_id"],
        how="left",
        suffixes=("", "_rp"),
    )
    if "extraction_status_rp" in merged.columns:
        merged["extraction_status"] = merged["extraction_status_rp"].fillna(
            merged.get("extraction_status", "")
        )
        merged = merged.drop(columns=["extraction_status_rp"], errors="ignore")
    merged = merged.rename(columns={"status": "url_status"})
    if "extraction_price" in merged.columns:
        merged["price"] = merged["extraction_price"]
    elif "extraction_discounted_price" in merged.columns:
        merged["price"] = merged["extraction_discounted_price"]
    if "extraction_price_per_base" in merged.columns:
        merged["price_per_base"] = merged["extraction_price_per_base"]
    if "run_date" not in merged.columns and "run_ts" in merged.columns:
        merged["run_date"] = pd.to_datetime(merged["run_ts"], errors="coerce").dt.date
    return merged


def _competitive_prices(detail: pd.DataFrame, *, include_pack_mismatch: bool) -> pd.DataFrame:
    if detail.empty:
        return detail
    out = detail[
        (detail["extraction_status"].astype(str) == "ok") & detail["price_per_base"].notna()
    ].copy()
    if not include_pack_mismatch:
        out = out[out["pack_match"].map(_pack_ok_for_competitive)]
    return out


def snapshot_kpis(conn, *, include_pack_mismatch: bool = False) -> dict[str, object]:
    detail = latest_prices_detail(conn)
    comp = _competitive_prices(detail, include_pack_mismatch=include_pack_mismatch)
    if comp.empty:
        return {
            "run_date": None,
            "store_totals": pd.DataFrame(),
            "viva_total": None,
            "viva_store_label": None,
            "cheapest_competitor_total": None,
            "cheapest_competitor_label": None,
            "gap_vs_cheapest_pct": None,
            "viva_rank": None,
            "store_count": 0,
            "coverage_pct": None,
        }

    run_date = comp["run_date"].iloc[0]
    comp_only = comp[~comp["brand_name"].astype(str).str.lower().eq("viva")]
    comp_store_totals = (
        comp_only.groupby(["store_label", "brand_name"], as_index=False)["price_per_base"]
        .sum()
        .rename(columns={"price_per_base": "normalized_basket_total"})
    )
    viva_total = viva_worst_basket_total(comp)
    viva_store_label = VIVA_REFERENCE_LABEL if viva_total is not None else None
    if viva_total is not None:
        viva_row = pd.DataFrame(
            [
                {
                    "store_label": VIVA_REFERENCE_LABEL,
                    "brand_name": "Viva",
                    "normalized_basket_total": viva_total,
                }
            ]
        )
        store_totals = pd.concat([viva_row, comp_store_totals], ignore_index=True).sort_values(
            "normalized_basket_total"
        )
    else:
        store_totals = comp_store_totals.sort_values("normalized_basket_total")

    comp_rows = comp_store_totals

    cheapest_comp_total = None
    cheapest_comp_label = None
    if not comp_rows.empty:
        best_comp = comp_rows.loc[comp_rows["normalized_basket_total"].idxmin()]
        cheapest_comp_total = float(best_comp["normalized_basket_total"])
        cheapest_comp_label = str(best_comp["store_label"])

    gap_vs_cheapest = None
    if viva_total is not None and cheapest_comp_total is not None and cheapest_comp_total > 0:
        gap_vs_cheapest = (viva_total - cheapest_comp_total) / cheapest_comp_total * 100.0

    viva_rank = None
    if viva_store_label and not store_totals.empty:
        ranked = store_totals.reset_index(drop=True)
        match = ranked[ranked["store_label"] == viva_store_label].index
        if len(match):
            viva_rank = int(match[0]) + 1

    ok_lines = comp.groupby("store_label")["line_no"].nunique()
    grid = pd.DataFrame([dict(r) for r in list_basket_store_grid(conn)])
    if grid.empty:
        coverage_pct = None
    else:
        total_by_store = grid.groupby("store_label")["line_no"].nunique()
        coverage_pct = float(
            (ok_lines / total_by_store).dropna().max() * 100.0
        ) if len(ok_lines) else None

    return {
        "run_date": run_date,
        "store_totals": store_totals,
        "viva_total": viva_total,
        "viva_store_label": viva_store_label,
        "cheapest_competitor_total": cheapest_comp_total,
        "cheapest_competitor_label": cheapest_comp_label,
        "gap_vs_cheapest_pct": gap_vs_cheapest,
        "viva_rank": viva_rank,
        "store_count": len(store_totals),
        "coverage_pct": coverage_pct,
    }


def category_rollup(conn, *, include_pack_mismatch: bool = False) -> pd.DataFrame:
    detail = latest_prices_detail(conn)
    comp = _competitive_prices(detail, include_pack_mismatch=include_pack_mismatch)
    if comp.empty:
        return pd.DataFrame()

    pivot = comp.pivot_table(
        index="category",
        columns="store_label",
        values="price_per_base",
        aggfunc="sum",
    )
    pivot = pivot.reset_index().rename_axis(None, axis=1)

    refs = viva_reference_by_line(comp)
    line_cat = comp.drop_duplicates("line_no")[["line_no", "category"]]
    ref_map = refs.set_index("line_no")["viva_reference_ppb"]
    viva_benchmark = (
        line_cat.assign(viva_benchmark=line_cat["line_no"].map(ref_map))
        .groupby("category")["viva_benchmark"]
        .sum()
    )
    pivot["viva_worst_benchmark"] = pivot["category"].map(viva_benchmark)

    store_cols = [c for c in pivot.columns if c not in {"category", "viva_worst_benchmark"}]
    viva_physical = {
        str(r["store_label"])
        for _, r in comp.iterrows()
        if str(r["brand_name"]).lower() == "viva"
    }
    for col in store_cols:
        if col in viva_physical:
            continue
        pivot[f"{col}_gap_vs_viva_pct"] = (
            (pivot[col] - pivot["viva_worst_benchmark"]) / pivot["viva_worst_benchmark"] * 100.0
        ).where(pivot["viva_worst_benchmark"].notna() & (pivot["viva_worst_benchmark"] != 0))

    return pivot


def top_gaps_and_wins(
    conn,
    *,
    top_n: int = 10,
    include_pack_mismatch: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    detail = latest_prices_detail(conn)
    comp = _competitive_prices(detail, include_pack_mismatch=include_pack_mismatch)
    if comp.empty:
        return pd.DataFrame(), pd.DataFrame()

    gap_rows: list[dict] = []
    win_rows: list[dict] = []
    refs = viva_reference_by_line(comp).set_index("line_no")

    for line_no, grp in comp.groupby("line_no"):
        if int(line_no) not in refs.index:
            continue
        ref = refs.loc[int(line_no)]
        viva_ppb = float(ref["viva_reference_ppb"])
        viva_store = str(ref["viva_reference_store"])
        viva_rows = grp[grp["brand_name"].str.lower() == "viva"]
        viva_meta = viva_rows.iloc[0] if not viva_rows.empty else grp.iloc[0]
        competitors = grp[grp["brand_name"].str.lower() != "viva"]
        if competitors.empty:
            continue

        comp_scored: list[tuple[pd.Series, float]] = []
        for _, r in competitors.iterrows():
            ppb = comparison_row_normalized(r).get("price_per_base")
            if ppb is not None and float(ppb) > 0:
                comp_scored.append((r, float(ppb)))
        if not comp_scored:
            continue
        best = min(comp_scored, key=lambda x: x[1])[0]
        best_ppb = min(ppb for _, ppb in comp_scored)
        comp_ppbs = [ppb for _, ppb in comp_scored]

        base = {
            "line_no": int(line_no),
            "category": str(viva_meta["category"] or ""),
            "basket_item": str(
                viva_meta.get("basket_label") or viva_meta.get("viva_name") or ""
            ),
            "viva_product": str(viva_meta["viva_name"] or ""),
            "viva_store": viva_store,
            "viva_price_per_base": viva_ppb,
            "pack_match": str(viva_meta.get("pack_match") or ""),
        }

        if viva_ppb > best_ppb:
            gap_rows.append(
                {
                    **base,
                    "best_competitor_store": str(best["store_label"]),
                    "best_competitor_price_per_base": best_ppb,
                    "gap_pct": (viva_ppb - best_ppb) / best_ppb * 100.0,
                }
            )
        elif viva_ppb <= min(comp_ppbs):
            comp_max = max(comp_ppbs)
            win_rows.append(
                {
                    **base,
                    "cheapest_competitor_store": str(best["store_label"]),
                    "advantage_pct": (comp_max - viva_ppb) / viva_ppb * 100.0
                    if viva_ppb > 0
                    else None,
                }
            )

    gaps = (
        pd.DataFrame(gap_rows).sort_values("gap_pct", ascending=False).head(top_n)
        if gap_rows
        else pd.DataFrame()
    )
    wins = (
        pd.DataFrame(win_rows).sort_values("advantage_pct", ascending=False).head(top_n)
        if win_rows
        else pd.DataFrame()
    )
    return gaps, wins


def url_coverage_by_store(conn) -> pd.DataFrame:
    grid = pd.DataFrame([dict(r) for r in list_url_master_grid(conn)])
    if grid.empty:
        return pd.DataFrame()

    def _has_url(row: pd.Series) -> bool:
        return bool(str(row.get("source_url") or "").strip()) and bool(
            str(row.get("item_id") or "").strip()
        )

    grid["has_url"] = grid.apply(_has_url, axis=1)
    rows = []
    for store_label, grp in grid.groupby("store_label"):
        total = len(grp)
        has_url = int(grp["has_url"].sum())
        status = grp["status"].astype(str).str.lower()
        rows.append(
            {
                "store_label": store_label,
                "grocery_chain": grp["grocery_chain_name"].iloc[0],
                "total_lines": total,
                "with_url": has_url,
                "coverage_pct": has_url / total * 100.0 if total else 0.0,
                "ok": int((status == "ok").sum()),
                "pack_mismatch": int(status.eq("pack_mismatch").sum()),
                "missing": int(status.eq("missing").sum()),
            }
        )
    return pd.DataFrame(rows).sort_values("store_label")


def extraction_audit(conn) -> pd.DataFrame:
    detail = latest_prices_detail(conn)
    if detail.empty:
        return pd.DataFrame()
    cols = [
        c
        for c in [
            "line_no",
            "category",
            "basket_label",
            "viva_name",
            "chain_item_name",
            "store_label",
            "brand_name",
            "extraction_status",
            "price",
            "price_per_base",
            "pack_match",
            "match_method",
            "url_status",
            "run_date",
        ]
        if c in detail.columns
    ]
    out = detail[cols].copy()
    rename = {
        "viva_name": "viva_product",
        "basket_label": "basket_item",
        "chain_item_name": "chain_product",
        "brand_name": "grocery_chain",
        "extraction_status": "status",
    }
    return out.rename(columns=rename)


def pack_mismatch_register(conn) -> pd.DataFrame:
    grid = pd.DataFrame([dict(r) for r in list_url_master_grid(conn)])
    if grid.empty:
        return pd.DataFrame()

    pm = grid["pack_match"].astype(str).str.lower()
    bad = grid[~pm.isin({"exact", ""})].copy()
    if bad.empty:
        return pd.DataFrame()

    return bad[
        [
            "line_no",
            "grocery_chain_name",
            "store_label",
            "chain_item_name",
            "pack_text",
            "catalog_pack_text",
            "pack_match",
            "match_method",
            "status",
        ]
    ].rename(
        columns={
            "grocery_chain_name": "grocery_chain",
            "chain_item_name": "basket_item_name",
            "pack_text": "basket_pack",
        }
    )


def match_method_breakdown(conn) -> pd.DataFrame:
    grid = pd.DataFrame([dict(r) for r in list_url_master_grid(conn)])
    if grid.empty:
        return pd.DataFrame()

    grid = grid[grid["source_url"].astype(str).str.strip() != ""]
    grid["match_method"] = grid["match_method"].fillna("").astype(str).str.strip()
    grid.loc[grid["match_method"] == "", "match_method"] = "unknown"
    return (
        grid.groupby(["store_label", "match_method"], as_index=False)
        .size()
        .rename(columns={"size": "count"})
        .sort_values(["store_label", "match_method"])
    )


def comparison_matrix(conn, *, include_pack_mismatch: bool = False) -> pd.DataFrame:
    cmp_df = viva_comparison_frame(conn, include_pack_mismatch=include_pack_mismatch)
    if cmp_df.empty:
        return cmp_df

    meta = _basket_line_meta(conn)
    cmp_df["category"] = cmp_df["line_no"].map(lambda n: meta.get(int(n), {}).get("category", ""))
    cmp_df["basket_item"] = cmp_df["line_no"].map(
        lambda n: meta.get(int(n), {}).get("basket_label", "")
    )
    cmp_df["viva_product"] = cmp_df["line_no"].map(
        lambda n: meta.get(int(n), {}).get("viva_name", "")
    )

    if not include_pack_mismatch:
        for col in list(cmp_df.columns):
            if not col.endswith("_gap_vs_viva_pct"):
                continue
            store = col[: -len("_gap_vs_viva_pct")]
            pc = f"{store}_pack_match"
            ppb = f"{store}_price_per_base"
            if pc not in cmp_df.columns:
                continue
            bad = ~cmp_df[pc].map(_pack_ok_for_competitive)
            cmp_df.loc[bad, col] = None
            if ppb in cmp_df.columns:
                cmp_df.loc[bad, ppb] = None
    return cmp_df


def gap_heatmap_frame(conn, *, include_pack_mismatch: bool = False) -> pd.DataFrame:
    cmp_df = comparison_matrix(conn, include_pack_mismatch=include_pack_mismatch)
    if cmp_df.empty:
        return pd.DataFrame()

    gap_cols = [c for c in cmp_df.columns if c.endswith("_gap_vs_viva_pct")]
    if not gap_cols:
        return pd.DataFrame()

    meta = cmp_df[["line_no", "category", "basket_item", "viva_product"]].copy()
    matrix = cmp_df.set_index("line_no")[gap_cols].copy()
    matrix.columns = [c[: -len("_gap_vs_viva_pct")] for c in matrix.columns]
    matrix = matrix.reset_index().merge(meta, on="line_no", how="left")
    return matrix


def promo_intensity_by_store(conn) -> pd.DataFrame:
    detail = latest_prices_detail(conn)
    if detail.empty:
        return pd.DataFrame()

    ok = detail[detail["extraction_status"].astype(str) == "ok"].copy()
    ok["discount_percentage"] = pd.to_numeric(ok["discount_percentage"], errors="coerce")
    rows = []
    for store_label, grp in ok.groupby("store_label"):
        disc = grp["discount_percentage"].dropna()
        promo_count = int((disc > 0).sum())
        rows.append(
            {
                "store_label": store_label,
                "grocery_chain": grp["brand_name"].iloc[0],
                "lines_with_price": len(grp),
                "lines_on_promo": promo_count,
                "avg_discount_pct": float(disc[disc > 0].mean()) if promo_count else None,
            }
        )
    return pd.DataFrame(rows).sort_values("store_label")


def viva_vs_best_gap_timeseries(conn) -> pd.DataFrame:
    adf = analytics_timeseries(conn)
    if adf.empty:
        return pd.DataFrame()

    adf = adf[
        (adf["extraction_status"].astype(str) == "ok") & adf["price_per_base"].notna()
    ].copy()
    if adf.empty:
        return pd.DataFrame()

    rows = []
    for run_date, grp in adf.groupby("run_date"):
        ok = grp[
            (grp["extraction_status"].astype(str) == "ok") & grp["price_per_base"].notna()
        ].copy()
        if ok.empty:
            continue
        viva_lines = (
            ok[ok["brand_name"].astype(str).str.lower() == "viva"]
            .groupby("line_no")["price_per_base"]
            .max()
        )
        comp = ok[ok["brand_name"].astype(str).str.lower() != "viva"]
        if viva_lines.empty or comp.empty:
            continue
        viva_total = float(viva_lines.sum())
        best_comp = float(comp.groupby("store_label")["price_per_base"].sum().min())
        if best_comp <= 0:
            continue
        rows.append(
            {
                "run_date": run_date,
                "viva_normalized_total": viva_total,
                "cheapest_competitor_total": best_comp,
                "gap_vs_cheapest_pct": (viva_total - best_comp) / best_comp * 100.0,
            }
        )
    return pd.DataFrame(rows)


def price_change_log(conn, *, threshold_pct: float = 5.0) -> pd.DataFrame:
    adf = analytics_timeseries(conn)
    if adf.empty:
        return pd.DataFrame()

    ok = adf[
        (adf["extraction_status"].astype(str) == "ok") & adf["price_per_base"].notna()
    ].copy()
    if ok.empty:
        return pd.DataFrame()

    ok = ok.sort_values(["store_label", "line_no", "run_ts"])
    ok["prev_ppb"] = ok.groupby(["store_label", "line_no"])["price_per_base"].shift(1)
    ok["change_pct"] = (
        (ok["price_per_base"].astype(float) - ok["prev_ppb"].astype(float))
        / ok["prev_ppb"].astype(float)
        * 100.0
    ).where(ok["prev_ppb"].notna() & (ok["prev_ppb"] != 0))

    moved = ok[ok["change_pct"].abs() >= threshold_pct].copy()
    if moved.empty:
        return pd.DataFrame()

    return moved[
        [
            "run_date",
            "store_label",
            "brand_name",
            "line_no",
            "category",
            "basket_label",
            "viva_name",
            "prev_ppb",
            "price_per_base",
            "change_pct",
        ]
    ].rename(
        columns={
            "brand_name": "grocery_chain",
            "viva_name": "viva_product",
            "basket_label": "basket_item",
        }
    )


def _comparison_row_normalized(r: pd.Series) -> dict[str, object]:
    """Backward-compatible alias."""
    return comparison_row_normalized(r)


def latest_comparison_long(conn) -> pd.DataFrame:
    detail = latest_prices_detail(conn)
    if detail.empty:
        return pd.DataFrame()

    comp = _competitive_prices(detail, include_pack_mismatch=True)
    refs = viva_reference_by_line(comp).set_index("line_no")

    rows = []
    for _, r in detail.iterrows():
        line = int(r["line_no"])
        norm = comparison_row_normalized(r)
        ppb = norm["price_per_base"]
        ref = refs.loc[line] if line in refs.index else None
        viva_ref_ppb = float(ref["viva_reference_ppb"]) if ref is not None else None
        viva_ref_store = str(ref["viva_reference_store"]) if ref is not None else ""
        gap = None
        if str(r["brand_name"]).lower() != "viva":
            gap = gap_vs_viva_pct(
                float(ppb) if ppb is not None else None,
                viva_ref_ppb,
            )
        ext_status = format_extraction_status(
            r.get("extraction_status"),
            r.get("extraction_error"),
        )
        rows.append(
            {
                "line": line,
                "category": r["category"],
                "basket_item": str(r.get("basket_label") or r.get("viva_name") or ""),
                "product_id": r.get("product_id"),
                "viva_product": r["viva_name"],
                "chain_product": r.get("chain_item_name"),
                "basket_pack": str(r.get("pack_text") or "").strip(),
                "catalog_item_name": norm["catalog_item_name"],
                "catalog_item_pack": norm["catalog_item_pack"],
                "store_label": r["store_label"],
                "grocery_chain": r["brand_name"],
                "price": r["price"],
                "original_price": r["original_price"],
                "discount_pct": r["discount_percentage"],
                "normalized_qty": norm["normalized_qty"],
                "price_per_base": ppb,
                "normalized_unit": norm["normalized_unit"],
                "gap_vs_viva_pct": gap,
                "viva_reference_store": viva_ref_store,
                "viva_reference_price_per_base": viva_ref_ppb,
                "pack_match": r["pack_match"],
                "extraction_status": ext_status,
                "url_status": r["url_status"],
                "match_method": r["match_method"],
                "run_date": r["run_date"],
                "item_url": str(r.get("source_url") or "").strip(),
            }
        )
    df = pd.DataFrame(rows)
    column_order = [
        "line",
        "category",
        "basket_item",
        "product_id",
        "viva_product",
        "chain_product",
        "basket_pack",
        "catalog_item_name",
        "catalog_item_pack",
        "store_label",
        "grocery_chain",
        "price",
        "original_price",
        "discount_pct",
        "normalized_qty",
        "price_per_base",
        "normalized_unit",
        "gap_vs_viva_pct",
        "viva_reference_store",
        "viva_reference_price_per_base",
        "pack_match",
        "extraction_status",
        "url_status",
        "match_method",
        "run_date",
        "item_url",
    ]
    return df[column_order]


def viva_cross_store_prices(conn) -> pd.DataFrame:
    """One row per basket line × Viva store with normalized pricing."""
    detail = latest_prices_detail(conn)
    if detail.empty:
        return pd.DataFrame()
    viva = detail[detail["brand_name"].astype(str).str.lower() == "viva"].copy()
    if viva.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    for _, r in viva.iterrows():
        norm = comparison_row_normalized(r)
        rows.append(
            {
                "line_no": int(r["line_no"]),
                "category": str(r.get("category") or ""),
                "basket_item": str(r.get("basket_label") or r.get("viva_name") or ""),
                "store_label": str(r["store_label"]),
                "price": r.get("price"),
                "price_per_base": norm.get("price_per_base"),
                "normalized_unit": norm.get("normalized_unit"),
                "pack_match": str(r.get("pack_match") or ""),
                "url_status": str(r.get("url_status") or ""),
                "extraction_status": format_extraction_status(
                    r.get("extraction_status"),
                    r.get("extraction_error"),
                ),
                "catalog_item_name": norm.get("catalog_item_name"),
                "catalog_item_pack": norm.get("catalog_item_pack"),
                "item_url": str(r.get("source_url") or "").strip(),
            }
        )
    return pd.DataFrame(rows).sort_values(["line_no", "store_label"])


def viva_store_spread(conn) -> pd.DataFrame:
    """Lines where multiple Viva branches have a normalized unit price."""
    cross = viva_cross_store_prices(conn)
    priced = cross[cross["price_per_base"].notna()].copy()
    if priced.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    comp = _competitive_prices(latest_prices_detail(conn), include_pack_mismatch=True)
    refs = viva_reference_by_line(comp).set_index("line_no")

    for line_no, grp in priced.groupby("line_no"):
        if len(grp) < 2:
            continue
        ppbs = grp["price_per_base"].astype(float)
        min_ppb = float(ppbs.min())
        max_ppb = float(ppbs.max())
        cheapest = grp.loc[ppbs.idxmin()]
        expensive = grp.loc[ppbs.idxmax()]
        ref_store = ""
        if int(line_no) in refs.index:
            ref_store = str(refs.loc[int(line_no)]["viva_reference_store"])
        rows.append(
            {
                "line_no": int(line_no),
                "category": str(cheapest.get("category") or ""),
                "basket_item": str(cheapest.get("basket_item") or ""),
                "min_price_per_base": min_ppb,
                "max_price_per_base": max_ppb,
                "spread_ppb": max_ppb - min_ppb,
                "spread_pct": ((max_ppb - min_ppb) / min_ppb * 100.0) if min_ppb > 0 else None,
                "cheapest_store": str(cheapest["store_label"]),
                "most_expensive_store": str(expensive["store_label"]),
                "viva_reference_store": ref_store,
            }
        )
    return pd.DataFrame(rows).sort_values("spread_pct", ascending=False, na_position="last")


def viva_unavailability(conn) -> pd.DataFrame:
    """Viva branch gaps: missing URL, failed extraction, or price at only one branch."""
    grid = pd.DataFrame([dict(r) for r in list_basket_store_grid(conn)])
    if grid.empty:
        return pd.DataFrame()
    viva = grid[grid["brand_name"].astype(str).str.lower() == "viva"].copy()
    if viva.empty:
        return pd.DataFrame()

    viva["_ppb"] = viva.apply(
        lambda row: comparison_row_normalized(row).get("price_per_base"), axis=1
    )
    priced_lines = (
        viva[viva["_ppb"].notna()].groupby("line_no")["store_label"].nunique()
    )

    rows: list[dict[str, object]] = []
    for _, r in viva.iterrows():
        line_no = int(r["line_no"])
        has_url = int(r.get("has_url") or 0) == 1
        ext = str(r.get("extraction_status") or "").strip().lower()
        url_st = str(r.get("status") or "").strip().lower()
        issue = ""
        if not has_url or url_st in {"", "missing"}:
            issue = "missing_url"
        elif ext and ext not in {"ok"}:
            issue = "extraction_failed"
        elif priced_lines.get(line_no, 0) == 1 and comparison_row_normalized(r).get("price_per_base"):
            issue = "single_branch_price"

        if not issue:
            continue
        rows.append(
            {
                "line_no": line_no,
                "category": str(r.get("category") or ""),
                "basket_item": str(r.get("basket_label") or r.get("viva_name") or ""),
                "store_label": str(r["store_label"]),
                "issue": issue,
                "url_status": url_st,
                "extraction_status": format_extraction_status(
                    r.get("extraction_status"),
                    r.get("extraction_error"),
                ),
                "chain_product": str(r.get("chain_item_name") or ""),
                "item_url": str(r.get("source_url") or "").strip(),
            }
        )
    return pd.DataFrame(rows).sort_values(["issue", "line_no", "store_label"])


def export_weekly_summary_csv(conn, out_path: Path, *, include_pack_mismatch: bool = False) -> Path:
    kpis = snapshot_kpis(conn, include_pack_mismatch=include_pack_mismatch)
    gaps, wins = top_gaps_and_wins(conn, include_pack_mismatch=include_pack_mismatch)
    categories = category_rollup(conn, include_pack_mismatch=include_pack_mismatch)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sections: list[pd.DataFrame] = []

    kpi_rows = [
        {"metric": "run_date", "value": kpis.get("run_date")},
        {"metric": "viva_store_label", "value": kpis.get("viva_store_label")},
        {"metric": "viva_normalized_total", "value": kpis.get("viva_total")},
        {"metric": "cheapest_competitor_label", "value": kpis.get("cheapest_competitor_label")},
        {"metric": "cheapest_competitor_total", "value": kpis.get("cheapest_competitor_total")},
        {"metric": "gap_vs_cheapest_pct", "value": kpis.get("gap_vs_cheapest_pct")},
        {"metric": "viva_rank", "value": kpis.get("viva_rank")},
        {"metric": "coverage_pct", "value": kpis.get("coverage_pct")},
    ]
    sections.append(pd.DataFrame(kpi_rows).assign(section="KPI"))

    store_totals = kpis.get("store_totals")
    if isinstance(store_totals, pd.DataFrame) and not store_totals.empty:
        sections.append(store_totals.assign(section="STORE_TOTALS"))

    if not gaps.empty:
        sections.append(gaps.assign(section="BIGGEST_GAPS"))
    if not wins.empty:
        sections.append(wins.assign(section="BIGGEST_WINS"))
    if not categories.empty:
        sections.append(categories.assign(section="CATEGORY_ROLLUP"))

    combined = pd.concat(sections, ignore_index=True, sort=False)
    write_presentation_csv(
        round_kpi_export_values(combined), out_path, quoting=csv.QUOTE_MINIMAL
    )
    return out_path


def export_extraction_audit_csv(conn, out_path: Path) -> Path:
    write_presentation_csv(extraction_audit(conn), out_path, quoting=csv.QUOTE_MINIMAL)
    return out_path


def export_price_history_long_csv(conn, out_path: Path) -> Path:
    write_presentation_csv(analytics_timeseries(conn), out_path, quoting=csv.QUOTE_MINIMAL)
    return out_path


def export_latest_long_csv(conn, out_path: Path) -> Path:
    write_presentation_csv(latest_comparison_long(conn), out_path, quoting=csv.QUOTE_MINIMAL)
    return out_path


def export_simple_prices_excel(conn, out_path: Path) -> Path:
    """One row per basket line; per store: Item Name, Item Qty, Item Price."""
    grid = pd.DataFrame([dict(r) for r in list_basket_store_grid(conn)])
    items = pd.DataFrame([dict(r) for r in list_basket_items(conn)])
    if items.empty or grid.empty:
        write_presentation_excel(pd.DataFrame(), out_path)
        return out_path

    price_lookup: dict[tuple[int, str], float | None] = {}
    name_lookup: dict[tuple[int, str], str] = {}
    qty_lookup: dict[tuple[int, str], str] = {}
    for _, g in grid.iterrows():
        line_no = int(g["line_no"])
        store_label = str(g["store_label"])
        brand_name = str(g["brand_name"])
        name_lookup[(line_no, brand_name)] = str(g.get("chain_item_name") or "").strip()
        qty_lookup[(line_no, brand_name)] = str(g.get("pack_text") or "").strip()
        val = g.get("extraction_price")
        if val is None or (isinstance(val, float) and pd.isna(val)):
            val = g.get("extraction_discounted_price")
        if val is None or (isinstance(val, float) and pd.isna(val)):
            price_lookup[(line_no, store_label)] = None
        else:
            price_lookup[(line_no, store_label)] = float(val)

    store_order = (
        grid[["store_label", "brand_name"]]
        .drop_duplicates()
        .sort_values(["brand_name", "store_label"])
    )
    rows: list[dict] = []
    for _, item in items.sort_values("line_no").iterrows():
        line_no = int(item["line_no"])
        row: dict = {
            "Line": line_no,
            "Category": str(item.get("category") or ""),
            "Basket Item": str(item.get("basket_label") or item.get("viva_name") or ""),
        }
        for _, store in store_order.iterrows():
            store_label = str(store["store_label"])
            brand_name = str(store["brand_name"])
            prefix = store_label
            row[f"{prefix} - Item Name"] = name_lookup.get((line_no, brand_name), "")
            row[f"{prefix} - Item Qty"] = qty_lookup.get((line_no, brand_name), "")
            row[f"{prefix} - Item Price"] = price_lookup.get((line_no, store_label))
        rows.append(row)

    write_presentation_excel(pd.DataFrame(rows), out_path)
    return out_path


def export_csv(
    conn,
    basket_csv: Path,
    out_path: Path,
    *,
    fmt: str = "wide",
    include_pack_mismatch: bool = False,
) -> Path:
    fmt = fmt.strip().lower()
    if fmt == "wide":
        return export_latest_csv(conn, basket_csv, out_path)
    if fmt == "long":
        return export_latest_long_csv(conn, out_path)
    if fmt in {"summary", "weekly"}:
        return export_weekly_summary_csv(
            conn, out_path, include_pack_mismatch=include_pack_mismatch
        )
    if fmt in {"audit", "extraction"}:
        return export_extraction_audit_csv(conn, out_path)
    if fmt in {"history", "timeseries"}:
        return export_price_history_long_csv(conn, out_path)
    if fmt in {"excel", "xlsx", "simple"}:
        return export_simple_prices_excel(conn, out_path)
    raise ValueError(f"Unknown export format: {fmt!r}")
