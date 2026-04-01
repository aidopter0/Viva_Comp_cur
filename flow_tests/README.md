# Flow tests and documentation assets

This folder holds **everything related to documenting and validating the Talabat pipeline** that is **not** production application code.

| Path | Purpose |
|------|---------|
| [`docs/PIPELINE.md`](docs/PIPELINE.md) | Full pipeline documentation (architecture, CLI, env, data flow). |
| [`PLAN.md`](PLAN.md) | Testing strategy, scope, and how automated tests map to cases. |
| [`cases/CASES.md`](cases/CASES.md) | Numbered test cases (steps, inputs, expected outcomes). |
| [`data/`](data/) | Small fixtures (minimal key lines, stores JSON, prepared JSON, sample raw extract). |
| [`results/`](results/) | **Generated** test outputs (gitignored). Do not commit large CSVs here. |
| [`expected/`](expected/) | Optional golden snippets or checksum notes for regression checks. |

Automated tests live in the repo-root [`tests/`](../tests/) directory and **load fixtures from `flow_tests/data/`** only. They do not ship with the app runtime.

## Key-item modules (repo root)

| Module | Role |
|--------|------|
| `key_item_line_parse.py` | Rule-based parsing / normalization of raw catalog lines. |
| `gemini_key_items_builder.py` | Gemini batch job → `key_items_prepared_gemini.json`. |
| `prepared_key_items_sync.py` | Hash gate + `ensure_key_items_gemini_json` before store runs. |
| `prepare_key_items_gemini.py` | Thin shim → `gemini_key_items_builder` (old script name). |

## Quick links

- Run unit tests: `pip install -r requirements-dev.txt` then `pytest` from the repo root.
- Read the pipeline: [`docs/PIPELINE.md`](docs/PIPELINE.md).
