"""Extended match engine tests: overrides, pack finalize."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from viva_tracker.basket_match_spec import LINE_ROLE_OUTSIDE_BRAND
from viva_tracker.catalog_index import CatalogIndex
from viva_tracker.match_engine import (
    _build_spec,
    _finalize_product_pick,
    _resolve_line_match_v2,
    _try_override,
)
from viva_tracker.match_overrides import load_match_overrides

from conftest import make_spec, sample_catalog


def test_outside_brand_falls_back_to_non_store_brand(sample_catalog):
    cats = json.loads(json.dumps(sample_catalog))
    for cat in cats["categories"]:
        if cat.get("name") != "Dairy":
            continue
        for sub in cat.get("subcategories") or []:
            if sub.get("name") != "Milk":
                continue
            sub["products"] = [
                p
                for p in sub.get("products") or []
                if "black forest" in str(p.get("product_name") or "").lower()
            ]
    index = CatalogIndex(cats)
    spec = make_spec(
        line_no=13,
        basket_label="Brand Organic Full Fat UHT Milk 1L",
        category="DAIRY",
        line_role=LINE_ROLE_OUTSIDE_BRAND,
        store_brand_name="Milba",
    )
    bl = {
        "line_no": 13,
        "basket_item_id": 13,
        "basket_label": spec.basket_label,
        "category": spec.category,
        "line_role": spec.line_role,
    }
    picked, status, _, _, _ = _resolve_line_match_v2(
        index, spec, bl, client=None, model="test"
    )
    assert picked is not None
    assert "black forest" in picked["product_name"].lower()


def test_finalize_sets_pack_mismatch_not_ok():
    row = {"product_name": "Beef Mince Brazil 500g", "url": "", "item_id": "x"}
    spec = make_spec(line_no=32, basket_label="Beef Mince 1kg", category="FRESH MEAT")
    _, status, pm, _, _ = _finalize_product_pick(row, spec, 0.9, "test")
    assert status == "pack_mismatch"
    assert pm == "different"


def test_override_missing_action(sample_catalog, monkeypatch, tmp_path):
    cfg = tmp_path / "match_overrides.json"
    cfg.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": [
                    {
                        "brand_name": "Carrefour",
                        "store_label": "Carrefour Arjan",
                        "line_no": 13,
                        "action": "missing",
                        "note": "Hayatna not stocked",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("viva_tracker.settings.CONFIG_DIR", tmp_path)
    load_match_overrides.cache_clear()
    load_match_overrides()

    index = CatalogIndex(sample_catalog)
    bl = {
        "line_no": 13,
        "basket_item_id": 13,
        "basket_label": "Brand Full Cream UHT Milk 1L",
        "category": "DAIRY",
        "line_role": LINE_ROLE_OUTSIDE_BRAND,
    }
    spec = _build_spec(bl, store_label="Carrefour Arjan", brand_name="Carrefour")
    result = _try_override(index, spec, bl, "Carrefour", "Carrefour Arjan")
    assert result is not None
    picked, status, _, _, reason = result
    assert picked is None
    assert status == "missing"
    assert reason is not None
