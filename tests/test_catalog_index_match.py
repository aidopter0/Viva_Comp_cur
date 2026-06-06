"""CatalogIndex basket-first matching tests."""

from __future__ import annotations

from viva_tracker.basket_match_spec import LINE_ROLE_OUTSIDE_BRAND, LINE_ROLE_OWN_BRAND
from viva_tracker.catalog_match import product_form_compatible

from conftest import make_spec


def test_milba_own_brand_uht_matches_milba(catalog_index):
    spec = make_spec(
        line_no=12,
        basket_label="Own Brand Full Cream UHT Milk 1L",
        category="DAIRY",
        line_role=LINE_ROLE_OWN_BRAND,
        store_brand_name="Milba",
    )
    row, pm, score = catalog_index.find_basket_match(spec)
    assert row is not None
    assert "milba" in row["product_name"].lower()
    assert pm == "exact"
    assert score >= 0.5


def test_outside_brand_uht_rejects_milba(catalog_index):
    spec = make_spec(
        line_no=13,
        basket_label="Brand Full Cream UHT Milk 1L",
        category="DAIRY",
        line_role=LINE_ROLE_OUTSIDE_BRAND,
        store_brand_name="Milba",
    )
    row, pm, score = catalog_index.find_basket_match(spec)
    assert row is not None
    assert "milba" not in row["product_name"].lower()
    assert pm == "exact"


def test_tomato_without_brand_passes_tokens(catalog_index):
    spec = make_spec(
        line_no=39,
        basket_label="Tomato 500g",
        category="PRODUCE",
        match_group="produce",
    )
    row, pm, _ = catalog_index.find_basket_match(spec)
    assert row is not None
    assert "tomato" in row["product_name"].lower()
    assert pm in {"close", "exact", "different"}


def test_frozen_chicken_rejects_chilled_whole(catalog_index):
    spec = make_spec(
        line_no=21,
        basket_label="Frozen whole chicken 900g",
        category="FREEZER",
    )
    chilled = {
        "subcategory_path": "Fresh Meat / Chicken",
        "product_name": "D'Pollo Chilled Fresh Whole Chicken UAE 900g",
        "url": "",
    }
    assert not product_form_compatible(spec.form_context(), chilled["product_name"], category=spec.category)


def test_fresh_whole_chicken_rejects_frozen(catalog_index):
    spec = make_spec(
        line_no=31,
        basket_label="Fresh whole chicken 1kg",
        category="FRESH MEAT",
    )
    frozen = {
        "subcategory_path": "Freezer / Chicken",
        "product_name": "Qualiko Frozen Whole Chicken Ukraine 1000g",
        "url": "",
    }
    assert not product_form_compatible(spec.form_context(), frozen["product_name"], category=spec.category)
