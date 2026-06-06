"""Shared fixtures for catalog matching tests."""

from __future__ import annotations

import pytest

from viva_tracker.basket_match_spec import BasketMatchSpec
from viva_tracker.catalog_index import CatalogIndex


def _product(name: str, *, item_id: str, url: str = "", category: str = "Dairy", sub: str = "Milk") -> dict:
    return {
        "product_name": name,
        "item_id": item_id,
        "url": url or f"https://example.com/product/{item_id}",
    }


@pytest.fixture
def sample_catalog() -> dict:
    return {
        "built_at": "2026-01-01",
        "product_count": 12,
        "categories": [
            {
                "name": "Dairy",
                "subcategories": [
                    {
                        "name": "Milk",
                        "products": [
                            _product(
                                "Milba Full Cream UHT Milk UAE 1L",
                                item_id="milba-uht-1l",
                                url="https://example.com/milba-uht-1l",
                            ),
                            _product(
                                "Hayatna Full Cream UHT Milk UAE 1L",
                                item_id="hayatna-uht-1l",
                                url="https://example.com/hayatna-uht-1l",
                            ),
                            _product(
                                "Black Forest Milk Organic Full Fat UHT 1L",
                                item_id="black-forest-uht-1l",
                                url="https://example.com/product/black-forest-milk-organic-full-fat-uht-1l/s/999",
                            ),
                            _product(
                                "Milba Full Cream Natural Yoghurt UAE 1kg",
                                item_id="milba-yoghurt-1kg",
                            ),
                        ],
                    }
                ],
            },
            {
                "name": "Freezer",
                "subcategories": [
                    {
                        "name": "Chicken",
                        "products": [
                            _product(
                                "Qualiko Frozen Whole Chicken Ukraine 1000g",
                                item_id="qualiko-frozen-1kg",
                                category="Freezer",
                                sub="Chicken",
                            ),
                        ],
                    }
                ],
            },
            {
                "name": "Fresh Meat",
                "subcategories": [
                    {
                        "name": "Chicken",
                        "products": [
                            _product(
                                "D'Pollo Chilled Fresh Whole Chicken UAE 900g",
                                item_id="dpollo-whole-900g",
                                category="Fresh Meat",
                                sub="Chicken",
                            ),
                            _product(
                                "Tender Chicken Breast UAE 400g",
                                item_id="tender-breast-400g",
                                category="Fresh Meat",
                                sub="Chicken",
                            ),
                        ],
                    },
                    {
                        "name": "Beef",
                        "products": [
                            _product(
                                "Beef Mince Brazil 500g",
                                item_id="beef-mince-500g",
                                category="Fresh Meat",
                                sub="Beef",
                            ),
                        ],
                    },
                ],
            },
            {
                "name": "Produce",
                "subcategories": [
                    {
                        "name": "Vegetables",
                        "products": [
                            _product("Tomato 500g", item_id="tomato-500g", category="Produce", sub="Vegetables"),
                            _product("Onion Pink 500g", item_id="onion-500g", category="Produce", sub="Vegetables"),
                        ],
                    }
                ],
            },
        ],
    }


@pytest.fixture
def catalog_index(sample_catalog) -> CatalogIndex:
    return CatalogIndex(sample_catalog)


def make_spec(**kwargs) -> BasketMatchSpec:
    defaults = {
        "line_no": 1,
        "basket_item_id": 1,
        "basket_label": "",
        "category": "",
        "match_group": "packaged",
        "line_role": "",
        "store_label": "",
        "store_brand_name": "",
    }
    defaults.update(kwargs)
    return BasketMatchSpec.from_basket_row(**defaults)
