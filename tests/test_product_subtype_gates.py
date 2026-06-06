"""Product subtype gates: mince, pasta, apple, multipack water."""

from __future__ import annotations

from viva_tracker.catalog_match import product_form_compatible


def test_beef_mince_rejects_chicken_mince():
    ok = product_form_compatible(
        "Beef Mince 1kg",
        "Chicken Mince Brazil 500g",
        category="FRESH MEAT",
        basket_label="Beef Mince 1kg",
        line_no=32,
    )
    assert not ok


def test_beef_mince_accepts_beef_mince():
    ok = product_form_compatible(
        "Beef Mince 1kg",
        "Carrefour BZ Beef Mince Brazil 1kg",
        category="FRESH MEAT",
        basket_label="Beef Mince 1kg",
        line_no=32,
    )
    assert ok


def test_penne_rejects_spaghetti():
    ok = product_form_compatible(
        "Penne Rigate Pasta 500g",
        "Reggia Spaghetti Pasta 500g",
        category="COOKING & BAKING INGREDIENTS",
        basket_label="Penne Rigate Pasta 500g",
    )
    assert not ok


def test_royal_gala_rejects_fuji():
    ok = product_form_compatible(
        "Royal Gala Apple 1kg",
        "Apple Fuji 1kg",
        category="FRESH FRUIT",
        basket_label="Royal Gala Apple 1kg",
    )
    assert not ok


def test_water_multipack_rejects_single_bottle():
    ok = product_form_compatible(
        "Bottled Water 1.5L x 6",
        "Al Reem Drinking Water 1.5L",
        category="COLD DRINKS",
        basket_label="Bottled Water 1.5L x 6",
        line_no=9,
    )
    assert not ok


def test_uht_milk_rejects_yogurt():
    ok = product_form_compatible(
        "Hayatna Milk Full Fat UHT DAIRY",
        "Hayatna Yogurt Mango Full Cream, UAE, 140g",
        category="DAIRY",
    )
    assert not ok
