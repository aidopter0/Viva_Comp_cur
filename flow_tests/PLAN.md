# Testing plan — Talabat competitive pricing pipeline

## Goals

1. **Document** the end-to-end flow in one place ([`docs/PIPELINE.md`](docs/PIPELINE.md)).
2. **Automate** fast, deterministic checks (parsing, hash logic, source-order invariants) without network calls by default.
3. **Isolate** fixtures, case definitions, and scratch results under **`flow_tests/`** so test assets never mix with [`app.py`](../app.py), extractors, or [`config/`](../config/) production files.

## What runs in CI vs locally

| Layer | Command | Network | Notes |
|-------|---------|---------|--------|
| Unit | `pytest -m "not integration"` | No | Default; uses `flow_tests/data` and temp dirs. |
| Integration (optional) | `pytest -m integration` | Maybe | Marked tests; may require API keys or manual opt-in. |
| Full live run | `python run_talabat_stores.py` | Yes | Documented in PIPELINE; not required for CI. |

## Mapping: cases to tests

See [`cases/CASES.md`](cases/CASES.md). Each `TC-xxx` maps to a test module or section in [`tests/`](../tests/).

## Results folder

[`results/`](results/) is for local inspection of pytest-generated artifacts (e.g. copied CSV snippets). Contents are gitignored except [`results/README.md`](results/README.md).
