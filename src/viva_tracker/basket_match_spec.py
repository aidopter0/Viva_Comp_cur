"""Basket-first match specification (v2): tokens + pack from basket_label only."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .catalog_match import _norm, token_hits
from .match_groups import match_group_for_category
from .pack_normalize import parse_title_pack_info, split_pack_fields
from .settings import CONFIG_DIR

LINE_ROLE_DEFAULT = "default"
LINE_ROLE_OWN_BRAND = "own_brand"
LINE_ROLE_OUTSIDE_BRAND = "outside_brand"


@lru_cache(maxsize=1)
def _load_line_roles_config() -> dict[str, Any]:
    path = CONFIG_DIR / "basket_line_roles.json"
    if not path.is_file():
        return {"lines": {}, "label_prefixes": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def line_role_for_line(line_no: int, basket_label: str = "") -> str:
    """Resolve line_role from config or basket_label prefix."""
    cfg = _load_line_roles_config()
    explicit = str((cfg.get("lines") or {}).get(str(line_no)) or "").strip()
    if explicit in {LINE_ROLE_OWN_BRAND, LINE_ROLE_OUTSIDE_BRAND, LINE_ROLE_DEFAULT}:
        return explicit
    label = str(basket_label or "").strip()
    if not label:
        return LINE_ROLE_DEFAULT
    lower = label.lower()
    if lower.startswith("own brand"):
        return LINE_ROLE_OWN_BRAND
    if lower.startswith("brand "):
        return LINE_ROLE_OUTSIDE_BRAND
    return LINE_ROLE_DEFAULT


def parse_pack_from_basket_label(basket_label: str) -> tuple[str, str]:
    """Parse pack qty/unit embedded in basket_label text."""
    label = str(basket_label or "").strip()
    if not label:
        return "", ""
    info = parse_title_pack_info(label)
    if info is not None:
        return info.total_qty, info.total_unit
    # Fallback: trailing "600g", "1kg", "30s"
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*(kg|g|gm|gms|gram|grams|ml|l|litre|liter|liters|litres|gal|gallon|gallons|pc|pcs|pk|pack|s)\b",
        label,
        flags=re.IGNORECASE,
    )
    if m:
        return split_pack_fields(m.group(1), m.group(2))
    multi = re.search(
        r"(\d+)\s*x\s*(\d+(?:\.\d+)?)\s*(l|litre|liter|ml|kg|g|gal)\b",
        label,
        flags=re.IGNORECASE,
    )
    if multi:
        return multi.group(1), f"x {multi.group(2)} {multi.group(3)}"
    return "", ""


def _strip_role_prefix(label: str) -> str:
    text = str(label or "").strip()
    lower = text.lower()
    if lower.startswith("own brand "):
        return text[len("Own Brand ") :].strip() if text.lower().startswith("own brand ") else text[10:].strip()
    if lower.startswith("brand "):
        return text[6:].strip()
    return text


def _strip_pack_from_label(label: str, pack_qty: str, pack_unit: str) -> str:
    text = str(label or "").strip()
    if not text:
        return ""
    info = parse_title_pack_info(text)
    if info is not None and info.display:
        for part in (info.display, f"{info.total_qty}{info.total_unit}", f"{info.total_qty} {info.total_unit}"):
            if part:
                text = re.sub(re.escape(part), " ", text, flags=re.IGNORECASE)
    if pack_qty and pack_unit:
        pattern = rf"{re.escape(pack_qty)}\s*{re.escape(pack_unit)}"
        text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
    # Remove common trailing pack fragments
    text = re.sub(
        r"\b\d+(?:\.\d+)?\s*(?:kg|g|gm|grams|ml|l|litre|liter|gal|gallon|pc|pcs|pk|pack|s)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\b\d+\s*x\s*\d+(?:\.\d+)?\s*(?:l|ml|kg|g|gal)\b", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def basket_tokens_from_label(basket_label: str) -> tuple[str, ...]:
    """Product tokens from basket_label (no stopword stripping)."""
    pack_qty, pack_unit = parse_pack_from_basket_label(basket_label)
    text = _strip_role_prefix(basket_label)
    text = _strip_pack_from_label(text, pack_qty, pack_unit)
    norm = _norm(text)
    tokens = [t for t in norm.split() if len(t) >= 2 or t.isdigit()]
    return tuple(tokens)


@dataclass(frozen=True)
class BasketMatchSpec:
    line_no: int
    basket_item_id: int
    basket_label: str
    basket_tokens: tuple[str, ...]
    pack_qty: str
    pack_unit: str
    category: str
    match_group: str
    line_role: str
    store_label: str
    store_brand_name: str

    @classmethod
    def from_basket_row(
        cls,
        *,
        line_no: int,
        basket_item_id: int,
        basket_label: str,
        category: str = "",
        match_group: str = "",
        line_role: str = "",
        store_label: str = "",
        store_brand_name: str = "",
    ) -> BasketMatchSpec:
        label = str(basket_label or "").strip()
        pack_qty, pack_unit = parse_pack_from_basket_label(label)
        group = str(match_group or "").strip() or match_group_for_category(category)
        role = str(line_role or "").strip() or line_role_for_line(line_no, label)
        return cls(
            line_no=int(line_no),
            basket_item_id=int(basket_item_id),
            basket_label=label,
            basket_tokens=basket_tokens_from_label(label),
            pack_qty=pack_qty,
            pack_unit=pack_unit,
            category=str(category or "").strip(),
            match_group=group,
            line_role=role,
            store_label=str(store_label or "").strip(),
            store_brand_name=str(store_brand_name or "").strip(),
        )

    def form_context(self) -> str:
        return " ".join(
            p
            for p in (self.category, self.basket_label)
            if str(p or "").strip()
        )

    def all_tokens_in_title(self, product_name: str, url: str = "") -> bool:
        if not self.basket_tokens:
            return bool(str(product_name or "").strip())
        slug = ""
        if "/product/" in str(url or ""):
            slug = url.split("/product/", 1)[1].split("/s/", 1)[0]
        blob = _norm(f"{product_name} {slug}")
        return token_hits(list(self.basket_tokens), blob) >= len(self.basket_tokens)
