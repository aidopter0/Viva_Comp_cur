"""TC-003, TC-004: prepared_key_items_sync hash logic in isolation."""

from __future__ import annotations

import prepared_key_items_sync as kp
import pytest


@pytest.fixture
def prep_paths(tmp_path, monkeypatch):
    txt = tmp_path / "key_items.txt"
    txt.write_text("line one\nline two\n", encoding="utf-8")
    gem = tmp_path / "prepared.json"
    gem.write_text("[]", encoding="utf-8")
    h = tmp_path / "hash.sha256"
    monkeypatch.setattr(kp, "KEY_ITEMS_TXT", txt)
    monkeypatch.setattr(kp, "GEMINI_JSON", gem)
    monkeypatch.setattr(kp, "HASH_FILE", h)
    return txt, gem, h


def test_needs_prep_when_no_hash(prep_paths) -> None:
    txt, gem, h = prep_paths
    gem.write_text('[{"line":1}]', encoding="utf-8")
    assert h.is_file() is False
    assert kp.needs_gemini_prep(force=False) is True


def test_needs_prep_false_when_hash_matches(prep_paths) -> None:
    txt, gem, h = prep_paths
    kp.write_stored_hash()
    assert kp.needs_gemini_prep(force=False) is False


def test_needs_prep_when_txt_changes(prep_paths) -> None:
    txt, gem, h = prep_paths
    kp.write_stored_hash()
    txt.write_text("changed\n", encoding="utf-8")
    assert kp.needs_gemini_prep(force=False) is True


def test_force_prep(prep_paths) -> None:
    assert kp.needs_gemini_prep(force=True) is True
