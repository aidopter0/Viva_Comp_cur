"""Match-group classification for basket lines (produce / meat / packaged)."""

from __future__ import annotations

MATCH_GROUP_PRODUCE = "produce"
MATCH_GROUP_MEAT = "meat"
MATCH_GROUP_PACKAGED = "packaged"


def match_group_for_category(category: str) -> str:
    """Derive match_group from basket CSV category."""
    cat = str(category or "").upper()
    if "FRESH VEGETABLE" in cat or "FRESH FRUIT" in cat:
        return MATCH_GROUP_PRODUCE
    if "FRESH MEAT" in cat or "FREEZER" in cat:
        return MATCH_GROUP_MEAT
    return MATCH_GROUP_PACKAGED


def is_unbranded_group(group: str) -> bool:
    """Produce lines never carry a brand token."""
    return str(group or "").strip() == MATCH_GROUP_PRODUCE


def is_meat_group(group: str) -> bool:
    return str(group or "").strip() == MATCH_GROUP_MEAT


def is_produce_group(group: str) -> bool:
    return str(group or "").strip() == MATCH_GROUP_PRODUCE
