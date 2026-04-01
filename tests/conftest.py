"""Pytest configuration: repo root on path, shared paths."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FLOW_TESTS = ROOT / "flow_tests"


@pytest.fixture
def flow_tests_dir() -> Path:
    return FLOW_TESTS


@pytest.fixture
def flow_tests_data(flow_tests_dir: Path) -> Path:
    return flow_tests_dir / "data"
