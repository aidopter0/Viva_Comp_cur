"""TC-006: orchestrator appends from reranked CSV only after rerank block."""

from __future__ import annotations

from pathlib import Path


def test_run_talabat_stores_append_after_rerank() -> None:
    root = Path(__file__).resolve().parents[1]
    src = (root / "run_talabat_stores.py").read_text(encoding="utf-8")
    assert "if not args.gemini_after:" in src
    assert "append_consolidated(args.consolidated" in src
    assert ".reranked.csv" in src
    idx_rerank = src.find("if args.gemini_after:")
    idx_append_rr = src.find("rr_csv = args.out_dir")
    assert idx_rerank != -1 and idx_append_rr != -1
    assert idx_rerank < idx_append_rr
