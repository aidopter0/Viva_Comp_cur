from __future__ import annotations

import sqlite3
from pathlib import Path

from .settings import DB_PATH, DATA_DIR


def connect_db(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or DB_PATH
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS brands (
          brand_id INTEGER PRIMARY KEY AUTOINCREMENT,
          brand_name TEXT NOT NULL UNIQUE,
          is_viva INTEGER NOT NULL DEFAULT 0,
          is_active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS stores (
          store_id INTEGER PRIMARY KEY AUTOINCREMENT,
          brand_id INTEGER NOT NULL,
          store_label TEXT NOT NULL UNIQUE,
          talabat_url TEXT,
          store_uuid TEXT,
          is_active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now')),
          FOREIGN KEY (brand_id) REFERENCES brands(brand_id)
        );

        CREATE TABLE IF NOT EXISTS basket_items (
          basket_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
          line_no INTEGER NOT NULL UNIQUE,
          category TEXT,
          product_id TEXT,
          basket_label TEXT,
          viva_name TEXT NOT NULL,
          is_active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS basket_item_brand_map (
          map_id INTEGER PRIMARY KEY AUTOINCREMENT,
          basket_item_id INTEGER NOT NULL,
          brand_id INTEGER NOT NULL,
          mapped_name TEXT NOT NULL,
          pack_qty TEXT,
          pack_unit TEXT,
          search_query TEXT,
          brand_token TEXT,
          generic_description TEXT,
          updated_at TEXT NOT NULL DEFAULT (datetime('now')),
          UNIQUE (basket_item_id, brand_id),
          FOREIGN KEY (basket_item_id) REFERENCES basket_items(basket_item_id) ON DELETE CASCADE,
          FOREIGN KEY (brand_id) REFERENCES brands(brand_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS item_url_master (
          item_url_id INTEGER PRIMARY KEY AUTOINCREMENT,
          store_id INTEGER NOT NULL,
          basket_item_id INTEGER NOT NULL,
          item_id TEXT,
          source_url TEXT,
          slug TEXT,
          item_title TEXT,
          status TEXT NOT NULL DEFAULT 'pending',
          error TEXT,
          last_verified_at TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          UNIQUE (store_id, basket_item_id),
          FOREIGN KEY (store_id) REFERENCES stores(store_id) ON DELETE CASCADE,
          FOREIGN KEY (basket_item_id) REFERENCES basket_items(basket_item_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS runs (
          run_id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_ts TEXT NOT NULL DEFAULT (datetime('now')),
          run_date TEXT NOT NULL,
          triggered_by TEXT
        );

        CREATE TABLE IF NOT EXISTS run_item_prices (
          obs_id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id INTEGER NOT NULL,
          store_id INTEGER NOT NULL,
          basket_item_id INTEGER NOT NULL,
          price REAL,
          original_price REAL,
          discount_percentage REAL,
          discounted_price REAL,
          status TEXT NOT NULL DEFAULT 'ok',
          error TEXT,
          item_json TEXT,
          UNIQUE (run_id, store_id, basket_item_id),
          FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
          FOREIGN KEY (store_id) REFERENCES stores(store_id) ON DELETE CASCADE,
          FOREIGN KEY (basket_item_id) REFERENCES basket_items(basket_item_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_runs_date ON runs(run_date);
        CREATE INDEX IF NOT EXISTS idx_prices_store_item ON run_item_prices(store_id, basket_item_id);
        """
    )
    conn.commit()
    _migrate_stores_created_at(conn)
    _migrate_basket_items_created_at(conn)
    _migrate_item_url_master_created_at(conn)
    _migrate_item_url_master_match_metadata(conn)
    _migrate_run_item_prices_normalized(conn)
    _migrate_basket_label_and_name_split(conn)
    _migrate_basket_match_group(conn)
    _migrate_basket_line_role(conn)
    _migrate_runs_export_label(conn)


def _migrate_basket_match_group(conn: sqlite3.Connection) -> None:
    from .match_groups import match_group_for_category

    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(basket_items)")}
    if "match_group" not in cols:
        try:
            conn.execute(
                "ALTER TABLE basket_items ADD COLUMN match_group TEXT NOT NULL DEFAULT 'packaged'"
            )
        except sqlite3.OperationalError:
            pass
    conn.commit()
    rows = conn.execute(
        "SELECT basket_item_id, category, match_group FROM basket_items"
    ).fetchall()
    for row in rows:
        category = str(row["category"] or "")
        expected = match_group_for_category(category)
        current = str(row["match_group"] or "").strip()
        if current != expected:
            conn.execute(
                "UPDATE basket_items SET match_group = ? WHERE basket_item_id = ?",
                (expected, int(row["basket_item_id"])),
            )
    conn.commit()


def _migrate_basket_line_role(conn: sqlite3.Connection) -> None:
    from .basket_match_spec import line_role_for_line

    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(basket_items)")}
    if "line_role" not in cols:
        try:
            conn.execute(
                "ALTER TABLE basket_items ADD COLUMN line_role TEXT NOT NULL DEFAULT 'default'"
            )
        except sqlite3.OperationalError:
            pass
    conn.commit()
    rows = conn.execute(
        "SELECT basket_item_id, line_no, basket_label, line_role FROM basket_items"
    ).fetchall()
    for row in rows:
        expected = line_role_for_line(int(row["line_no"]), str(row["basket_label"] or ""))
        current = str(row["line_role"] or "").strip()
        if current != expected:
            conn.execute(
                "UPDATE basket_items SET line_role = ? WHERE basket_item_id = ?",
                (expected, int(row["basket_item_id"])),
            )
    conn.commit()


def _migrate_runs_export_label(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(runs)")}
    if "export_label" in cols:
        return
    try:
        conn.execute("ALTER TABLE runs ADD COLUMN export_label TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass


def _migrate_basket_label_and_name_split(conn: sqlite3.Connection) -> None:
    bi_cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(basket_items)")}
    if "basket_label" not in bi_cols:
        try:
            conn.execute("ALTER TABLE basket_items ADD COLUMN basket_label TEXT")
        except sqlite3.OperationalError:
            pass
    map_cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(basket_item_brand_map)")}
    for name in ("brand_token", "generic_description"):
        if name in map_cols:
            continue
        try:
            conn.execute(f"ALTER TABLE basket_item_brand_map ADD COLUMN {name} TEXT")
        except sqlite3.OperationalError:
            pass
    conn.commit()


def _migrate_stores_created_at(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(stores)")}
    if "created_at" in cols:
        return
    try:
        conn.execute("ALTER TABLE stores ADD COLUMN created_at TEXT")
        conn.execute(
            """
            UPDATE stores
            SET created_at = COALESCE(updated_at, datetime('now'))
            WHERE created_at IS NULL OR created_at = ''
            """
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


def _migrate_basket_items_created_at(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(basket_items)")}
    if "created_at" in cols:
        return
    try:
        conn.execute("ALTER TABLE basket_items ADD COLUMN created_at TEXT")
        conn.execute(
            """
            UPDATE basket_items
            SET created_at = (
              SELECT MIN(m.updated_at)
              FROM basket_item_brand_map m
              WHERE m.basket_item_id = basket_items.basket_item_id
            )
            WHERE created_at IS NULL OR created_at = ''
            """
        )
        conn.execute(
            """
            UPDATE basket_items
            SET created_at = datetime('now')
            WHERE created_at IS NULL OR created_at = ''
            """
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


def _migrate_item_url_master_created_at(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(item_url_master)")}
    if "created_at" in cols:
        return
    try:
        conn.execute("ALTER TABLE item_url_master ADD COLUMN created_at TEXT")
        conn.execute(
            """
            UPDATE item_url_master
            SET created_at = COALESCE(last_verified_at, datetime('now'))
            WHERE created_at IS NULL OR created_at = ''
            """
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass


def _migrate_item_url_master_match_metadata(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(item_url_master)")}
    additions = {
        "match_method": "TEXT",
        "match_confidence": "REAL",
        "match_reason": "TEXT",
        "pack_match": "TEXT",
        "catalog_pack_text": "TEXT",
    }
    for name, col_type in additions.items():
        if name in cols:
            continue
        try:
            conn.execute(f"ALTER TABLE item_url_master ADD COLUMN {name} {col_type}")
        except sqlite3.OperationalError:
            pass
    conn.commit()


def _migrate_run_item_prices_normalized(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(run_item_prices)")}
    additions = {
        "normalized_unit": "TEXT",
        "normalized_qty": "REAL",
        "price_per_base": "REAL",
    }
    for name, col_type in additions.items():
        if name in cols:
            continue
        try:
            conn.execute(f"ALTER TABLE run_item_prices ADD COLUMN {name} {col_type}")
        except sqlite3.OperationalError:
            pass
    conn.commit()
