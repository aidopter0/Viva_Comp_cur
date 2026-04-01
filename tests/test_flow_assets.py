"""TC-007: documentation and fixture assets exist."""

from __future__ import annotations

from pathlib import Path


def test_flow_tests_layout(flow_tests_dir: Path) -> None:
    assert (flow_tests_dir / "README.md").is_file()
    assert (flow_tests_dir / "PLAN.md").is_file()
    assert (flow_tests_dir / "docs" / "PIPELINE.md").is_file()
    assert (flow_tests_dir / "cases" / "CASES.md").is_file()


def test_data_fixtures_exist(flow_tests_data: Path) -> None:
    for name in (
        "minimal_key_items.txt",
        "minimal_stores.json",
        "minimal_prepared_gemini.json",
        "minimal_raw_extract.json",
    ):
        p = flow_tests_data / name
        assert p.is_file(), f"missing fixture {p}"
