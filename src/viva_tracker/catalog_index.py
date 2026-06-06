"""Full-catalog index and basket-first candidate retrieval."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .basket_match_spec import BasketMatchSpec
from .basket_matcher import (
    cheapest_row,
    fallback_pool_rows,
    pack_tier,
    shortlist_rows,
)
from .pack_normalize import format_title_pack_display, pack_matches_target
from .settings import CATALOGS_DIR


def slug_from_row(row: dict[str, str]) -> str:
    url = str(row.get("url") or "")
    if "/product/" not in url:
        return ""
    return url.split("/product/", 1)[1].split("?", 1)[0]


def safe_store_filename(store_label: str) -> str:
    s = re.sub(r'[<>:"/\\|?*]', "_", (store_label or "").strip())
    s = re.sub(r"\s+", " ", s).strip()
    return s or "store"


def catalog_path_for_store(store_label: str) -> Path:
    return CATALOGS_DIR / f"{safe_store_filename(store_label)}.json"


def load_catalog_file(store_label: str) -> dict[str, Any]:
    path = catalog_path_for_store(store_label)
    if not path.is_file():
        raise FileNotFoundError(f"Catalog not found: {path} (build catalog first)")
    return json.loads(path.read_text(encoding="utf-8"))


def catalog_exists(store_label: str) -> bool:
    return catalog_path_for_store(store_label).is_file()


def catalog_meta(store_label: str) -> dict[str, Any] | None:
    path = catalog_path_for_store(store_label)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return {
        "path": str(path),
        "built_at": data.get("built_at"),
        "product_count": data.get("product_count"),
    }


class CatalogIndex:
    """In-memory index over every product in a saved store catalog."""

    def __init__(self, catalog: dict[str, Any]) -> None:
        self.rows: list[dict[str, str]] = []
        self._by_ref: dict[str, dict[str, str]] = {}
        for cat in catalog.get("categories") or []:
            cname = str(cat.get("name") or "")
            for sub in cat.get("subcategories") or []:
                sname = str(sub.get("name") or "")
                label = f"{cname} / {sname}".strip(" /")
                for prod in sub.get("products") or []:
                    if not isinstance(prod, dict):
                        continue
                    iid = str(prod.get("item_id") or "").strip()
                    title = str(prod.get("product_name") or "").strip()
                    if not iid or not title:
                        continue
                    ref = str(len(self.rows))
                    row = {
                        "ref": ref,
                        "subcategory_path": label,
                        "product_name": title,
                        "url": str(prod.get("url") or ""),
                        "item_id": iid,
                        "category_hint": cname,
                        "price": prod.get("price"),
                        "discounted_price": prod.get("discounted_price"),
                    }
                    self.rows.append(row)
                    self._by_ref[ref] = row

    def __len__(self) -> int:
        return len(self.rows)

    def get(self, ref: str) -> dict[str, str] | None:
        return self._by_ref.get(ref)

    def iter_basket_shortlist(
        self,
        spec: BasketMatchSpec,
        *,
        exclude_urls: set[str] | None = None,
    ) -> list[dict[str, str]]:
        return shortlist_rows(self, spec, exclude_urls=exclude_urls)

    def iter_fallback_pool(
        self,
        spec: BasketMatchSpec,
        *,
        exclude_urls: set[str] | None = None,
    ) -> list[dict[str, str]]:
        return fallback_pool_rows(self, spec, exclude_urls=exclude_urls)

    def find_basket_match(
        self,
        spec: BasketMatchSpec,
        *,
        exclude_urls: set[str] | None = None,
    ) -> tuple[dict[str, str] | None, str, float]:
        """Cheapest exact-pack shortlist row, if any."""
        rows = self.iter_basket_shortlist(spec, exclude_urls=exclude_urls)
        pick = cheapest_row(rows)
        if pick is None:
            return None, "unknown", 0.0
        tier = pack_tier(pick, spec)
        pm = pack_matches_target(pick["product_name"], spec.pack_qty, spec.pack_unit)
        if pm == "unknown":
            pm = tier if tier in {"exact", "close", "different"} else "unknown"
        return pick, pm, 0.95

    def catalog_has_basket_candidate(
        self,
        spec: BasketMatchSpec,
        *,
        exclude_urls: set[str] | None = None,
    ) -> bool:
        return bool(self.iter_basket_shortlist(spec, exclude_urls=exclude_urls))

    def catalog_pack_text(self, product_name: str) -> str:
        return format_title_pack_display(product_name)
