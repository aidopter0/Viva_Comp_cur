"""Basket-first catalog matching (v2): tokens + pack from basket_label, GPT verify/pick."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from openai import OpenAI

from .basket_match_spec import BasketMatchSpec, parse_pack_from_basket_label
from .basket_matcher import (
    cheapest_among_intent_peers,
    cheapest_loose_row,
    cheapest_row,
    fallback_pool_rows,
    is_loose_fresh,
    near_match_pool_rows,
    order_candidates_for_gpt_pick,
    pack_tier,
    score_fallback_row,
    score_near_match_row,
    shortlist_rows,
    top_near_match_rows,
    top_percentile_rows,
)
from .catalog_index import CatalogIndex, catalog_exists, load_catalog_file
from .gpt_match_verify import openai_client, pick_from_candidates, verify_pick
from .match_form_guards import passes_form_guard
from .match_overrides import lookup_override, resolve_slug_row
from .match_progress import MatchProgress
from .pack_normalize import pack_matches_target
from .repository import (
    basket_ids_with_urls,
    list_basket_items,
    list_stores,
    upsert_item_url_master,
)
from .settings import OPENAI_DEFAULT_MODEL
from .storefront import is_grocery_product_url


def _slug_from_url(url: str) -> str:
    if url and "/product/" in url:
        parts = url.split("/product/", 1)
        if len(parts) > 1:
            return parts[1].split("/s/", 1)[0]
    return ""


def _write_match(
    conn,
    *,
    store_id: int,
    basket_item_id: int,
    picked: dict[str, str] | None,
    status: str,
    pack_match: str,
    match_method: str,
    match_confidence: float | None,
    match_reason: str | None,
    error: str | None,
    index: CatalogIndex,
) -> None:
    if picked and status in {"ok", "pack_mismatch"}:
        url = picked["url"]
        iid = picked["item_id"]
        title = picked["product_name"]
        upsert_item_url_master(
            conn,
            store_id=store_id,
            basket_item_id=basket_item_id,
            item_id=iid,
            source_url=url,
            slug=_slug_from_url(url) or None,
            item_title=title,
            status=status,
            error=error,
            match_method=match_method,
            match_confidence=match_confidence,
            match_reason=match_reason,
            pack_match=pack_match,
            catalog_pack_text=index.catalog_pack_text(title),
        )
    else:
        upsert_item_url_master(
            conn,
            store_id=store_id,
            basket_item_id=basket_item_id,
            item_id=None,
            source_url=None,
            slug=None,
            item_title=None,
            status="missing",
            error=error or "No catalog match",
            match_method=match_method,
            match_confidence=match_confidence,
            match_reason=match_reason,
            pack_match="unknown",
            catalog_pack_text=None,
        )


def _finalize_product_pick(
    row: dict[str, str],
    spec: BasketMatchSpec,
    conf: float | None,
    reason: str,
) -> tuple[dict[str, str], str, str, float | None, str]:
    tier = pack_tier(row, spec)
    if tier in {"exact", "multipack_exact"}:
        return row, "ok", tier if tier != "multipack_exact" else "exact", conf, reason
    # Loose fresh produce/meat is bought by weight: a pack difference is fine because
    # prices are compared per kg/L. Treat as a clean match (normalized), not a mismatch.
    if is_loose_fresh(spec):
        return row, "ok", "normalized", conf, reason
    pm = pack_matches_target(row["product_name"], spec.pack_qty, spec.pack_unit)
    pm_out = pm if pm != "unknown" else "different"
    return row, "pack_mismatch", pm_out, conf, reason


def _apply_pick_stats(stats: dict[str, int], status: str) -> None:
    if status == "ok":
        stats["ok"] += 1
    elif status == "pack_mismatch":
        stats["pack_mismatch"] += 1
    else:
        stats["missing"] += 1


def _record_pick(
    conn,
    *,
    store_id: int,
    bl: dict[str, Any],
    picked: dict[str, str] | None,
    status: str,
    pack_match: str,
    match_method: str,
    match_confidence: float | None,
    match_reason: str | None,
    error: str | None,
    index: CatalogIndex,
    stats: dict[str, int],
    dry_run: bool,
    used_urls: dict[str, int],
) -> None:
    _apply_pick_stats(stats, status)
    if picked and status in {"ok", "pack_mismatch"}:
        url = str(picked.get("url") or "")
        if url:
            used_urls[url] = int(bl["line_no"])
    if not dry_run:
        _write_match(
            conn,
            store_id=store_id,
            basket_item_id=int(bl["basket_item_id"]),
            picked=picked,
            status=status,
            pack_match=pack_match,
            match_method=match_method,
            match_confidence=match_confidence,
            match_reason=match_reason,
            error=error,
            index=index,
        )


def _build_spec(bl: dict[str, Any], *, store_label: str, brand_name: str) -> BasketMatchSpec:
    return BasketMatchSpec.from_basket_row(
        line_no=int(bl["line_no"]),
        basket_item_id=int(bl["basket_item_id"]),
        basket_label=str(bl.get("basket_label") or ""),
        category=str(bl.get("category") or ""),
        match_group=str(bl.get("match_group") or ""),
        line_role=str(bl.get("line_role") or ""),
        store_label=store_label,
        store_brand_name=brand_name,
    )


def _try_override(
    index: CatalogIndex,
    spec: BasketMatchSpec,
    bl: dict[str, Any],
    brand_name: str,
    store_label: str,
) -> tuple[dict[str, str] | None, str, str, float | None, str | None] | None:
    override = lookup_override(brand_name, store_label, spec.line_no)
    if override is None:
        return None
    if override.action == "missing":
        return None, "missing", "unknown", None, override.note or "Override: missing"
    if override.action == "slug" and override.product_slug:
        row = resolve_slug_row(index, override.product_slug)
        if row is None:
            return None
        if not passes_form_guard(row, spec):
            return None
        return _finalize_product_pick(
            row,
            spec,
            1.0,
            override.note or f"Override slug: {override.product_slug}",
        )
    return None


_NEAR_MATCH_REASON_PREFIX = "Near-match: "
_CHEAPEST_PEER_NOTE = "cheapest among intent peers"


def _finalize_gpt_pool_pick(
    row: dict[str, str],
    pool: list[dict[str, str]],
    spec: BasketMatchSpec,
    *,
    score_fn,
    pack_tolerance: float,
    loose: bool,
    conf: float,
    reason: str,
) -> tuple[dict[str, str], str, str, float | None, str]:
    """Finalize a GPT pick, swapping to the cheapest peer when an equivalent exists."""
    if loose:
        # Loose fresh shortlist already picks cheapest per kg/L; do not re-pool swap.
        refined = row
    else:
        refined = cheapest_among_intent_peers(
            row,
            pool,
            spec,
            score_fn=score_fn,
            pack_tolerance=pack_tolerance,
            loose=False,
        )
    out_reason = reason
    if refined.get("item_id") != row.get("item_id"):
        out_reason = f"{reason}; {_CHEAPEST_PEER_NOTE}"
    return _finalize_product_pick(refined, spec, conf, out_reason)


def _resolve_line_match_v2(
    index: CatalogIndex,
    spec: BasketMatchSpec,
    bl: dict[str, Any],
    *,
    client: OpenAI | None,
    model: str,
    used_urls: set[str] | None = None,
    forbid_slugs: tuple[str, ...] = (),
) -> tuple[dict[str, str] | None, str, str, float | None, str | None]:
    used_urls = used_urls or set()

    def _forbidden(row: dict[str, str]) -> bool:
        slug = _slug_from_url(str(row.get("url") or ""))
        return bool(forbid_slugs) and any(f in slug for f in forbid_slugs)

    loose = is_loose_fresh(spec)
    exclude = set(used_urls)
    shortlist = [
        r for r in shortlist_rows(index, spec, exclude_urls=exclude) if not _forbidden(r)
    ]
    pick = cheapest_loose_row(shortlist) if loose else cheapest_row(shortlist)

    if pick is not None:
        accept = True
        reason = (
            "Basket match (loose fresh, cheapest per kg)"
            if loose
            else "Basket match (exact pack, cheapest)"
        )
        if client is not None:
            verdict = verify_pick(client, model, spec, pick, pack_flexible=loose)
            accept = bool(verdict.get("accept"))
            if not accept:
                reason = str(verdict.get("reason") or "GPT rejected basket match")
        if accept:
            return _finalize_product_pick(pick, spec, 0.95, reason)

    pool = [r for r in fallback_pool_rows(index, spec, exclude_urls=exclude) if not _forbidden(r)]
    candidates = order_candidates_for_gpt_pick(
        top_percentile_rows(pool, spec, fraction=0.10),
        spec,
        score_fallback_row,
    )
    if client is None:
        if pick is not None:
            return _finalize_product_pick(pick, spec, 0.9, "Basket match (no GPT verify)")
        return None, "missing", "unknown", None, "No basket token+pack shortlist"

    if candidates:
        gpt = pick_from_candidates(client, model, spec, candidates, pack_flexible=loose)
        ref = gpt.get("ref")
        if ref:
            row = index.get(str(ref))
            if (
                row is not None
                and str(row.get("url") or "") not in used_urls
                and not _forbidden(row)
                and passes_form_guard(row, spec)
            ):
                conf = float(gpt.get("confidence") or 0.0)
                return _finalize_gpt_pool_pick(
                    row,
                    pool,
                    spec,
                    score_fn=score_fallback_row,
                    pack_tolerance=0.08,
                    loose=loose,
                    conf=conf,
                    reason=str(gpt.get("reason") or "GPT pick from fallback pool"),
                )

    # Round 2: relaxed near-match retrieval + GPT intent pick.
    return _round2_near_match(
        index,
        spec,
        client=client,
        model=model,
        used_urls=used_urls,
        forbidden=_forbidden,
        loose=loose,
        exclude=exclude,
    )


def _round2_near_match(
    index: CatalogIndex,
    spec: BasketMatchSpec,
    *,
    client: OpenAI,
    model: str,
    used_urls: set[str],
    forbidden,
    loose: bool,
    exclude: set[str],
) -> tuple[dict[str, str] | None, str, str, float | None, str | None]:
    near = [r for r in near_match_pool_rows(index, spec, exclude_urls=exclude) if not forbidden(r)]
    near_pool = order_candidates_for_gpt_pick(
        top_near_match_rows(near, spec, limit=20),
        spec,
        score_near_match_row,
        pack_tolerance=0.12,
    )
    if not near_pool:
        return None, "missing", "unknown", None, "No near-match candidates"

    gpt = pick_from_candidates(
        client,
        model,
        spec,
        near_pool,
        pack_flexible=loose,
        intent_pick=True,
        pack_tolerance_pct=12.0,
    )
    ref = gpt.get("ref")
    if not ref:
        return None, "missing", "unknown", None, str(gpt.get("reason") or "GPT near-match found no fit")

    row = index.get(str(ref))
    if row is None or str(row.get("url") or "") in used_urls or forbidden(row):
        return None, "missing", "unknown", None, "GPT near pick invalid"

    if not passes_form_guard(row, spec):
        return None, "missing", "unknown", None, "GPT near pick failed form guard"

    conf = float(gpt.get("confidence") or 0.0)
    reason = _NEAR_MATCH_REASON_PREFIX + str(gpt.get("reason") or "GPT intent pick")
    return _finalize_gpt_pool_pick(
        row,
        near,
        spec,
        score_fn=score_near_match_row,
        pack_tolerance=0.12,
        loose=loose,
        conf=conf,
        reason=reason,
    )


def match_store(
    conn,
    *,
    store_id: int,
    store_label: str,
    brand_name: str,
    model: str | None = None,
    batch_size: int = 8,
    dry_run: bool = False,
    skip_existing: bool = True,
    basket_item_ids: list[int] | None = None,
    progress_callback: Callable[[MatchProgress], None] | None = None,
) -> dict[str, int]:
    del batch_size  # v2 resolves per line; batch_size kept for CLI compat
    if not catalog_exists(store_label):
        raise FileNotFoundError(f"No catalog JSON for store {store_label!r}. Build catalog first.")

    catalog = load_catalog_file(store_label)
    index = CatalogIndex(catalog)

    basket_lines: list[dict[str, Any]] = []
    for bi in list_basket_items(conn):
        row = dict(bi)
        bid = int(row["basket_item_id"])
        if basket_item_ids is not None and bid not in {int(x) for x in basket_item_ids}:
            continue
        label = str(row.get("basket_label") or "").strip()
        if not label:
            continue
        pack_qty, pack_unit = parse_pack_from_basket_label(label)
        basket_lines.append(
            {
                "line_no": int(row["line_no"]),
                "basket_item_id": bid,
                "basket_label": label,
                "category": str(row.get("category") or ""),
                "match_group": str(row.get("match_group") or "packaged"),
                "line_role": str(row.get("line_role") or "default"),
                "pack_qty": pack_qty,
                "pack_unit": pack_unit,
            }
        )

    already_have_url: set[int] = set()
    if skip_existing:
        already_have_url = basket_ids_with_urls(conn, store_id)
        skipped_n = sum(1 for bl in basket_lines if bl["basket_item_id"] in already_have_url)
        basket_lines = [bl for bl in basket_lines if bl["basket_item_id"] not in already_have_url]
    else:
        skipped_n = 0

    stats = {"ok": 0, "pack_mismatch": 0, "missing": 0, "skipped": skipped_n}

    def emit(phase: str, **kwargs: Any) -> None:
        if progress_callback is None:
            return
        progress_callback(
            MatchProgress(
                store_label=store_label,
                phase=phase,  # type: ignore[arg-type]
                skipped=kwargs.pop("skipped", skipped_n),
                ok=kwargs.pop("ok", stats["ok"]),
                pack_mismatch=kwargs.pop("pack_mismatch", stats["pack_mismatch"]),
                missing=kwargs.pop("missing", stats["missing"]),
                **kwargs,
            )
        )

    emit("starting", lines_total=len(basket_lines), lines_completed=0)

    if not basket_lines:
        emit("done", lines_total=0, lines_completed=0)
        return stats

    model_name = model or os.environ.get("OPENAI_MODEL", OPENAI_DEFAULT_MODEL)
    client = openai_client()
    used_urls: dict[str, int] = {}
    lines_total = len(basket_lines)
    lines_completed = 0

    for bl in basket_lines:
        spec = _build_spec(bl, store_label=store_label, brand_name=brand_name)
        override_result = _try_override(index, spec, bl, brand_name, store_label)
        if override_result is not None:
            picked, status, pack_match, conf, reason = override_result
            _record_pick(
                conn,
                store_id=store_id,
                bl=bl,
                picked=picked,
                status=status,
                pack_match=pack_match,
                match_method="override",
                match_confidence=conf,
                match_reason=reason,
                error=None if picked else reason,
                index=index,
                stats=stats,
                dry_run=dry_run,
                used_urls=used_urls,
            )
            lines_completed += 1
            emit(
                "matching",
                lines_total=lines_total,
                lines_completed=lines_completed,
                line_no=int(bl["line_no"]),
                basket_label=str(bl["basket_label"]),
            )
            continue

        override = lookup_override(brand_name, store_label, int(bl["line_no"]))
        forbid = override.forbid_slugs if override else ()
        picked, status, pack_match, conf, reason = _resolve_line_match_v2(
            index,
            spec,
            bl,
            client=client,
            model=model_name,
            used_urls=set(used_urls.keys()),
            forbid_slugs=forbid,
        )

        if picked and not is_grocery_product_url(picked.get("url") or ""):
            picked = None
            status = "missing"
            reason = "Non-product URL"

        method = "heuristic" if status == "ok" and client is None else "gpt"
        if status == "ok" and reason and reason.startswith("Basket match"):
            method = "heuristic"
        if reason and reason.startswith(_NEAR_MATCH_REASON_PREFIX):
            method = "gpt_near_pick"

        _record_pick(
            conn,
            store_id=store_id,
            bl=bl,
            picked=picked,
            status=status,
            pack_match=pack_match,
            match_method=method,
            match_confidence=conf,
            match_reason=reason,
            error=None if picked else reason,
            index=index,
            stats=stats,
            dry_run=dry_run,
            used_urls=used_urls,
        )
        lines_completed += 1
        emit(
            "matching",
            lines_total=lines_total,
            lines_completed=lines_completed,
            line_no=int(bl["line_no"]),
            basket_label=str(bl["basket_label"]),
        )

    emit("done", lines_total=lines_total, lines_completed=lines_total)
    return stats


def match_all_stores(
    conn,
    *,
    store_ids: list[int] | None = None,
    model: str | None = None,
    batch_size: int = 8,
    dry_run: bool = False,
    skip_existing: bool = True,
    progress_callback: Callable[[MatchProgress], None] | None = None,
) -> list[dict[str, Any]]:
    stores = list_stores(conn)
    if store_ids is not None:
        want = {int(x) for x in store_ids}
        stores = [s for s in stores if int(s["store_id"]) in want]
    out: list[dict[str, Any]] = []
    for s in stores:
        label = str(s["store_label"])
        if not catalog_exists(label):
            out.append(
                {
                    "store_id": int(s["store_id"]),
                    "store_label": label,
                    "status": "skipped",
                    "error": "catalog missing",
                }
            )
            continue
        try:
            stats = match_store(
                conn,
                store_id=int(s["store_id"]),
                store_label=label,
                brand_name=str(s["brand_name"]),
                model=model,
                batch_size=batch_size,
                dry_run=dry_run,
                skip_existing=skip_existing,
                basket_item_ids=None,
                progress_callback=progress_callback,
            )
            out.append({"store_id": int(s["store_id"]), "store_label": label, "status": "ok", **stats})
        except Exception as e:  # noqa: BLE001
            out.append(
                {
                    "store_id": int(s["store_id"]),
                    "store_label": label,
                    "status": "error",
                    "error": str(e),
                }
            )
    return out


def match_selected(
    conn,
    selections: list[tuple[int, int]],
    *,
    model: str | None = None,
    batch_size: int = 8,
    dry_run: bool = False,
    skip_existing: bool = False,
    progress_callback: Callable[[MatchProgress], None] | None = None,
) -> dict[str, int]:
    totals: dict[str, object] = {
        "ok": 0,
        "pack_mismatch": 0,
        "missing": 0,
        "skipped": 0,
        "errors": 0,
        "error_messages": [],
    }
    if not selections:
        return totals

    stores_by_id = {int(s["store_id"]): s for s in list_stores(conn)}
    by_store: dict[int, list[int]] = {}
    for store_id, basket_item_id in selections:
        by_store.setdefault(int(store_id), []).append(int(basket_item_id))

    error_messages: list[str] = []
    for store_id, bids in by_store.items():
        store = stores_by_id.get(store_id)
        if store is None:
            totals["errors"] = int(totals["errors"]) + len(bids)
            error_messages.append(f"Store id {store_id}: not found")
            continue
        label = str(store["store_label"])
        brand = str(store["brand_name"])
        try:
            stats = match_store(
                conn,
                store_id=store_id,
                store_label=label,
                brand_name=brand,
                model=model,
                batch_size=batch_size,
                dry_run=dry_run,
                skip_existing=skip_existing,
                basket_item_ids=list(dict.fromkeys(bids)),
                progress_callback=progress_callback,
            )
            for key in ("ok", "pack_mismatch", "missing", "skipped"):
                totals[key] = int(totals[key]) + int(stats.get(key, 0))
        except Exception as e:  # noqa: BLE001
            totals["errors"] = int(totals["errors"]) + len(bids)
            error_messages.append(f"{label}: {e}")

    totals["error_messages"] = error_messages
    return totals


def match_identity_retries(
    conn,
    *,
    store_id: int,
    store_label: str,
    brand_name: str,
    basket_item_ids: list[int],
    model: str | None = None,
    batch_size: int = 8,
    dry_run: bool = False,
) -> dict[str, int]:
    return match_store(
        conn,
        store_id=store_id,
        store_label=store_label,
        brand_name=brand_name,
        model=model,
        batch_size=batch_size,
        dry_run=dry_run,
        skip_existing=False,
        basket_item_ids=basket_item_ids,
    )
