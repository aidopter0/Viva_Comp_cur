"""Basket-first catalog shortlist, role filters, and cheapest pick (v2)."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .basket_match_spec import (
    LINE_ROLE_DEFAULT,
    LINE_ROLE_OUTSIDE_BRAND,
    LINE_ROLE_OWN_BRAND,
    BasketMatchSpec,
)
from .catalog_match import (
    _compact_alnum,
    _norm,
    all_distinctive_tokens_hit,
    distinctive_name_tokens,
    name_similarity_parts,
    token_hits,
)
from .match_form_guards import passes_form_guard
from .match_groups import MATCH_GROUP_MEAT, MATCH_GROUP_PRODUCE
from .pack_normalize import compute_normalized_price, pack_match_tier, parse_pack_from_title

if TYPE_CHECKING:
    from .catalog_index import CatalogIndex

# Loose fresh items (fresh produce/fruit + fresh meat) are bought by arbitrary
# weight, so catalog pack size differs from the basket pack but stays comparable
# per kg/L. These are the unbranded lines; pack is normalized, not gated.
LOOSE_FRESH_GROUPS = frozenset({MATCH_GROUP_PRODUCE, MATCH_GROUP_MEAT})

# Packaged/processed lines (pickles, processed meat, etc.) keep exact-pack gating.
_PACKAGED_PACK_TIERS = frozenset({"multipack_exact", "exact"})
_PACKAGED_FALLBACK_TIERS = frozenset({"multipack_exact", "exact", "close"})
_LOOSE_PACK_TIERS = frozenset({"multipack_exact", "exact", "close", "different", "unknown"})


def is_loose_fresh(spec: BasketMatchSpec) -> bool:
    return str(getattr(spec, "match_group", "") or "").strip() in LOOSE_FRESH_GROUPS


def _name_tokens_simple(text: str) -> list[str]:
    return [t for t in _norm(text).split() if len(t) > 2]


def row_product_blob(row: dict[str, str]) -> str:
    """Product title + URL slug only (brand identity — not subcategory path)."""
    url = str(row.get("url") or "")
    slug = ""
    if "/product/" in url:
        slug = url.split("/product/", 1)[1].split("?", 1)[0]
    return _norm(f"{row.get('product_name', '')} {slug}")


def _brand_tokens_in_blob(brand: str, blob: str) -> bool:
    brand = str(brand or "").strip()
    if not brand:
        return True
    if all_distinctive_tokens_hit(brand, blob):
        return True
    brand_compact = _compact_alnum(brand)
    blob_compact = _compact_alnum(blob)
    if brand_compact and brand_compact in blob_compact:
        return True
    return token_hits(distinctive_name_tokens(brand) or _name_tokens_simple(brand), blob) >= 1

PACK_RANK = {
    "multipack_exact": 5,
    "exact": 4,
    "close": 2,
    "different": 1,
    "unknown": 0,
}


def row_price(row: dict[str, Any]) -> float | None:
    for key in ("discounted_price", "price"):
        val = row.get(key)
        try:
            f = float(val)
        except (TypeError, ValueError):
            continue
        if f > 0:
            return f
    return None


def passes_line_role(row: dict[str, str], spec: BasketMatchSpec) -> bool:
    role = str(spec.line_role or LINE_ROLE_DEFAULT).strip()
    brand = str(spec.store_brand_name or "").strip()
    if role == LINE_ROLE_DEFAULT or not brand:
        return True
    blob = row_product_blob(row)
    has_brand = _brand_tokens_in_blob(brand, blob)
    if role == LINE_ROLE_OWN_BRAND:
        return has_brand
    if role == LINE_ROLE_OUTSIDE_BRAND:
        return not has_brand
    return True


def pack_tier(row: dict[str, str], spec: BasketMatchSpec, *, tolerance: float = 0.08) -> str:
    return pack_match_tier(row["product_name"], spec.pack_qty, spec.pack_unit, tolerance=tolerance)


def row_passes_basket_gates(
    row: dict[str, str],
    spec: BasketMatchSpec,
    *,
    pack_tiers: frozenset[str],
) -> bool:
    if not spec.all_tokens_in_title(row["product_name"], str(row.get("url") or "")):
        return False
    tier = pack_tier(row, spec)
    if tier not in pack_tiers:
        return False
    if not passes_line_role(row, spec):
        return False
    if not passes_form_guard(row, spec):
        return False
    return True


def shortlist_rows(
    index: CatalogIndex,
    spec: BasketMatchSpec,
    *,
    exclude_urls: set[str] | None = None,
) -> list[dict[str, str]]:
    """All basket tokens + role + form. Exact pack for packaged; pack-agnostic for loose fresh."""
    tiers = _LOOSE_PACK_TIERS if is_loose_fresh(spec) else _PACKAGED_PACK_TIERS
    exclude_urls = exclude_urls or set()
    out: list[dict[str, str]] = []
    for row in index.rows:
        url = str(row.get("url") or "")
        if url and url in exclude_urls:
            continue
        if row_passes_basket_gates(row, spec, pack_tiers=tiers):
            out.append(row)
    return out


def fallback_pool_rows(
    index: CatalogIndex,
    spec: BasketMatchSpec,
    *,
    exclude_urls: set[str] | None = None,
) -> list[dict[str, str]]:
    """Same token/role/form gates; allow close pack tier (pack-agnostic for loose fresh)."""
    tiers = _LOOSE_PACK_TIERS if is_loose_fresh(spec) else _PACKAGED_FALLBACK_TIERS
    exclude_urls = exclude_urls or set()
    out: list[dict[str, str]] = []
    for row in index.rows:
        url = str(row.get("url") or "")
        if url and url in exclude_urls:
            continue
        if row_passes_basket_gates(row, spec, pack_tiers=tiers):
            out.append(row)
    return out


def score_fallback_row(row: dict[str, str], spec: BasketMatchSpec) -> float:
    tier = pack_tier(row, spec)
    pack_score = float(PACK_RANK.get(tier, 0))
    price = row_price(row)
    price_score = -price if price is not None else 0.0
    return pack_score * 1000.0 + price_score * 0.001


def top_percentile_rows(
    rows: list[dict[str, str]],
    spec: BasketMatchSpec,
    *,
    fraction: float = 0.10,
) -> list[dict[str, str]]:
    if not rows:
        return []
    ranked = sorted(rows, key=lambda r: score_fallback_row(r, spec), reverse=True)
    n = max(1, math.ceil(len(ranked) * fraction))
    return ranked[:n]


def cheapest_row(rows: list[dict[str, str]]) -> dict[str, str] | None:
    priced = [(row_price(r), r) for r in rows]
    priced = [(p, r) for p, r in priced if p is not None]
    if not priced:
        return rows[0] if rows else None
    priced.sort(key=lambda x: x[0])
    return priced[0][1]


def price_per_base_for_row(row: dict[str, str]) -> float | None:
    """Catalog price normalized per kg/L using the title pack.

    Loose produce/meat is typically priced per kg already, so when no pack can be
    parsed from the title we treat the shelf price as the per-base price.
    """
    price = row_price(row)
    if price is None:
        return None
    qty, unit = parse_pack_from_title(str(row.get("product_name") or ""))
    if qty and unit:
        _, _, ppb = compute_normalized_price(price, qty, unit)
        if ppb is not None and ppb > 0:
            return ppb
    return price


def cheapest_loose_row(rows: list[dict[str, str]]) -> dict[str, str] | None:
    """Pick the cheapest loose-fresh row by normalized price-per-base (per kg/L)."""
    priced = [(price_per_base_for_row(r), r) for r in rows]
    priced = [(p, r) for p, r in priced if p is not None]
    if not priced:
        return cheapest_row(rows)
    priced.sort(key=lambda x: x[0])
    return priced[0][1]


# Round 2 near-match retrieval: relax the all-token gate to a single distinctive
# token hit, keep packaged packs within +/-12% of the basket pack, and still run
# role + form guards so GPT only ever sees plausible same-need candidates.
_NEAR_PACK_TIERS = frozenset({"multipack_exact", "exact", "close"})


def _distinctive_or_all_tokens(spec: BasketMatchSpec) -> list[str]:
    """Core nouns of the basket label, falling back to all product tokens."""
    distinctive = distinctive_name_tokens(" ".join(spec.basket_tokens))
    if distinctive:
        return distinctive
    return list(spec.basket_tokens)


def near_match_pool_rows(
    index: CatalogIndex,
    spec: BasketMatchSpec,
    *,
    exclude_urls: set[str] | None = None,
    pack_tolerance: float = 0.12,
) -> list[dict[str, str]]:
    """Partial-token retrieval pool for Round 2: >=1 distinctive token + pack band + guards."""
    tokens = _distinctive_or_all_tokens(spec)
    if not tokens:
        return []
    exclude_urls = exclude_urls or set()
    loose = is_loose_fresh(spec)
    has_pack = bool(spec.pack_qty or spec.pack_unit)
    out: list[dict[str, str]] = []
    for row in index.rows:
        url = str(row.get("url") or "")
        if url and url in exclude_urls:
            continue
        blob = row_product_blob(row)
        if token_hits(tokens, blob) < 1:
            continue
        if not loose and has_pack:
            tier = pack_match_tier(
                row["product_name"], spec.pack_qty, spec.pack_unit, tolerance=pack_tolerance
            )
            if tier not in _NEAR_PACK_TIERS:
                continue
        if not passes_line_role(row, spec):
            continue
        if not passes_form_guard(row, spec):
            continue
        out.append(row)
    return out


def _row_intent_score(
    row: dict[str, str],
    spec: BasketMatchSpec,
    score_fn: Callable[..., float],
    *,
    pack_tolerance: float,
) -> float:
    try:
        return score_fn(row, spec, pack_tolerance=pack_tolerance)
    except TypeError:
        return score_fn(row, spec)


def score_near_match_row(
    row: dict[str, str], spec: BasketMatchSpec, *, pack_tolerance: float = 0.12
) -> float:
    blob = row_product_blob(row)
    name_score, _, _ = name_similarity_parts(spec.basket_label, row.get("product_name", ""), blob)
    tier = pack_tier(row, spec, tolerance=pack_tolerance)
    pack_bonus = 0.15 * (PACK_RANK.get(tier, 0) / 5.0)
    price = row_price(row)
    # Tie-break toward cheaper options at similar intent fit (price tracking goal).
    price_bonus = (-price * 1e-4) if price is not None else 0.0
    return name_score + pack_bonus + price_bonus


def order_candidates_for_gpt_pick(
    rows: list[dict[str, str]],
    spec: BasketMatchSpec,
    score_fn: Callable[..., float],
    *,
    pack_tolerance: float = 0.08,
) -> list[dict[str, str]]:
    """Best intent fit first; cheapest first among ties (GPT payload order)."""

    def sort_key(row: dict[str, str]) -> tuple[float, float]:
        score = _row_intent_score(row, spec, score_fn, pack_tolerance=pack_tolerance)
        price = row_price(row)
        price_key = price if price is not None else float("inf")
        return (-score, price_key)

    return sorted(rows, key=sort_key)


def cheapest_among_intent_peers(
    picked: dict[str, str],
    candidates: list[dict[str, str]],
    spec: BasketMatchSpec,
    *,
    score_fn: Callable[..., float],
    pack_tolerance: float = 0.08,
    score_slack: float = 0.13,
    loose: bool = False,
) -> dict[str, str]:
    """Return the cheapest row among intent-equivalent peers of the GPT pick in the pool.

    GPT validates customer intent; peers must share the same pack tier, pass form guards,
    sit within ``score_slack`` of the picked row's intent score, and retain comparable
    basket-token overlap so unrelated cheaper SKUs are not swapped in.
    """
    pool = list(candidates)
    if picked not in pool:
        pool.append(picked)
    if not pool:
        return picked

    if loose:
        peers = [row for row in pool if passes_form_guard(row, spec)]
        best = cheapest_loose_row(peers)
        return best or picked

    picked_score = _row_intent_score(picked, spec, score_fn, pack_tolerance=pack_tolerance)
    picked_tier = pack_tier(picked, spec, tolerance=pack_tolerance)
    dist = _distinctive_or_all_tokens(spec)
    picked_hits = token_hits(dist, row_product_blob(picked))
    peers: list[dict[str, str]] = []
    for row in pool:
        if not passes_form_guard(row, spec):
            continue
        if pack_tier(row, spec, tolerance=pack_tolerance) != picked_tier:
            continue
        score = _row_intent_score(row, spec, score_fn, pack_tolerance=pack_tolerance)
        if score < picked_score - score_slack:
            continue
        peer_hits = token_hits(dist, row_product_blob(row))
        if peer_hits < picked_hits:
            continue
        peers.append(row)

    best = cheapest_row(peers)
    return best or picked


def top_near_match_rows(
    rows: list[dict[str, str]],
    spec: BasketMatchSpec,
    *,
    limit: int = 20,
    pack_tolerance: float = 0.12,
) -> list[dict[str, str]]:
    if not rows:
        return []
    ranked = sorted(
        rows,
        key=lambda r: score_near_match_row(r, spec, pack_tolerance=pack_tolerance),
        reverse=True,
    )
    return ranked[:limit]
