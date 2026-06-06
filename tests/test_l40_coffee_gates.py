"""L40 3-in-1 coffee identity gates."""

from __future__ import annotations

from viva_tracker.catalog_match import required_subtype_phrases_in_blob
from viva_tracker.match_form_guards import passes_form_guard

from conftest import make_spec


def test_l40_rejects_plain_instant_coffee():
    spec = make_spec(
        line_no=40,
        basket_label="3-in-1 Coffee Mix 30 sachets",
        category="HOT DRINKS",
    )
    row = {
        "product_name": "Belmundo Cremoso Caffe GOLD Instant Coffee, 100g",
        "subcategory_path": "Hot Drinks / Coffee",
        "url": "",
    }
    assert not passes_form_guard(row, spec)


def test_l40_accepts_3in1_mix_600g():
    spec = make_spec(
        line_no=40,
        basket_label="3-in-1 Coffee Mix 30 sachets",
        category="HOT DRINKS",
    )
    row = {
        "product_name": "Belmundo Classic 3-in-1 Coffee Mix 600g",
        "subcategory_path": "Hot Drinks / Coffee",
        "url": "",
    }
    assert passes_form_guard(row, spec)


def test_l40_accepts_3in1_variant_in_title():
    spec = make_spec(
        line_no=40,
        basket_label="3-in-1 Coffee Mix 30 sachets",
        category="HOT DRINKS",
    )
    row = {
        "product_name": "Golden Best 3In1 Classic Instant Coffee 432g",
        "subcategory_path": "Hot Drinks / Coffee",
        "url": "",
    }
    assert passes_form_guard(row, spec)


def test_required_subtype_3in1_coffee():
    assert required_subtype_phrases_in_blob(
        "3 in 1 instant coffee",
        "3-in-1 Coffee Mix 30 sachets",
        "belmundo classic 3-in-1 coffee mix 600g",
        mapped_name="Belmundo 3 in 1 instant coffe",
    )
    assert not required_subtype_phrases_in_blob(
        "3 in 1 instant coffee",
        "3-in-1 Coffee Mix 30 sachets",
        "belmundo cremoso instant coffee 100g",
        mapped_name="Belmundo 3 in 1 instant coffe",
    )
