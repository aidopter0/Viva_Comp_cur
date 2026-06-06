"""Progress reporting for GPT catalog matching (Streamlit + CLI)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MatchProgressPhase = Literal["starting", "matching", "done", "error"]


@dataclass
class MatchProgress:
    store_label: str
    phase: MatchProgressPhase
    lines_total: int = 0
    lines_completed: int = 0
    line_no: int = 0
    basket_label: str = ""
    ok: int = 0
    pack_mismatch: int = 0
    missing: int = 0
    skipped: int = 0
    message: str = ""

    @property
    def progress_fraction(self) -> float:
        if self.phase == "done":
            return 1.0
        if self.lines_total <= 0:
            return 0.0
        return min(1.0, max(0.0, self.lines_completed / self.lines_total))


def format_match_progress(progress: MatchProgress) -> str:
    if progress.phase == "starting":
        skipped = progress.skipped
        suffix = f" ({skipped} skipped)" if skipped else ""
        return f"Starting — {progress.lines_total} basket line(s) to match{suffix}"
    if progress.phase == "done":
        return (
            f"Done — ok={progress.ok}, pack_mismatch={progress.pack_mismatch}, "
            f"missing={progress.missing}, skipped={progress.skipped}"
        )
    if progress.phase == "error":
        return progress.message or "GPT matching failed"
    label = progress.basket_label.strip()
    if len(label) > 48:
        label = label[:45] + "…"
    line_part = f"L{progress.line_no} {label}" if progress.line_no else label or "?"
    return (
        f"Line {progress.lines_completed}/{progress.lines_total} ({line_part}) | "
        f"ok={progress.ok} pack_mismatch={progress.pack_mismatch} missing={progress.missing}"
    )


def cli_match_progress_callback(progress: MatchProgress) -> None:
    """Print one progress line for CLI GPT matching."""
    prefix = progress.store_label or "store"
    print(f"  [{prefix}] {format_match_progress(progress)}")
