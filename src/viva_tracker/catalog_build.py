"""Build and save per-store Talabat grocery catalog JSON files."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .catalog_index import catalog_path_for_store
from .storefront import (
    build_grocery_product_url,
    category_url,
    fetch_html,
    load_store_categories,
    parse_next_data,
    validate_store_url,
)

CatalogBuildPhase = Literal["starting", "fetching", "saving", "done", "error"]


@dataclass
class CatalogBuildProgress:
    store_label: str
    phase: CatalogBuildPhase
    category_name: str = ""
    subcategory_name: str = ""
    page_current: int = 0
    page_total: int = 1
    pages_completed: int = 0
    subcategories_completed: int = 0
    subcategories_total: int = 0
    products_collected: int = 0
    message: str = ""

    @property
    def progress_fraction(self) -> float:
        if self.subcategories_total <= 0:
            if self.phase == "done":
                return 1.0
            return 0.0
        if self.phase == "done":
            return 1.0
        sub_progress = self.subcategories_completed
        if self.phase == "fetching" and self.page_total > 0:
            sub_progress += self.page_current / self.page_total
        return min(1.0, max(0.0, sub_progress / self.subcategories_total))


def format_catalog_build_progress(progress: CatalogBuildProgress) -> str:
    if progress.phase == "starting":
        return f"Starting — {progress.subcategories_total} subcategories to crawl"
    if progress.phase == "saving":
        return (
            f"Pages completed: {progress.pages_completed} | "
            f"Saving catalog ({progress.products_collected} products)…"
        )
    if progress.phase == "done":
        return (
            f"Pages completed: {progress.pages_completed} | "
            f"Done — {progress.products_collected} products"
        )
    if progress.phase == "error":
        return progress.message or "Catalog build failed"
    location = progress.category_name
    if progress.subcategory_name:
        location = f"{location} / {progress.subcategory_name}" if location else progress.subcategory_name
    page_part = ""
    if progress.page_current > 0:
        page_part = f" — page {progress.page_current}/{progress.page_total}"
    return (
        f"Pages completed: {progress.pages_completed} | "
        f"{location}{page_part} | Products: {progress.products_collected}"
    )


def _count_subcategories(categories_tree: list[dict[str, Any]]) -> int:
    total = 0
    for cat in categories_tree:
        total += len(cat.get("subcategories") or [])
    return total


def _product_row(store_url: str, item: dict[str, Any], listing_url: str) -> dict[str, Any] | None:
    iid = str(item.get("id") or "").strip()
    title = str(item.get("title") or "").strip()
    if not iid or not title:
        return None
    price = item.get("price")
    original = item.get("originalPrice")
    try:
        price_f = float(price) if price is not None else None
    except (TypeError, ValueError):
        price_f = None
    try:
        orig_f = float(original) if original is not None else None
    except (TypeError, ValueError):
        orig_f = None
    discounted = price_f
    if orig_f is not None and price_f is not None and orig_f > price_f:
        discounted = price_f
    elif orig_f is not None:
        discounted = orig_f if price_f is None else price_f
    url = build_grocery_product_url(store_url, item)
    if not url:
        url = listing_url
    image = str(item.get("image") or "").strip()
    if not image:
        imgs = item.get("images")
        if isinstance(imgs, list) and imgs:
            image = str(imgs[0] or "").strip()
    return {
        "item_id": iid,
        "product_name": title,
        "url": url,
        "price": orig_f if orig_f is not None else price_f,
        "discounted_price": discounted,
        "image_url": image,
    }


def build_store_catalog(
    store_url: str,
    *,
    store_label: str = "",
    page_delay_s: float = 0.85,
    log: bool = True,
    progress_callback: Callable[[CatalogBuildProgress], None] | None = None,
) -> dict[str, Any]:
    store_url = validate_store_url(store_url)
    categories_tree = load_store_categories(store_url)
    subcategories_total = _count_subcategories(categories_tree)
    built_categories: list[dict[str, Any]] = []
    total_products = 0
    pages_completed = 0
    subcategories_completed = 0
    label = store_label or store_url

    def products_in_built_categories() -> int:
        return sum(
            len(sub.get("products") or [])
            for built in built_categories
            for sub in built.get("subcategories") or []
        )

    def emit(phase: CatalogBuildPhase, **kwargs: Any) -> None:
        if progress_callback is None:
            return
        products_collected = kwargs.pop("products_collected", total_products)
        progress_callback(
            CatalogBuildProgress(
                store_label=label,
                phase=phase,
                pages_completed=pages_completed,
                subcategories_completed=subcategories_completed,
                subcategories_total=subcategories_total,
                products_collected=products_collected,
                **kwargs,
            )
        )

    emit("starting")

    for cat in categories_tree:
        cat_name = cat["name"]
        cat_slug = cat["slug"]
        sub_out: list[dict[str, Any]] = []
        for sub in cat.get("subcategories") or []:
            sub_name = sub["name"]
            sub_slug = sub["slug"]
            base_url = category_url(store_url, cat_slug, sub_slug)
            products: list[dict[str, Any]] = []
            page_count = 1
            page = 1
            while page <= page_count:
                url = base_url
                if page > 1:
                    sep = "&" if "?" in base_url else "?"
                    url = f"{base_url}{sep}page={page}"
                try:
                    html = fetch_html(url)
                    nd = parse_next_data(html)
                    idata = (
                        nd.get("props", {})
                        .get("pageProps", {})
                        .get("initialState", {})
                        .get("itemsData")
                        or {}
                    )
                    items = idata.get("items") or []
                    if page == 1:
                        page_count = max(1, int(idata.get("pageCount") or 1))
                    elif not items:
                        break
                    for it in items:
                        if not isinstance(it, dict):
                            continue
                        row = _product_row(store_url, it, url)
                        if row:
                            products.append(row)
                except Exception as exc:  # noqa: BLE001
                    if log:
                        print(f"  warn: {cat_name} / {sub_name} page {page}: {exc}")
                pages_completed += 1
                emit(
                    "fetching",
                    category_name=str(cat_name),
                    subcategory_name=str(sub_name),
                    page_current=page,
                    page_total=page_count,
                    products_collected=products_in_built_categories() + len(products),
                )
                page += 1
                if page_delay_s > 0:
                    time.sleep(page_delay_s)
            if log:
                print(f"  {cat_name} / {sub_name}: {len(products)} products")
            total_products += len(products)
            subcategories_completed += 1
            emit(
                "fetching",
                category_name=str(cat_name),
                subcategory_name=str(sub_name),
                page_current=page_count,
                page_total=page_count,
                products_collected=total_products,
            )
            sub_out.append(
                {
                    "name": sub_name,
                    "slug": sub_slug,
                    "listing_url": base_url,
                    "products": products,
                }
            )
        built_categories.append(
            {
                "name": cat_name,
                "slug": cat_slug,
                "subcategories": sub_out,
            }
        )

    if log:
        print(f"  total products: {total_products}")
    return {
        "categories": built_categories,
        "product_count": total_products,
    }


def save_catalog(
    path: Path,
    *,
    store_id: int,
    store_label: str,
    brand_name: str,
    talabat_url: str,
    catalog_body: dict[str, Any],
) -> None:
    payload = {
        "store_id": store_id,
        "store_label": store_label,
        "brand_name": brand_name,
        "talabat_url": talabat_url,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "format_version": 1,
        **catalog_body,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_and_save_catalog_for_store(
    *,
    store_id: int,
    store_label: str,
    brand_name: str,
    talabat_url: str,
    page_delay_s: float = 0.85,
    log: bool = True,
    progress_callback: Callable[[CatalogBuildProgress], None] | None = None,
) -> dict[str, Any]:
    pages_completed = 0
    products_collected = 0
    subcategories_total = 0

    def tracked_callback(progress: CatalogBuildProgress) -> None:
        nonlocal pages_completed, products_collected, subcategories_total
        pages_completed = progress.pages_completed
        products_collected = progress.products_collected
        subcategories_total = progress.subcategories_total
        if progress_callback is not None:
            progress_callback(progress)

    body = build_store_catalog(
        talabat_url,
        store_label=store_label,
        page_delay_s=page_delay_s,
        log=log,
        progress_callback=tracked_callback,
    )
    if progress_callback is not None:
        progress_callback(
            CatalogBuildProgress(
                store_label=store_label,
                phase="saving",
                pages_completed=pages_completed,
                subcategories_completed=subcategories_total,
                subcategories_total=subcategories_total,
                products_collected=body["product_count"],
            )
        )
    out_path = catalog_path_for_store(store_label)
    save_catalog(
        out_path,
        store_id=store_id,
        store_label=store_label,
        brand_name=brand_name,
        talabat_url=talabat_url,
        catalog_body=body,
    )
    if progress_callback is not None:
        progress_callback(
            CatalogBuildProgress(
                store_label=store_label,
                phase="done",
                pages_completed=pages_completed,
                subcategories_completed=subcategories_total,
                subcategories_total=subcategories_total,
                products_collected=body["product_count"],
            )
        )
    return {
        "path": str(out_path),
        "product_count": body["product_count"],
        "built_at": datetime.now(timezone.utc).isoformat(),
    }


def cli_catalog_progress_callback(progress: CatalogBuildProgress) -> None:
    """Print one progress line for CLI catalog builds."""
    prefix = progress.store_label or "store"
    print(f"  [{prefix}] {format_catalog_build_progress(progress)}")
