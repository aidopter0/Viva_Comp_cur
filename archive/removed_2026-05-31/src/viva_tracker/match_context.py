"""Three-tier basket match context: generic product type, brand, then pack."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .catalog_match import (
    _compact_alnum,
    _norm,
    all_distinctive_tokens_hit,
    distinctive_name_tokens,
    name_similarity_parts,
    required_subtype_phrases_in_blob,
    token_hits,
)
from .pack_normalize import pack_match_tier, pack_matches_target


PACK_RANK = {
    "multipack_exact": 5,
    "exact": 3,
    "close": 2,
    "different": 1,
    "unknown": 0,
}


@dataclass(frozen=True)
class BasketMatchContext:
    line_no: int
    basket_item_id: int
    mapped_name: str
    generic_description: str
    brand_token: str
    pack_qty: str
    pack_unit: str
    category: str
    basket_label: str
    form_context: str
    grocery_chain_name: str = ""
    match_group: str = "packaged"

    @property
    def brand_required(self) -> bool:
        return bool(str(self.brand_token or "").strip())

    def effective_generic(self) -> str:
        generic = str(self.generic_description or "").strip()
        if generic:
            return generic
        mapped = str(self.mapped_name or "").strip()
        if not mapped:
            return ""
        brand = str(self.brand_token or "").strip()
        if brand:
            tokens = distinctive_name_tokens(mapped)
            brand_norm = _norm(brand)
            rest = [t for t in tokens if _norm(t) != brand_norm and brand_norm not in _norm(t)]
            if rest:
                return " ".join(rest)
        return mapped

    @classmethod
    def from_basket_line(cls, bl: dict[str, Any], *, grocery_chain_name: str = "") -> BasketMatchContext:
        parts = [
            bl.get("category"),
            bl.get("basket_label"),
            bl.get("mapped_name"),
            bl.get("generic_description"),
        ]
        form_context = " ".join(str(p or "").strip() for p in parts if str(p or "").strip())
        return cls(
            line_no=int(bl["line_no"]),
            basket_item_id=int(bl["basket_item_id"]),
            mapped_name=str(bl.get("mapped_name") or "").strip(),
            generic_description=str(bl.get("generic_description") or "").strip(),
            brand_token=str(bl.get("brand_token") or "").strip(),
            pack_qty=str(bl.get("pack_qty") or "").strip(),
            pack_unit=str(bl.get("pack_unit") or "").strip(),
            category=str(bl.get("category") or "").strip(),
            basket_label=str(bl.get("basket_label") or "").strip(),
            form_context=form_context,
            grocery_chain_name=str(grocery_chain_name or bl.get("grocery_chain_name") or "").strip(),
            match_group=str(bl.get("match_group") or "packaged").strip(),
        )


def row_blob(row: dict[str, str]) -> str:
    return _norm(f"{row.get('subcategory_path', '')} {row.get('product_name', '')}")


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
    return token_hits(distinctive_name_tokens(brand) or name_tokens_simple(brand), blob) >= 1


def brand_token_in_product(row: dict[str, str], ctx: BasketMatchContext) -> bool:
    if not ctx.brand_required:
        return True
    return _brand_tokens_in_blob(str(ctx.brand_token or "").strip(), row_product_blob(row))


def generic_tokens(ctx: BasketMatchContext) -> list[str]:
    text = ctx.effective_generic()
    tokens = distinctive_name_tokens(text)
    if tokens:
        return tokens
    return [t for t in _norm(text).split() if len(t) > 2]


def passes_generic_gate(catalog_blob: str, ctx: BasketMatchContext, *, min_hits: int = 1) -> bool:
    generic = ctx.effective_generic()
    if not generic:
        return bool(ctx.mapped_name.strip())
    if not required_subtype_phrases_in_blob(
        generic,
        ctx.basket_label,
        catalog_blob,
        mapped_name=ctx.mapped_name,
    ):
        return False
    if all_distinctive_tokens_hit(generic, catalog_blob):
        return True
    tokens = generic_tokens(ctx)
    if not tokens:
        return False
    strong_tokens = [t for t in tokens if len(t) >= 4]
    if strong_tokens and token_hits(strong_tokens, catalog_blob) < 1:
        return False
    hits = token_hits(tokens, catalog_blob)
    if hits >= max(min_hits, len(tokens) // 2 + (1 if len(tokens) <= 2 else 0)):
        return True
    _, sim_hits, _ = name_similarity_parts(generic, "", catalog_blob)
    return sim_hits >= min_hits


def passes_brand_gate(catalog_blob: str, ctx: BasketMatchContext) -> bool:
    """Legacy full-blob brand gate (subcategory + title). Prefer passes_brand_gate_product."""
    if not ctx.brand_required:
        return True
    brand = str(ctx.brand_token or "").strip()
    return _brand_tokens_in_blob(brand, catalog_blob)


def passes_brand_gate_product(row: dict[str, str], ctx: BasketMatchContext) -> bool:
    """Brand must appear in product title or URL slug."""
    if not ctx.brand_required:
        return True
    return brand_token_in_product(row, ctx)


def name_tokens_simple(text: str) -> list[str]:
    return [t for t in _norm(text).split() if len(t) > 2]


def generic_score(catalog_blob: str, title: str, ctx: BasketMatchContext) -> float:
    generic = ctx.effective_generic()
    if not generic:
        return 0.0
    score, _, _ = name_similarity_parts(generic, title, catalog_blob)
    return score


def brand_token_score(row: dict[str, str], ctx: BasketMatchContext) -> float:
    """Brand-token similarity in product title/slug only (not mapped_name)."""
    if not ctx.brand_required:
        return 1.0
    blob = row_product_blob(row)
    title = row.get("product_name") or ""
    brand = str(ctx.brand_token or "").strip()
    score, _, _ = name_similarity_parts(brand, title, blob)
    return score


def pack_rank_for_row(row: dict[str, str], ctx: BasketMatchContext) -> int:
    tier = pack_match_tier(row["product_name"], ctx.pack_qty, ctx.pack_unit)
    return PACK_RANK.get(tier, 0)


def brand_score(catalog_blob: str, title: str, ctx: BasketMatchContext) -> float:
    if not ctx.brand_required:
        return 1.0
    brand = str(ctx.brand_token or "").strip()
    score, _, _ = name_similarity_parts(brand, title, catalog_blob)
    mapped_score, _, _ = name_similarity_parts(ctx.mapped_name, title, catalog_blob)
    return max(score, mapped_score)


def passes_three_tier_gates(row: dict[str, str], ctx: BasketMatchContext) -> bool:
    from .basket_match_spec import BasketMatchSpec
    from .match_form_guards import passes_form_guard

    spec = BasketMatchSpec.from_basket_row(
        line_no=ctx.line_no,
        basket_item_id=ctx.basket_item_id,
        basket_label=ctx.basket_label or ctx.mapped_name,
        category=ctx.category,
        match_group=ctx.match_group,
        store_brand_name=ctx.grocery_chain_name,
    )
    return passes_form_guard(row, spec)


def rank_rows_by_pack(
    rows: list[dict[str, str]], ctx: BasketMatchContext
) -> list[tuple[int, dict[str, str], str]]:
    ranked: list[tuple[int, dict[str, str], str]] = []
    for row in rows:
        pm = pack_matches_target(row["product_name"], ctx.pack_qty, ctx.pack_unit)
        ranked.append((PACK_RANK.get(pm, 0), row, pm))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return ranked
