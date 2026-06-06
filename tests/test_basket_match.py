"""Basket-first matching v2: tokens, pack, role filters, shortlist."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from viva_tracker.basket_match_spec import (
    LINE_ROLE_OUTSIDE_BRAND,
    LINE_ROLE_OWN_BRAND,
    basket_tokens_from_label,
    line_role_for_line,
    parse_pack_from_basket_label,
)
from viva_tracker.basket_matcher import (
    cheapest_among_intent_peers,
    cheapest_row,
    near_match_pool_rows,
    order_candidates_for_gpt_pick,
    passes_line_role,
    score_near_match_row,
    shortlist_rows,
    top_near_match_rows,
    top_percentile_rows,
)
from viva_tracker.catalog_index import CatalogIndex
from viva_tracker.match_engine import _build_spec, _resolve_line_match_v2
from viva_tracker.match_form_guards import passes_form_guard

from conftest import make_spec


def test_basket_tokens_keep_discriminators():
    tokens = basket_tokens_from_label("White Sliced Bread Large 600g")
    assert "white" in tokens
    assert "sliced" in tokens
    assert "bread" in tokens
    assert "600g" not in tokens


def test_parse_pack_from_basket_label():
    qty, unit = parse_pack_from_basket_label("Shredded Mozzarella 500g")
    assert qty == "500"
    assert unit.lower().startswith("g")


def test_line_role_from_config():
    assert line_role_for_line(12, "Own Brand Full Cream UHT Milk 1L") == LINE_ROLE_OWN_BRAND
    assert line_role_for_line(13, "Brand Full Cream UHT Milk 1L") == LINE_ROLE_OUTSIDE_BRAND
    assert line_role_for_line(5, "Shredded Mozzarella 500g") == "default"


def test_own_brand_role_requires_store_name_in_title():
    spec = make_spec(
        line_no=12,
        basket_label="Own Brand Full Cream UHT Milk 1L",
        line_role=LINE_ROLE_OWN_BRAND,
        store_brand_name="Milba",
        category="DAIRY",
    )
    milba = {
        "product_name": "Milba Full Cream UHT Milk UAE 1L",
        "subcategory_path": "Dairy / Milk",
        "url": "https://example.com/product/milba-uht-1l",
    }
    hayatna = {
        "product_name": "Hayatna Full Cream UHT Milk UAE 1L",
        "subcategory_path": "Dairy / Milk",
        "url": "https://example.com/product/hayatna-uht-1l",
    }
    assert passes_line_role(milba, spec)
    assert not passes_line_role(hayatna, spec)


def test_outside_brand_role_excludes_store_name(catalog_index):
    spec = make_spec(
        line_no=13,
        basket_label="Brand Full Cream UHT Milk 1L",
        line_role=LINE_ROLE_OUTSIDE_BRAND,
        store_brand_name="Milba",
        category="DAIRY",
    )
    row, pm, _ = catalog_index.find_basket_match(spec)
    assert row is not None
    assert "milba" not in row["product_name"].lower()
    assert pm == "exact"


def test_carrot_shortlist_cheapest(sample_catalog):
    index = CatalogIndex(
        {
            "categories": [
                {
                    "name": "Fresh Vegetable",
                    "subcategories": [
                        {
                            "name": "Root",
                            "products": [
                                {
                                    "item_id": "c1",
                                    "product_name": "Carrot, 1kg",
                                    "url": "https://example.com/product/carrot-1kg/s/1",
                                    "price": 4.5,
                                },
                                {
                                    "item_id": "c2",
                                    "product_name": "Carrot Bag, 1kg",
                                    "url": "https://example.com/product/carrot-bag-1kg/s/2",
                                    "price": 2.75,
                                },
                            ],
                        }
                    ],
                }
            ]
        }
    )
    spec = make_spec(
        line_no=38,
        basket_label="Carrot 1kg",
        category="FRESH VEGETABLE",
        match_group="produce",
    )
    shortlist = shortlist_rows(index, spec)
    pick = cheapest_row(shortlist)
    assert pick is not None
    assert pick["item_id"] == "c2"


def test_mozzarella_requires_500g_pack():
    index = CatalogIndex(
        {
            "categories": [
                {
                    "name": "Dairy",
                    "subcategories": [
                        {
                            "name": "Cheese",
                            "products": [
                                {
                                    "item_id": "m400",
                                    "product_name": "Shredded Mozzarella 400g",
                                    "url": "https://example.com/product/mozz-400/s/1",
                                    "price": 10.0,
                                },
                                {
                                    "item_id": "m500",
                                    "product_name": "Shredded Mozzarella 500g",
                                    "url": "https://example.com/product/mozz-500/s/2",
                                    "price": 12.0,
                                },
                            ],
                        }
                    ],
                }
            ]
        }
    )
    spec = make_spec(
        line_no=5,
        basket_label="Shredded Mozzarella 500g",
        category="DAIRY",
    )
    shortlist = shortlist_rows(index, spec)
    assert len(shortlist) == 1
    assert shortlist[0]["item_id"] == "m500"


def test_carrot_form_guard_rejects_peas_mix():
    spec = make_spec(
        line_no=38,
        basket_label="Carrot 1kg",
        category="FRESH VEGETABLE",
        match_group="produce",
    )
    row = {
        "product_name": "Giovanni Peas & Carrots, Italy, 400g",
        "subcategory_path": "Cans / Vegetables",
        "url": "https://example.com/product/peas-carrots/s/1",
        "category_hint": "Canned",
    }
    assert not passes_form_guard(row, spec)


def test_top_percentile_pool():
    spec = make_spec(line_no=1, basket_label="Tomato 1kg", category="PRODUCE")
    rows = [{"ref": str(i), "product_name": f"Tomato {i}kg", "url": ""} for i in range(10)]
    top = top_percentile_rows(rows, spec, fraction=0.10)
    assert len(top) == 1


def test_resolve_v2_auto_pick_without_gpt(catalog_index):
    bl = {
        "line_no": 12,
        "basket_item_id": 12,
        "basket_label": "Own Brand Full Cream UHT Milk 1L",
        "category": "DAIRY",
        "match_group": "packaged",
        "line_role": LINE_ROLE_OWN_BRAND,
    }
    spec = _build_spec(bl, store_label="Test", brand_name="Milba")
    picked, status, pack_match, _, _ = _resolve_line_match_v2(
        catalog_index, spec, bl, client=None, model="gpt-test"
    )
    assert picked is not None
    assert status == "ok"
    assert pack_match == "exact"
    assert "milba" in picked["product_name"].lower()


def _bar_catalog(name: str, *, price: float = 5.0) -> CatalogIndex:
    return CatalogIndex(
        {
            "categories": [
                {
                    "name": "Snacks",
                    "subcategories": [
                        {
                            "name": "Cereal Bars",
                            "products": [
                                {
                                    "item_id": "b1",
                                    "product_name": name,
                                    "url": "https://example.com/product/near-bar/s/1",
                                    "price": price,
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )


def test_near_pool_mixed_nuts_bar():
    index = _bar_catalog("Nature Valley Nut Cereal Bar 40g")
    spec = make_spec(line_no=3, basket_label="Mixed Nuts Bar 40g", category="SNACKS")
    # Strict shortlist is empty (no title has every token mixed+nuts+bar).
    assert shortlist_rows(index, spec) == []
    pool = near_match_pool_rows(index, spec)
    assert any(r["item_id"] == "b1" for r in pool)
    top = top_near_match_rows(pool, spec, limit=20)
    assert top and top[0]["item_id"] == "b1"


def test_near_pool_respects_12pct_pack():
    index = _bar_catalog("Nature Valley Nut Cereal Bar 125g")
    spec = make_spec(line_no=3, basket_label="Mixed Nuts Bar 40g", category="SNACKS")
    # 125g is far outside +/-12% of a 40g target.
    assert near_match_pool_rows(index, spec) == []


def test_near_pool_excludes_wrong_form():
    index = _bar_catalog("Dove Beauty Soap Bar 40g")
    spec = make_spec(line_no=3, basket_label="Mixed Nuts Bar 40g", category="SNACKS")
    # Same pack and shares the "bar" token, but the form guard blocks a soap bar.
    assert near_match_pool_rows(index, spec) == []


def test_cheapest_among_intent_peers():
    rows = [
        {
            "ref": "0",
            "item_id": "expensive",
            "product_name": "L'usine Sliced White Bread, 600g",
            "subcategory_path": "Bakery / Bread",
            "url": "https://example.com/product/lusine-white-600/s/1",
            "price": 8.0,
        },
        {
            "ref": "1",
            "item_id": "cheap",
            "product_name": "Yaumi Sliced White Bread, 600g",
            "subcategory_path": "Bakery / Bread",
            "url": "https://example.com/product/yaumi-white-600/s/2",
            "price": 5.0,
        },
    ]
    spec = make_spec(line_no=2, basket_label="White Sliced Bread Large 600g", category="BAKERY")
    picked = rows[0]
    best = cheapest_among_intent_peers(
        picked,
        rows,
        spec,
        score_fn=score_near_match_row,
        pack_tolerance=0.12,
    )
    assert best["item_id"] == "cheap"


def test_cheapest_peer_keeps_carrefour_white_bread():
    rows = [
        {
            "ref": "0",
            "item_id": "lusine",
            "product_name": "L'usine Sliced White Bread, 600g",
            "subcategory_path": "Bakery / Bread",
            "url": "https://example.com/product/lusine/s/1",
            "price": 4.49,
        },
        {
            "ref": "1",
            "item_id": "carrefour",
            "product_name": "Carrefour White Bread, 600g",
            "subcategory_path": "Bakery / Bread",
            "url": "https://example.com/product/carrefour/s/2",
            "price": 3.29,
        },
    ]
    spec = make_spec(line_no=2, basket_label="White Sliced Bread Large 600g", category="BAKERY")
    best = cheapest_among_intent_peers(
        rows[0],
        rows,
        spec,
        score_fn=score_near_match_row,
        pack_tolerance=0.12,
    )
    assert best["item_id"] == "carrefour"


def test_nuts_bar_near_pool_excludes_chocolate_bars():
    index = CatalogIndex(
        {
            "categories": [
                {
                    "name": "Snacks",
                    "subcategories": [
                        {
                            "name": "Bars",
                            "products": [
                                {
                                    "item_id": "snickers",
                                    "product_name": "Snickers Chocolate Bar Filled with Caramel & Peanuts 50g",
                                    "url": "https://example.com/product/snickers/s/1",
                                    "price": 3.5,
                                },
                                {
                                    "item_id": "nutbar",
                                    "product_name": "Be-Kind Roasted Honey Nuts & Sea Salt Bar, 40g",
                                    "url": "https://example.com/product/bekind/s/2",
                                    "price": 5.0,
                                },
                            ],
                        }
                    ],
                }
            ]
        }
    )
    spec = make_spec(line_no=3, basket_label="Mixed Nuts Bar 40g", category="SNACKS")
    pool = near_match_pool_rows(index, spec)
    ids = {r["item_id"] for r in pool}
    assert "nutbar" in ids
    assert "snickers" not in ids


def test_order_candidates_for_gpt_pick_cheapest_first():
    rows = [
        {
            "ref": "0",
            "item_id": "a",
            "product_name": "White Sliced Bread 600g",
            "subcategory_path": "Bakery / Bread",
            "url": "https://example.com/product/a/s/1",
            "price": 9.0,
        },
        {
            "ref": "1",
            "item_id": "b",
            "product_name": "White Sliced Bread 600g",
            "subcategory_path": "Bakery / Bread",
            "url": "https://example.com/product/b/s/2",
            "price": 4.0,
        },
    ]
    spec = make_spec(line_no=2, basket_label="White Sliced Bread Large 600g", category="BAKERY")
    ordered = order_candidates_for_gpt_pick(rows, spec, score_near_match_row, pack_tolerance=0.12)
    assert ordered[0]["item_id"] == "b"


@patch("viva_tracker.match_engine.verify_pick")
def test_resolve_v2_gpt_reject_falls_back(mock_verify, catalog_index):
    mock_verify.return_value = {"accept": False, "reason": "wrong product"}
    bl = {
        "line_no": 12,
        "basket_item_id": 12,
        "basket_label": "Own Brand Full Cream UHT Milk 1L",
        "category": "DAIRY",
        "match_group": "packaged",
        "line_role": LINE_ROLE_OWN_BRAND,
    }
    spec = _build_spec(bl, store_label="Test", brand_name="Milba")
    client = MagicMock()
    with patch("viva_tracker.match_engine.pick_from_candidates") as mock_pick:
        mock_pick.return_value = {"ref": None, "reason": "none", "confidence": 0.0}
        picked, status, _, _, reason = _resolve_line_match_v2(
            catalog_index, spec, bl, client=client, model="gpt-test"
        )
    assert picked is None
    assert status == "missing"
    mock_verify.assert_called_once()
