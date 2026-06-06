"""Category/form guards for basket-first matching (v2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .catalog_match import _compact_alnum, _norm, product_form_compatible
from .match_groups import MATCH_GROUP_MEAT, MATCH_GROUP_PRODUCE
from .pack_normalize import multipack_pattern_compatible

if TYPE_CHECKING:
    from .basket_match_spec import BasketMatchSpec

_FRESH_PRODUCE_FORBID = (
    "drink",
    "juice",
    "nectar",
    "squash",
    "candy",
    "sweet",
    "bonbon",
    "gel",
    "cleaning",
    "cleaner",
    "detergent",
    "cake",
    "biscuit",
    "cookie",
    "chocolate",
    "yoghurt",
    "yogurt",
    "milk",
    "baby food",
    "puree",
    "jam",
    "jelly",
    "sauce",
    "ketchup",
    "pickle",
    "canned",
    "dried",
    "powder",
    "flavoured",
    "flavored",
    "carbonated",
    "soda",
    "cola",
    "snack",
    "chips",
    "crisp",
    "frozen",
    "soap",
    "shampoo",
    "candle",
    "laban",
    "labneh",
    "smoothie",
    "tea",
    "flavour",
    "scrub",
    "lotion",
    "scent",
    "garbage",
    "gallon",
    "cosmetic",
    "perfume",
    "shower",
    "wipes",
)

_FRESH_MEAT_FORBID = (
    "jerky",
    "snack",
    "nugget",
    "sausage",
    "burger",
    "bacon",
    "salami",
    "canned",
    "corned",
    "hot dog",
    "frankfurter",
    "luncheon",
    "spread",
    "paste",
    "stock",
    "broth",
    "bouillon",
    "pie",
    "samosa",
    "spring roll",
)

_NON_PRODUCE_DEPARTMENTS = (
    "frozen",
    "canned",
    "jarred",
    "dried",
    "bakery",
    "dairy",
    "beverage",
    "snack",
    "chocolate",
    "personal care",
    "hair",
    "disposable",
    "baby",
    "cleaning",
    "laundry",
    "pet",
    "ice cream",
    "coffee",
    "tea",
    "breakfast",
    "condiment",
    "health",
    "household",
    "deli",
)


def _basket_is_fresh_meat(basket_blob: str) -> bool:
    return any(
        t in basket_blob
        for t in ("mince", "ground", "chicken", "beef", "lamb", "breast", "whole", "tender")
    )


def _fresh_produce_department_ok(row: dict[str, str]) -> bool:
    dept_hint = _norm(str(row.get("category_hint") or ""))
    sub = _norm(str(row.get("subcategory_path") or ""))
    dept = f"{dept_hint} {sub}"
    if not any(t in dept for t in ("fruit", "veg", "produce", "herb")):
        return False
    if any(t in dept_hint for t in _NON_PRODUCE_DEPARTMENTS):
        return False
    return True


def _apply_line_produce_rules(
    *,
    basket_blob: str,
    cat_blob: str,
    basket_label: str,
    category: str,
    line_no: int,
    match_group: str = "",
) -> bool:
    cat_upper = category.upper()
    label = _norm(basket_label)
    group = str(match_group or "").strip()

    if group == MATCH_GROUP_PRODUCE or "FRESH FRUIT" in cat_upper or "FRESH VEGETABLE" in cat_upper:
        for bad in _FRESH_PRODUCE_FORBID:
            if bad in cat_blob:
                return False

    if group == MATCH_GROUP_MEAT and _basket_is_fresh_meat(basket_blob):
        for bad in _FRESH_MEAT_FORBID:
            if bad in cat_blob:
                return False

    if line_no == 24 or ("strawberry" in label and "FRESH FRUIT" in cat_upper):
        if "strawberry" not in cat_blob:
            return False
        for bad in ("donut", "croissant", "cream", "yogurt", "jam", "jelly", "candy", "milkshake", "filling"):
            if bad in cat_blob:
                return False

    if line_no == 19 or ("eggs" in label and "EGGS" in cat_upper):
        if "eggplant" in cat_blob:
            return False
        if "egg" not in cat_blob and "eggs" not in cat_blob:
            return False
        for bad in (
            "laban",
            "labneh",
            "drink",
            "milk",
            "juice",
            "yoghurt",
            "yogurt",
            "cheese",
            "butter",
            "cream",
            "powder",
        ):
            if bad in cat_blob:
                return False

    if line_no == 17 or ("sugar" in label and "granulated" in label):
        if "sugar" not in cat_blob:
            return False
        for bad in ("sprite", "coke", "pepsi", "soft drink", "zero sugar", "soda", "cola"):
            if bad in cat_blob:
                return False

    if line_no == 14 or ("sunflower" in label and "oil" in label):
        if "sunflower" not in cat_blob or "oil" not in cat_blob:
            return False

    if line_no == 4 or "sweet corn" in label:
        if "corn" not in cat_blob:
            return False

    if line_no == 35 or ("onion" in label and "pink" in label):
        if "onion" not in cat_blob:
            return False
        for bad in ("ring", "cracker", "powder", "dip", "cream", "snack"):
            if bad in cat_blob:
                return False

    if line_no == 34 or ("cabbage" in label and "white" in label):
        if "cabbage" not in cat_blob:
            return False
        for bad in ("grape", "cherry tomato"):
            if bad in cat_blob:
                return False
        if "tomato" in cat_blob and "cabbage" not in cat_blob:
            return False

    if line_no == 36 or ("garlic" in label and "pure" in label):
        if "garlic" not in cat_blob:
            return False
        if "cheese" in cat_blob and not cat_blob.strip().startswith("garlic"):
            return False

    if line_no == 39 or label.endswith("tomato 1kg"):
        if "tomato" not in cat_blob:
            return False
        if "cherry" in cat_blob and "1kg" in label:
            return False

    if line_no == 9 or ("water" in label and "1.5" in label):
        if "water" not in cat_blob:
            return False
        for bad in ("tonic", "sparkling", "soda", "flavoured", "flavored", "jelly", "cola", "juice"):
            if bad in cat_blob:
                return False

    if line_no == 41 or "facial tissue" in label:
        if "tissue" not in cat_blob and "tissues" not in cat_blob:
            return False
        for bad in ("toilet", "kitchen roll", "wet wipe"):
            if bad in cat_blob:
                return False

    if line_no == 3 or ("nuts" in label and "bar" in label):
        if "bar" not in cat_blob:
            return False
        words = set(cat_blob.split())
        nut_ok = (
            any(x in cat_blob for x in ("nuts", "almond", "peanut", "cashew", "walnut", "hazelnut", "coconut"))
            or "nut" in words
        )
        if not nut_ok:
            return False
        for bad in ("chocolate", "caramel", "snickers", "kinder", "galaxy", "soap", "shampoo"):
            if bad in cat_blob:
                return False

    if line_no == 33 or ("cucumber" in label and "FRESH VEGETABLE" in cat_upper):
        if "cucumber" not in cat_blob:
            return False
        if "pickle" in cat_blob or "pickled" in cat_blob:
            return False

    if line_no == 38 or (label.strip() == "carrot 1kg" or label.endswith("carrot 1kg")):
        if "carrot" not in cat_blob:
            return False
        for bad in ("peas", "mixed", "cake", "juice", "frozen"):
            if bad in cat_blob:
                return False

    if line_no == 29 or ("orange juice" in label):
        if "orange" not in cat_blob or "juice" not in cat_blob:
            return False
        for bad in ("concentrate", "nectar", "drink", "squash"):
            if bad in cat_blob:
                return False

    if line_no == 1 or "arabic bread" in label or "khubz" in label:
        if "bread" not in cat_blob and "khubz" not in cat_blob and "arabic" not in cat_blob:
            return False
        for bad in ("pizza", "base", "masala", "powder"):
            if bad in cat_blob:
                return False

    if line_no == 2 or "sliced bread" in label or "sandwich bread" in label:
        if "bread" not in cat_blob:
            return False
        for bad in ("pizza", "base"):
            if bad in cat_blob:
                return False
        wants_white = (
            line_no == 2
            or ("white" in label and "bread" in label)
            or ("white" in basket_blob and "bread" in basket_blob)
        )
        if wants_white and "arabic" not in label and "khubz" not in label:
            if "white" not in cat_blob:
                return False
            if "milk" in cat_blob:
                return False

    if line_no == 40 or ("coffee" in label and "3" in label):
        if "coffee" not in cat_blob:
            return False
        cat_compact = _compact_alnum(cat_blob)
        has_3in1 = (
            "3 in 1" in cat_blob
            or "3-in-1" in cat_blob
            or "3in1" in cat_compact
            or "mix" in cat_blob
        )
        if not has_3in1:
            return False
        if "instant" in cat_blob and not has_3in1:
            return False

    if line_no == 5 or ("mozzarella" in label and "shredded" in label):
        if "mozzarella" not in cat_blob:
            return False
        if "shredded" in label and "shredded" not in cat_blob and "grated" not in cat_blob:
            return False

    return True


def passes_form_guard(row: dict[str, str], spec: BasketMatchSpec) -> bool:
    """Form/subtype guards using basket_label + category only."""
    group = str(spec.match_group or "").strip()
    cat_upper = spec.category.upper()
    if group == MATCH_GROUP_PRODUCE or "FRESH FRUIT" in cat_upper or "FRESH VEGETABLE" in cat_upper:
        if not _fresh_produce_department_ok(row):
            return False
    if not product_form_compatible(
        spec.form_context(),
        row["product_name"],
        category=spec.category,
        subcategory_path=row["subcategory_path"],
        catalog_url=row.get("url") or "",
        basket_label=spec.basket_label,
        line_no=spec.line_no,
    ):
        return False
    if not multipack_pattern_compatible(spec.pack_qty, spec.pack_unit, row["product_name"]):
        return False
    basket_blob = _norm(f"{spec.form_context()} {spec.category} {spec.basket_label}")
    cat_blob = _norm(f"{row.get('subcategory_path', '')} {row['product_name']} {row.get('url', '')}")
    return _apply_line_produce_rules(
        basket_blob=basket_blob,
        cat_blob=cat_blob,
        basket_label=spec.basket_label,
        category=spec.category,
        line_no=spec.line_no,
        match_group=group,
    )
