from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .settings import (
    BASKET_CSV_PATH,
    EXPORTS_DIR,
    MAX_RUN_EXPORT_RETENTION,
    RUNS_EXPORT_DIR,
)


@dataclass
class RunExportBundle:
    run_id: int
    export_label: str
    export_dir: Path
    exported_files: list[Path] = field(default_factory=list)
    pruned_dirs: list[Path] = field(default_factory=list)


@dataclass
class ExtractionResult:
    run_id: int
    export_label: str | None = None
    export_dir: Path | None = None
    exported_files: list[Path] = field(default_factory=list)
    pruned_dirs: list[Path] = field(default_factory=list)


RUN_EXPORT_FILES: tuple[tuple[str, str], ...] = (
    ("price_basket.xlsx", "excel"),
    ("latest_comparison.csv", "wide"),
    ("latest_comparison_long.csv", "long"),
    ("weekly_summary.csv", "summary"),
    ("extraction_audit.csv", "audit"),
    ("price_history_long.csv", "history"),
)

LATEST_ROOT_FILES: tuple[str, ...] = tuple(name for name, _ in RUN_EXPORT_FILES)


def compute_run_export_label(conn: sqlite3.Connection, run_id: int) -> str:
    row = conn.execute(
        "SELECT run_date, export_label FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown run_id: {run_id}")
    existing = str(row["export_label"] or "").strip()
    if existing:
        return existing
    run_date = str(row["run_date"])
    seq = int(
        conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM runs
            WHERE run_date = ? AND run_id <= ?
            """,
            (run_date, run_id),
        ).fetchone()["n"]
    )
    return f"{run_date}_{seq:03d}"


def persist_run_export_label(conn: sqlite3.Connection, run_id: int, export_label: str) -> None:
    conn.execute(
        "UPDATE runs SET export_label = ? WHERE run_id = ?",
        (export_label, run_id),
    )
    conn.commit()


def list_run_export_dirs(runs_dir: Path | None = None) -> list[tuple[int, str, Path]]:
    root = runs_dir or RUNS_EXPORT_DIR
    if not root.is_dir():
        return []
    entries: list[tuple[int, str, Path]] = []
    for path in root.iterdir():
        if not path.is_dir():
            continue
        manifest_path = path / "manifest.json"
        if manifest_path.is_file():
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                run_id = int(data["run_id"])
                label = str(data.get("export_label") or path.name)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                run_id = 0
                label = path.name
        else:
            run_id = 0
            label = path.name
        entries.append((run_id, label, path))
    entries.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return entries


def prune_run_exports(
    runs_dir: Path | None = None,
    *,
    keep: int = MAX_RUN_EXPORT_RETENTION,
) -> list[Path]:
    if keep < 1:
        raise ValueError("keep must be at least 1")
    entries = list_run_export_dirs(runs_dir)
    deleted: list[Path] = []
    for _, _, path in entries[keep:]:
        shutil.rmtree(path)
        deleted.append(path)
    return deleted


def sync_latest_root_exports(run_dir: Path, exports_dir: Path | None = None) -> list[Path]:
    root = exports_dir or EXPORTS_DIR
    root.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for name in LATEST_ROOT_FILES:
        src = run_dir / name
        if not src.is_file():
            continue
        dest = root / name
        try:
            shutil.copy2(src, dest)
        except OSError:
            continue
        copied.append(dest)
    return copied


def export_extraction_artifacts(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    basket_csv: Path | None = None,
    exports_dir: Path | None = None,
    runs_dir: Path | None = None,
    keep: int | None = None,
) -> RunExportBundle:
    from .jobs import export_csv

    basket_path = basket_csv or BASKET_CSV_PATH
    exports_root = exports_dir or EXPORTS_DIR
    runs_root = runs_dir or (exports_root / "runs")
    retention = keep if keep is not None else MAX_RUN_EXPORT_RETENTION

    export_label = compute_run_export_label(conn, run_id)
    run_dir = runs_root / export_label
    run_dir.mkdir(parents=True, exist_ok=True)

    exported_files: list[Path] = []
    file_map: dict[str, str] = {}
    for filename, fmt in RUN_EXPORT_FILES:
        out_path = run_dir / filename
        saved = export_csv(conn, basket_path, out_path, fmt=fmt)
        exported_files.append(saved)
        file_map[filename] = str(saved)

    row = conn.execute(
        "SELECT run_date, run_ts, triggered_by FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    manifest = {
        "run_id": run_id,
        "export_label": export_label,
        "run_date": str(row["run_date"]) if row else None,
        "run_ts": str(row["run_ts"]) if row else None,
        "triggered_by": str(row["triggered_by"]) if row and row["triggered_by"] else None,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "files": file_map,
    }
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    exported_files.append(manifest_path)

    persist_run_export_label(conn, run_id, export_label)
    sync_latest_root_exports(run_dir, exports_root)
    pruned_dirs = prune_run_exports(runs_root, keep=retention)

    return RunExportBundle(
        run_id=run_id,
        export_label=export_label,
        export_dir=run_dir,
        exported_files=exported_files,
        pruned_dirs=pruned_dirs,
    )
