from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from .match_groups import match_group_for_category
from .basket_match_spec import line_role_for_line


def upsert_brand(conn: sqlite3.Connection, brand_name: str, *, is_viva: bool) -> int:
    conn.execute(
        """
        INSERT INTO brands(brand_name, is_viva) VALUES(?, ?)
        ON CONFLICT(brand_name) DO UPDATE SET is_viva=excluded.is_viva, is_active=1
        """,
        (brand_name, 1 if is_viva else 0),
    )
    row = conn.execute("SELECT brand_id FROM brands WHERE brand_name = ?", (brand_name,)).fetchone()
    conn.commit()
    return int(row["brand_id"])


def list_brands(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM brands WHERE is_active=1 ORDER BY is_viva DESC, brand_name"))


def upsert_store(
    conn: sqlite3.Connection,
    *,
    brand_id: int,
    store_label: str,
    talabat_url: str,
    store_uuid: str | None,
) -> int:
    conn.execute(
        """
        INSERT INTO stores(brand_id, store_label, talabat_url, store_uuid, is_active)
        VALUES (?, ?, ?, ?, 1)
        ON CONFLICT(store_label) DO UPDATE SET
          brand_id=excluded.brand_id,
          talabat_url=excluded.talabat_url,
          store_uuid=excluded.store_uuid,
          is_active=1,
          updated_at=datetime('now')
        """,
        (brand_id, store_label, talabat_url, store_uuid),
    )
    row = conn.execute("SELECT store_id FROM stores WHERE store_label = ?", (store_label,)).fetchone()
    conn.commit()
    return int(row["store_id"])


def update_store(
    conn: sqlite3.Connection,
    store_id: int,
    *,
    brand_id: int,
    store_label: str,
    talabat_url: str,
    store_uuid: str | None,
) -> None:
    conn.execute(
        """
        UPDATE stores SET
          brand_id = ?,
          store_label = ?,
          talabat_url = ?,
          store_uuid = ?,
          is_active = 1,
          updated_at = datetime('now')
        WHERE store_id = ?
        """,
        (brand_id, store_label, talabat_url, store_uuid, store_id),
    )
    conn.commit()


def list_stores(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT s.*, b.brand_name
            FROM stores s
            JOIN brands b ON b.brand_id = s.brand_id
            WHERE s.is_active=1
            ORDER BY b.brand_name, s.store_label
            """
        )
    )


def deactivate_store(conn: sqlite3.Connection, store_id: int) -> None:
    conn.execute("UPDATE stores SET is_active=0, updated_at=datetime('now') WHERE store_id = ?", (store_id,))
    conn.commit()


def upsert_basket_item(
    conn: sqlite3.Connection,
    *,
    line_no: int,
    category: str,
    product_id: str,
    viva_name: str,
    basket_label: str = "",
) -> int:
    match_group = match_group_for_category(category)
    line_role = line_role_for_line(line_no, basket_label or "")
    conn.execute(
        """
        INSERT INTO basket_items(line_no, category, product_id, basket_label, viva_name, match_group, line_role, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT(line_no) DO UPDATE SET
          category=excluded.category,
          product_id=excluded.product_id,
          basket_label=excluded.basket_label,
          viva_name=excluded.viva_name,
          match_group=excluded.match_group,
          line_role=excluded.line_role,
          is_active=1
        """,
        (line_no, category, product_id, basket_label or None, viva_name, match_group, line_role),
    )
    row = conn.execute("SELECT basket_item_id FROM basket_items WHERE line_no = ?", (line_no,)).fetchone()
    conn.commit()
    return int(row["basket_item_id"])


def list_basket_items(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM basket_items WHERE is_active=1 ORDER BY line_no"))


def deactivate_basket_item(conn: sqlite3.Connection, basket_item_id: int) -> None:
    conn.execute("UPDATE basket_items SET is_active=0 WHERE basket_item_id = ?", (basket_item_id,))
    conn.commit()


def upsert_basket_brand_map(
    conn: sqlite3.Connection,
    *,
    basket_item_id: int,
    brand_id: int,
    mapped_name: str,
    pack_qty: str,
    pack_unit: str,
    search_query: str,
) -> None:
    conn.execute(
        """
        INSERT INTO basket_item_brand_map(
          basket_item_id, brand_id, mapped_name, pack_qty, pack_unit, search_query
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(basket_item_id, brand_id) DO UPDATE SET
          mapped_name=excluded.mapped_name,
          pack_qty=excluded.pack_qty,
          pack_unit=excluded.pack_unit,
          search_query=excluded.search_query,
          updated_at=datetime('now')
        """,
        (basket_item_id, brand_id, mapped_name, pack_qty, pack_unit, search_query),
    )
    conn.commit()


def update_basket_map_name_split(
    conn: sqlite3.Connection,
    *,
    basket_item_id: int,
    brand_id: int,
    brand_token: str,
    generic_description: str,
) -> None:
    conn.execute(
        """
        UPDATE basket_item_brand_map SET
          brand_token = ?,
          generic_description = ?,
          updated_at = datetime('now')
        WHERE basket_item_id = ? AND brand_id = ?
        """,
        (brand_token, generic_description, basket_item_id, brand_id),
    )
    conn.commit()


def list_basket_brand_maps(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT m.*, bi.line_no, bi.category, bi.match_group, bi.line_role, bi.basket_label, b.brand_name
            FROM basket_item_brand_map m
            JOIN basket_items bi ON bi.basket_item_id = m.basket_item_id
            JOIN brands b ON b.brand_id = m.brand_id
            WHERE bi.is_active=1
            ORDER BY bi.line_no, b.brand_name
            """
        )
    )


def upsert_item_url_master(
    conn: sqlite3.Connection,
    *,
    store_id: int,
    basket_item_id: int,
    item_id: str | None,
    source_url: str | None,
    slug: str | None,
    item_title: str | None,
    status: str,
    error: str | None = None,
    match_method: str | None = None,
    match_confidence: float | None = None,
    match_reason: str | None = None,
    pack_match: str | None = None,
    catalog_pack_text: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO item_url_master(
          store_id, basket_item_id, item_id, source_url, slug, item_title, status, error,
          last_verified_at, created_at,
          match_method, match_confidence, match_reason, pack_match, catalog_pack_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?, ?, ?, ?)
        ON CONFLICT(store_id, basket_item_id) DO UPDATE SET
          item_id=excluded.item_id,
          source_url=excluded.source_url,
          slug=excluded.slug,
          item_title=excluded.item_title,
          status=excluded.status,
          error=excluded.error,
          last_verified_at=excluded.last_verified_at,
          match_method=excluded.match_method,
          match_confidence=excluded.match_confidence,
          match_reason=excluded.match_reason,
          pack_match=excluded.pack_match,
          catalog_pack_text=excluded.catalog_pack_text
        """,
        (
            store_id,
            basket_item_id,
            item_id,
            source_url,
            slug,
            item_title,
            status,
            error,
            datetime.utcnow().isoformat(),
            match_method,
            match_confidence,
            match_reason,
            pack_match,
            catalog_pack_text,
        ),
    )
    conn.commit()


def basket_ids_with_urls(conn: sqlite3.Connection, store_id: int) -> set[int]:
    """Basket item ids that already have a usable URL + Talabat item id for this store."""
    rows = conn.execute(
        """
        SELECT basket_item_id
        FROM item_url_master
        WHERE store_id = ?
          AND TRIM(COALESCE(source_url, '')) != ''
          AND TRIM(COALESCE(item_id, '')) != ''
        """,
        (store_id,),
    ).fetchall()
    return {int(r["basket_item_id"]) for r in rows}


def list_basket_store_grid(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Unified store × basket grid: brand maps + URL master + latest extraction."""
    return list(
        conn.execute(
            """
            SELECT
              s.store_id,
              s.store_label,
              s.brand_id,
              b.brand_name,
              b.brand_name AS grocery_chain_name,
              bi.basket_item_id,
              bi.line_no,
              bi.category,
              IFNULL(bi.match_group, 'packaged') AS match_group,
              IFNULL(bi.line_role, 'default') AS line_role,
              bi.product_id,
              IFNULL(bi.basket_label, '') AS basket_label,
              bi.viva_name,
              IFNULL(m.mapped_name, '') AS chain_item_name,
              IFNULL(m.pack_qty, '') AS pack_qty,
              IFNULL(m.pack_unit, '') AS pack_unit,
              TRIM(
                CASE
                  WHEN IFNULL(m.pack_qty, '') != '' AND IFNULL(m.pack_unit, '') != ''
                    THEN m.pack_qty || ' ' || m.pack_unit
                  WHEN IFNULL(m.pack_qty, '') != ''
                    THEN m.pack_qty
                  ELSE IFNULL(m.pack_unit, '')
                END
              ) AS pack_text,
              IFNULL(m.brand_token, '') AS brand_token,
              IFNULL(m.generic_description, '') AS generic_description,
              u.item_url_id,
              u.item_id,
              u.source_url,
              u.slug,
              u.item_title,
              IFNULL(u.status, '') AS status,
              IFNULL(u.pack_match, '') AS pack_match,
              IFNULL(u.catalog_pack_text, '') AS catalog_pack_text,
              u.match_confidence,
              IFNULL(u.match_reason, '') AS match_reason,
              IFNULL(u.match_method, '') AS match_method,
              u.error,
              u.created_at,
              (
                SELECT MAX(r.run_ts)
                FROM run_item_prices rip
                JOIN runs r ON r.run_id = rip.run_id
                WHERE rip.store_id = s.store_id
                  AND rip.basket_item_id = bi.basket_item_id
                  AND rip.status = 'ok'
              ) AS last_used_at,
              latest_rp.status AS extraction_status,
              latest_rp.error AS extraction_error,
              latest_rp.price AS extraction_price,
              latest_rp.discounted_price AS extraction_discounted_price,
              latest_rp.price_per_base AS extraction_price_per_base,
              CASE
                WHEN TRIM(COALESCE(u.source_url, '')) != ''
                 AND TRIM(COALESCE(u.item_id, '')) != ''
                THEN 1 ELSE 0
              END AS has_url
            FROM stores s
            JOIN brands b ON b.brand_id = s.brand_id
            JOIN basket_items bi ON bi.is_active = 1
            LEFT JOIN basket_item_brand_map m
              ON m.basket_item_id = bi.basket_item_id AND m.brand_id = s.brand_id
            LEFT JOIN item_url_master u
              ON u.store_id = s.store_id AND u.basket_item_id = bi.basket_item_id
            LEFT JOIN (
              SELECT rp.*
              FROM run_item_prices rp
              JOIN (SELECT MAX(run_id) AS run_id FROM runs) lr ON lr.run_id = rp.run_id
            ) latest_rp
              ON latest_rp.store_id = s.store_id
             AND latest_rp.basket_item_id = bi.basket_item_id
            WHERE s.is_active = 1
              AND bi.is_active = 1
            ORDER BY b.brand_name, s.store_label, bi.line_no
            """
        )
    )


def list_url_master_grid(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Full store × basket grid with optional existing URL master data."""
    return list_basket_store_grid(conn)


def list_item_url_master(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT
              u.*,
              s.store_label,
              s.brand_id,
              b.brand_name AS grocery_chain_name,
              bi.line_no,
              IFNULL(m.mapped_name, '') AS chain_item_name,
              IFNULL(m.pack_qty, '') AS pack_qty,
              IFNULL(m.pack_unit, '') AS pack_unit,
              TRIM(
                CASE
                  WHEN IFNULL(m.pack_qty, '') != '' AND IFNULL(m.pack_unit, '') != ''
                    THEN m.pack_qty || ' ' || m.pack_unit
                  WHEN IFNULL(m.pack_qty, '') != ''
                    THEN m.pack_qty
                  ELSE IFNULL(m.pack_unit, '')
                END
              ) AS pack_text,
              (
                SELECT MAX(r.run_ts)
                FROM run_item_prices rip
                JOIN runs r ON r.run_id = rip.run_id
                WHERE rip.store_id = u.store_id
                  AND rip.basket_item_id = u.basket_item_id
                  AND rip.status = 'ok'
              ) AS last_used_at
            FROM item_url_master u
            JOIN stores s ON s.store_id = u.store_id
            JOIN brands b ON b.brand_id = s.brand_id
            JOIN basket_items bi ON bi.basket_item_id = u.basket_item_id
            LEFT JOIN basket_item_brand_map m
              ON m.basket_item_id = u.basket_item_id AND m.brand_id = s.brand_id
            WHERE s.is_active = 1 AND bi.is_active = 1
            ORDER BY b.brand_name, s.store_label, bi.line_no
            """
        )
    )


def create_run(conn: sqlite3.Connection, *, triggered_by: str) -> int:
    run_date = datetime.utcnow().date().isoformat()
    conn.execute("INSERT INTO runs(run_date, triggered_by) VALUES (?, ?)", (run_date, triggered_by))
    row = conn.execute("SELECT last_insert_rowid() AS run_id").fetchone()
    conn.commit()
    return int(row["run_id"])


def save_price_observation(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    store_id: int,
    basket_item_id: int,
    price: float | None,
    original_price: float | None,
    discount_percentage: float | None,
    discounted_price: float | None,
    status: str,
    error: str | None,
    item_json: dict | None,
    normalized_unit: str | None = None,
    normalized_qty: float | None = None,
    price_per_base: float | None = None,
) -> None:
    """Persist a full-precision price observation (no presentation rounding)."""
    conn.execute(
        """
        INSERT OR REPLACE INTO run_item_prices(
          run_id, store_id, basket_item_id, price, original_price, discount_percentage, discounted_price,
          status, error, item_json, normalized_unit, normalized_qty, price_per_base
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            store_id,
            basket_item_id,
            price,
            original_price,
            discount_percentage,
            discounted_price,
            status,
            error,
            json.dumps(item_json or {}, ensure_ascii=False),
            normalized_unit,
            normalized_qty,
            price_per_base,
        ),
    )
    conn.commit()


def latest_prices_frame(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            WITH latest AS (
              SELECT MAX(run_id) AS run_id FROM runs
            )
            SELECT
              b.brand_name,
              s.store_label,
              bi.line_no,
              bi.viva_name,
              rp.price,
              rp.original_price,
              rp.discount_percentage,
              rp.discounted_price,
              rp.status,
              rp.normalized_unit,
              rp.normalized_qty,
              rp.price_per_base,
              r.run_ts
            FROM latest l
            JOIN run_item_prices rp ON rp.run_id = l.run_id
            JOIN runs r ON r.run_id = rp.run_id
            JOIN stores s ON s.store_id = rp.store_id
            JOIN brands b ON b.brand_id = s.brand_id
            JOIN basket_items bi ON bi.basket_item_id = rp.basket_item_id
            ORDER BY bi.line_no, s.store_label
            """
        )
    )
