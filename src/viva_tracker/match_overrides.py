"""Tier-0 identity overrides: slug, missing, or forbid_slugs per store line."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from . import settings


@dataclass(frozen=True)
class MatchOverride:
    brand_name: str
    store_label: str
    line_no: int
    action: str
    product_slug: str = ""
    forbid_slugs: tuple[str, ...] = ()
    note: str = ""


def _norm_key(brand_name: str, store_label: str) -> tuple[str, str]:
    return brand_name.strip().lower(), store_label.strip().lower()


@lru_cache(maxsize=1)
def load_match_overrides(path: str | None = None) -> tuple[MatchOverride, ...]:
    cfg_path = settings.CONFIG_DIR / "match_overrides.json" if path is None else settings.CONFIG_DIR / path
    if not cfg_path.is_file():
        return ()
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    out: list[MatchOverride] = []
    for row in data.get("entries") or []:
        if not isinstance(row, dict):
            continue
        brand = str(row.get("brand_name") or "").strip()
        label = str(row.get("store_label") or "").strip()
        if not brand or not label:
            continue
        action = str(row.get("action") or "slug").strip().lower()
        forbid = row.get("forbid_slugs") or []
        if isinstance(forbid, str):
            forbid = [forbid]
        out.append(
            MatchOverride(
                brand_name=brand,
                store_label=label,
                line_no=int(row.get("line_no") or 0),
                action=action,
                product_slug=str(row.get("product_slug") or "").strip(),
                forbid_slugs=tuple(str(x).strip() for x in forbid if str(x).strip()),
                note=str(row.get("note") or "").strip(),
            )
        )
    return tuple(out)


def lookup_override(
    brand_name: str,
    store_label: str,
    line_no: int,
    *,
    overrides: tuple[MatchOverride, ...] | None = None,
) -> MatchOverride | None:
    items = overrides if overrides is not None else load_match_overrides()
    want_brand, want_label = _norm_key(brand_name, store_label)
    for item in items:
        if item.line_no != int(line_no):
            continue
        ib, il = _norm_key(item.brand_name, item.store_label)
        if ib == want_brand and il == want_label:
            return item
    return None


def resolve_slug_row(index: Any, product_slug: str) -> dict[str, str] | None:
    """Find catalog row by slug path (path after /product/)."""
    slug_norm = product_slug.strip().lower()
    if not slug_norm:
        return None
    for row in index.rows:
        url = str(row.get("url") or "")
        if "/product/" not in url:
            continue
        path = url.split("/product/", 1)[1].split("?", 1)[0].lower()
        if path == slug_norm or path.startswith(slug_norm.split("/s/", 1)[0]):
            return row
        slug_base = slug_norm.split("/s/", 1)[0]
        if slug_base and slug_base in path:
            return row
    return None
