"""Match engine v2 resolve-path tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from viva_tracker.basket_match_spec import LINE_ROLE_OWN_BRAND
from viva_tracker.catalog_index import CatalogIndex
from viva_tracker.match_engine import _build_spec, _resolve_line_match_v2

from conftest import make_spec


def _nut_bar_index() -> CatalogIndex:
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
                                    "product_name": "Nature Valley Nut Cereal Bar 40g",
                                    "url": "https://example.com/product/nv-nut-bar-40g/s/1",
                                    "price": 5.0,
                                }
                            ],
                        }
                    ],
                }
            ]
        }
    )


def test_own_brand_uht_picks_milba(catalog_index):
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
        catalog_index, spec, bl, client=None, model="test"
    )
    assert picked is not None
    assert status == "ok"
    assert pack_match == "exact"
    assert "milba" in picked["product_name"].lower()


def test_outside_brand_prefers_non_milba(catalog_index):
    bl = {
        "line_no": 13,
        "basket_item_id": 13,
        "basket_label": "Brand Full Cream UHT Milk 1L",
        "category": "DAIRY",
        "match_group": "packaged",
        "line_role": "outside_brand",
    }
    spec = _build_spec(bl, store_label="Test", brand_name="Milba")
    picked, status, _, _, _ = _resolve_line_match_v2(
        catalog_index, spec, bl, client=None, model="test"
    )
    assert picked is not None
    assert "milba" not in picked["product_name"].lower()


@patch("viva_tracker.match_engine.verify_pick")
def test_gpt_verify_reject_triggers_fallback(mock_verify, catalog_index):
    mock_verify.return_value = {"accept": False, "reason": "wrong"}
    bl = {
        "line_no": 12,
        "basket_item_id": 12,
        "basket_label": "Own Brand Full Cream UHT Milk 1L",
        "category": "DAIRY",
        "line_role": LINE_ROLE_OWN_BRAND,
    }
    spec = _build_spec(bl, store_label="Test", brand_name="Milba")
    client = MagicMock()
    with patch("viva_tracker.match_engine.pick_from_candidates") as mock_pick:
        milba_ref = next(
            r["ref"]
            for r in catalog_index.rows
            if "milba" in r["product_name"].lower() and "uht" in r["product_name"].lower()
        )
        mock_pick.return_value = {"ref": milba_ref, "confidence": 0.8, "reason": "picked"}
        picked, status, _, _, _ = _resolve_line_match_v2(
            catalog_index, spec, bl, client=client, model="test"
        )
    assert picked is not None
    mock_pick.assert_called_once()


@patch("viva_tracker.match_engine.pick_from_candidates")
def test_resolve_round2_gpt_pick(mock_pick):
    index = _nut_bar_index()
    bl = {
        "line_no": 3,
        "basket_item_id": 3,
        "basket_label": "Mixed Nuts Bar 40g",
        "category": "SNACKS",
        "match_group": "packaged",
        "line_role": "",
    }
    spec = _build_spec(bl, store_label="Test", brand_name="Test")
    ref = index.rows[0]["ref"]
    mock_pick.return_value = {"ref": ref, "confidence": 0.9, "reason": "same nut-bar category"}
    client = MagicMock()
    picked, status, pack_match, _, reason = _resolve_line_match_v2(
        index, spec, bl, client=client, model="test"
    )
    assert picked is not None
    assert picked["item_id"] == "b1"
    assert status == "ok"
    assert pack_match == "exact"
    assert reason.startswith("Near-match: ")
    mock_pick.assert_called_once()
    _, kwargs = mock_pick.call_args
    assert kwargs.get("intent_pick") is True


def test_round1_unchanged(catalog_index):
    """A clean exact match still resolves in Round 1 and never enters Round 2."""
    bl = {
        "line_no": 12,
        "basket_item_id": 12,
        "basket_label": "Own Brand Full Cream UHT Milk 1L",
        "category": "DAIRY",
        "match_group": "packaged",
        "line_role": LINE_ROLE_OWN_BRAND,
    }
    spec = _build_spec(bl, store_label="Test", brand_name="Milba")
    picked, status, pack_match, _, reason = _resolve_line_match_v2(
        catalog_index, spec, bl, client=None, model="test"
    )
    assert picked is not None
    assert status == "ok"
    assert pack_match == "exact"
    assert not reason.startswith("Near-match: ")
