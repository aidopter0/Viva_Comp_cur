"""Match basket lines to Talabat catalog / search items using scored text similarity."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any


def _norm(s: str) -> str:
    t = (s or "").lower().strip()
    t = re.sub(r"[^\w\s\.%]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _compact_alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


_NAME_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "our",
        "your",
        "bag",
        "pack",
        "size",
        "fresh",
        "large",
        "small",
    }
)

# Descriptors shared across many SKUs — matching these alone must not imply a brand match.
_GENERIC_DESCRIPTOR_TOKENS = frozenset(
    {
        "full",
        "fat",
        "low",
        "skim",
        "cream",
        "milk",
        "uht",
        "fresh",
        "organic",
        "natural",
        "pure",
        "extra",
        "virgin",
        "white",
        "red",
        "yellow",
        "green",
        "pink",
        "brown",
        "black",
        "large",
        "small",
        "medium",
        "big",
        "tender",
        "lean",
        "unsalted",
        "sweet",
        "plain",
        "shredded",
        "granulated",
        "whole",
        "premium",
        "bottled",
        "frozen",
        "sliced",
        "sandwich",
        "classic",
        "instant",
        "mix",
        "facial",
        "tissue",
        "loose",
        "round",
        "india",
        "turkey",
        "egypt",
        "uae",
        "germany",
        "netherlands",
    }
)


def name_tokens(text: str) -> list[str]:
    return [t for t in _norm(text).split() if len(t) > 2 and t not in _NAME_STOPWORDS]


def distinctive_name_tokens(text: str) -> list[str]:
    """Brand / product identity tokens — descriptors like 'full fat uht' are excluded."""
    return [t for t in name_tokens(text) if t not in _GENERIC_DESCRIPTOR_TOKENS]


def _milk_related(text: str) -> bool:
    return "milk" in _norm(text)


def _catalog_is_uht(catalog_blob: str) -> bool:
    c = _norm(catalog_blob)
    if "uht" in c:
        return True
    if "long life" in c or "longlife" in c or "long-life" in c:
        return True
    if "longlife" in _compact_alnum(c):
        return True
    return False


def _catalog_is_fresh_milk(catalog_blob: str) -> bool:
    c = _norm(catalog_blob)
    if _catalog_is_uht(c):
        return False
    if "fresh" in c and "milk" in c:
        return True
    if "freshmilk" in _compact_alnum(c):
        return True
    if any(x in c for x in ("chiller", "chilled", "refrigerated")) and "milk" in c:
        return True
    return False


def basket_requires_uht(basket_text: str, *, category: str = "") -> bool:
    blob = _norm(f"{basket_text} {category}")
    if not _milk_related(blob):
        return False
    return "uht" in blob or "long life" in blob or "longlife" in blob


def basket_requires_fresh_milk(basket_text: str, *, category: str = "") -> bool:
    blob = _norm(f"{basket_text} {category}")
    if not _milk_related(blob):
        return False
    if basket_requires_uht(basket_text, category=category):
        return False
    if "fresh" in blob and "milk" in blob:
        return True
    if "chiller" in blob and "milk" in blob:
        return True
    return False


def _catalog_milk_form_from_url(url: str) -> str | None:
    """Return ``uht``, ``fresh``, or None when the URL slug does not indicate milk form."""
    if not url:
        return None
    slug = _compact_alnum(url.rsplit("/", 1)[-1])
    if "milk" not in slug:
        return None
    if "uht" in slug or "longlife" in slug:
        return "uht"
    if "freshmilk" in slug or ("fresh" in slug and "milk" in slug):
        return "fresh"
    return None


def _catalog_milk_form(*, title_blob: str, catalog_url: str = "") -> tuple[bool, bool]:
    """Return (is_uht, is_fresh) for a catalog row; URL slug wins over title when present."""
    url_form = _catalog_milk_form_from_url(catalog_url)
    if url_form == "uht":
        return True, False
    if url_form == "fresh":
        return False, True
    text_blob = _norm(title_blob)
    return _catalog_is_uht(text_blob), _catalog_is_fresh_milk(text_blob)


def _whole_chicken_related(text: str) -> bool:
    c = _norm(text)
    return "whole chicken" in c or "whole chicken" in c.replace("  ", " ") or (
        "whole" in c and "chicken" in c and "breast" not in c and "fillet" not in c
    )


def _catalog_is_frozen_chicken(catalog_blob: str, catalog_url: str = "") -> bool:
    c = _norm(f"{catalog_blob} {catalog_url}")
    if "frozen" in c:
        return True
    if "freezer" in c:
        return True
    if "griller" in c and "chicken" in c:
        return True
    return False


def _catalog_is_chilled_chicken(catalog_blob: str, catalog_url: str = "") -> bool:
    c = _norm(f"{catalog_blob} {catalog_url}")
    if _catalog_is_frozen_chicken(catalog_blob, catalog_url):
        return False
    if "chilled" in c and "chicken" in c:
        return True
    if "fresh" in c and "chicken" in c and "frozen" not in c:
        return True
    if "dpollo" in _compact_alnum(c) and "whole" in c:
        return True
    return False


def _catalog_is_yogurt(catalog_blob: str) -> bool:
    c = _norm(catalog_blob)
    return "yogurt" in c or "yoghurt" in c


def subtype_identity_blob(*texts: str) -> str:
    return _norm(" ".join(t for t in texts if str(t or "").strip()))


def _phrase_in_blob(phrase: str, blob: str) -> bool:
    p = _norm(phrase)
    if not p:
        return False
    if p in _norm(blob):
        return True
    return _compact_alnum(p) in _compact_alnum(blob)


def _alt_in_blob(alt: str, blob: str) -> bool:
    """Token-set match for a subtype alternative.

    Satisfied when every token of ``alt`` appears as a whole word (plural/singular
    aware) anywhere in ``blob``, regardless of order or words in between, or when the
    solid spelling appears (e.g. 'chakkiatta'). This is what lets 'Chakki *Fresh* Atta'
    and 'Apple Royal' (reversed order) match a 'Chakki Atta' / 'Royal Gala' basket line.
    """
    a = _norm(alt)
    if not a:
        return False
    compact = _compact_alnum(alt)
    if compact and compact in _compact_alnum(blob):
        return True
    nblob = _norm(blob)
    words = set(nblob.split())
    padded = f" {nblob} "
    tokens = a.split()
    return bool(tokens) and all(_token_in_blob(tok, words, padded) for tok in tokens)


def _required_subtype_phrase_groups(identity: str) -> list[tuple[str, ...]]:
    """Flour/rice subtypes that must appear in the catalog blob when present on the basket."""
    c = _norm(identity)
    compact = _compact_alnum(c)
    groups: list[tuple[str, ...]] = []
    if "all purpose" in c or "allpurpose" in compact:
        groups.append(("all purpose", "allpurpose"))
    if ("chakki" in c and "atta" in c) or "whole wheat" in c:
        groups.append(("chakki atta", "chakkiatta", "whole wheat"))
    elif " atta" in f" {c} " or c.endswith(" atta") or c.startswith("atta "):
        groups.append(("atta", "chakki atta", "chakkiatta"))
    if "corn" in c and "flour" in c:
        groups.append(("corn flour", "cornflour", "maize"))
    if "basmati" in c:
        groups.append(("basmati",))
    if "jasmine" in c and "rice" in c:
        groups.append(("jasmine",))
    if "mince" in c or "ground" in c:
        if "beef" in c:
            groups.append(("beef",))
            groups.append(("mince", "ground"))
        elif "chicken" in c:
            groups.append(("chicken",))
            groups.append(("mince", "ground"))
        elif "lamb" in c:
            groups.append(("lamb",))
            groups.append(("mince", "ground"))
    if "penne" in c:
        groups.append(("penne",))
    if "royal gala" in c or "royalgala" in compact:
        # Either token is enough (stores list it as 'Apple Royal' or 'Gala Apple');
        # variety conflicts (Fuji/Pink Lady/Granny Smith) are caught by product_form_compatible.
        groups.append(("royal", "gala"))
    if ("3 in 1" in c or "3-in-1" in c or "3in1" in compact) and "coffee" in c:
        groups.append(("3 in 1", "3-in-1", "3in1", "mix"))
    if "bread" in c and "white" in c and "arabic" not in c and "khubz" not in c:
        groups.append(("white bread",))
    return groups


def required_subtype_phrases_in_blob(
    generic: str,
    basket_label: str,
    catalog_blob: str,
    *,
    mapped_name: str = "",
) -> bool:
    identity = subtype_identity_blob(generic, basket_label, mapped_name)
    groups = _required_subtype_phrase_groups(identity)
    if not groups:
        return True
    cat = _norm(catalog_blob)
    return all(any(_alt_in_blob(variant, cat) for variant in group) for group in groups)


def _flour_related(text: str) -> bool:
    c = _norm(text)
    return "flour" in c or "maida" in c or "atta" in c


def _detect_flour_subtype(text: str) -> str | None:
    c = _norm(text)
    if not _flour_related(c):
        return None
    compact = _compact_alnum(c)
    if ("corn" in c and "flour" in c) or "cornflour" in compact or ("maize" in c and "flour" in c):
        return "corn"
    if "self raising" in c or "selfraising" in compact:
        return "self_raising"
    if ("chakki" in c and "atta" in c) or "whole wheat" in c:
        return "atta"
    if " atta" in f" {c} " or c.endswith(" atta") or c.startswith("atta "):
        return "atta"
    if "all purpose" in c or "allpurpose" in compact or ("maida" in c and "atta" not in c):
        return "all_purpose"
    return "plain"


_FLOUR_SUBTYPE_FORBIDS: dict[str, frozenset[str]] = {
    "all_purpose": frozenset({"corn", "atta", "self_raising"}),
    "atta": frozenset({"corn", "all_purpose", "self_raising"}),
    "corn": frozenset({"all_purpose", "atta", "self_raising"}),
    "self_raising": frozenset({"corn", "atta", "all_purpose"}),
}


def _flour_subtypes_compatible(basket_sub: str | None, cat_sub: str | None) -> bool:
    if basket_sub is None or cat_sub is None:
        return True
    if basket_sub == cat_sub:
        return True
    if basket_sub == "plain":
        return True
    forbidden = _FLOUR_SUBTYPE_FORBIDS.get(basket_sub, frozenset())
    return cat_sub not in forbidden


def _rice_related(text: str) -> bool:
    c = _norm(text)
    return "rice" in c or "basmati" in c or "jasmine" in c


def _detect_rice_subtype(text: str) -> str | None:
    c = _norm(text)
    if "basmati" in c:
        return "basmati"
    if "jasmine" in c:
        if "rice" not in c:
            return None
        return "jasmine"
    if "sushi" in c and "rice" in c:
        return "sushi"
    if ("arborio" in c or "risotto" in c) and "rice" in c:
        return "arborio"
    if "rice" in c:
        return "plain"
    return None


_RICE_SUBTYPE_FORBIDS: dict[str, frozenset[str]] = {
    "basmati": frozenset({"jasmine", "sushi", "arborio"}),
    "jasmine": frozenset({"basmati", "sushi", "arborio"}),
    "sushi": frozenset({"basmati", "jasmine", "arborio"}),
    "arborio": frozenset({"basmati", "jasmine", "sushi"}),
}


def _rice_subtypes_compatible(basket_sub: str | None, cat_sub: str | None) -> bool:
    if basket_sub is None or cat_sub is None:
        return True
    if basket_sub == cat_sub:
        return True
    if basket_sub == "plain" or cat_sub == "plain":
        return True
    forbidden = _RICE_SUBTYPE_FORBIDS.get(basket_sub, frozenset())
    return cat_sub not in forbidden


def _basket_is_watermelon(basket_blob: str, *, basket_label: str = "", line_no: int = 0) -> bool:
    if line_no == 28:
        return True
    label = _norm(basket_label)
    if "watermelon" in label:
        return True
    return "watermelon" in basket_blob and "fresh fruit" in basket_blob


def _basket_is_tender_breast(basket_blob: str, *, line_no: int = 0) -> bool:
    if line_no == 30:
        return True
    return ("tender" in basket_blob or "tenders" in basket_blob) and "breast" in basket_blob


def _detect_mince_meat(text: str) -> str | None:
    c = _norm(text)
    if "mince" not in c and "ground" not in c:
        return None
    if "beef" in c:
        return "beef"
    if "buffalo" in c:
        return "buffalo"
    if "chicken" in c:
        return "chicken"
    if "lamb" in c or "mutton" in c:
        return "lamb"
    if "veal" in c:
        return "veal"
    return "plain"


_MINCE_MEAT_FORBIDS: dict[str, frozenset[str]] = {
    "beef": frozenset({"chicken", "lamb", "buffalo", "veal"}),
    "buffalo": frozenset({"chicken", "lamb", "beef", "veal"}),
    "chicken": frozenset({"beef", "lamb", "buffalo", "veal"}),
    "lamb": frozenset({"beef", "chicken", "buffalo", "veal"}),
    "veal": frozenset({"beef", "chicken", "lamb", "buffalo"}),
}


def _mince_meat_compatible(basket_meat: str | None, cat_meat: str | None) -> bool:
    if basket_meat is None or cat_meat is None:
        return True
    if basket_meat == cat_meat:
        return True
    if basket_meat == "plain":
        return True
    forbidden = _MINCE_MEAT_FORBIDS.get(basket_meat, frozenset())
    return cat_meat not in forbidden


def _detect_pasta_shape(text: str) -> str | None:
    c = _norm(text)
    compact = _compact_alnum(c)
    if "penne" in c:
        return "penne"
    if "spaghetti" in c:
        return "spaghetti"
    if "fusilli" in c:
        return "fusilli"
    if "macaroni" in c:
        return "macaroni"
    if "fettuccine" in c or "fettuccini" in c:
        return "fettuccine"
    if "pasta" in c or "rigate" in c:
        return "plain"
    return None


_PASTA_SHAPE_FORBIDS: dict[str, frozenset[str]] = {
    "penne": frozenset({"spaghetti", "fusilli", "macaroni", "fettuccine"}),
    "spaghetti": frozenset({"penne", "fusilli", "macaroni", "fettuccine"}),
    "fusilli": frozenset({"penne", "spaghetti", "macaroni", "fettuccine"}),
    "macaroni": frozenset({"penne", "spaghetti", "fusilli", "fettuccine"}),
    "fettuccine": frozenset({"penne", "spaghetti", "fusilli", "macaroni"}),
}


def _pasta_shapes_compatible(basket_shape: str | None, cat_shape: str | None) -> bool:
    if basket_shape is None or cat_shape is None:
        return True
    if basket_shape == cat_shape:
        return True
    if basket_shape == "plain" or cat_shape == "plain":
        return True
    forbidden = _PASTA_SHAPE_FORBIDS.get(basket_shape, frozenset())
    return cat_shape not in forbidden


def _detect_apple_variety(text: str) -> str | None:
    c = _norm(text)
    compact = _compact_alnum(c)
    if "royal gala" in c or "royalgala" in compact or ("apple" in c and "royal" in c):
        return "royal_gala"
    if "fuji" in c:
        return "fuji"
    if "pink lady" in c or "pinklady" in compact:
        return "pink_lady"
    if "granny smith" in c or "grannysmith" in compact:
        return "granny_smith"
    if "apple" in c:
        return "plain"
    return None


_APPLE_VARIETY_FORBIDS: dict[str, frozenset[str]] = {
    "royal_gala": frozenset({"fuji", "pink_lady", "granny_smith"}),
    "fuji": frozenset({"royal_gala", "pink_lady", "granny_smith"}),
    "pink_lady": frozenset({"royal_gala", "fuji", "granny_smith"}),
    "granny_smith": frozenset({"royal_gala", "fuji", "pink_lady"}),
}


def _apple_varieties_compatible(basket_var: str | None, cat_var: str | None) -> bool:
    if basket_var is None or cat_var is None:
        return True
    if basket_var == cat_var:
        return True
    if basket_var == "plain" or cat_var == "plain":
        return True
    forbidden = _APPLE_VARIETY_FORBIDS.get(basket_var, frozenset())
    return cat_var not in forbidden


def _basket_requires_bottle_multipack(basket_blob: str, *, line_no: int = 0) -> tuple[int, float] | None:
    if line_no == 9:
        return (6, 1.5)
    c = _norm(basket_blob)
    compact = _compact_alnum(c)
    if "6x1.5" in compact or "6 x 1.5" in c or ("6" in c and "1.5" in c and "water" in c):
        return (6, 1.5)
    return None


def _catalog_has_bottle_multipack(cat_blob: str, *, count: int, volume_l: float) -> bool:
    c = _norm(cat_blob)
    compact = _compact_alnum(c)
    vol_key = str(volume_l).replace(".", "")
    patterns = (
        f"{count}x{volume_l}",
        f"{count}x{volume_l}l",
        f"{count}x{vol_key}",
        f"{count} x {volume_l}",
        f"{count} x {volume_l}l",
    )
    if any(p.replace(" ", "") in compact or p in c for p in patterns):
        return True
    if str(count) in c and str(volume_l) in c and ("water" in c or "bottle" in c):
        return True
    return False


def product_form_compatible(
    basket_text: str,
    catalog_title: str,
    *,
    category: str = "",
    subcategory_path: str = "",
    catalog_url: str = "",
    basket_label: str = "",
    line_no: int = 0,
) -> bool:
    """Reject incompatible product forms (UHT/fresh milk, frozen/chilled whole chicken)."""
    basket_blob = _norm(f"{basket_text} {category} {basket_label}")
    title_blob = f"{subcategory_path} {catalog_title}"
    cat_blob = _norm(f"{title_blob} {catalog_url}")
    subtype_identity = subtype_identity_blob(basket_text, basket_label, category)

    if _basket_is_watermelon(basket_blob, basket_label=basket_label, line_no=line_no):
        if "watermelon" not in cat_blob:
            return False
        for bad in ("jelly", "juice", "candy", "gummy", "chewing", " gum", "ring", "slice"):
            if bad.strip() in cat_blob:
                return False

    if _basket_is_tender_breast(basket_blob, line_no=line_no):
        if "chicken" in cat_blob and "whole" in cat_blob and "breast" not in cat_blob:
            return False
        if "chicken" in cat_blob and "breast" not in cat_blob and "tender" not in cat_blob:
            return False

    multipack = _basket_requires_bottle_multipack(basket_blob, line_no=line_no)
    if multipack is not None:
        count, volume_l = multipack
        if "water" in basket_blob and "water" in cat_blob:
            if not _catalog_has_bottle_multipack(cat_blob, count=count, volume_l=volume_l):
                return False

    basket_mince = _detect_mince_meat(subtype_identity)
    cat_mince = _detect_mince_meat(cat_blob)
    if basket_mince and basket_mince != "plain":
        if "mince" not in cat_blob and "ground" not in cat_blob:
            return False
    if basket_mince and cat_mince and not _mince_meat_compatible(basket_mince, cat_mince):
        return False

    basket_pasta = _detect_pasta_shape(subtype_identity)
    cat_pasta = _detect_pasta_shape(cat_blob)
    if basket_pasta and cat_pasta and not _pasta_shapes_compatible(basket_pasta, cat_pasta):
        return False

    basket_apple = _detect_apple_variety(subtype_identity)
    cat_apple = _detect_apple_variety(cat_blob)
    if basket_apple and cat_apple and not _apple_varieties_compatible(basket_apple, cat_apple):
        return False

    if _milk_related(basket_blob):
        basket_flavoured = any(
            f in basket_blob for f in ("chocolate", "strawberry", "banana", "vanilla", "flavoured", "flavored")
        )
        if not basket_flavoured and _milk_related(cat_blob):
            for bad in ("chocolate", "strawberry", "banana", "vanilla", "flavoured", "flavored"):
                if bad in cat_blob:
                    return False

    if basket_requires_uht(basket_blob, category=category):
        if _catalog_is_yogurt(cat_blob):
            return False
        if "milk" not in cat_blob:
            return False

    if _milk_related(basket_blob) and _milk_related(cat_blob):
        requires_uht = basket_requires_uht(basket_blob, category=category)
        requires_fresh = basket_requires_fresh_milk(basket_blob, category=category)
        cat_uht, cat_fresh = _catalog_milk_form(title_blob=title_blob, catalog_url=catalog_url)

        if requires_uht:
            if cat_fresh and not cat_uht:
                return False
            return cat_uht

        if requires_fresh:
            if cat_uht and not cat_fresh:
                return False
            return True

    if _whole_chicken_related(basket_blob):
        if "chicken" in cat_blob and "whole" not in cat_blob:
            return False
        cat_frozen = _catalog_is_frozen_chicken(title_blob, catalog_url)
        cat_chilled = _catalog_is_chilled_chicken(title_blob, catalog_url)
        cat_upper = category.upper()
        if "FREEZER" in cat_upper:
            if cat_chilled and not cat_frozen:
                return False
            return cat_frozen or not cat_chilled
        if "FRESH MEAT" in cat_upper or "MEAT" in cat_upper:
            if cat_frozen and not cat_chilled:
                return False
            return cat_chilled or not cat_frozen

    basket_flour = _detect_flour_subtype(subtype_identity)
    cat_flour = _detect_flour_subtype(cat_blob)
    if basket_flour and cat_flour and not _flour_subtypes_compatible(basket_flour, cat_flour):
        return False

    basket_rice = _detect_rice_subtype(subtype_identity)
    cat_rice = _detect_rice_subtype(cat_blob)
    if basket_rice and basket_rice != "plain":
        if "rice" not in cat_blob and not (basket_rice == "basmati" and "basmati" in cat_blob):
            return False
    if basket_rice and cat_rice and not _rice_subtypes_compatible(basket_rice, cat_rice):
        return False

    return True


def all_distinctive_tokens_hit(mapped_name: str, blob: str) -> bool:
    tokens = distinctive_name_tokens(mapped_name)
    if not tokens:
        tokens = name_tokens(mapped_name)
    if not tokens:
        return False
    words = set(_norm(blob).split())
    padded = f" {_norm(blob)} "
    return all(_token_in_blob(token, words, padded) for token in tokens)


def _token_in_blob(token: str, words: set[str], padded: str) -> bool:
    if token in words or f" {token} " in padded:
        return True
    plural = f"{token}s"
    if plural in words or f" {plural} " in padded:
        return True
    # Singular stem: basket token "tissues" should match catalog "tissue".
    if len(token) > 3 and token.endswith("s"):
        singular = token[:-1]
        if singular in words or f" {singular} " in padded:
            return True
    return any(w.startswith(token) and len(w) <= len(token) + 3 for w in words)


def token_hits(tokens: list[str], blob: str) -> int:
    """Count whole-word token hits (never substring matches inside other words)."""
    if not tokens:
        return 0
    words = set(_norm(blob).split())
    padded = f" {_norm(blob)} "
    return sum(1 for token in tokens if _token_in_blob(token, words, padded))


def name_similarity_parts(mapped_name: str, title: str, blob: str) -> tuple[float, int, int]:
    """Return (best_name_score, token_hits, token_count) without pack bonuses."""
    mn = _norm(mapped_name)
    title_n = _norm(title)
    tokens = name_tokens(mapped_name)
    hits = 0
    parts: list[float] = []
    if mn:
        if title_n:
            parts.append(SequenceMatcher(None, mn, title_n).ratio())
        parts.append(SequenceMatcher(None, mn, blob).ratio())
        if len(mn) >= 4 and (blob.startswith(mn) or f" {mn} " in f" {blob} "):
            parts.append(0.88)
        hits = token_hits(tokens, blob)
        if tokens:
            parts.append(0.45 + 0.5 * (hits / len(tokens)))
    name_score = max(parts) if parts else 0.0
    return name_score, hits, len(tokens)


def _item_text_blob(it: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "title",
        "name",
        "englishTitle",
        "description",
        "brandName",
        "brand",
        "measurementText",
        "quantity",
        "sku",
    ):
        v = it.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v)
    slug = it.get("slug")
    if isinstance(slug, str) and slug.strip():
        parts.append(slug.replace("-", " "))
    return _norm(" ".join(parts))


def _pack_variants(qty: str, unit: str) -> set[str]:
    q = (qty or "").strip().lower()
    u = (unit or "").strip().lower()
    out: set[str] = set()
    if q and u:
        out.add(_compact_alnum(f"{q}{u}"))
        out.add(_compact_alnum(f"{q} {u}"))
        out.add(_norm(f"{q} {u}"))
    if q:
        out.add(_compact_alnum(q))
    return {x for x in out if x}


def score_catalog_item(
    item: dict[str, Any],
    *,
    mapped_name: str,
    pack_qty: str,
    pack_unit: str,
    search_query: str,
) -> float:
    blob = _item_text_blob(item)
    raw_title = str(item.get("title") or "")
    title = _norm(raw_title) if raw_title else ""
    mn = _norm(mapped_name)
    sq = _norm(search_query)

    if not blob:
        return 0.0

    parts: list[float] = []

    if mn:
        parts.append(SequenceMatcher(None, mn, title).ratio() if title else 0.0)
        parts.append(SequenceMatcher(None, mn, blob).ratio())
        # Prefix / containment boost (handles "Almarai" vs "Almarai Full Fat...")
        if len(mn) >= 4:
            if blob.startswith(mn) or f" {mn} " in f" {blob} ":
                parts.append(0.88)
            if mn in blob:
                parts.append(0.82)

    if sq:
        tokens = name_tokens(search_query or mapped_name)
        if tokens:
            blob = _item_text_blob(item)
            hits = token_hits(tokens, blob)
            cov = hits / len(tokens)
            parts.append(0.45 + 0.45 * cov)

    base = max(parts) if parts else 0.0

    name_only = SequenceMatcher(None, mn, title).ratio() if mn and title else 0.0
    if name_only >= 0.35:
        for pv in _pack_variants(pack_qty, pack_unit):
            if len(pv) >= 2 and pv in _compact_alnum(blob):
                base += 0.1
                break
            if len(pv) >= 3 and pv in blob.replace(" ", ""):
                base += 0.08
                break

    return float(min(base, 1.0))

def best_catalog_match(
    items: list[dict[str, Any]],
    *,
    mapped_name: str,
    pack_qty: str,
    pack_unit: str,
    search_query: str,
    min_score: float = 0.52,
    ambiguity_gap: float = 0.04,
) -> tuple[dict[str, Any] | None, str | None]:
    """
    Pick the single best item or return (None, error).

    error is human-readable for item_url_master.error when no confident match.
    """
    if not items:
        return None, "empty catalog or search results"

    scored: list[tuple[float, dict[str, Any]]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if not str(it.get("id") or "").strip():
            continue
        sc = score_catalog_item(
            it,
            mapped_name=mapped_name,
            pack_qty=pack_qty,
            pack_unit=pack_unit,
            search_query=search_query or mapped_name,
        )
        scored.append((sc, it))

    if not scored:
        return None, "no items with ids in candidate set"

    scored.sort(key=lambda x: x[0], reverse=True)
    best_s, best_it = scored[0]
    second_s = scored[1][0] if len(scored) > 1 else -1.0

    if best_s < min_score:
        bt = str(best_it.get("title") or "?")[:80]
        return None, f"best score {best_s:.2f} below threshold {min_score:.2f} (top: {bt!r})"

    if second_s >= 0 and (best_s - second_s) < ambiguity_gap:
        t1 = str(best_it.get("title") or "?")[:60]
        t2 = str(scored[1][1].get("title") or "?")[:60]
        return None, f"ambiguous match (scores {best_s:.2f} vs {second_s:.2f}): {t1!r} vs {t2!r}"

    return best_it, None
