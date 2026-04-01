# Test cases catalog

| ID | Title | Type | Automated |
|----|-------|------|-----------|
| TC-001 | `parse_line` strips category and yields expected + query | Unit | `tests/test_key_item_line_parse.py` |
| TC-002 | `search_synonym_for_row` for whole chicken 900g | Unit | `tests/test_key_item_line_parse.py` |
| TC-003 | `needs_gemini_prep` true when hash missing or mismatch | Unit | `tests/test_prepared_key_items_sync.py` |
| TC-004 | `needs_gemini_prep` false when hash matches and JSON exists | Unit | `tests/test_prepared_key_items_sync.py` |
| TC-005 | Prepared JSON fixture validates `talabat_extract.load_prepared_key_items` | Unit | `tests/test_talabat_extract_loader.py` |
| TC-006 | `run_talabat_stores.py` appends consolidated **after** rerank when Gemini on | Doc / source | `tests/test_pipeline_order.py` |
| TC-007 | Documentation files and fixtures exist | Smoke | `tests/test_flow_assets.py` |
| TC-008 | Full HTTP pipeline (optional) | Integration | Mark `integration`; skip if no network |

## TC-006 detail (consolidated vs rerank)

**Expectation:** When Gemini rerank is enabled (default), `append_consolidated` must use **`*.reranked.csv`**, not base `*.csv`, for the same extraction run.

**Verification:** Automated test asserts source structure in [`run_talabat_stores.py`](../run_talabat_stores.py) (rerank block before consolidated append from reranked paths).
