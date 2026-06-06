from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path
import sys

import pandas as pd
import plotly.express as px
import streamlit as st

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from viva_tracker.db import connect_db, init_db
from viva_tracker.presentation_format import (
    round_for_presentation,
    streamlit_presentation_column_config,
)
from viva_tracker.match_groups import match_group_for_category
from viva_tracker.jobs import (
    analytics_timeseries,
    backfill_basket_labels_from_csv,
    category_rollup,
    comparison_matrix,
    export_csv,
    export_latest_csv,
    extract_prices,
    extraction_audit,
    gap_heatmap_frame,
    grid_integrity_report,
    initialize_from_csv,
    match_method_breakdown,
    pack_mismatch_register,
    price_change_log,
    promo_intensity_by_store,
    refresh_store_uuids,
    snapshot_kpis,
    top_gaps_and_wins,
    url_coverage_by_store,
    viva_cross_store_prices,
    viva_store_spread,
    viva_unavailability,
    viva_vs_best_gap_timeseries,
)
from viva_tracker.viva_reference import VIVA_REFERENCE_LABEL
from viva_tracker.catalog_build import (
    build_and_save_catalog_for_store,
    format_catalog_build_progress,
)
from viva_tracker.catalog_index import catalog_exists, catalog_meta
from viva_tracker.match_engine import match_selected, match_store
from viva_tracker.match_progress import MatchProgress, format_match_progress
from viva_tracker.missing_match_audit import refresh_missing_match_audit
from viva_tracker.pack_normalize import format_title_pack_display, pack_matches_target
from viva_tracker.repository import (
    deactivate_basket_item,
    deactivate_store,
    list_basket_brand_maps,
    list_basket_items,
    list_brands,
    list_url_master_grid,
    list_stores,
    update_store,
    upsert_basket_brand_map,
    upsert_basket_item,
    upsert_brand,
    upsert_item_url_master,
    upsert_store,
)
from viva_tracker.storefront import (
    fetch_store_metadata,
    is_grocery_product_url,
    resolve_url_master_from_product_url,
)
from viva_tracker.settings import BASKET_CSV_PATH, EXPORTS_DIR, MAX_RUN_EXPORT_RETENTION


def _fmt_display_ts(val: object) -> str:
    if val is None or val == "":
        return ""
    try:
        if isinstance(val, float) and pd.isna(val):
            return ""
    except TypeError:
        pass
    s = str(val).strip()
    if "T" in s:
        return s.replace("T", " ")[:19]
    return s[:19] if len(s) > 19 else s


def _fmt_display_date(val: object) -> str:
    if val is None or val == "":
        return ""
    try:
        if isinstance(val, float) and pd.isna(val):
            return ""
    except TypeError:
        pass
    s = str(val).strip()
    if "T" in s:
        return s.split("T", 1)[0]
    if " " in s:
        return s.split(" ", 1)[0]
    return s[:10] if len(s) >= 10 else s


_URL_MASTER_URL_COL_WIDTH = 300  # ~75% of Streamlit "large" column width
_VIVA_CHART_COLOR = "#E85D04"


def _store_color_map(store_labels: list[str], brand_by_store: dict[str, str]) -> dict[str, str]:
    colors: dict[str, str] = {}
    idx = 0
    palette = px.colors.qualitative.Set2
    for label in store_labels:
        if str(brand_by_store.get(label, "")).lower() == "viva":
            colors[label] = _VIVA_CHART_COLOR
        else:
            colors[label] = palette[idx % len(palette)]
            idx += 1
    return colors


def _df(rows) -> pd.DataFrame:
    return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()


def _basket_display_table(bidf: pd.DataFrame, bmdf: pd.DataFrame) -> pd.DataFrame:
    if bidf.empty:
        return pd.DataFrame()

    base = bidf.copy()
    if "line_no" not in base.columns:
        return pd.DataFrame()

    base = base.rename(
        columns={
            "line_no": "_line_no",
            "category": "Category",
            "product_id": "Product ID",
            "basket_label": "Basket Item",
            "viva_name": "Viva Product",
        }
    )
    if "created_at" in base.columns:
        base["Added Date"] = base["created_at"].map(_fmt_display_date).astype(str)

    if not bmdf.empty and {"line_no", "brand_name", "mapped_name", "pack_qty", "pack_unit"}.issubset(
        bmdf.columns
    ):
        maps = bmdf.copy()
        maps["line_no"] = pd.to_numeric(maps["line_no"], errors="coerce")
        maps = maps.dropna(subset=["line_no"])
        maps["line_no"] = maps["line_no"].astype(int)
        maps["brand_name"] = maps["brand_name"].astype(str).str.strip()
        maps["mapped_name"] = maps["mapped_name"].fillna("").astype(str).str.strip()
        maps["pack_qty"] = maps["pack_qty"].fillna("").astype(str).str.strip()
        maps["pack_unit"] = maps["pack_unit"].fillna("").astype(str).str.strip()
        maps["pack_text"] = (
            maps["pack_qty"] + " " + maps["pack_unit"]
        ).str.strip()

        prod_wide = (
            maps.pivot_table(
                index="line_no",
                columns="brand_name",
                values="mapped_name",
                aggfunc="first",
            )
            .rename(columns=lambda b: f"{b} Product")
            .reset_index()
            .rename(columns={"line_no": "_line_no"})
        )
        duplicate_base_cols = [c for c in prod_wide.columns if c != "_line_no" and c in base.columns]
        if duplicate_base_cols:
            prod_wide = prod_wide.drop(columns=duplicate_base_cols)
        pack_wide = (
            maps.pivot_table(
                index="line_no",
                columns="brand_name",
                values="pack_text",
                aggfunc="first",
            )
            .rename(columns=lambda b: f"{b} Pack")
            .reset_index()
            .rename(columns={"line_no": "_line_no"})
        )

        view = base.merge(prod_wide, how="left", on="_line_no")
        view = view.merge(pack_wide, how="left", on="_line_no")

        # Defensive cleanup in case future merges introduce suffix pairs.
        for c in list(view.columns):
            if c.endswith("_x"):
                root = c[:-2]
                ycol = f"{root}_y"
                if ycol in view.columns:
                    lx = view[c].fillna("").astype(str).str.strip()
                    view[root] = view[c].where(lx != "", view[ycol])
                    view = view.drop(columns=[c, ycol])
    else:
        view = base

    preferred = [
        "Category",
        "Product ID",
        "Basket Item",
        "Viva Product",
        "Added Date",
        "Viva Pack",
        "Carrefour Product",
        "Carrefour Pack",
        "Lulu Product",
        "Lulu Pack",
        "GALA Product",
        "GALA Pack",
        "Sava Product",
        "Sava Pack",
    ]
    ordered = [c for c in preferred if c in view.columns]
    remainder = [
        c
        for c in view.columns
        if c not in ordered
        and c not in {"_line_no", "is_active", "basket_item_id", "created_at"}
        and not c.startswith("_")
    ]
    cols = ["_line_no"] + ordered + remainder if (ordered or remainder) else list(view.columns)
    out = view[cols].copy()
    return out.fillna("")


def _resolve_brand_id(_conn: object, fetched_brand: str, brand_lookup: dict[str, int]) -> int:
    """Match Talabat chain text to an existing brand only (never create brands here)."""
    name = (fetched_brand or "").strip()
    if not name:
        raise ValueError("Talabat returned an empty brand name.")
    if not brand_lookup:
        raise ValueError(
            "No brands configured. Add your grocery chains under **Manage Brands** before adding stores."
        )
    if name in brand_lookup:
        return brand_lookup[name]
    lower_to_id = {k.lower(): v for k, v in brand_lookup.items()}
    fl = name.lower()
    if fl in lower_to_id:
        return lower_to_id[fl]
    # Map Talabat names like "Gala Supermarket" to an existing brand when exactly one needle matches.
    by_needle_in_fetched = [
        bid for bn, bid in brand_lookup.items() if len(bn) >= 2 and bn.lower() in fl
    ]
    if len(by_needle_in_fetched) == 1:
        return by_needle_in_fetched[0]
    if len(by_needle_in_fetched) > 1:
        amb = sorted({next(bn for bn, bid in brand_lookup.items() if bid == x) for x in by_needle_in_fetched})
        raise ValueError(
            f"Talabat chain “{name}” matches more than one configured brand ({', '.join(amb)}). "
            "Adjust **Manage Brands** so only one brand name matches this chain."
        )
    by_fetched_in_bn = [bid for bn, bid in brand_lookup.items() if fl in bn.lower()]
    if len(by_fetched_in_bn) == 1:
        return by_fetched_in_bn[0]
    if len(by_fetched_in_bn) > 1:
        amb = sorted({next(bn for bn, bid in brand_lookup.items() if bid == x) for x in by_fetched_in_bn})
        raise ValueError(
            f"Talabat chain “{name}” is ambiguous across brands ({', '.join(amb)}). "
            "Use clearer brand names under **Manage Brands**."
        )
    known = ", ".join(sorted(brand_lookup.keys()))
    raise ValueError(
        f"Could not match Talabat chain “{name}” to a configured brand. "
        f"**Manage Brands** must list the chain (e.g. GALA, Carrefour). Current brands: {known}."
    )


def _show_table(df: pd.DataFrame, **kwargs) -> None:
    """Render a pricing/analytics table at the UI presentation boundary."""
    if df is None or df.empty:
        st.dataframe(df, **kwargs)
        return
    display_df = round_for_presentation(df)
    column_config = kwargs.pop("column_config", None) or {}
    auto_config = streamlit_presentation_column_config(display_df)
    merged_config = {**auto_config, **column_config}
    if merged_config:
        kwargs["column_config"] = merged_config
    st.dataframe(display_df, **kwargs)


def _split_pasted_store_urls(raw: str) -> list[str]:
    """Split textarea input into URLs: one per line and/or comma- or semicolon-separated."""
    out: list[str] = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        for piece in line.replace(";", ",").split(","):
            u = piece.strip()
            if u:
                out.append(u)
    return out


def _format_pack_text(pack_qty: str, pack_unit: str) -> str:
    qty = str(pack_qty or "").strip()
    unit = str(pack_unit or "").strip()
    if qty and unit:
        return f"{qty} {unit}"
    return qty or unit


def _series_pack_quantity(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=str)
    if "pack_text" in df.columns:
        from_text = df["pack_text"].fillna("").astype(str).str.strip()
        if from_text.ne("").any():
            return from_text
    if {"pack_qty", "pack_unit"}.issubset(df.columns):
        qty = df["pack_qty"].fillna("").astype(str).str.strip()
        unit = df["pack_unit"].fillna("").astype(str).str.strip()
        both = qty.ne("") & unit.ne("")
        out = pd.Series([""] * len(df), index=df.index, dtype=str)
        out.loc[both] = qty[both] + " " + unit[both]
        out.loc[~both & qty.ne("")] = qty[~both & qty.ne("")]
        out.loc[~both & qty.eq("") & unit.ne("")] = unit[~both & qty.eq("") & unit.ne("")]
        return out
    return pd.Series([""] * len(df), index=df.index, dtype=str)


def _grid_row_has_url(row: pd.Series) -> bool:
    if "has_url" in row.index:
        try:
            return int(row["has_url"]) == 1
        except (TypeError, ValueError):
            pass
    surl = str(row.get("source_url") or "").strip()
    iid = str(row.get("item_id") or "").strip()
    return bool(surl and iid)


def _grid_selection_key(store_id: int, basket_item_id: int) -> str:
    return f"{store_id}_{basket_item_id}"


def _selected_pairs_from_seed(
    grid_df: pd.DataFrame,
    seed: dict[str, bool],
) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    for _, r in grid_df.iterrows():
        key = _grid_selection_key(int(r["store_id"]), int(r["basket_item_id"]))
        if seed.get(key):
            pairs.append((int(r["store_id"]), int(r["basket_item_id"])))
    return pairs


def _sync_editor_selection_to_seed(edited: pd.DataFrame) -> dict[str, bool]:
    """Persist checkbox edits from the data editor into the selection seed."""
    out: dict[str, bool] = {}
    for _, row in edited.iterrows():
        if row.get("Select") is True:
            out[_grid_selection_key(int(row["_store_id"]), int(row["_basket_item_id"]))] = True
    return out


def _match_progress_handler(
    widgets: dict[str, dict[str, object]],
) -> Callable[[MatchProgress], None]:
    """Return a callback that updates per-store progress bars during GPT matching."""

    def on_progress(progress: MatchProgress) -> None:
        w = widgets.get(progress.store_label) or {}
        bar = w.get("progress")
        cap = w.get("caption")
        if bar is not None:
            bar.progress(progress.progress_fraction)
        if cap is not None:
            cap.caption(format_match_progress(progress))

    return on_progress


def _store_display_name(store_label: str, chain_name: str = "") -> str:
    """Short store label without the grocery chain prefix (e.g. Muhaisnah 4)."""
    label = str(store_label or "").strip()
    if not label:
        return ""
    if "," in label:
        return label.rsplit(",", 1)[-1].strip()
    chain = str(chain_name or "").strip()
    if chain:
        for prefix in (
            f"{chain} Hypermarket",
            f"{chain} Supermarket",
            chain,
            chain.title(),
            chain.upper(),
        ):
            if label.lower().startswith(prefix.lower()):
                rest = label[len(prefix) :].strip(" ,")
                if rest:
                    return rest
    return label


def _extract_catalog_pack(catalog_title: str, catalog_pack_text: str = "") -> str:
    stored = str(catalog_pack_text or "").strip()
    if stored:
        return stored
    return format_title_pack_display(catalog_title)


def _series_catalog_pack(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=str)
    return df.apply(
        lambda r: _extract_catalog_pack(
            str(r.get("item_title") or ""),
            str(r.get("catalog_pack_text") or ""),
        ),
        axis=1,
    ).astype(str)


_PACK_SUFFIX_PATTERNS = (
    r"[\s,.-]+\d+s\b",
    r"[\s,.-]+\d+\s*(?:pieces?|pcs)\b",
    r"[\s,.-]+\d+\s*x\s*\d+(?:\.\d+)?\s*(?:kg|g|ml|l|litre|liter|liters|litres|gal|gallon|gallons|pc|pcs|pk|pack)\b",
    r"[\s,.-]+\d+(?:\.\d+)?\s*(?:kg|g|ml|l|litre|liter|liters|litres|gal|gallon|gallons|pc|pcs|pk|pack)\b",
    r"[\s,.-]+\d+(?:\.\d+)?\s*grm\b",
)


def _strip_pack_from_catalog_title(title: str, catalog_pack_text: str = "") -> str:
    """Remove pack / quantity suffixes from a Talabat product title for display."""
    t = str(title or "").strip()
    if not t:
        return ""

    pack = _extract_catalog_pack(t, catalog_pack_text)
    if pack:
        variants = {
            pack,
            pack.replace(" ", ""),
            pack.lower(),
            pack.replace(" ", "").lower(),
        }
        for variant in variants:
            if not variant:
                continue
            escaped = re.escape(variant).replace(r"\ ", r"\s*")
            t = re.sub(rf"[\s,.-]*{escaped}\s*$", "", t, flags=re.IGNORECASE).strip()

    changed = True
    while changed:
        changed = False
        for pat in _PACK_SUFFIX_PATTERNS:
            m = re.search(pat + r"\s*$", t, flags=re.IGNORECASE)
            if m:
                t = t[: m.start()].rstrip(" ,.-–")
                changed = True
                break

    return t.strip()


def _series_catalog_item_name(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=str)
    return df.apply(
        lambda r: _strip_pack_from_catalog_title(
            str(r.get("item_title") or ""),
            str(r.get("catalog_pack_text") or ""),
        ),
        axis=1,
    ).astype(str)


def _split_pack_text(pack_text: str) -> tuple[str, str]:
    from viva_tracker.pack_normalize import split_pack_fields

    raw = str(pack_text or "").strip()
    if not raw:
        return "", ""
    parts = raw.split()
    if len(parts) == 1:
        return split_pack_fields(parts[0], "")
    return parts[0], " ".join(parts[1:])


def _save_basket_row(conn, edited_row: pd.Series, brands_df: pd.DataFrame) -> int:
    """Persist one basket line and its per-chain mappings. Returns line_no."""
    line_no = int(edited_row["_line_no"])
    basket_item_id = upsert_basket_item(
        conn,
        line_no=line_no,
        category=str(edited_row.get("Category") or "").strip(),
        product_id=str(edited_row.get("Product ID") or "").strip(),
        viva_name=str(edited_row.get("Viva Product") or "").strip(),
        basket_label=str(edited_row.get("Basket Item") or "").strip(),
    )
    product_brands = {
        c[: -len(" Product")]
        for c in edited_row.index
        if isinstance(c, str) and c.endswith(" Product")
    }
    for _, brand_row in brands_df.iterrows():
        brand_name = str(brand_row["brand_name"])
        if brand_name not in product_brands:
            continue
        brand_id = int(brand_row["brand_id"])
        mapped_name = str(edited_row.get(f"{brand_name} Product") or "").strip()
        pack_text = str(edited_row.get(f"{brand_name} Pack") or "").strip()
        pack_qty, pack_unit = _split_pack_text(pack_text)
        upsert_basket_brand_map(
            conn,
            basket_item_id=basket_item_id,
            brand_id=brand_id,
            mapped_name=mapped_name,
            pack_qty=pack_qty,
            pack_unit=pack_unit,
            search_query=mapped_name.lower(),
        )
    return line_no


def main() -> None:
    st.set_page_config(page_title="Viva Basket Tracker", layout="wide")
    st.title("Viva Basket Tracker (Greenfield)")

    conn = connect_db()
    init_db(conn)
    backfill_basket_labels_from_csv(conn, BASKET_CSV_PATH)

    with st.sidebar:
        st.caption("Seed CSV")
        st.code(str(BASKET_CSV_PATH))
        missing_labels = conn.execute(
            """
            SELECT COUNT(*) AS n FROM basket_items
            WHERE is_active = 1
              AND TRIM(COALESCE(basket_label, '')) = ''
            """
        ).fetchone()["n"]
        if missing_labels:
            st.warning(
                f"{missing_labels} active basket line(s) missing **Basket Item** label. "
                "Re-run **Initialize from basket CSV** or fill **Basket Item** on Manage Basket."
            )
        if st.button("Initialize from basket CSV", width="stretch", key="btn_init_csv"):
            initialize_from_csv(conn, BASKET_CSV_PATH)
            st.success("DB initialized from basket CSV.")
        if st.button("Refresh all store UUIDs", width="stretch", key="btn_refresh_uuids"):
            res = refresh_store_uuids(conn)
            st.info(f"UUID refresh complete: {len(res)} stores.")
        if st.button("Run extraction now", width="stretch", key="btn_run_extraction"):
            result = extract_prices(conn, triggered_by="streamlit")
            msg = f"Extraction run completed. run_id={result.run_id}"
            if result.export_label:
                msg += f" | export={result.export_label}"
            st.success(msg)
            if result.export_dir:
                st.caption(f"Versioned exports: `{result.export_dir}`")
                if result.pruned_dirs:
                    st.caption(
                        f"Retained latest {MAX_RUN_EXPORT_RETENTION} iterations; "
                        f"removed {len(result.pruned_dirs)} older folder(s)."
                    )
        st.markdown("**Exports**")
        if st.button("Export wide CSV", width="stretch", key="btn_export_wide_csv"):
            out = export_latest_csv(conn, BASKET_CSV_PATH, EXPORTS_DIR / "latest_comparison.csv")
            st.success(f"Exported: {out}")
        if st.button("Export long CSV", width="stretch", key="btn_export_long_csv"):
            out = export_csv(conn, BASKET_CSV_PATH, EXPORTS_DIR / "latest_comparison_long.csv", fmt="long")
            st.success(f"Exported: {out}")
        if st.button("Export weekly summary", width="stretch", key="btn_export_summary_csv"):
            out = export_csv(conn, BASKET_CSV_PATH, EXPORTS_DIR / "weekly_summary.csv", fmt="summary")
            st.success(f"Exported: {out}")
        if st.button("Export extraction audit", width="stretch", key="btn_export_audit_csv"):
            out = export_csv(conn, BASKET_CSV_PATH, EXPORTS_DIR / "extraction_audit.csv", fmt="audit")
            st.success(f"Exported: {out}")
        if st.button("Export price history", width="stretch", key="btn_export_history_csv"):
            out = export_csv(conn, BASKET_CSV_PATH, EXPORTS_DIR / "price_history_long.csv", fmt="history")
            st.success(f"Exported: {out}")
        if st.button("Export price basket (Excel)", width="stretch", key="btn_export_prices_excel"):
            out = export_csv(conn, BASKET_CSV_PATH, EXPORTS_DIR / "price_basket.xlsx", fmt="excel")
            st.success(f"Exported: {out}")

    tab_setup, tab_basket, tab_urls, tab_analytics, tab_viva = st.tabs(
        ["Setup", "Basket", "URL Master", "Analytics", "Viva"]
    )

    with tab_setup:
        st.subheader("Manage Brands")
        bdf = _df(list_brands(conn))
        if bdf.empty:
            bdf_editor = pd.DataFrame([{"Select Row": False, "_brand_id": None, "Brand": ""}])
        else:
            bdf_editor = pd.DataFrame(
                {
                    "Select Row": False,
                    "_brand_id": bdf["brand_id"],
                    "Brand": bdf["brand_name"],
                }
            )
        bdf_edited = st.data_editor(
            bdf_editor,
            width="stretch",
            height=260,
            hide_index=True,
            column_config={
                "_brand_id": None,
                "Select Row": st.column_config.CheckboxColumn("Select Row"),
            },
            disabled=["_brand_id"],
            num_rows="dynamic",
            key="brands_selector_table",
        )

        selected_brand_rows = (
            bdf_edited[bdf_edited["Select Row"] == True]  # noqa: E712
            if not bdf_edited.empty and "Select Row" in bdf_edited.columns
            else pd.DataFrame()
        )
        selected_brand = selected_brand_rows.iloc[0] if len(selected_brand_rows) == 1 else None

        b1, b2 = st.columns(2)
        amend_brand = b1.button("Amend Selected Row", width="stretch", key="btn_amend_brand")
        delete_brand = b2.button("Delete Selected Row", width="stretch", key="btn_delete_brand")

        if amend_brand:
            if selected_brand is None:
                st.error("Select exactly one row in the table first.")
            else:
                brand_name = str(selected_brand.get("Brand") or "").strip()
                if not brand_name:
                    st.error("Brand name is required.")
                else:
                    raw_id = selected_brand.get("_brand_id")
                    if pd.isna(raw_id):
                        # New ad-hoc row defaults to non-Viva.
                        is_viva_val = False
                    else:
                        existing = bdf.loc[bdf["brand_id"] == int(raw_id)]
                        is_viva_val = bool(int(existing.iloc[0]["is_viva"])) if not existing.empty else False
                    upsert_brand(conn, brand_name, is_viva=is_viva_val)
                    st.success("Brand amended.")

        if delete_brand:
            if selected_brand_rows.empty:
                st.error("Select one or more rows in the table first.")
            else:
                deleted = 0
                skipped = 0
                for _, row in selected_brand_rows.iterrows():
                    raw_id = row.get("_brand_id")
                    if pd.isna(raw_id):
                        skipped += 1
                        continue
                    conn.execute("UPDATE brands SET is_active = 0 WHERE brand_id = ?", (int(raw_id),))
                    deleted += 1
                conn.commit()
                if deleted:
                    st.success(f"Deleted (deactivated) {deleted} brand row(s).")
                if skipped:
                    st.warning(f"Skipped {skipped} unsaved row(s).")

        st.divider()
        st.subheader("Manage Stores")

        def _brand_lookup() -> dict[str, int]:
            bdf = _df(list_brands(conn))
            if bdf.empty:
                return {}
            return {str(r["brand_name"]): int(r["brand_id"]) for _, r in bdf.iterrows()}

        st.caption(
            "**Add / Amend** saves storefront metadata from Talabat (store name, brand, UUID, URL). "
            "When adding a **new store**, select it and run **Build catalog** then **Run GPT URL matching** (Setup section below)."
        )
        new_urls = st.text_area(
            "New storefront URLs",
            height=100,
            placeholder=(
                "One URL per line, or separate with commas:\n"
                "https://www.talabat.com/.../grocery/..."
            ),
            key="stores_new_urls_textarea",
        )
        if st.button("Add stores from URLs", width="stretch", key="btn_add_stores_urls"):
            lines = _split_pasted_store_urls(new_urls or "")
            seen: set[str] = set()
            uniq_lines: list[str] = []
            for ln in lines:
                if ln in seen:
                    continue
                seen.add(ln)
                uniq_lines.append(ln)
            if not uniq_lines:
                st.error("Enter at least one URL.")
            else:
                ok = 0
                errors: list[str] = []
                blu = _brand_lookup()
                for url in uniq_lines:
                    try:
                        meta = fetch_store_metadata(url)
                        brand_id = _resolve_brand_id(conn, meta["brand_name"], blu)
                        upsert_store(
                            conn,
                            brand_id=brand_id,
                            store_label=meta["store_label"],
                            talabat_url=meta["talabat_url"],
                            store_uuid=meta["store_uuid"],
                        )
                        blu = _brand_lookup()
                        ok += 1
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"{url}: {e}")
                if ok:
                    st.success(
                        f"Saved {ok} store(s). Build catalog and run GPT URL matching when ready."
                    )
                if errors:
                    st.error("Some URLs failed:\n\n" + "\n".join(errors))

        sdf = _df(list_stores(conn))
        brand_lookup = _brand_lookup()
        if sdf.empty:
            st.info("No saved stores yet. Add URLs above.")
            sdf_edited = pd.DataFrame()
        else:
            sdf_editor = pd.DataFrame(
                {
                    "Select Row": False,
                    "_store_id": sdf["store_id"],
                    "Talabat URL": sdf["talabat_url"],
                    "Store": sdf["store_label"],
                    "Brand": sdf["brand_name"],
                    "Store UUID": sdf["store_uuid"].fillna("").astype(str),
                    "Added Date": (
                        sdf["created_at"].map(_fmt_display_date).astype(str)
                        if "created_at" in sdf.columns
                        else [""] * len(sdf)
                    ),
                    "Active": sdf["is_active"].map({1: "Yes", 0: "No"}).fillna("Yes"),
                }
            )
            sdf_edited = st.data_editor(
                sdf_editor,
                width="stretch",
                height=320,
                hide_index=True,
                column_config={
                    "_store_id": None,
                    "Select Row": st.column_config.CheckboxColumn("Select Row"),
                    "Added Date": st.column_config.TextColumn("Added Date", width="small"),
                    "Active": None,
                },
                disabled=["_store_id", "Store", "Brand", "Store UUID", "Added Date", "Active"],
                key="stores_selector_table_v2",
            )

        selected_store_rows = (
            sdf_edited[sdf_edited["Select Row"] == True]  # noqa: E712
            if not sdf_edited.empty and "Select Row" in sdf_edited.columns
            else pd.DataFrame()
        )
        selected_store = selected_store_rows.iloc[0] if len(selected_store_rows) == 1 else None

        s1, s2 = st.columns(2)
        amend_store = s1.button("Amend Selected Row", width="stretch", key="btn_amend_store")
        delete_store = s2.button("Delete Selected Row", width="stretch", key="btn_delete_store")

        st.markdown("##### Catalog and URL matching (rare — new store or basket item)")
        st.caption(
            "1. **Build catalog** crawls Talabat and saves JSON under `catalogs/`. "
            "2. **Run GPT URL matching** uses basket item name + pack from the catalog, verifies picks with GPT, then fills URL master. "
            "Requires `OPENAI_API_KEY` in `.env`."
        )
        gpt_match_missing_only = st.checkbox(
            "Only match items without URLs already in URL master",
            value=True,
            key="gpt_match_skip_existing",
            help="Skip basket lines that already have a saved Item URL and Talabat item ID for this store.",
        )
        c1, c2 = st.columns(2)
        build_catalog_btn = c1.button(
            "Build catalog for selected stores",
            width="stretch",
            key="btn_build_catalog_selected",
            help="Select one or more stores. Slow (several minutes per store).",
        )
        gpt_match_btn = c2.button(
            "Run GPT URL matching for selected stores",
            width="stretch",
            key="btn_gpt_match_selected",
            help="Requires catalog JSON from Build catalog step.",
        )

        if build_catalog_btn:
            if selected_store_rows.empty:
                st.error("Select one or more store rows first.")
            else:
                built: list[str] = []
                failures: list[str] = []
                build_rows = [
                    row for _, row in selected_store_rows.iterrows()
                    if str(row.get("Talabat URL") or "").strip()
                ]
                missing_url = [
                    str(row.get("Store") or "?")
                    for _, row in selected_store_rows.iterrows()
                    if not str(row.get("Talabat URL") or "").strip()
                ]
                failures.extend(f"{label}: no Talabat URL" for label in missing_url)

                with st.status("Building catalogs…", expanded=True) as build_status:
                    store_widgets: dict[str, dict[str, object]] = {}
                    for row in build_rows:
                        store_label = str(row.get("Store") or "").strip() or "?"
                        st.markdown(f"**{store_label}**")
                        store_widgets[store_label] = {
                            "progress": st.progress(0.0),
                            "caption": st.empty(),
                        }
                        store_widgets[store_label]["caption"].caption("Waiting…")

                    for row in build_rows:
                        raw_id = row.get("_store_id")
                        talabat_url = str(row.get("Talabat URL") or "").strip()
                        store_label = str(row.get("Store") or "").strip()
                        brand_name = str(row.get("Brand") or "").strip()
                        widgets = store_widgets.get(store_label) or {}

                        def _on_catalog_progress(progress, *, _widgets=widgets):
                            bar = _widgets.get("progress")
                            cap = _widgets.get("caption")
                            if bar is not None:
                                bar.progress(progress.progress_fraction)
                            if cap is not None:
                                cap.caption(format_catalog_build_progress(progress))

                        try:
                            result = build_and_save_catalog_for_store(
                                store_id=int(raw_id),
                                store_label=store_label,
                                brand_name=brand_name,
                                talabat_url=talabat_url,
                                page_delay_s=0.85,
                                log=False,
                                progress_callback=_on_catalog_progress,
                            )
                            bar = widgets.get("progress")
                            if bar is not None:
                                bar.progress(1.0)
                            cap = widgets.get("caption")
                            if cap is not None:
                                cap.caption(
                                    f"Pages completed | Done — {result['product_count']} products"
                                )
                            built.append(
                                f"{store_label}: {result['product_count']} products → `{result['path']}`"
                            )
                        except Exception as e:  # noqa: BLE001
                            cap = widgets.get("caption")
                            if cap is not None:
                                cap.caption(f"Failed: {e}")
                            failures.append(f"{store_label or '?'}: {e}")

                    if built and not failures:
                        build_status.update(label="Catalog build finished", state="complete")
                    elif built:
                        build_status.update(label="Catalog build finished with errors", state="error")
                    elif failures:
                        build_status.update(label="Catalog build failed", state="error")
                    else:
                        build_status.update(label="Nothing to build", state="complete")

                if built:
                    st.success("Catalog build finished:\n\n" + "\n\n".join(built))
                if failures:
                    st.error("Some catalogs failed:\n\n" + "\n\n".join(failures))

        elif gpt_match_btn:
            if selected_store_rows.empty:
                st.error("Select one or more store rows first.")
            elif not os.environ.get("OPENAI_API_KEY"):
                st.error("Set OPENAI_API_KEY in `.env` before running GPT matching.")
            else:
                matched: list[str] = []
                failures: list[str] = []
                match_rows = [
                    row
                    for _, row in selected_store_rows.iterrows()
                    if catalog_exists(str(row.get("Store") or ""))
                ]
                missing_catalog = [
                    str(row.get("Store") or "?")
                    for _, row in selected_store_rows.iterrows()
                    if not catalog_exists(str(row.get("Store") or ""))
                ]
                failures.extend(
                    f"{label}: no catalog JSON — run **Build catalog** first"
                    for label in missing_catalog
                )

                with st.status("GPT matching…", expanded=True) as match_status:
                    store_widgets: dict[str, dict[str, object]] = {}
                    for row in match_rows:
                        store_label = str(row.get("Store") or "").strip() or "?"
                        st.markdown(f"**{store_label}**")
                        store_widgets[store_label] = {
                            "progress": st.progress(0.0),
                            "caption": st.empty(),
                        }
                        store_widgets[store_label]["caption"].caption("Waiting…")

                    for row in match_rows:
                        raw_id = int(row.get("_store_id"))
                        store_label = str(row.get("Store") or "").strip()
                        brand_name = str(row.get("Brand") or "").strip()
                        widgets = store_widgets.get(store_label) or {}
                        on_progress = _match_progress_handler(store_widgets)

                        try:
                            stats = match_store(
                                conn,
                                store_id=raw_id,
                                store_label=store_label,
                                brand_name=brand_name,
                                skip_existing=gpt_match_missing_only,
                                progress_callback=on_progress,
                            )
                            bar = widgets.get("progress")
                            if bar is not None:
                                bar.progress(1.0)
                            cap = widgets.get("caption")
                            if cap is not None:
                                cap.caption(
                                    f"Done — ok={stats['ok']}, "
                                    f"pack_mismatch={stats['pack_mismatch']}, "
                                    f"missing={stats['missing']}, skipped={stats['skipped']}"
                                )
                            matched.append(
                                f"{store_label}: ok={stats['ok']}, "
                                f"pack_mismatch={stats['pack_mismatch']}, "
                                f"missing={stats['missing']}, skipped={stats['skipped']}"
                            )
                        except Exception as e:  # noqa: BLE001
                            cap = widgets.get("caption")
                            if cap is not None:
                                cap.caption(f"Failed: {e}")
                            failures.append(f"{store_label}: {e}")

                    if matched and not failures:
                        match_status.update(label="GPT matching finished", state="complete")
                    elif matched:
                        match_status.update(label="GPT matching finished with errors", state="error")
                    elif failures:
                        match_status.update(label="GPT matching failed", state="error")
                    else:
                        match_status.update(label="Nothing to match", state="complete")

                if matched:
                    audit_note = ""
                    try:
                        audit = refresh_missing_match_audit(conn)
                        audit_note = (
                            f"\n\nMissing-match audit: **{audit['missing_count']}** row(s) → "
                            f"`{audit['xlsx_path']}`"
                        )
                    except Exception as audit_err:  # noqa: BLE001
                        audit_note = f"\n\nMissing-match audit export failed: {audit_err}"
                    st.success(
                        "GPT matching finished. Review **URL Master** tab.\n\n"
                        + "\n\n".join(matched)
                        + audit_note
                    )
                if failures:
                    st.error("Some stores failed:\n\n" + "\n\n".join(failures))

        if len(selected_store_rows) == 1:
            selected_store = selected_store_rows.iloc[0]
            meta = catalog_meta(str(selected_store.get("Store") or ""))
            if meta:
                st.info(
                    f"Catalog on disk: {meta.get('product_count', '?')} products, "
                    f"built {meta.get('built_at', '?')}"
                )
            else:
                st.warning("No catalog JSON yet for the selected store.")

        if amend_store:
            if selected_store is None:
                st.error("Select exactly one row in the table first.")
            else:
                raw_id = selected_store.get("_store_id")
                if raw_id is None or pd.isna(raw_id):
                    st.error(
                        "Amend only applies to stores already in the database. "
                        "Use **Add stores from URLs** for new links."
                    )
                else:
                    talabat_url = str(selected_store.get("Talabat URL") or "").strip()
                    if not talabat_url:
                        st.error("Talabat URL is required.")
                    else:
                        try:
                            meta = fetch_store_metadata(talabat_url)
                        except Exception as e:  # noqa: BLE001
                            st.error(f"Could not read storefront: {e}")
                        else:
                            try:
                                brand_id = _resolve_brand_id(
                                    conn, meta["brand_name"], brand_lookup
                                )
                            except ValueError as e:
                                st.error(str(e))
                            else:
                                try:
                                    update_store(
                                        conn,
                                        int(raw_id),
                                        brand_id=brand_id,
                                        store_label=meta["store_label"],
                                        talabat_url=meta["talabat_url"],
                                        store_uuid=meta["store_uuid"],
                                    )
                                except Exception as e:  # noqa: BLE001
                                    st.error(f"Could not save store: {e}")
                                else:
                                    st.success(
                                        f"Updated store “{meta['store_label']}” "
                                        f"({meta['brand_name']}, UUID {meta['store_uuid']}). "
                                        "Click **Refresh item URLs** when you want to recrawl item links for this store."
                                    )

        if delete_store:
            if selected_store_rows.empty:
                st.error("Select one or more rows in the table first.")
            else:
                deleted = 0
                skipped = 0
                for _, row in selected_store_rows.iterrows():
                    raw_id = row.get("_store_id")
                    if pd.isna(raw_id):
                        skipped += 1
                        continue
                    deactivate_store(conn, int(raw_id))
                    deleted += 1
                if deleted:
                    st.success(f"Deleted (deactivated) {deleted} store row(s).")
                if skipped:
                    st.warning(f"Skipped {skipped} unsaved row(s).")

    with tab_basket:
        st.subheader("Manage Basket")
        st.caption(
            "Edit chain product names and packs, tick **Select Row** on each line to save, then click **Amend Selected Rows**. "
            "**Item name (chain)** on URL Master comes from here — changes are not saved until you amend. "
            "Chain product names are reference data only — matching uses **Basket Item** + parsed pack."
        )
        bidf = _df(list_basket_items(conn))
        bmdf = _df(list_basket_brand_maps(conn))
        basket_view = _basket_display_table(bidf, bmdf)
        if not basket_view.empty:
            sel_df = basket_view.copy()
            sel_df.insert(0, "Select", False)
            with st.form("basket_edit_form", clear_on_submit=False):
                edited_df = st.data_editor(
                    sel_df,
                    width="stretch",
                    height=620,
                    hide_index=True,
                    column_config={
                        "_line_no": None,
                        "Select": st.column_config.CheckboxColumn(
                            "Select Row",
                            help="Select one or more rows to save or delete",
                        ),
                        "Added Date": st.column_config.TextColumn("Added Date", width="small"),
                    },
                    disabled=["_line_no", "Added Date"],
                    key="basket_selector_table_v2",
                )
                b1, b2 = st.columns(2)
                with b1:
                    amend_clicked = st.form_submit_button(
                        "Amend Selected Rows",
                        use_container_width=True,
                    )
                with b2:
                    delete_clicked = st.form_submit_button(
                        "Delete Selected Rows",
                        use_container_width=True,
                    )
        else:
            edited_df = pd.DataFrame()
            amend_clicked = False
            delete_clicked = False
            st.dataframe(basket_view, width="stretch", height=620, hide_index=True)

        brands_df = _df(list_brands(conn))
        brand_lookup = {
            f"{r['brand_name']} ({r['brand_id']})": int(r["brand_id"])
            for _, r in brands_df.iterrows()
        } if not brands_df.empty else {}
        if bidf.empty:
            st.info("No basket items found. Initialize from CSV first.")
        elif not brand_lookup:
            st.info("No brands found. Add brands first.")
        else:
            selected_rows = (
                edited_df[edited_df["Select"] == True]  # noqa: E712
                if not edited_df.empty and "Select" in edited_df.columns
                else pd.DataFrame()
            )
            if amend_clicked:
                if selected_rows.empty:
                    st.error("Select one or more rows in the basket table first.")
                else:
                    saved_lines: list[int] = []
                    for _, edited_row in selected_rows.iterrows():
                        saved_lines.append(_save_basket_row(conn, edited_row, brands_df))
                    st.success(
                        f"Saved {len(saved_lines)} row(s): lines {', '.join(str(n) for n in saved_lines)}. "
                        "**Item name (chain)** on URL Master updates immediately. "
                        "Re-run **Get URL (GPT)** for stores whose names changed."
                    )
                    st.rerun()

            elif delete_clicked:
                if selected_rows.empty:
                    st.error("Select one or more rows in the table first.")
                else:
                    deleted = 0
                    for _, row in selected_rows.iterrows():
                        line_no = int(row["_line_no"])
                        selected_row = bidf.loc[bidf["line_no"] == line_no].iloc[0]
                        deactivate_basket_item(conn, int(selected_row["basket_item_id"]))
                        deleted += 1
                    st.success(f"Deleted (deactivated) {deleted} basket row(s).")
                    st.rerun()

    with tab_urls:
        st.subheader("URL Master Repository")
        st.caption(
            "Select rows for **Get URL (GPT)**, or edit **Item URL** manually and **Save**. "
            "Only rows whose **Item URL** cell changed are written on save — not the whole grid. "
            "**Basket Item Name** is read live from **Manage Basket**. "
            "**Basket Item** and parsed pack drive catalog matching (GPT verifies auto-picks). "
            "**Generic** / **Brand token** are legacy chain reference columns. "
            "Wrong **Catalog Item** vs basket → check **Pack match** / **Status**. "
            "Compare **Basket Item Quantity** with **Catalog pack** for size mismatches. "
            "Build catalog first on **Setup** if the store has no catalog JSON."
        )

        flash = st.session_state.pop("url_match_flash", None)
        if flash:
            kind, message = flash
            if kind == "success":
                st.success(message)
            elif kind == "warning":
                st.warning(message)
            else:
                st.error(message)

        stores_master = _df(list_stores(conn))
        if stores_master.empty:
            st.warning("No stores configured. Add stores on the **Setup** tab first.")
        else:
            brands_master = _df(list_brands(conn))
            if "brand_name" not in stores_master.columns and not brands_master.empty:
                stores_master = stores_master.merge(
                    brands_master[["brand_id", "brand_name"]],
                    on="brand_id",
                    how="left",
                )
            stores_master["store_display"] = stores_master.apply(
                lambda r: _store_display_name(
                    str(r.get("store_label") or ""),
                    str(r.get("brand_name") or ""),
                ),
                axis=1,
            )
            grid_df = _df(list_url_master_grid(conn))
            if grid_df.empty:
                st.info("No basket mappings for configured stores yet. Initialize from CSV on the Basket tab.")
            else:
                chain_opts = ["(All chains)"] + sorted(
                    brands_master["brand_name"].dropna().astype(str).str.strip().unique().tolist()
                )
                fc1, fc2 = st.columns(2)
                with fc1:
                    sel_chain = st.selectbox(
                        "Grocery chain",
                        chain_opts,
                        key="url_master_filter_chain",
                    )
                with fc2:
                    if sel_chain == "(All chains)":
                        store_rows = stores_master
                    else:
                        store_rows = stores_master[
                            stores_master["brand_name"].astype(str).str.strip() == sel_chain.strip()
                        ]
                    store_opts = ["(All stores)"] + sorted(
                        store_rows["store_display"].dropna().astype(str).str.strip().unique().tolist()
                    )
                    sel_store = st.selectbox(
                        "Store name",
                        store_opts,
                        key="url_master_filter_store",
                    )

                fgrid = grid_df
                if sel_chain != "(All chains)" and "grocery_chain_name" in fgrid.columns:
                    fgrid = fgrid[
                        fgrid["grocery_chain_name"].astype(str).str.strip() == sel_chain.strip()
                    ]
                if sel_store != "(All stores)":
                    match_labels = stores_master[
                        stores_master["store_display"].astype(str).str.strip() == sel_store.strip()
                    ]["store_label"].astype(str).str.strip().tolist()
                    fgrid = fgrid[fgrid["store_label"].astype(str).str.strip().isin(match_labels)]

                if fgrid.empty:
                    st.warning("No rows match the selected filters.")
                else:
                    seed_key = f"url_match_seed__{sel_chain}__{sel_store}"
                    if seed_key not in st.session_state:
                        st.session_state[seed_key] = {}

                    seed = st.session_state.get(seed_key, {})
                    catalog_items = _series_catalog_item_name(fgrid)
                    catalog_packs = _series_catalog_pack(fgrid)
                    basket_packs = _series_pack_quantity(fgrid)
                    unified_df = pd.DataFrame(
                        {
                            "Select": [
                                bool(
                                    seed.get(
                                        _grid_selection_key(int(r["store_id"]), int(r["basket_item_id"])),
                                        False,
                                    )
                                )
                                for _, r in fgrid.iterrows()
                            ],
                            "_store_id": fgrid["store_id"].astype(int),
                            "_basket_item_id": fgrid["basket_item_id"].astype(int),
                            "_slug": fgrid["slug"].fillna("").astype(str),
                            "_item_title": fgrid["item_title"].fillna("").astype(str),
                            "_item_id": fgrid["item_id"].fillna("").astype(str),
                            "Line": fgrid["line_no"].astype(int),
                            "Basket Item": fgrid["basket_label"].fillna("").astype(str),
                            "Grocery chain": fgrid["grocery_chain_name"].fillna("").astype(str),
                            "Store name": fgrid.apply(
                                lambda r: _store_display_name(
                                    str(r.get("store_label") or ""),
                                    str(r.get("grocery_chain_name") or ""),
                                ),
                                axis=1,
                            ).astype(str),
                            "Basket Item Name": fgrid["chain_item_name"].fillna("").astype(str),
                            "Generic": fgrid["generic_description"].fillna("").astype(str),
                            "Brand token": fgrid["brand_token"].fillna("").astype(str),
                            "Basket Item Quantity": basket_packs.astype(str),
                            "Catalog Item": catalog_items,
                            "Catalog pack": catalog_packs.astype(str),
                            "Pack match": fgrid["pack_match"].fillna("").astype(str),
                            "Match confidence": fgrid["match_confidence"].fillna("").astype(str),
                            "Match reason": fgrid["match_reason"].fillna("").astype(str),
                            "Status": fgrid["status"].fillna("").astype(str),
                            "Item URL": fgrid["source_url"].fillna("").astype(str),
                            "Added Date": fgrid["created_at"].map(_fmt_display_date).astype(str),
                            "Last used": fgrid["last_used_at"].map(_fmt_display_ts).astype(str),
                        }
                    )

                    editor_key = f"url_master_unified_v10__{sel_chain}__{sel_store}"

                    mq1, mq2, mq3 = st.columns(3)
                    if mq1.button("Select all missing", width="stretch", key="btn_url_select_missing"):
                        seed = {}
                        for _, r in fgrid.iterrows():
                            if not _grid_row_has_url(r):
                                seed[_grid_selection_key(int(r["store_id"]), int(r["basket_item_id"]))] = True
                        st.session_state[seed_key] = seed
                        st.session_state.pop(editor_key, None)
                        st.rerun()
                    if mq2.button("Select all pack_mismatch", width="stretch", key="btn_url_select_mismatch"):
                        seed = {}
                        for _, r in fgrid.iterrows():
                            st_val = str(r.get("status") or "").strip().lower()
                            pm_val = str(r.get("pack_match") or "").strip().lower()
                            if st_val == "pack_mismatch" or pm_val in {"different", "mismatch"}:
                                seed[_grid_selection_key(int(r["store_id"]), int(r["basket_item_id"]))] = True
                        st.session_state[seed_key] = seed
                        st.session_state.pop(editor_key, None)
                        st.rerun()
                    if mq3.button("Clear selection", width="stretch", key="btn_url_clear_selection"):
                        st.session_state[seed_key] = {}
                        st.session_state.pop(editor_key, None)
                        st.rerun()

                    url_match_skip_existing = st.checkbox(
                        "Skip rows that already have a URL (when selected for GPT)",
                        value=False,
                        key="url_match_skip_existing",
                    )

                    edited = st.data_editor(
                        unified_df,
                        width="stretch",
                        height=560,
                        hide_index=True,
                        num_rows="fixed",
                        column_config={
                            "_store_id": None,
                            "_basket_item_id": None,
                            "_slug": None,
                            "_item_title": None,
                            "_item_id": None,
                            "Select": st.column_config.CheckboxColumn("Select"),
                            "Line": st.column_config.NumberColumn("Line", width="small"),
                            "Grocery chain": st.column_config.TextColumn("Grocery chain", width="small"),
                            "Store name": st.column_config.TextColumn("Store name", width="small"),
                            "Basket Item Name": st.column_config.TextColumn(
                                "Basket Item Name", width="medium"
                            ),
                            "Generic": st.column_config.TextColumn("Generic", width="small"),
                            "Brand token": st.column_config.TextColumn("Brand token", width="small"),
                            "Basket Item Quantity": st.column_config.TextColumn(
                                "Basket Item Quantity", width="small"
                            ),
                            "Catalog Item": st.column_config.TextColumn("Catalog Item", width="medium"),
                            "Catalog pack": st.column_config.TextColumn("Catalog pack", width="small"),
                            "Pack match": st.column_config.TextColumn("Pack match", width="small"),
                            "Match confidence": st.column_config.TextColumn(
                                "Match confidence",
                                width="small",
                                help="Generic + brand similarity (pack not included)",
                            ),
                            "Match reason": st.column_config.TextColumn("Match reason", width="medium"),
                            "Status": st.column_config.TextColumn("Status", width="small"),
                            "Item URL": st.column_config.TextColumn(
                                "Item URL",
                                width=_URL_MASTER_URL_COL_WIDTH,
                            ),
                            "Added Date": st.column_config.TextColumn("Added Date", width="small"),
                            "Last used": st.column_config.TextColumn("Last used", width="small"),
                        },
                        disabled=[
                            "Line",
                            "Grocery chain",
                            "Store name",
                            "Basket Item Name",
                            "Generic",
                            "Brand token",
                            "Basket Item Quantity",
                            "Catalog Item",
                            "Catalog pack",
                            "Pack match",
                            "Match confidence",
                            "Match reason",
                            "Status",
                            "Added Date",
                            "Last used",
                        ],
                        key=editor_key,
                    )

                    seed = _sync_editor_selection_to_seed(edited)
                    st.session_state[seed_key] = seed
                    selected_count = len(seed)
                    if selected_count:
                        st.caption(f"{selected_count} row(s) selected for GPT matching.")

                    act1, act2 = st.columns(2)
                    get_gpt = act1.button(
                        "Get URL (GPT) for selected rows",
                        width="stretch",
                        key="btn_url_get_gpt",
                    )
                    save_urls = act2.button(
                        "Save URL changes",
                        width="stretch",
                        key="btn_save_url_master",
                    )

                    if get_gpt:
                        selected_pairs = _selected_pairs_from_seed(fgrid, seed)
                        if not selected_pairs:
                            st.error("Select at least one row (use checkboxes or **Select all missing**).")
                        elif not os.environ.get("OPENAI_API_KEY"):
                            st.error("Set OPENAI_API_KEY in `.env` before running GPT matching.")
                        else:
                            store_labels = {
                                int(r["store_id"]): str(r["store_label"])
                                for _, r in stores_master.iterrows()
                            }
                            missing_catalog = sorted(
                                {
                                    store_labels[sid]
                                    for sid, _ in selected_pairs
                                    if not catalog_exists(store_labels.get(sid, ""))
                                }
                            )
                            if missing_catalog:
                                st.error(
                                    "Build catalog first on **Setup** for: "
                                    + ", ".join(missing_catalog)
                                )
                            else:
                                try:
                                    store_ids_ordered = sorted({sid for sid, _ in selected_pairs})
                                    match_store_labels = [
                                        store_labels[sid] for sid in store_ids_ordered
                                    ]
                                    with st.status("GPT matching…", expanded=True) as match_status:
                                        store_widgets: dict[str, dict[str, object]] = {}
                                        for label in match_store_labels:
                                            st.markdown(f"**{label}**")
                                            store_widgets[label] = {
                                                "progress": st.progress(0.0),
                                                "caption": st.empty(),
                                            }
                                            store_widgets[label]["caption"].caption("Waiting…")

                                        stats = match_selected(
                                            conn,
                                            selected_pairs,
                                            skip_existing=url_match_skip_existing,
                                            progress_callback=_match_progress_handler(store_widgets),
                                        )
                                        for label in match_store_labels:
                                            widgets = store_widgets.get(label) or {}
                                            bar = widgets.get("progress")
                                            if bar is not None:
                                                bar.progress(1.0)
                                        match_status.update(
                                            label="GPT matching finished",
                                            state="complete" if not stats.get("error_messages") else "error",
                                        )

                                    err_msgs = stats.get("error_messages") or []
                                    summary = (
                                        f"GPT matching finished: ok={stats['ok']}, "
                                        f"pack_mismatch={stats['pack_mismatch']}, "
                                        f"missing={stats['missing']}, "
                                        f"skipped={stats['skipped']}, errors={stats['errors']}."
                                    )
                                    st.session_state[seed_key] = {}
                                    st.session_state.pop(editor_key, None)
                                    if err_msgs:
                                        st.session_state["url_match_flash"] = (
                                            "error",
                                            summary + "\n\n" + "\n".join(str(m) for m in err_msgs),
                                        )
                                    elif int(stats.get("ok", 0)) == 0 and int(stats.get("pack_mismatch", 0)) == 0:
                                        st.session_state["url_match_flash"] = (
                                            "warning",
                                            summary + " No URL was matched — check catalog coverage or basket name/pack.",
                                        )
                                    else:
                                        st.session_state["url_match_flash"] = ("success", summary)
                                    st.rerun()
                                except Exception as e:  # noqa: BLE001
                                    st.error(f"GPT matching failed: {e}")

                    if save_urls:
                        failures: list[str] = []
                        saved = 0
                        skipped = 0
                        store_labels = {
                            int(r["store_id"]): str(r["store_label"])
                            for _, r in stores_master.iterrows()
                        }
                        grid_pack_lookup = {
                            (int(r["store_id"]), int(r["basket_item_id"])): (
                                str(r.get("pack_qty") or "").strip(),
                                str(r.get("pack_unit") or "").strip(),
                            )
                            for _, r in fgrid.iterrows()
                        }
                        original_urls = {
                            (int(r["store_id"]), int(r["basket_item_id"])): str(
                                r.get("source_url") or ""
                            ).strip()
                            for _, r in fgrid.iterrows()
                        }
                        for _, row in edited.iterrows():
                            sid = int(row["_store_id"])
                            bid = int(row["_basket_item_id"])
                            edited_url = str(row.get("Item URL") or "").strip()
                            if edited_url == original_urls.get((sid, bid), ""):
                                skipped += 1
                                continue

                            surl = edited_url or None
                            iid = str(row.get("_item_id") or "").strip() or None
                            slug = str(row.get("_slug") or "").strip() or None
                            title = str(row.get("_item_title") or "").strip() or None
                            store_hint = store_labels.get(sid, str(sid))

                            if surl:
                                if is_grocery_product_url(surl):
                                    try:
                                        meta = resolve_url_master_from_product_url(surl)
                                        surl = meta["source_url"]
                                        iid = meta["item_id"]
                                        slug = meta["slug"] or None
                                        title = meta["item_title"] or None
                                    except Exception as e:  # noqa: BLE001
                                        failures.append(f"{store_hint}: {e}")
                                        continue
                                elif not iid:
                                    failures.append(
                                        f"{store_hint}: Non-product URL needs a **Talabat item ID**, or paste a `…/product/…` link."
                                    )
                                    continue
                                status = "ok"
                                err = None
                                pack_qty, pack_unit = grid_pack_lookup.get((sid, bid), ("", ""))
                                catalog_title = title or ""
                                pack_match = pack_matches_target(catalog_title, pack_qty, pack_unit)
                                catalog_pack_text = (
                                    format_title_pack_display(catalog_title)
                                    if catalog_title
                                    else None
                                )
                                if pack_match not in {"exact", "close"} and pack_match != "unknown":
                                    status = "pack_mismatch"
                            else:
                                iid = None
                                slug = None
                                title = None
                                surl = None
                                status = "missing"
                                err = None
                                pack_match = "unknown"
                                catalog_pack_text = None
                            upsert_item_url_master(
                                conn,
                                store_id=sid,
                                basket_item_id=bid,
                                item_id=iid,
                                source_url=surl,
                                slug=slug,
                                item_title=title,
                                status=status,
                                error=err,
                                match_method="manual",
                                match_confidence=None,
                                match_reason="Manual URL edit",
                                pack_match=pack_match,
                                catalog_pack_text=catalog_pack_text,
                            )
                            saved += 1
                        if failures:
                            st.error("Some rows were not saved:\n\n" + "\n\n".join(failures))
                        if saved:
                            st.session_state.pop(editor_key, None)
                            st.success(f"Saved {saved} changed row(s).")
                            st.rerun()
                        elif not failures:
                            st.info(
                                f"No URL changes to save ({skipped} row(s) unchanged in this view)."
                            )

                    has_url = fgrid.apply(_grid_row_has_url, axis=1)
                    okish = fgrid["status"].astype(str).str.lower().isin(["ok", "pack_mismatch"])
                    if len(fgrid) > 0:
                        coverage = ((has_url | okish).sum() / len(fgrid)) * 100.0
                        st.metric("URL coverage (filtered view)", f"{coverage:.2f}%")

    with tab_analytics:
        st.subheader("Analytics")
        adf = analytics_timeseries(conn)
        if adf.empty:
            st.info("No extraction history yet. Run extraction first.")
        else:
            adf["run_ts"] = pd.to_datetime(adf["run_ts"])
            adf["run_date"] = pd.to_datetime(adf["run_date"])
            latest_date = adf["run_date"].max()
            st.caption(f"Latest snapshot date: {latest_date.date().isoformat()}")

            include_mismatch = st.checkbox(
                "Include pack_mismatch lines in competitiveness metrics",
                value=False,
                key="analytics_include_pack_mismatch",
                help="When off, only exact pack matches count toward KPIs, gaps, and heatmaps.",
            )

            tab_snap, tab_compare, tab_trends, tab_quality = st.tabs(
                ["Snapshot", "Compare", "Trends", "Data quality"]
            )

            with tab_snap:
                kpis = snapshot_kpis(conn, include_pack_mismatch=include_mismatch)
                store_totals = kpis.get("store_totals")
                if not isinstance(store_totals, pd.DataFrame) or store_totals.empty:
                    st.info("No competitive price data for the latest run.")
                else:
                    k1, k2, k3, k4 = st.columns(4)
                    k1.metric(
                        "Viva normalized basket",
                        f"{kpis['viva_total']:.2f}" if kpis.get("viva_total") is not None else "—",
                        help=f"Worst-of-branches synthetic total ({VIVA_REFERENCE_LABEL})",
                    )
                    rank = kpis.get("viva_rank")
                    k2.metric(
                        "Viva rank",
                        f"#{rank}" if rank else "—",
                        help=f"Rank of {VIVA_REFERENCE_LABEL} vs competitor stores (1 = cheapest)",
                    )
                    gap = kpis.get("gap_vs_cheapest_pct")
                    k3.metric(
                        "Gap vs cheapest competitor",
                        f"{gap:+.2f}%" if gap is not None else "—",
                        help=str(kpis.get("cheapest_competitor_label") or ""),
                    )
                    cov = kpis.get("coverage_pct")
                    k4.metric(
                        "Price coverage",
                        f"{cov:.2f}%" if cov is not None else "—",
                        help="Share of basket lines with valid normalized prices",
                    )

                    brand_map = {
                        str(r["store_label"]): str(r["brand_name"])
                        for _, r in store_totals.iterrows()
                    }
                    bar_df = store_totals.sort_values("normalized_basket_total", ascending=True)
                    fig_bar = px.bar(
                        bar_df,
                        x="normalized_basket_total",
                        y="store_label",
                        orientation="h",
                        title="Normalized basket total (latest run)",
                        color="store_label",
                        color_discrete_map=_store_color_map(
                            bar_df["store_label"].astype(str).tolist(), brand_map
                        ),
                    )
                    fig_bar.update_layout(showlegend=False, yaxis={"categoryorder": "total ascending"})
                    fig_bar.update_yaxes(tickformat=",.2f")
                    fig_bar.update_traces(hovertemplate="%{y:.2f}<extra></extra>")
                    st.plotly_chart(fig_bar, width="stretch")

                    if st.button("Download weekly summary CSV", key="btn_snap_export_summary"):
                        out = export_csv(
                            conn,
                            BASKET_CSV_PATH,
                            EXPORTS_DIR / "weekly_summary.csv",
                            fmt="summary",
                            include_pack_mismatch=include_mismatch,
                        )
                        st.success(f"Exported: {out}")

                    g1, g2 = st.columns(2)
                    gaps, wins = top_gaps_and_wins(
                        conn, top_n=10, include_pack_mismatch=include_mismatch
                    )
                    with g1:
                        st.markdown("##### Biggest gaps (Viva more expensive)")
                        if gaps.empty:
                            st.caption("No gap rows for current filters.")
                        else:
                            _show_table(gaps, width="stretch", hide_index=True, height=320)
                    with g2:
                        st.markdown("##### Biggest wins (Viva cheapest)")
                        if wins.empty:
                            st.caption("No win rows for current filters.")
                        else:
                            _show_table(wins, width="stretch", hide_index=True, height=320)

                    cat_df = category_rollup(conn, include_pack_mismatch=include_mismatch)
                    st.markdown("##### Category summary")
                    if cat_df.empty:
                        st.caption("No category rollup available.")
                    else:
                        _show_table(cat_df, width="stretch", hide_index=True, height=320)

            with tab_compare:
                cmp_df = comparison_matrix(conn, include_pack_mismatch=include_mismatch)
                if cmp_df.empty:
                    st.info("No comparison data for the latest run.")
                else:
                    categories = sorted(
                        c for c in cmp_df["category"].dropna().astype(str).unique().tolist() if c
                    )
                    fc1, fc2 = st.columns(2)
                    with fc1:
                        cat_filter = st.selectbox(
                            "Category",
                            ["(All categories)"] + categories,
                            key="analytics_compare_category",
                        )
                    with fc2:
                        show_mismatch_only = st.checkbox(
                            "Show pack_mismatch lines only",
                            value=False,
                            key="analytics_compare_mismatch_only",
                        )

                    view_cmp = cmp_df.copy()
                    if cat_filter != "(All categories)":
                        view_cmp = view_cmp[view_cmp["category"].astype(str) == cat_filter]
                    if show_mismatch_only:
                        mismatch_cols = [c for c in view_cmp.columns if c.endswith("_pack_match")]
                        if mismatch_cols:
                            mask = pd.Series(False, index=view_cmp.index)
                            for c in mismatch_cols:
                                mask = mask | view_cmp[c].astype(str).str.lower().isin(
                                    {"mismatch", "different", "pack_mismatch"}
                                )
                            view_cmp = view_cmp[mask]

                    st.markdown("##### Normalized price comparison vs Viva")
                    _show_table(view_cmp, width="stretch", height=320, hide_index=True)

                    heat = gap_heatmap_frame(conn, include_pack_mismatch=include_mismatch)
                    if not heat.empty:
                        st.markdown("##### Competitive gap heatmap (% vs Viva)")
                        heat_cols = [
                            c
                            for c in heat.columns
                            if c not in {"line_no", "category", "viva_product"}
                        ]
                        z = heat.set_index("line_no")[heat_cols]
                        y_labels = (
                            heat["line_no"].astype(str)
                            + " "
                            + heat["viva_product"].fillna("").astype(str).str.slice(0, 28)
                        )
                        fig_hm = px.imshow(
                            z,
                            x=heat_cols,
                            y=y_labels,
                            color_continuous_scale="RdYlGn_r",
                            aspect="auto",
                            title="Gap vs Viva by line and store (red = Viva more expensive)",
                        )
                        st.plotly_chart(fig_hm, width="stretch")

                    cat_chart = category_rollup(conn, include_pack_mismatch=include_mismatch)
                    if not cat_chart.empty:
                        st.markdown("##### Category rollup by store")
                        store_cols = [
                            c
                            for c in cat_chart.columns
                            if c != "category" and not c.endswith("_gap_vs_viva_pct")
                        ]
                        melt = cat_chart.melt(
                            id_vars=["category"],
                            value_vars=store_cols,
                            var_name="store_label",
                            value_name="normalized_subtotal",
                        )
                        fig_cat = px.bar(
                            melt,
                            x="category",
                            y="normalized_subtotal",
                            color="store_label",
                            barmode="stack",
                            title="Normalized subtotal by category",
                        )
                        st.plotly_chart(fig_cat, width="stretch")

                    promo_df = promo_intensity_by_store(conn)
                    if not promo_df.empty:
                        st.markdown("##### Promo intensity")
                        _show_table(promo_df, width="stretch", hide_index=True, height=220)
                        fig_promo = px.bar(
                            promo_df,
                            x="store_label",
                            y="lines_on_promo",
                            color="store_label",
                            title="Lines on promotion by store",
                        )
                        fig_promo.update_layout(showlegend=False)
                        st.plotly_chart(fig_promo, width="stretch")

            with tab_trends:
                totals = (
                    adf.groupby(["run_date", "store_label"], as_index=False)["price"]
                    .sum()
                    .rename(columns={"price": "basket_total"})
                )
                fig = px.line(
                    totals,
                    x="run_date",
                    y="basket_total",
                    color="store_label",
                    markers=True,
                    title="Basket total over time (shelf prices)",
                )
                st.plotly_chart(fig, width="stretch")

                if "price_per_base" in adf.columns:
                    norm_totals = (
                        adf.dropna(subset=["price_per_base"])
                        .groupby(["run_date", "store_label"], as_index=False)["price_per_base"]
                        .sum()
                        .rename(columns={"price_per_base": "normalized_basket_total"})
                    )
                    if not norm_totals.empty:
                        fig2 = px.line(
                            norm_totals,
                            x="run_date",
                            y="normalized_basket_total",
                            color="store_label",
                            markers=True,
                            title="Normalized basket total over time",
                        )
                        st.plotly_chart(fig2, width="stretch")

                gap_ts = viva_vs_best_gap_timeseries(conn)
                if not gap_ts.empty:
                    gap_ts["run_date"] = pd.to_datetime(gap_ts["run_date"])
                    fig_gap = px.line(
                        gap_ts,
                        x="run_date",
                        y="gap_vs_cheapest_pct",
                        markers=True,
                        title="Viva vs cheapest competitor gap over time (%)",
                    )
                    fig_gap.add_hline(y=0, line_dash="dot")
                    st.plotly_chart(fig_gap, width="stretch")

                tc1, tc2 = st.columns(2)
                line_opts = sorted(adf["line_no"].dropna().astype(int).unique().tolist())
                store_opts = sorted(adf["store_label"].dropna().astype(str).unique().tolist())
                cat_opts = sorted(
                    c for c in adf["category"].dropna().astype(str).unique().tolist() if c
                )
                with tc1:
                    sel_line = st.selectbox("Item trend — basket line", line_opts, key="analytics_trend_line")
                    sel_store = st.selectbox(
                        "Item trend — store", store_opts, key="analytics_trend_store"
                    )
                with tc2:
                    sel_category = st.selectbox(
                        "Category trend", cat_opts, key="analytics_trend_category"
                    )

                item_ts = adf[
                    (adf["line_no"] == sel_line) & (adf["store_label"].astype(str) == sel_store)
                ]
                if not item_ts.empty:
                    fig_item = px.line(
                        item_ts,
                        x="run_date",
                        y=["price", "price_per_base"],
                        markers=True,
                        title=f"Line {sel_line} @ {sel_store}",
                    )
                    st.plotly_chart(fig_item, width="stretch")

                cat_ts = adf[adf["category"].astype(str) == sel_category].dropna(
                    subset=["price_per_base"]
                )
                if not cat_ts.empty:
                    cat_run = (
                        cat_ts.groupby(["run_date", "store_label"], as_index=False)["price_per_base"]
                        .sum()
                        .rename(columns={"price_per_base": "category_normalized_total"})
                    )
                    fig_ct = px.line(
                        cat_run,
                        x="run_date",
                        y="category_normalized_total",
                        color="store_label",
                        markers=True,
                        title=f"Category trend: {sel_category}",
                    )
                    st.plotly_chart(fig_ct, width="stretch")

                threshold = st.slider(
                    "Price change log threshold (%)",
                    min_value=1,
                    max_value=25,
                    value=5,
                    key="analytics_change_threshold",
                )
                changes = price_change_log(conn, threshold_pct=float(threshold))
                st.markdown("##### Price change log")
                if changes.empty:
                    st.caption(f"No line/store changes ≥ {threshold}% vs prior run.")
                else:
                    _show_table(changes, width="stretch", hide_index=True, height=280)

            with tab_quality:
                integrity_df = grid_integrity_report(conn)
                st.markdown("##### Grid integrity (store × basket)")
                if integrity_df.empty:
                    st.info("No grid rows yet.")
                else:
                    _show_table(integrity_df, width="stretch", hide_index=True, height=220)

                cov_df = url_coverage_by_store(conn)
                st.markdown("##### URL coverage by store")
                if cov_df.empty:
                    st.info("No URL master rows yet.")
                else:
                    _show_table(cov_df, width="stretch", hide_index=True, height=220)
                    fig_cov = px.bar(
                        cov_df,
                        x="store_label",
                        y="coverage_pct",
                        color="store_label",
                        title="URL coverage % by store",
                    )
                    fig_cov.update_layout(showlegend=False)
                    st.plotly_chart(fig_cov, width="stretch")

                audit_df = extraction_audit(conn)
                st.markdown("##### Last extraction status")
                if audit_df.empty:
                    st.caption("No extraction audit rows.")
                else:
                    _show_table(audit_df, width="stretch", hide_index=True, height=280)

                mismatch_df = pack_mismatch_register(conn)
                st.markdown("##### Pack mismatch register")
                if mismatch_df.empty:
                    st.caption("No pack mismatches recorded.")
                else:
                    _show_table(mismatch_df, width="stretch", hide_index=True, height=280)

                method_df = match_method_breakdown(conn)
                st.markdown("##### Match method breakdown")
                if method_df.empty:
                    st.caption("No match methods recorded.")
                else:
                    fig_mm = px.bar(
                        method_df,
                        x="store_label",
                        y="count",
                        color="match_method",
                        barmode="stack",
                        title="URL match methods by store",
                    )
                    st.plotly_chart(fig_mm, width="stretch")
                    _show_table(method_df, width="stretch", hide_index=True, height=220)

    with tab_viva:
        st.subheader("Viva stores")
        st.caption(
            f"Cross-branch view for Viva. Competitive benchmarks elsewhere use "
            f"**{VIVA_REFERENCE_LABEL}** (highest price_per_base per line)."
        )
        cross = viva_cross_store_prices(conn)
        spread = viva_store_spread(conn)
        unavail = viva_unavailability(conn)

        m1, m2, m3 = st.columns(3)
        m1.metric("Viva branches tracked", cross["store_label"].nunique() if not cross.empty else 0)
        m2.metric("Lines with branch price spread", len(spread))
        m3.metric("Unavailability issues", len(unavail))

        st.markdown("##### Price differences across Viva branches")
        if spread.empty:
            st.info("No lines with prices at more than one Viva branch yet.")
        else:
            spread_show = spread[
                [
                    "line_no",
                    "category",
                    "basket_item",
                    "min_price_per_base",
                    "max_price_per_base",
                    "spread_pct",
                    "cheapest_store",
                    "most_expensive_store",
                    "viva_reference_store",
                ]
            ]
            _show_table(spread_show, width="stretch", hide_index=True, height=360)
            fig_spread = px.bar(
                spread.sort_values("spread_pct", ascending=False).head(20),
                x="basket_item",
                y="spread_pct",
                color="most_expensive_store",
                title="Top 20 lines by Viva branch spread (%)",
            )
            fig_spread.update_layout(xaxis_tickangle=-45)
            st.plotly_chart(fig_spread, width="stretch")

        st.markdown("##### All Viva branch prices")
        if cross.empty:
            st.caption("No Viva store rows in the grid.")
        else:
            _show_table(cross, width="stretch", hide_index=True, height=320)

        st.markdown("##### Unavailability & gaps")
        if unavail.empty:
            st.success("No missing URLs, extraction failures, or single-branch-only prices flagged.")
        else:
            for issue, label in (
                ("missing_url", "Missing URL"),
                ("extraction_failed", "Extraction failed"),
                ("single_branch_price", "Price at one branch only"),
            ):
                part = unavail[unavail["issue"] == issue]
                if part.empty:
                    continue
                st.markdown(f"**{label}** ({len(part)})")
                _show_table(part, width="stretch", hide_index=True, height=220)

        e1, e2 = st.columns(2)
        if e1.button("Export Viva spread CSV", width="stretch", key="btn_export_viva_spread"):
            out = EXPORTS_DIR / "viva_store_spread.csv"
            out.parent.mkdir(parents=True, exist_ok=True)
            spread.to_csv(out, index=False)
            st.success(f"Exported: {out}")
        if e2.button("Export Viva unavailability CSV", width="stretch", key="btn_export_viva_unavail"):
            out = EXPORTS_DIR / "viva_unavailability.csv"
            out.parent.mkdir(parents=True, exist_ok=True)
            unavail.to_csv(out, index=False)
            st.success(f"Exported: {out}")


if __name__ == "__main__":
    main()
