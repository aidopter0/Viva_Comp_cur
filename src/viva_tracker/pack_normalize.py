"""Normalize basket pack sizes and compute comparable unit prices."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class NormalizedPack:
    base_unit: str  # kg | L | item
    base_qty: float
    display_unit: str  # price_per_kg | price_per_L | price_per_item


_WEIGHT_TO_KG = {
    "g": 0.001,
    "gram": 0.001,
    "grams": 0.001,
    "kg": 1.0,
    "kilogram": 1.0,
    "kilograms": 1.0,
}

_VOLUME_TO_L = {
    "ml": 0.001,
    "milliliter": 0.001,
    "millilitre": 0.001,
    "l": 1.0,
    "liter": 1.0,
    "litre": 1.0,
    "liters": 1.0,
    "litres": 1.0,
    "gal": 3.78541,
    "gallon": 3.78541,
    "gallons": 3.78541,
}

_COUNT_UNITS = {"pc", "pcs", "piece", "pieces", "pk", "pack", "packs", "unit", "units", "each", "s"}


def _clean_unit(unit: str) -> str:
    return re.sub(r"[^a-z]", "", (unit or "").strip().lower())


def _display_unit(unit: str) -> str:
    u = _clean_unit(unit)
    if u in {"g", "gram", "grams", "gm", "gms"}:
        return "g"
    if u in {"kg", "kilogram", "kilograms"}:
        return "kg"
    if u in {"ml", "milliliter", "millilitre"}:
        return "ml"
    if u in {"l", "liter", "litre", "liters", "litres"}:
        return "L"
    if u in {"gal", "gallon", "gallons"}:
        return "gal"
    if u in _COUNT_UNITS:
        return "pc"
    return (unit or "").strip()


@dataclass(frozen=True)
class TitlePackInfo:
    display: str
    total_qty: str
    total_unit: str
    pack_count: int | None = None
    unit_qty: str | None = None
    unit: str | None = None


_WEIGHT_VOLUME_UNITS = frozenset(
    {"g", "gram", "grams", "gm", "gms", "grm", "kg", "kilogram", "kilograms",
     "ml", "milliliter", "millilitre", "l", "liter", "litre", "liters", "litres",
     "gal", "gallon", "gallons"}
)


def _is_weight_volume_unit(unit: str) -> bool:
    return _clean_unit(unit) in _WEIGHT_VOLUME_UNITS or _display_unit(unit) in {"g", "kg", "ml", "L", "gal"}


def parse_title_pack_info(title: str) -> TitlePackInfo | None:
    """
    Parse Talabat product title packs, including bundles.

    Examples:
      - "400g 2s" -> 2 x 400 g (800 g total)
      - "4x1L" -> 4 x 1 L
      - "30 Pieces" -> 30 pc
    """
    t = str(title or "").strip()
    if not t:
        return None

    multi = re.search(
        r"(\d+)\s*x\s*(\d+(?:\.\d+)?)\s*(kg|g|gm|gms|gram|grams|ml|l|litre|liter|liters|litres|gal|gallon|gallons|pc|pcs|pk|pack)\b",
        t,
        flags=re.IGNORECASE,
    )
    if multi:
        count = int(multi.group(1))
        unit_qty = multi.group(2)
        unit = _display_unit(multi.group(3))
        total = float(unit_qty) * count
        total_qty = str(int(total)) if total.is_integer() else str(total)
        return TitlePackInfo(
            display=f"{count} x {unit_qty} {unit}",
            total_qty=total_qty,
            total_unit=unit,
            pack_count=count,
            unit_qty=unit_qty,
            unit=unit,
        )

    bundle = re.search(
        r"(\d+(?:\.\d+)?)\s*(kg|g|gm|gms|gram|grams|ml|l|litre|liter|liters|litres|gal|gallon|gallons)\s*(\d+)\s*s\b",
        t,
        flags=re.IGNORECASE,
    )
    if bundle:
        unit_qty = bundle.group(1)
        unit = _display_unit(bundle.group(2))
        count = int(bundle.group(3))
        total = float(unit_qty) * count
        total_qty = str(int(total)) if total.is_integer() else str(total)
        return TitlePackInfo(
            display=f"{count} x {unit_qty} {unit}",
            total_qty=total_qty,
            total_unit=unit,
            pack_count=count,
            unit_qty=unit_qty,
            unit=unit,
        )

    pieces = re.search(r"(\d+(?:\.\d+)?)\s*(?:pieces?|pcs)\b", t, flags=re.IGNORECASE)
    if pieces:
        qty = pieces.group(1)
        total_qty = str(int(float(qty))) if float(qty).is_integer() else qty
        return TitlePackInfo(
            display=f"{total_qty} pc",
            total_qty=total_qty,
            total_unit="pc",
        )

    single = re.search(
        r"(\d+(?:\.\d+)?)\s*(kg|g|gm|gms|gram|grams|ml|l|litre|liter|liters|litres|gal|gallon|gallons|pc|pcs|pk|pack|grm)\b",
        t,
        flags=re.IGNORECASE,
    )
    if single:
        qty = single.group(1)
        raw_unit = single.group(2)
        unit = "g" if _clean_unit(raw_unit) == "grm" else _display_unit(raw_unit)
        return TitlePackInfo(
            display=f"{qty} {unit}",
            total_qty=qty,
            total_unit=unit,
        )

    return None


def format_title_pack_display(title: str) -> str:
    info = parse_title_pack_info(title)
    return info.display if info else ""


def title_pack_normalized(title: str) -> NormalizedPack | None:
    info = parse_title_pack_info(title)
    if info is None:
        return None
    return parse_pack(info.total_qty, info.total_unit)


def split_pack_fields(pack_qty: str, pack_unit: str) -> tuple[str, str]:
    """Split basket pack fields, including combined values like ``500g`` or ``2kg``."""
    qty = str(pack_qty or "").strip().replace(",", "")
    unit = str(pack_unit or "").strip()
    if unit:
        combined = f"{qty} {unit}".strip()
        bundle = parse_title_pack_info(combined)
        if bundle is not None:
            return bundle.total_qty, bundle.total_unit
        return qty, unit
    if not qty:
        return "", ""

    combined = re.match(
        r"^(\d+(?:\.\d+)?)\s*(kg|g|gm|gms|gram|grams|ml|l|litre|liter|liters|litres|gal|gallon|gallons|pc|pcs|pk|pack|grm)\s*$",
        qty,
        flags=re.IGNORECASE,
    )
    if combined:
        raw_unit = combined.group(2)
        return combined.group(1), "g" if _clean_unit(raw_unit) == "grm" else _display_unit(raw_unit)

    bundle = parse_title_pack_info(qty)
    if bundle is not None:
        return bundle.total_qty, bundle.total_unit

    return qty, unit


def parse_basket_multipack(pack_qty: str, pack_unit: str) -> TitlePackInfo | None:
    """Parse basket pack fields into a multipack spec when NxM + unit is present."""
    qty = str(pack_qty or "").strip().replace(",", "")
    unit = str(pack_unit or "").strip()
    if not qty:
        return None
    combined = f"{qty} {unit}".strip() if unit else qty
    info = parse_title_pack_info(combined)
    if info is None or info.pack_count is None:
        return None
    if not _is_weight_volume_unit(info.unit or info.total_unit):
        return None
    return info


def multipacks_equivalent(a: TitlePackInfo, b: TitlePackInfo) -> bool:
    """True when two multipacks match exactly, including swapped count×unit_qty (20×30 == 30×20)."""
    if a.pack_count is None or b.pack_count is None or a.unit_qty is None or b.unit_qty is None:
        return False
    a_unit = _display_unit(a.unit or a.total_unit or "")
    b_unit = _display_unit(b.unit or b.total_unit or "")
    if a_unit != b_unit:
        return False
    try:
        a_count, a_uq = int(a.pack_count), float(a.unit_qty)
        b_count, b_uq = int(b.pack_count), float(b.unit_qty)
    except (TypeError, ValueError):
        return False
    if a_count == b_count and a_uq == b_uq:
        return True
    if a_count == int(b_uq) and a_uq == b_count:
        return True
    return False


def multipack_pattern_compatible(
    pack_qty: str,
    pack_unit: str,
    catalog_title: str,
) -> bool:
    """
    When basket specifies a weight/volume multipack (e.g. 20x30 g), reject catalog rows
    whose title shows a different explicit multipack (e.g. 15x40 g). Plain total-weight
    titles (600g only) remain allowed.
    """
    basket_mp = parse_basket_multipack(pack_qty, pack_unit)
    if basket_mp is None:
        return True
    title_mp = parse_title_pack_info(catalog_title)
    if title_mp is None or title_mp.pack_count is None:
        return True
    if not _is_weight_volume_unit(title_mp.unit or title_mp.total_unit):
        return True
    return multipacks_equivalent(basket_mp, title_mp)


def parse_pack(pack_qty: str, pack_unit: str) -> NormalizedPack | None:
    qty_raw, unit_raw = split_pack_fields(pack_qty, pack_unit)
    unit = _clean_unit(unit_raw)
    if not qty_raw:
        return None
    try:
        qty = float(qty_raw)
    except ValueError:
        return None
    if qty <= 0:
        return None

    if unit in _WEIGHT_TO_KG:
        return NormalizedPack("kg", qty * _WEIGHT_TO_KG[unit], "price_per_kg")
    if unit in _VOLUME_TO_L:
        return NormalizedPack("L", qty * _VOLUME_TO_L[unit], "price_per_L")
    if unit in _COUNT_UNITS or not unit:
        return NormalizedPack("item", qty, "price_per_item")
    return None


def resolve_price_normalization_pack(
    *,
    pack_qty: str,
    pack_unit: str,
    pack_text: str = "",
    catalog_title: str = "",
) -> tuple[str, str]:
    """
    Choose pack fields for unit-price normalization.

    Prefer the matched catalog title when it parses; otherwise basket mapping,
    then combined pack text (handles values like ``6 x 1.5`` + ``L``).
    """
    cq, cu = parse_pack_from_title(catalog_title)
    if cq and parse_pack(cq, cu) is not None:
        return cq, cu

    qty, unit = split_pack_fields(pack_qty, pack_unit)
    if parse_pack(qty, unit) is not None:
        return qty, unit

    combined = str(pack_text or "").strip() or f"{pack_qty} {pack_unit}".strip()
    bundle = parse_title_pack_info(combined)
    if bundle is not None and parse_pack(bundle.total_qty, bundle.total_unit) is not None:
        return bundle.total_qty, bundle.total_unit

    return qty, unit


def compute_normalized_price(
    shelf_price: float | None,
    pack_qty: str,
    pack_unit: str,
) -> tuple[str | None, float | None, float | None]:
    """
    Return (base_unit, base_qty, price_per_base) for a shelf price and pack size.

    ``price_per_base`` is ``shelf_price / base_qty`` (e.g. AED per kg or per L).
    Values are returned at full precision; round for display via ``presentation_format``.
    """
    if shelf_price is None:
        return None, None, None
    norm = parse_pack(pack_qty, pack_unit)
    if norm is None or norm.base_qty <= 0:
        return None, None, None
    return norm.base_unit, norm.base_qty, float(shelf_price) / norm.base_qty


def parse_pack_from_title(title: str) -> tuple[str, str]:
    """Best-effort pack extraction from a Talabat product title (total pack size)."""
    info = parse_title_pack_info(title)
    if info is None:
        return "", ""
    return info.total_qty, info.total_unit


def pack_matches_target(
    catalog_title: str,
    target_qty: str,
    target_unit: str,
    *,
    tolerance: float = 0.08,
) -> str:
    """
    Compare catalog title pack to basket target pack.
    Returns: exact | close | different | unknown
    """
    target = parse_pack(target_qty, target_unit)
    catalog = title_pack_normalized(catalog_title)
    if target is None or catalog is None:
        return "unknown"
    if target.base_unit != catalog.base_unit:
        return "different"
    if target.base_qty <= 0:
        return "unknown"
    rel = abs(catalog.base_qty - target.base_qty) / target.base_qty
    if rel <= 0.01:
        return "exact"
    if rel <= tolerance:
        return "close"
    return "different"


def pack_match_tier(
    catalog_title: str,
    target_qty: str,
    target_unit: str,
    *,
    tolerance: float = 0.08,
) -> str:
    """
    Tiered pack comparison for ranking.

    Returns: multipack_exact | exact | close | different | unknown
    """
    basket_mp = parse_basket_multipack(target_qty, target_unit)
    title_mp = parse_title_pack_info(catalog_title)

    if basket_mp is not None and title_mp is not None and title_mp.pack_count is not None:
        if _is_weight_volume_unit(title_mp.unit or title_mp.total_unit):
            if multipacks_equivalent(basket_mp, title_mp):
                return "multipack_exact"

    return pack_matches_target(
        catalog_title, target_qty, target_unit, tolerance=tolerance
    )
