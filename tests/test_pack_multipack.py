"""Multipack parsing and tiered pack ranking."""

from __future__ import annotations

from viva_tracker.basket_matcher import PACK_RANK
from viva_tracker.pack_normalize import (
    pack_match_tier,
    TitlePackInfo,
    multipack_pattern_compatible,
    multipacks_equivalent,
    parse_basket_multipack,
    parse_title_pack_info,
    split_pack_fields,
)


def test_split_pack_fields_20x30_g():
    assert split_pack_fields("20x30", "g") == ("600", "g")


def test_split_pack_fields_30_x_20_g():
    assert split_pack_fields("30 x 20", "g") == ("600", "g")


def test_multipacks_equivalent_swapped():
    a = TitlePackInfo("20 x 30 g", "600", "g", pack_count=20, unit_qty="30", unit="g")
    b = TitlePackInfo("30 x 20 g", "600", "g", pack_count=30, unit_qty="20", unit="g")
    assert multipacks_equivalent(a, b)


def test_multipacks_not_equivalent_15x40():
    a = TitlePackInfo("20 x 30 g", "600", "g", pack_count=20, unit_qty="30", unit="g")
    b = TitlePackInfo("15 x 40 g", "600", "g", pack_count=15, unit_qty="40", unit="g")
    assert not multipacks_equivalent(a, b)


def test_multipack_pattern_rejects_wrong_explicit_multipack():
    assert not multipack_pattern_compatible("20x30", "g", "Some Coffee 15 x 40 g")


def test_multipack_pattern_allows_plain_total_weight():
    assert multipack_pattern_compatible("20x30", "g", "Belmundo Classic 3-in-1 Coffee Mix 600g")


def _rank(title: str) -> int:
    return PACK_RANK.get(pack_match_tier(title, "20x30", "g"), 0)


def test_pack_rank_multipack_beats_total_weight():
    assert _rank("Belmundo 3-in-1 Coffee 20 x 30 g") > _rank("Belmundo Classic 3-in-1 Coffee Mix 600g")
    assert _rank("Belmundo Classic 3-in-1 Coffee Mix 600g") > _rank("Belmundo Instant Coffee 100g")



def test_parse_basket_multipack_skips_count_pack_lines():
    assert parse_basket_multipack("150 x 5", "pack") is None


def test_parse_title_pack_info_preserves_multipack_fields():
    info = parse_title_pack_info("Belmundo 3-in-1 Coffee 20 x 30 g")
    assert info is not None
    assert info.pack_count == 20
    assert info.unit_qty == "30"
    assert info.total_qty == "600"
