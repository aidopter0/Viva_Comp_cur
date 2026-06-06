"""Tests for match_group form guards."""

from __future__ import annotations

from viva_tracker.match_form_guards import passes_form_guard

from conftest import make_spec


def test_meat_group_rejects_jerky():
    spec = make_spec(
        line_no=32,
        basket_label="Beef Mince 1kg",
        match_group="meat",
        category="FRESH MEAT",
    )
    row = {
        "product_name": "Beef Jerky Snack Pack 50g",
        "subcategory_path": "Snacks / Meat",
        "url": "https://example.com/product/beef-jerky-snack/s/1",
    }
    assert not passes_form_guard(row, spec)


def test_produce_group_rejects_carrot_flavoured_drink():
    spec = make_spec(
        line_no=38,
        basket_label="Carrot 1kg",
        match_group="produce",
        category="FRESH VEGETABLE",
    )
    row = {
        "product_name": "Carrot Flavoured Juice Drink 1L",
        "subcategory_path": "Beverages / Juice",
        "url": "https://example.com/product/carrot-juice/s/1",
        "category_hint": "Beverage",
    }
    assert not passes_form_guard(row, spec)
