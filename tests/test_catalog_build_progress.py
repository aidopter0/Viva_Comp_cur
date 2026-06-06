from __future__ import annotations

from unittest.mock import patch

import pytest

from viva_tracker.catalog_build import build_store_catalog


def _items_data(page: int, page_count: int, item_id: str) -> dict:
    return {
        "props": {
            "pageProps": {
                "initialState": {
                    "itemsData": {
                        "pageCount": page_count,
                        "items": [
                            {
                                "id": item_id,
                                "title": f"Product {item_id} p{page}",
                                "price": 10.0,
                            }
                        ],
                    }
                }
            }
        }
    }


@pytest.fixture
def categories_tree():
    return [
        {
            "name": "Cat A",
            "slug": "cat-a",
            "subcategories": [
                {"name": "Sub 1", "slug": "sub-1"},
                {"name": "Sub 2", "slug": "sub-2"},
            ],
        }
    ]


def test_build_store_catalog_progress_callback_sequence(categories_tree):
    events: list[tuple[str, int]] = []

    def on_progress(progress):
        events.append((progress.phase, progress.pages_completed))

    html = "<html></html>"
    side_effect = [
        _items_data(1, 2, "a1"),
        _items_data(2, 2, "a2"),
        _items_data(1, 2, "b1"),
        _items_data(2, 2, "b2"),
    ]

    with (
        patch("viva_tracker.catalog_build.validate_store_url", side_effect=lambda u: u),
        patch("viva_tracker.catalog_build.load_store_categories", return_value=categories_tree),
        patch("viva_tracker.catalog_build.category_url", return_value="https://talabat.com/listing"),
        patch("viva_tracker.catalog_build.fetch_html", return_value=html),
        patch("viva_tracker.catalog_build.parse_next_data", side_effect=side_effect),
        patch("viva_tracker.catalog_build.time.sleep"),
        patch(
            "viva_tracker.catalog_build.build_grocery_product_url",
            return_value="https://talabat.com/product",
        ),
    ):
        result = build_store_catalog(
            "https://talabat.com/store/1",
            store_label="Test Store",
            page_delay_s=0,
            log=False,
            progress_callback=on_progress,
        )

    page_events = [pages for phase, pages in events if phase == "fetching"]
    assert page_events == [1, 2, 2, 3, 4, 4]
    assert events[0][0] == "starting"
    assert events[-1][0] == "fetching"
    assert result["product_count"] == 4


def test_build_store_catalog_progress_fraction(categories_tree):
    fractions: list[float] = []

    def on_progress(progress):
        fractions.append(progress.progress_fraction)

    with (
        patch("viva_tracker.catalog_build.validate_store_url", side_effect=lambda u: u),
        patch("viva_tracker.catalog_build.load_store_categories", return_value=categories_tree),
        patch("viva_tracker.catalog_build.category_url", return_value="https://talabat.com/listing"),
        patch("viva_tracker.catalog_build.fetch_html", return_value="<html></html>"),
        patch(
            "viva_tracker.catalog_build.parse_next_data",
            side_effect=[
                _items_data(1, 1, "a1"),
                _items_data(1, 1, "b1"),
            ],
        ),
        patch("viva_tracker.catalog_build.time.sleep"),
        patch(
            "viva_tracker.catalog_build.build_grocery_product_url",
            return_value="https://talabat.com/product",
        ),
    ):
        build_store_catalog(
            "https://talabat.com/store/1",
            store_label="Test Store",
            page_delay_s=0,
            log=False,
            progress_callback=on_progress,
        )

    assert fractions[0] == 0.0
    assert fractions[-1] == 1.0
    assert all(0.0 <= value <= 1.0 for value in fractions)


def test_build_and_save_catalog_emits_saving_and_done(categories_tree):
    from viva_tracker.catalog_build import build_and_save_catalog_for_store

    phases: list[str] = []

    def on_progress(progress):
        phases.append(progress.phase)

    with (
        patch("viva_tracker.catalog_build.validate_store_url", side_effect=lambda u: u),
        patch("viva_tracker.catalog_build.load_store_categories", return_value=categories_tree),
        patch("viva_tracker.catalog_build.category_url", return_value="https://talabat.com/listing"),
        patch("viva_tracker.catalog_build.fetch_html", return_value="<html></html>"),
        patch(
            "viva_tracker.catalog_build.parse_next_data",
            side_effect=[
                _items_data(1, 1, "a1"),
                _items_data(1, 1, "b1"),
            ],
        ),
        patch("viva_tracker.catalog_build.time.sleep"),
        patch(
            "viva_tracker.catalog_build.build_grocery_product_url",
            return_value="https://talabat.com/product",
        ),
        patch("viva_tracker.catalog_build.save_catalog"),
    ):
        build_and_save_catalog_for_store(
            store_id=1,
            store_label="Test Store",
            brand_name="Viva",
            talabat_url="https://talabat.com/store/1",
            page_delay_s=0,
            log=False,
            progress_callback=on_progress,
        )

    assert phases[0] == "starting"
    assert "saving" in phases
    assert phases[-1] == "done"
