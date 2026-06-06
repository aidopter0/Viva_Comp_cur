"""L2 white sliced bread form guards."""

from __future__ import annotations

from viva_tracker.catalog_match import required_subtype_phrases_in_blob
from viva_tracker.match_form_guards import passes_form_guard

from conftest import make_spec


def _l2_spec(**kwargs):
    defaults = dict(
        line_no=2,
        basket_label="White Sliced Bread Large 600g",
        category="BAKERY",
    )
    defaults.update(kwargs)
    return make_spec(**defaults)


def test_l2_rejects_milk_bread():
    spec = _l2_spec()
    row = {
        "product_name": "Carrefour Milk Bread, 600g",
        "subcategory_path": "Bakery / Bread",
        "url": "",
    }
    assert not passes_form_guard(row, spec)


def test_l2_accepts_white_bread_600g():
    spec = _l2_spec()
    row = {
        "product_name": "Carrefour White Bread, 600g",
        "subcategory_path": "Bakery / Bread",
        "url": "",
    }
    assert passes_form_guard(row, spec)


def test_l2_accepts_white_bread_360g():
    spec = _l2_spec()
    row = {
        "product_name": "Carrefour White Bread, 360g",
        "subcategory_path": "Bakery / Bread",
        "url": "",
    }
    assert passes_form_guard(row, spec)


def test_l2_rejects_brown_bread():
    spec = _l2_spec()
    row = {
        "product_name": "L'usine Sliced Brown Bread, 600g",
        "subcategory_path": "Bakery / Bread",
        "url": "",
    }
    assert not passes_form_guard(row, spec)


def test_required_subtype_white_bread():
    assert required_subtype_phrases_in_blob(
        "",
        "White Sliced Bread Large 600g",
        "bakery bread carrefour white bread 600g",
        mapped_name="Carrefour White bread",
    )
    assert not required_subtype_phrases_in_blob(
        "",
        "White Sliced Bread Large 600g",
        "bakery bread carrefour milk bread 600g",
        mapped_name="Carrefour White bread",
    )
