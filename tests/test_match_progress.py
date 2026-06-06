"""Tests for GPT matching progress reporting."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from viva_tracker.match_progress import MatchProgress, format_match_progress
from viva_tracker.match_engine import match_store


def test_format_match_progress_starting():
    text = format_match_progress(
        MatchProgress(store_label="Test", phase="starting", lines_total=41, skipped=3)
    )
    assert "41 basket line(s)" in text
    assert "3 skipped" in text


def test_format_match_progress_matching():
    text = format_match_progress(
        MatchProgress(
            store_label="Test",
            phase="matching",
            lines_total=10,
            lines_completed=4,
            line_no=12,
            basket_label="Own Brand Full Cream UHT Milk 1L",
            ok=3,
            pack_mismatch=0,
            missing=1,
        )
    )
    assert "Line 4/10" in text
    assert "L12" in text
    assert "ok=3" in text


def test_match_progress_fraction():
    p = MatchProgress(
        store_label="Test",
        phase="matching",
        lines_total=8,
        lines_completed=2,
    )
    assert p.progress_fraction == 0.25
    done = MatchProgress(store_label="Test", phase="done", lines_total=8, lines_completed=8)
    assert done.progress_fraction == 1.0


def test_match_store_emits_progress_phases():
    events: list[str] = []

    def on_progress(progress: MatchProgress) -> None:
        events.append(progress.phase)

    conn = MagicMock()
    with (
        patch("viva_tracker.match_engine.catalog_exists", return_value=True),
        patch("viva_tracker.match_engine.load_catalog_file", return_value={"categories": []}),
        patch("viva_tracker.match_engine.CatalogIndex", return_value=MagicMock()),
        patch(
            "viva_tracker.match_engine.list_basket_items",
            return_value=[
                {
                    "line_no": 1,
                    "basket_item_id": 1,
                    "basket_label": "Tomato 1kg",
                    "category": "PRODUCE",
                    "match_group": "produce",
                    "line_role": "default",
                }
            ],
        ),
        patch("viva_tracker.match_engine.basket_ids_with_urls", return_value=set()),
        patch("viva_tracker.match_engine.openai_client", return_value=None),
        patch("viva_tracker.match_engine.lookup_override", return_value=None),
        patch("viva_tracker.match_engine._try_override", return_value=None),
        patch(
            "viva_tracker.match_engine._resolve_line_match_v2",
            return_value=(None, "missing", "unknown", None, "none"),
        ),
        patch("viva_tracker.match_engine._record_pick"),
    ):
        match_store(
            conn,
            store_id=1,
            store_label="Test Store",
            brand_name="Test",
            skip_existing=False,
            progress_callback=on_progress,
        )

    assert events[0] == "starting"
    assert "matching" in events
    assert events[-1] == "done"
