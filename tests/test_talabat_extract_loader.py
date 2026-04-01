"""TC-005: load_prepared_key_items accepts flow_tests fixture."""

from __future__ import annotations

from talabat_extract import load_prepared_key_items


def test_load_minimal_prepared_gemini(flow_tests_data) -> None:
    path = flow_tests_data / "minimal_prepared_gemini.json"
    rows = load_prepared_key_items(path)
    assert len(rows) == 2
    assert rows[0]["line"] == 1
    assert "search_query" in rows[0]
