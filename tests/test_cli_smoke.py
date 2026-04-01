"""Smoke: CLIs import and --help exit 0."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_help(module: str) -> None:
    r = subprocess.run(
        [sys.executable, module, "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr


def test_run_talabat_stores_help() -> None:
    _run_help("run_talabat_stores.py")


def test_talabat_extract_help() -> None:
    _run_help("talabat_extract.py")


def test_gemini_key_items_builder_help() -> None:
    _run_help("gemini_key_items_builder.py")
