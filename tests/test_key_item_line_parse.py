"""TC-001, TC-002: key_item_line_parse parsing and synonyms."""

from __future__ import annotations

from key_item_line_parse import normalize_for_compare, parse_line, search_synonym_for_row


def test_parse_line_strips_category_and_digits() -> None:
    raw = "EGGS 2White eggs 30's (S)30pc"
    expected, q = parse_line(raw)
    assert "egg" in expected.lower()
    assert q


def test_search_synonym_whole_chicken_900g() -> None:
    raw = "PROTEINSWhole chicken 900g900g"
    exp2, _ = parse_line(raw)
    syn = search_synonym_for_row(exp2)
    assert syn is not None
    assert "griller" in syn.lower()


def test_banana_premium_normalize() -> None:
    assert "banana" in normalize_for_compare("Banana Premium 1 kg")
