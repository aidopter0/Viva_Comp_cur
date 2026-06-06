"""Table-driven Viva regression expectations against fixture catalog."""

from __future__ import annotations

import pytest

from viva_tracker.basket_match_spec import LINE_ROLE_OUTSIDE_BRAND, LINE_ROLE_OWN_BRAND

from conftest import make_spec

VIVA_CASES = [
    pytest.param(
        12,
        {
            "basket_label": "Own Brand Full Cream UHT Milk 1L",
            "category": "DAIRY",
            "line_role": LINE_ROLE_OWN_BRAND,
            "store_brand_name": "Milba",
        },
        {"expect_brand": "milba", "forbid_brand": None},
        id="L12_milba_uht",
    ),
    pytest.param(
        13,
        {
            "basket_label": "Brand Full Cream UHT Milk 1L",
            "category": "DAIRY",
            "line_role": LINE_ROLE_OUTSIDE_BRAND,
            "store_brand_name": "Milba",
        },
        {"expect_brand": None, "forbid_brand": "milba"},
        id="L13_hayatna_not_milba",
    ),
    pytest.param(
        21,
        {
            "basket_label": "Frozen whole chicken 900g",
            "category": "FREEZER",
        },
        {"expect_brand": "qualiko", "forbid_brand": "dpollo"},
        id="L21_frozen_chicken",
    ),
    pytest.param(
        31,
        {
            "basket_label": "Fresh whole chicken 1kg",
            "category": "FRESH MEAT",
        },
        {"expect_brand": "pollo", "forbid_brand": "qualiko"},
        id="L31_fresh_whole_chicken",
    ),
    pytest.param(
        32,
        {
            "basket_label": "Beef Mince 1kg",
            "category": "FRESH MEAT",
        },
        {"expect_brand": "beef", "forbid_brand": "onion"},
        id="L32_beef_mince",
    ),
]


@pytest.mark.parametrize("line_no, fields, expected", VIVA_CASES)
def test_viva_regression(catalog_index, line_no, fields, expected):
    spec = make_spec(line_no=line_no, basket_item_id=line_no, **fields)
    row, pm, score = catalog_index.find_basket_match(spec)
    if expected["forbid_brand"] and row is not None:
        assert expected["forbid_brand"] not in row["product_name"].lower()
    if expected["expect_brand"] and row is not None:
        assert expected["expect_brand"] in row["product_name"].lower()
    if row is not None and expected["forbid_brand"]:
        assert expected["forbid_brand"] not in row["product_name"].lower()
