from __future__ import annotations

import json
from pathlib import Path

import pytest

from viva_tracker.db import connect_db, init_db
from viva_tracker.extraction_exports import (
    compute_run_export_label,
    export_extraction_artifacts,
    list_run_export_dirs,
    prune_run_exports,
)


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    connection = connect_db(db_path)
    init_db(connection)
    return connection


def _insert_run(conn, run_date: str) -> int:
    conn.execute(
        "INSERT INTO runs(run_date, triggered_by) VALUES (?, ?)",
        (run_date, "test"),
    )
    row = conn.execute("SELECT last_insert_rowid() AS run_id").fetchone()
    conn.commit()
    return int(row["run_id"])


def test_compute_run_export_label_daily_sequence(conn):
    run1 = _insert_run(conn, "2026-05-24")
    run2 = _insert_run(conn, "2026-05-24")
    run3 = _insert_run(conn, "2026-05-25")

    assert compute_run_export_label(conn, run1) == "2026-05-24_001"
    assert compute_run_export_label(conn, run2) == "2026-05-24_002"
    assert compute_run_export_label(conn, run3) == "2026-05-25_001"


def test_compute_run_export_label_is_idempotent(conn):
    run_id = _insert_run(conn, "2026-05-24")
    conn.execute("UPDATE runs SET export_label = ? WHERE run_id = ?", ("2026-05-24_007", run_id))
    conn.commit()
    assert compute_run_export_label(conn, run_id) == "2026-05-24_007"


def test_prune_run_exports_keeps_latest_five(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    for run_id in range(1, 8):
        run_dir = runs_dir / f"2026-05-24_{run_id:03d}"
        run_dir.mkdir()
        manifest = {"run_id": run_id, "export_label": run_dir.name}
        (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    deleted = prune_run_exports(runs_dir, keep=5)
    remaining = list_run_export_dirs(runs_dir)

    assert len(deleted) == 2
    assert len(remaining) == 5
    assert remaining[0][0] == 7
    assert remaining[-1][0] == 3


def test_export_extraction_artifacts_writes_manifest_and_prunes(conn, tmp_path, monkeypatch):
    run_id = _insert_run(conn, "2026-05-24")
    exports_dir = tmp_path / "exports"
    runs_dir = exports_dir / "runs"

    for idx in range(1, 6):
        old_dir = runs_dir / f"2026-05-23_{idx:03d}"
        old_dir.mkdir(parents=True)
        (old_dir / "manifest.json").write_text(
            json.dumps({"run_id": idx, "export_label": old_dir.name}),
            encoding="utf-8",
        )

    def fake_export_csv(_conn, _basket_csv, out_path, *, fmt="wide", include_pack_mismatch=False):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "excel":
            out_path.write_bytes(b"PK")
        else:
            out_path.write_text(f"fmt={fmt}\n", encoding="utf-8")
        return out_path

    monkeypatch.setattr("viva_tracker.jobs.export_csv", fake_export_csv)

    bundle = export_extraction_artifacts(
        conn,
        run_id,
        basket_csv=tmp_path / "basket.csv",
        exports_dir=exports_dir,
        keep=5,
    )

    assert bundle.export_label == "2026-05-24_001"
    assert bundle.export_dir.is_dir()
    assert (bundle.export_dir / "manifest.json").is_file()
    assert (bundle.export_dir / "price_basket.xlsx").is_file()
    assert (exports_dir / "price_basket.xlsx").is_file()
    assert len(list_run_export_dirs(runs_dir)) == 5
    assert len(bundle.pruned_dirs) == 1
