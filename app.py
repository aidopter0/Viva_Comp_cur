"""
Streamlit dashboard: Talabat key-item prices across stores (bouquets, time series, heatmaps).
Run from repo root:  streamlit run app.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent
_DASHBOARD = ROOT / "output" / "consolidated_dashboard.csv"
_LEGACY = ROOT / "output" / "consolidated_pricing.csv"
BOUQUETS_JSON = ROOT / "config" / "bouquets.json"
DEFAULT_SCORE_WARN = 0.55


@st.cache_data
def load_bouquets(path: Path) -> tuple[list[dict], dict[int, tuple[str, str]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    bouquets = data["bouquets"]
    line_to_bouquet: dict[int, tuple[str, str]] = {}
    for b in bouquets:
        bid = b["id"]
        name = b["name"]
        for ln in b["lines"]:
            line_to_bouquet[int(ln)] = (bid, name)
    return bouquets, line_to_bouquet


def _normalize_pricing_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["extraction_date"] = pd.to_datetime(out["extraction_date"], errors="coerce")
    out["line"] = pd.to_numeric(out["line"], errors="coerce").astype("Int64")
    out["match_1_price"] = pd.to_numeric(out["match_1_price"], errors="coerce")
    out["match_score_best"] = pd.to_numeric(out["match_score_best"], errors="coerce")
    out["_raw_key"] = out["raw"].fillna("").astype(str).str.strip()
    return out


@st.cache_data
def load_consolidated_for_dashboard() -> pd.DataFrame:
    """
    Merge legacy append-only file with dashboard snapshot (reranked-first).
    On duplicate (extraction_date, store_name, line), keep dashboard row (Gemini-corrected).
    """
    parts: list[pd.DataFrame] = []
    if _LEGACY.is_file():
        parts.append(_normalize_pricing_df(pd.read_csv(_LEGACY)))
    if _DASHBOARD.is_file():
        parts.append(_normalize_pricing_df(pd.read_csv(_DASHBOARD)))
    if not parts:
        return pd.DataFrame()
    merged = pd.concat(parts, ignore_index=True)
    merged = merged.drop_duplicates(
        subset=["extraction_date", "store_name", "line"],
        keep="last",
    )
    return merged


def dedupe_basket_rows(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (date, store, raw) — keeps lowest line index for duplicate key-item lines."""
    if df.empty:
        return df
    out = df.sort_values("line", na_position="last")
    return out.drop_duplicates(subset=["extraction_date", "store_name", "_raw_key"], keep="first")


def add_bouquet_column(
    df: pd.DataFrame, line_to_bouquet: dict[int, tuple[str, str]]
) -> pd.DataFrame:
    out = df.copy()

    def map_line(ln: object) -> tuple[str | None, str | None]:
        if ln is None or (isinstance(ln, float) and pd.isna(ln)):
            return None, None
        try:
            i = int(ln)
        except (TypeError, ValueError):
            return None, None
        if i in line_to_bouquet:
            bid, name = line_to_bouquet[i]
            return bid, name
        return None, None

    mapped = [map_line(x) for x in out["line"]]
    out["bouquet_id"] = [m[0] for m in mapped]
    out["bouquet_name"] = [m[1] for m in mapped]
    return out


def filter_frame(
    df: pd.DataFrame,
    date_from,
    date_to,
    stores: list[str],
    lines: list[int] | None,
    score_min: float | None,
) -> pd.DataFrame:
    d = df.copy()
    if date_from is not None:
        d = d[d["extraction_date"] >= pd.Timestamp(date_from)]
    if date_to is not None:
        d = d[d["extraction_date"] <= pd.Timestamp(date_to) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)]
    if stores is not None:
        if len(stores) == 0:
            return d.iloc[0:0]
        d = d[d["store_name"].isin(stores)]
    if lines is not None:
        d = d[d["line"].isin(lines)]
    if score_min is not None:
        d = d[d["match_score_best"].isna() | (d["match_score_best"] >= score_min)]
    return d


def basket_totals(deduped: pd.DataFrame) -> pd.DataFrame:
    """Sum match_1_price per date × store (already deduped)."""
    g = (
        deduped.dropna(subset=["match_1_price"])
        .groupby(["extraction_date", "store_name"], as_index=False)["match_1_price"]
        .sum()
    )
    return g.rename(columns={"match_1_price": "basket_total"})


def bouquet_totals(deduped: pd.DataFrame) -> pd.DataFrame:
    d = deduped.dropna(subset=["match_1_price", "bouquet_id"])
    g = (
        d.groupby(["extraction_date", "store_name", "bouquet_id", "bouquet_name"], as_index=False)[
            "match_1_price"
        ]
        .sum()
    )
    return g.rename(columns={"match_1_price": "bouquet_total"})


def _category_mismatch_hint(expected: str, matched_title: str) -> str | None:
    """Heuristic: key label vs matched title (no LLM)."""
    e = expected.lower()
    t = matched_title.lower()
    if ("yoghurt" in e or "yogurt" in e) and "yoghurt" not in t and "yogurt" not in t:
        return (
            "**Category check:** your key label is **yoghurt**, but the matched Talabat title does **not** "
            "mention yoghurt. **This usually explains an extreme price** (wrong SKU on that store). "
            "**Do not treat this as a real yoghurt price** until the match is corrected."
        )
    if "chicken" in e or "whole chicken" in e:
        if "chicken" not in t and "griller" not in t and "poultry" not in t:
            return (
                "**Category check:** key item is **chicken**, but the matched title may point to a different "
                "product type — verify the SKU if the price looks off."
            )
    return None


def is_suspect_match(expected: str, matched_title: str, score) -> bool:
    """Unreliable primary match: low score or obvious category mismatch (e.g. yoghurt vs beef)."""
    if score is not None and pd.notna(score) and float(score) < DEFAULT_SCORE_WARN:
        return True
    if _category_mismatch_hint(str(expected), str(matched_title)):
        return True
    return False


def item_insights_rule_based(
    snapshot_df: pd.DataFrame,
    line: int,
    focus_store: str,
) -> tuple[str, pd.DataFrame]:
    """
    Deterministic 'why is this price high/low here?' text from prices, matched titles, and scores.
    No LLM — cannot infer promotions, supply chain, or store strategy beyond what the table shows.
    """
    sub = snapshot_df[snapshot_df["line"] == line].copy()
    if sub.empty:
        return "**No rows** for this line on the selected snapshot.", pd.DataFrame()

    sub = sub.dropna(subset=["match_1_price"])
    if sub.empty:
        return "**No valid price** for this line on the selected snapshot.", pd.DataFrame()

    label = ""
    if "expected" in sub.columns and not sub["expected"].isna().all():
        label = str(sub["expected"].iloc[0])
    tbl = sub[
        ["store_name", "match_1_price", "match_1_title", "match_score_best"]
    ].sort_values("match_1_price")
    tbl = tbl.rename(
        columns={
            "match_1_price": "Price (AED)",
            "match_1_title": "Matched product (Talabat)",
            "match_score_best": "Match score",
        }
    )

    prices = sub["match_1_price"].astype(float)
    pmin, pmax = float(prices.min()), float(prices.max())
    cheapest = sub.loc[prices.idxmin(), "store_name"]
    priciest = sub.loc[prices.idxmax(), "store_name"]
    titles_norm = (
        sub["match_1_title"].fillna("").astype(str).str.strip().str.lower()
    )
    n_distinct_titles = titles_norm.nunique()

    lines_out: list[str] = []
    if label:
        lines_out.append(f"**Key item:** `{label}` (line **{line}**).")

    fs_row = sub[sub["store_name"] == focus_store]
    if fs_row.empty:
        lines_out.append(f"**{focus_store}** has no row for this line on this date (filtered out or missing).")
        return "\n\n".join(lines_out), tbl

    fs_price = float(fs_row["match_1_price"].iloc[0])
    fs_title = str(fs_row["match_1_title"].iloc[0])
    fs_score = fs_row["match_score_best"].iloc[0]

    tbl_sorted = sub.sort_values("match_1_price").reset_index(drop=True)
    rank_idx = tbl_sorted.index[tbl_sorted["store_name"] == focus_store].tolist()
    rank_pos = rank_idx[0] + 1 if rank_idx else None
    n_stores = len(sub)

    if pmin > 0:
        pct_vs_min = 100.0 * (fs_price - pmin) / pmin
    else:
        pct_vs_min = 0.0

    lines_out.append(
        f"**{focus_store}** lists **AED {fs_price:.2f}** for the primary match on this date "
        f"- **rank {rank_pos} of {n_stores}** stores (1 = cheapest)."
    )
    lines_out.append(
        f"**Cheapest** among selected stores: **{cheapest}** at **AED {pmin:.2f}**. "
        f"**Highest:** **{priciest}** at **AED {pmax:.2f}**. "
        f"**Spread:** AED {pmax - pmin:.2f}."
    )
    if focus_store == priciest and n_stores > 1:
        lines_out.append(
            f"On this snapshot, **{focus_store} is the most expensive** for this key item - "
            f"**+AED {fs_price - pmin:.2f}** vs the cheapest option (**+{pct_vs_min:.1f}%** vs min)."
        )
    elif focus_store == cheapest and n_stores > 1:
        lines_out.append(
            f"**{focus_store} is the cheapest** for this key item among selected stores on this date."
        )
    elif n_stores > 1:
        lines_out.append(
            f"**Gap vs cheapest:** +AED {fs_price - pmin:.2f} (**+{pct_vs_min:.1f}%** vs **{cheapest}**)."
        )

    # Matched product explanation (data-only)
    lines_out.append(f"**Matched listing at {focus_store}:** {fs_title}")
    if label:
        hint = _category_mismatch_hint(label, fs_title)
        if hint:
            lines_out.append(hint)
    if n_distinct_titles == 1:
        lines_out.append(
            "**Same matched product title** at every store — differences are **store list prices** "
            "for that SKU on Talabat (not a brand-mix effect in the matcher)."
        )
    else:
        lines_out.append(
            "**Different matched product titles** across stores for this key line — the pipeline picks "
            "the best similar SKU per store, so **pack, brand, or size may differ**. Compare the "
            "**Matched product** column in the table before treating this as a pure chain-to-chain price gap."
        )

    if pd.notna(fs_score) and float(fs_score) < DEFAULT_SCORE_WARN:
        lines_out.append(
            f"**Match score is low ({float(fs_score):.2f})** — the listing may not be the intended product; "
            "verify on Talabat before drawing conclusions."
        )

    lines_out.append(
        "_Insights are generated from your extract (price, title, score). They do **not** explain supplier "
        "costs, promotions, or store pricing strategy — only what the data shows._"
    )

    return "\n\n".join(lines_out), tbl


def main() -> None:
    st.set_page_config(page_title="Talabat price tracker", layout="wide")
    st.title("Talabat key-item price comparison")

    if not _DASHBOARD.is_file() and not _LEGACY.is_file():
        st.error(
            f"No consolidated data. Create `{_LEGACY}` or `{_DASHBOARD}` by running "
            "`python run_talabat_stores.py` (writes both raw append + dashboard snapshot), then refresh."
        )
        return

    bouquets, line_to_bouquet = load_bouquets(BOUQUETS_JSON)
    df = load_consolidated_for_dashboard()
    if df.empty:
        st.warning("Consolidated CSV is empty.")
        return

    df = add_bouquet_column(df, line_to_bouquet)

    # --- Sidebar ---
    if _DASHBOARD.is_file() and _LEGACY.is_file():
        st.sidebar.caption(
            "**Data:** merged `consolidated_pricing.csv` + `consolidated_dashboard.csv` "
            "(same date/store/line uses dashboard / Gemini when present)."
        )
    elif _DASHBOARD.is_file():
        st.sidebar.caption("**Data:** `consolidated_dashboard.csv` (reranked store CSVs when present).")
    else:
        st.sidebar.caption("**Data:** `consolidated_pricing.csv` only (run fetch to add `consolidated_dashboard.csv`).")
    st.sidebar.header("Filters")
    min_date = df["extraction_date"].min()
    max_date = df["extraction_date"].max()
    dr = st.sidebar.date_input(
        "Date range",
        value=(min_date.date(), max_date.date()),
        min_value=min_date.date(),
        max_value=max_date.date(),
    )
    if isinstance(dr, tuple) and len(dr) == 2:
        start_d, end_d = dr[0], dr[1]
    else:
        start_d = end_d = dr

    all_stores = sorted(df["store_name"].dropna().unique().tolist())
    sel_stores = st.sidebar.multiselect("Stores", options=all_stores, default=all_stores)

    scope = st.sidebar.radio(
        "Item scope",
        ("Full basket (deduped)", "By bouquet", "Custom line numbers"),
        index=0,
    )

    selected_lines: list[int] | None = None
    if scope == "By bouquet":
        b_ids = [b["id"] for b in bouquets]
        b_labels = [b["name"] for b in bouquets]
        pick = st.sidebar.multiselect("Bouquets", options=b_ids, format_func=lambda x: b_labels[b_ids.index(x)], default=b_ids)
        if pick:
            selected_lines = []
            for b in bouquets:
                if b["id"] in pick:
                    selected_lines.extend(b["lines"])
            selected_lines = sorted(set(selected_lines))
        else:
            selected_lines = []
    elif scope == "Custom line numbers":
        nums = st.sidebar.text_input("Line numbers (comma-separated, e.g. 1,2,5)", "")
        selected_lines = []
        for part in nums.split(","):
            part = part.strip()
            if part.isdigit():
                selected_lines.append(int(part))
        if not selected_lines:
            selected_lines = None

    score_min = st.sidebar.slider(
        "Minimum match score (exclude weaker matches)",
        min_value=0.0,
        max_value=1.0,
        value=DEFAULT_SCORE_WARN,
        step=0.01,
    )

    # Apply filters (before dedupe: filter rows; weak scores dropped from basket views)
    filtered = filter_frame(df, start_d, end_d, sel_stores, selected_lines, score_min)
    deduped = dedupe_basket_rows(filtered)

    # Quality tab: same date/store/line scope but ignore score cutoff so weak rows stay visible
    for_quality = filter_frame(df, start_d, end_d, sel_stores, selected_lines, score_min=None)
    low_score_rows = for_quality[
        for_quality["match_score_best"].notna() & (for_quality["match_score_best"] < DEFAULT_SCORE_WARN)
    ]

    st.sidebar.metric("Rows (raw)", len(filtered))
    st.sidebar.metric("Rows (deduped for sums)", len(deduped))

    # --- Main ---
    tab_ts, tab_bar, tab_heat, tab_bouq, tab_q = st.tabs(
        ["Time series", "Current snapshot (bars)", "Heatmap", "Bouquet breakdown", "Data quality"]
    )

    totals = basket_totals(deduped)
    btot = bouquet_totals(deduped)

    with tab_ts:
        st.subheader("Basket total over time (sum of match_1_price, deduped by item)")
        if totals.empty:
            st.info("No data for current filters.")
        else:
            fig = px.line(
                totals,
                x="extraction_date",
                y="basket_total",
                color="store_name",
                markers=True,
                labels={"basket_total": "AED (basket total)", "extraction_date": "Date"},
            )
            fig.update_layout(hovermode="x unified", legend_title="Store")
            st.plotly_chart(fig, use_container_width=True)

        st.divider()
        st.subheader("Item price over time")
        st.caption(
            "**Primary match** price (`match_1_price`, AED) by extraction date and store. "
            "Add more extraction runs over time to see trends."
        )
        item_ts = deduped.dropna(subset=["match_1_price", "line"]).copy()
        if item_ts.empty:
            st.info("No priced rows for current filters.")
        else:
            n_dates = item_ts["extraction_date"].nunique()
            if n_dates < 2:
                st.caption(
                    f"_Only **{n_dates}** extraction date(s) in range — lines will show markers at a single date._"
                )

            lines_avail = sorted({int(x) for x in item_ts["line"].dropna().unique()})

            def _item_label(ln: int) -> str:
                sub = item_ts[item_ts["line"] == ln]
                if sub.empty:
                    return str(ln)
                exp = sub["expected"].iloc[0] if "expected" in sub.columns else ""
                return f"{ln} — {exp}"

            default_n = min(6, len(lines_avail))
            sel_lines = st.multiselect(
                "Key items to plot",
                options=lines_avail,
                format_func=_item_label,
                default=lines_avail[:default_n],
                key="item_ts_lines",
            )
            if not sel_lines:
                st.info("Select one or more key items above.")
            else:
                plot_i = item_ts[item_ts["line"].isin(sel_lines)].copy()
                plot_i["_item"] = plot_i["line"].map(lambda ln: _item_label(int(ln)))
                n_facets = len(sel_lines)
                wrap = min(3, max(1, n_facets))
                fig_i = px.line(
                    plot_i,
                    x="extraction_date",
                    y="match_1_price",
                    color="store_name",
                    facet_col="_item",
                    facet_col_wrap=wrap,
                    markers=True,
                    labels={"match_1_price": "AED", "extraction_date": "Date"},
                )
                fig_i.update_layout(
                    hovermode="x unified",
                    legend_title="Store",
                    height=max(360, 320 * ((n_facets + wrap - 1) // wrap)),
                )

                def _short_facet_title(ann) -> None:
                    if ann.text and "=" in ann.text:
                        ann.update(text=ann.text.split("=", 1)[-1].strip())

                fig_i.for_each_annotation(_short_facet_title)
                st.plotly_chart(fig_i, use_container_width=True)

    with tab_bar:
        st.subheader("Basket total by store (latest date in range)")
        if totals.empty:
            st.info("No data.")
        else:
            last_dt = totals["extraction_date"].max()
            snap = totals[totals["extraction_date"] == last_dt].sort_values("basket_total")
            fig = px.bar(
                snap,
                x="store_name",
                y="basket_total",
                color="store_name",
                labels={"basket_total": "AED", "store_name": "Store"},
                text="basket_total",
            )
            fig.update_traces(texttemplate="%{text:.2f}", textposition="outside")
            fig.update_layout(showlegend=False, xaxis_title="")
            st.caption(f"Snapshot date: **{last_dt.date()}**")
            st.plotly_chart(fig, use_container_width=True)

            idx = snap.copy()
            m = idx["basket_total"].min()
            if m and m > 0:
                idx["index_vs_min"] = 100.0 * idx["basket_total"] / m
                out_idx = idx[["store_name", "basket_total", "index_vs_min"]].round(2)
                st.dataframe(out_idx, use_container_width=True)

    with tab_heat:
        st.subheader("Price heatmap — store × item")
        with st.expander("How to read this chart", expanded=True):
            st.markdown(
                """
                **What it shows:** One **snapshot date** at a time. Each **cell** is the **listed price (AED)** 
                for that **key item** at that **store**, using the **primary match** (`match_1_price` — the 
                cheapest among the top similar products from the Talabat search).

                **Rows:** Key items (`line — expected` from your key list).  
                **Columns:** Stores (respecting sidebar filters).  
                **Colour scale (yellow → orange → red):** **Darker / warmer** colours mean **higher** price 
                among **trusted** matches. **Grey cells** are **not** coloured on the price scale: the matcher 
                flagged a **low confidence score** or a **likely wrong product** (e.g. yoghurt key line matched 
                to a non-yoghurt SKU). The **printed AED** is still the API listing — treat grey cells as 
                **verify before comparing**.

                **How to use it:**  
                - **Across a row:** compare stores for the **same** item on that date.  
                - **Down a column:** see which items are **expensive vs cheap** at one store.  
                - **Missing cells** mean no data for that store–item (filtered out or no match).

                **Not shown:** Alternative brands (`match_2` / `match_3`), time trends, or basket totals — use 
                other tabs for those.
                """
            )
        h_date = st.selectbox(
            "Snapshot date",
            options=sorted(deduped["extraction_date"].dropna().unique(), reverse=True),
            format_func=lambda x: x.strftime("%Y-%m-%d") if hasattr(x, "strftime") else str(x),
        )
        heat_df = deduped[deduped["extraction_date"] == h_date].copy()
        if heat_df.empty:
            st.info("No rows for that date.")
        else:
            heat_df["_label"] = (
                heat_df["line"].astype(str) + " — " + heat_df["expected"].fillna("").astype(str)
            )
            heat_df["suspect"] = heat_df.apply(
                lambda r: is_suspect_match(
                    str(r.get("expected", "")),
                    str(r.get("match_1_title", "")),
                    r.get("match_score_best"),
                ),
                axis=1,
            )
            pivot = heat_df.pivot_table(
                index="_label",
                columns="store_name",
                values="match_1_price",
                aggfunc="first",
            )
            pivot_suspect = heat_df.pivot_table(
                index="_label",
                columns="store_name",
                values="suspect",
                aggfunc="first",
            )
            pivot_suspect = pivot_suspect.reindex(index=pivot.index, columns=pivot.columns)

            z = pivot.values.astype(float)
            sus = pivot_suspect.fillna(False).values
            z_valid = np.where(sus, np.nan, z)
            z_grey = np.where(sus, 1.0, np.nan)

            n_rows, n_cols = pivot.shape
            text = np.empty((n_rows, n_cols), dtype=object)
            for i in range(n_rows):
                for j in range(n_cols):
                    v = z[i, j]
                    if np.isnan(v):
                        text[i, j] = ""
                    elif sus[i, j]:
                        text[i, j] = f"{v:.2f} ⚠"
                    else:
                        text[i, j] = f"{v:.2f}"

            vmin = np.nanmin(z_valid)
            vmax = np.nanmax(z_valid)
            if np.isnan(vmin) or np.isnan(vmax) or vmin == vmax:
                vmin, vmax = 0.0, 1.0

            x_labels = pivot.columns.tolist()
            y_labels = pivot.index.tolist()

            cell_px = 26
            margin_l, margin_r, margin_t, margin_b = 280, 100, 72, 56
            fig_w = margin_l + margin_r + n_cols * cell_px
            fig_h = margin_t + margin_b + n_rows * cell_px

            title_date = h_date.strftime("%Y-%m-%d") if hasattr(h_date, "strftime") else str(h_date)
            fig = go.Figure()
            fig.add_trace(
                go.Heatmap(
                    z=z_grey,
                    x=x_labels,
                    y=y_labels,
                    colorscale=[[0.0, "#c5c5c5"], [1.0, "#c5c5c5"]],
                    showscale=False,
                    hoverinfo="skip",
                    name="Suspect match",
                )
            )
            fig.add_trace(
                go.Heatmap(
                    z=z_valid,
                    x=x_labels,
                    y=y_labels,
                    customdata=z,
                    colorscale="YlOrRd",
                    zmin=float(vmin),
                    zmax=float(vmax),
                    text=text,
                    texttemplate="%{text}",
                    textfont={"size": 10},
                    colorbar=dict(title="AED (trusted)"),
                    hovertemplate="Item=%{y}<br>Store=%{x}<br>AED=%{customdata:.2f}<extra></extra>",
                    name="Price",
                )
            )
            fig.update_layout(
                title=f"Price (AED) by store and item — {title_date}",
                width=fig_w,
                height=fig_h,
                margin=dict(l=margin_l, r=margin_r, t=margin_t, b=margin_b),
                autosize=False,
                showlegend=False,
                xaxis=dict(side="top", constrain="domain", type="category"),
                yaxis=dict(autorange="reversed", constrain="domain", type="category"),
            )
            st.plotly_chart(fig, use_container_width=False)
            st.caption(
                "Cells are **squares**. **Grey background + ⚠** = suspect match (low score or wrong category); "
                "colour scale applies only to other cells so one bad SKU does not paint max-red. "
                "Scroll if the chart is tall. Hover for AED."
            )

            st.divider()
            st.subheader("Item-level analysis")
            st.caption(
                "Structured, **rule-based** notes from this snapshot’s prices and matched Talabat titles "
                "(no LLM). They show *what the data says* — rank vs other stores, gap vs cheapest, whether "
                "titles match — not causal reasons (promotions, costs, etc.)."
            )
            lines_available = sorted(
                {int(x) for x in heat_df["line"].dropna().unique() if pd.notna(x)}
            )

            def _line_option_label(ln: int) -> str:
                sub_l = heat_df[heat_df["line"] == ln]
                if sub_l.empty:
                    return str(ln)
                return f"{ln} — {sub_l['expected'].iloc[0]}"

            col_a, col_b = st.columns(2)
            with col_a:
                sel_line = st.selectbox(
                    "Key item (line)",
                    options=lines_available,
                    format_func=_line_option_label,
                    index=lines_available.index(39) if 39 in lines_available else 0,
                )
            with col_b:
                focus_options = sorted(heat_df["store_name"].dropna().unique().tolist())
                default_focus = "Viva_AlSeyouh" if "Viva_AlSeyouh" in focus_options else focus_options[0]
                focus_store = st.selectbox(
                    "Focus store",
                    options=focus_options,
                    index=focus_options.index(default_focus) if default_focus in focus_options else 0,
                )

            insight_md, insight_tbl = item_insights_rule_based(heat_df, sel_line, focus_store)
            st.markdown(insight_md)
            if not insight_tbl.empty:
                st.dataframe(insight_tbl, use_container_width=True)

    with tab_bouq:
        st.subheader("Bouquet totals over time")
        if btot.empty:
            st.info("No bouquet data (check bouquet config lines vs CSV).")
        else:
            fig = px.line(
                btot,
                x="extraction_date",
                y="bouquet_total",
                color="store_name",
                facet_col="bouquet_name",
                facet_col_wrap=3,
                markers=True,
                labels={"bouquet_total": "AED", "extraction_date": "Date"},
            )
            fig.update_layout(hovermode="x unified")
            st.plotly_chart(fig, use_container_width=True)

            st.subheader("Bouquet totals — latest date in range")
            last_dt = btot["extraction_date"].max()
            snap_b = btot[btot["extraction_date"] == last_dt]
            if not snap_b.empty:
                fig2 = px.bar(
                    snap_b,
                    x="bouquet_name",
                    y="bouquet_total",
                    color="store_name",
                    barmode="group",
                    labels={"bouquet_total": "AED"},
                )
                fig2.update_layout(xaxis_title="")
                st.plotly_chart(fig2, use_container_width=True)

    with tab_q:
        st.subheader("Rows with match_score below warning threshold")
        st.caption(
            f"Shown: rows where match_score_best < {DEFAULT_SCORE_WARN} (still filtered by sidebar minimum)."
        )
        if low_score_rows.empty:
            st.success("No rows below the dashboard warning threshold in the current filter window.")
        else:
            show = low_score_rows[
                [
                    "extraction_date",
                    "store_name",
                    "line",
                    "expected",
                    "match_score_best",
                    "match_1_title",
                    "match_1_price",
                ]
            ].sort_values(["extraction_date", "store_name", "line"])
            st.dataframe(show, use_container_width=True, height=400)

        st.subheader("Optional: price ladder (match_1 vs match_3)")
        lad = filtered.dropna(subset=["match_1_price"]).copy()
        lad["match_3_price"] = pd.to_numeric(lad["match_3_price"], errors="coerce")
        lad["spread"] = lad["match_3_price"] - lad["match_1_price"]
        lad = lad[lad["spread"].notna() & (lad["spread"] > 0)]
        if not lad.empty:
            st.dataframe(
                lad[
                    ["extraction_date", "store_name", "line", "expected", "match_1_price", "match_3_price", "spread"]
                ].head(200),
                use_container_width=True,
            )
        else:
            st.caption("No multi-step ladder data in selection (or match_3 empty).")

    st.divider()
    st.caption(
        "Basket sums use **match_1_price** only (cheapest of top similar matches). "
        "Duplicate key lines with the same `raw` text are counted once per store/date."
    )


if __name__ == "__main__":
    main()
