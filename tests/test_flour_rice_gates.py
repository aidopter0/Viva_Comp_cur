"""Flour/rice subtype gates and descriptor token behavior."""

from __future__ import annotations

from viva_tracker.catalog_match import (
    distinctive_name_tokens,
    product_form_compatible,
    required_subtype_phrases_in_blob,
)


def test_all_purpose_flour_keeps_purpose_as_distinctive_token():
    tokens = distinctive_name_tokens("All Purpose Flour")
    assert "purpose" in tokens
    assert "flour" in tokens


def test_all_purpose_flour_rejects_corn_flour():
    ok = product_form_compatible(
        "All Purpose Flour 2kg",
        "Carrefour Corn Flour 400g",
        category="COMMODITIES",
        basket_label="All Purpose Flour 2kg",
    )
    assert not ok


def test_all_purpose_flour_accepts_all_purpose_match():
    ok = product_form_compatible(
        "All Purpose Flour 2kg",
        "Ama All Purpose Flour 2kg",
        category="COMMODITIES",
        basket_label="All Purpose Flour 2kg",
    )
    assert ok


def test_chakki_atta_rejects_all_purpose_flour():
    ok = product_form_compatible(
        "Chakki Atta Whole Wheat Flour 5kg",
        "Ama All Purpose Flour 2kg",
        category="COMMODITIES",
        basket_label="Chakki Atta Whole Wheat Flour 5kg",
    )
    assert not ok


def test_jasmine_rice_rejects_non_rice_jasmine_product():
    ok = product_form_compatible(
        "Jasmine Rice 5kg",
        "LuLu Toilet Cleaner Peach & Jasmine, 500ml",
        category="COMMODITIES",
        basket_label="Jasmine Rice 5kg",
    )
    assert not ok


def test_basmati_rejects_jasmine_rice():
    ok = product_form_compatible(
        "Basmati Rice 5kg",
        "Tilda Jasmine Fragrant Rice 1kg",
        category="COMMODITIES",
        basket_label="Basmati Rice 5kg",
    )
    assert not ok


def test_required_subtype_phrases_basmati():
    assert required_subtype_phrases_in_blob(
        "basmati rice",
        "Basmati Rice 5kg",
        "carrefour basmati rice india 5kg",
    )
    assert not required_subtype_phrases_in_blob(
        "basmati rice",
        "Basmati Rice 5kg",
        "carrefour jasmine fragrant rice 1kg",
    )


def test_chakki_atta_subtype_allows_interrupting_word():
    # "Chakki Atta" with "Fresh" between the two tokens must still satisfy the subtype.
    assert required_subtype_phrases_in_blob(
        "Chakki Atta Whole Wheat Flour",
        "Chakki Atta Whole Wheat Flour 5kg",
        "flour & grains / atta gala chakki fresh atta 5kg",
        mapped_name="Gala Chakki Fresh Atta",
    )


def test_royal_gala_subtype_allows_reversed_word_order():
    # Store lists the variety as "Apple Royal" (reversed) — token-set match must accept it.
    assert required_subtype_phrases_in_blob(
        "Royal Gala Apple",
        "Royal Gala Apple 1kg",
        "fresh fruit / apples apple royal approx. 900g-1000g",
        mapped_name="Apple Royal",
    )


def test_plural_basket_token_matches_singular_catalog():
    # Basket generic "facial tissues" (plural) must match catalog "Facial Tissue" (singular).
    assert required_subtype_phrases_in_blob(
        "facial tissues",
        "Facial Tissue 5 Pack",
        "household / tissue nuvette super soft 2-ply facial tissue 5x200 sheets",
        mapped_name="soft facial tissues",
    )
