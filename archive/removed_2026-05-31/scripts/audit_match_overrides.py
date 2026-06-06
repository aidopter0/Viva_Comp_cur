#!/usr/bin/env python3
"""Catalog-assisted identity override builder (slug or missing per store line)."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from viva_tracker.catalog_index import catalog_path_for_store, load_catalog_file
from viva_tracker.settings import CONFIG_DIR

# Viva slug keys from apply_viva_url_corrections.py (shared across branches)
VIVA_PRODUCTS = {
    "milba_uht_1l": "milba-milk-uht-full-fat-original-germany-1l/s/6299200005708",
    "milba_yoghurt_1kg": "milba-full-cream-natural-yoghurt-uae-1kg/s/6299200051545",
    "chakki_atta_5kg": "eatistaan-chakki-atta-uae-5kg/s/6299200053136",
    "ama_basmati_5kg": "ama-basmati-rice-india-5kg/s/6299200046749",
    "sweet_corn_340g": "freshly-pick-sweet-corn-china-340g/s/6299200032957",
    "milba_butter_unsalted": "milba-butter-unsalted-germany-200g/s/6299200031905",
    "cipri_orange_juice_1l": "cipri-fresh-orange-juice-bottle-uae-1l/s/6299200048651",
    "potato_premium_1kg": "potato-premium-1kg/s/103555",
    "white_cabbage_1kg": "white-cabbage-iran-1kg/s/101625",
    "lemon_bag_1kg": "lemon-bag-egypt-1kg/s/6299200049511",
    "orange_500g": "orange-500g/s/101645",
    "tomato_500g": "tomato-500g/s/101837",
    "tender_chicken_400g": "tender-chicken-breast-uae-400g/s/112320",
    "beef_mince_500g": "beef-mince-brazil-500g/s/106443",
    "dpollo_whole_chicken_900g": "dpollo-chilled-fresh-whole-chicken-uae-900g/s/6299200046343",
    "onion_pink_500g": "onion-pink-500g/s/101654",
    "carrot_bag_1kg": "carrot-bag-australia-1kg/s/9323676000022",
    "qualiko_frozen_chicken_1kg": "qualiko-frozen-whole-chicken-ukraine-1000g/s/4820107353283",
}

VIVA_CORRECTIONS: dict[int, dict[str, str | None]] = {
    4: {"VIVA Supermarket, Arjan 2": "sweet_corn_340g", "Viva Supermarket, Muraqqabat": "sweet_corn_340g"},
    6: {"VIVA Supermarket, Arjan 2": "milba_butter_unsalted"},
    8: {"Viva Supermarket, Muraqqabat": "milba_yoghurt_1kg"},
    11: {"Viva Supermarket, Muraqqabat": "chakki_atta_5kg"},
    12: {
        "VIVA Supermarket, Arjan 2": "milba_uht_1l",
        "Viva Supermarket, Muraqqabat": "milba_uht_1l",
    },
    13: {"VIVA Supermarket, Arjan 2": None, "Viva Supermarket, Muraqqabat": None},
    16: {"Viva Supermarket, Muraqqabat": "ama_basmati_5kg"},
    21: {
        "VIVA Supermarket, Arjan 2": "qualiko_frozen_chicken_1kg",
        "Viva Supermarket, Muraqqabat": "qualiko_frozen_chicken_1kg",
    },
    26: {"Viva Supermarket, Muraqqabat": "lemon_bag_1kg"},
    27: {"Viva Supermarket, Muraqqabat": "orange_500g"},
    28: {"VIVA Supermarket, Arjan 2": None, "Viva Supermarket, Muraqqabat": None},
    29: {"VIVA Supermarket, Arjan 2": "cipri_orange_juice_1l"},
    30: {"Viva Supermarket, Muraqqabat": "tender_chicken_400g"},
    31: {
        "VIVA Supermarket, Arjan 2": "dpollo_whole_chicken_900g",
        "Viva Supermarket, Muraqqabat": "dpollo_whole_chicken_900g",
    },
    32: {
        "VIVA Supermarket, Arjan 2": "beef_mince_500g",
        "Viva Supermarket, Muraqqabat": "beef_mince_500g",
    },
    34: {"Viva Supermarket, Muraqqabat": "white_cabbage_1kg"},
    35: {"Viva Supermarket, Muraqqabat": "onion_pink_500g"},
    37: {"VIVA Supermarket, Arjan 2": "potato_premium_1kg"},
    38: {"Viva Supermarket, Muraqqabat": "carrot_bag_1kg"},
    39: {"VIVA Supermarket, Arjan 2": "tomato_500g", "Viva Supermarket, Muraqqabat": "tomato_500g"},
}

# Cross-chain P0 identity overrides (slug path after /product/)
CHAIN_OVERRIDES: list[dict] = [
    # L12 UHT
    {"brand_name": "Lulu", "store_label": "LuLu Hypermarket, Dubai Motor City", "line_no": 12,
     "action": "slug", "product_slug": "lulu-uht-long-life-full-fat-milk-4x1l/s/2226395_CH1"},
    {"brand_name": "Lulu", "store_label": "Lulu Hypermarket, Muhaisnah 4", "line_no": 12,
     "action": "slug", "product_slug": "lulu-uht-long-life-full-fat-milk-4x1l/s/2226395_CH1"},
    {"brand_name": "Sava", "store_label": "SAVA Supermarket, Al Muteena", "line_no": 12,
     "action": "slug", "product_slug": "meggle-uht-full-fat-milk-1l/s/101395_8585002505071"},
    {"brand_name": "Sava", "store_label": "SAVA Supermarket, Jumeirah Beach Residence - JBR", "line_no": 12,
     "action": "slug", "product_slug": "meggle-uht-full-fat-milk-1l/s/101395_8585002505071"},
    # L13 Hayatna / Al Ain — missing where no Hayatna UHT milk in catalog
    {"brand_name": "Viva", "store_label": "VIVA Supermarket, Arjan 2", "line_no": 13, "action": "missing",
     "note": "Hayatna UHT not in Viva catalog"},
    {"brand_name": "Viva", "store_label": "Viva Supermarket, Muraqqabat", "line_no": 13, "action": "missing",
     "note": "Hayatna UHT not in Viva catalog"},
    {"brand_name": "Carrefour", "store_label": "Carrefour, Century Mall", "line_no": 13, "action": "missing",
     "note": "Hayatna UHT milk not in Carrefour catalog"},
    {"brand_name": "Lulu", "store_label": "LuLu Hypermarket, Dubai Motor City", "line_no": 13, "action": "missing",
     "note": "Hayatna UHT milk not in Lulu catalog"},
    {"brand_name": "Lulu", "store_label": "Lulu Hypermarket, Muhaisnah 4", "line_no": 13, "action": "missing",
     "note": "Hayatna UHT milk not in Lulu catalog"},
    {"brand_name": "GALA", "store_label": "Gala Supermarket Karama", "line_no": 13, "action": "missing",
     "note": "Shirin Asal UHT not in catalog"},
    {"brand_name": "GALA", "store_label": "Gala Supermarket, Al Barari", "line_no": 13, "action": "missing",
     "note": "Shirin Asal UHT not in catalog"},
    {"brand_name": "Sava", "store_label": "SAVA Supermarket, Al Muteena", "line_no": 13, "action": "missing",
     "note": "Al Ain UHT milk not in Sava catalog"},
    {"brand_name": "Sava", "store_label": "SAVA Supermarket, Jumeirah Beach Residence - JBR", "line_no": 13,
     "action": "missing", "note": "Al Ain UHT milk not in Sava catalog"},
    # L21 frozen whole chicken
    {"brand_name": "Carrefour", "store_label": "Carrefour, Century Mall", "line_no": 21, "action": "slug",
     "product_slug": "qualiko-frozen-whole-chicken-ukraine-1000g/s/1428633_4820107353283"},
    {"brand_name": "GALA", "store_label": "Gala Supermarket Karama", "line_no": 21, "action": "slug",
     "product_slug": "qualiko-frozen-whole-chicken-ukraine-1100g/s/4820107353290"},
    {"brand_name": "Lulu", "store_label": "Lulu Hypermarket, Muhaisnah 4", "line_no": 21, "action": "slug",
     "product_slug": "qualiko-frozen-whole-chicken-ukraine-1000g/s/1567068_EA", "note": "Qualiko frozen L21"},
    {"brand_name": "Carrefour", "store_label": "Carrefour, Century Mall", "line_no": 12, "action": "slug",
     "product_slug": "carrefour-long-life-uht-full-fat-milk-1l/s/2190706_6290361531525",
     "note": "Carrefour long-life UHT 1L"},
    {"brand_name": "GALA", "store_label": "Gala Supermarket Karama", "line_no": 12, "action": "missing",
     "note": "Safa UHT not in Karama catalog"},
    {"brand_name": "GALA", "store_label": "Gala Supermarket, Al Barari", "line_no": 12, "action": "slug",
     "product_slug": "al-safa-long-life-full-fat-milk-4-pieces/s/6291044170413", "note": "Safa UHT 4x"},
    {"brand_name": "Lulu", "store_label": "LuLu Hypermarket, Dubai Motor City", "line_no": 21, "action": "slug",
     "product_slug": "sadia-frozen-grade-a-whole-griller-chicken-1kg/s/127965_EA", "note": "Frozen whole chicken"},
    {"brand_name": "Lulu", "store_label": "LuLu Hypermarket, Dubai Motor City", "line_no": 32, "action": "missing",
     "note": "No Lulu beef mince in catalog"},
    {"brand_name": "GALA", "store_label": "Gala Supermarket, Al Barari", "line_no": 21, "action": "missing",
     "note": "Qualiko not in Al Barari catalog"},
    {"brand_name": "Sava", "store_label": "SAVA Supermarket, Al Muteena", "line_no": 21, "action": "missing",
     "note": "No whole chicken SKU"},
    {"brand_name": "Sava", "store_label": "SAVA Supermarket, Jumeirah Beach Residence - JBR", "line_no": 21,
     "action": "missing", "note": "No whole chicken SKU"},
    {"brand_name": "Sava", "store_label": "SAVA Supermarket, Al Muteena", "line_no": 30, "action": "missing",
     "note": "No tender breast SKU"},
    {"brand_name": "Sava", "store_label": "SAVA Supermarket, Jumeirah Beach Residence - JBR", "line_no": 30,
     "action": "missing", "note": "No tender breast SKU"},
    {"brand_name": "GALA", "store_label": "Gala Supermarket, Al Barari", "line_no": 31, "action": "missing",
     "note": "No whole chicken SKU"},
    {"brand_name": "Lulu", "store_label": "LuLu Hypermarket, Dubai Motor City", "line_no": 31, "action": "missing",
     "note": "No mapped whole chicken SKU"},
    {"brand_name": "Sava", "store_label": "SAVA Supermarket, Al Muteena", "line_no": 31, "action": "missing",
     "note": "No whole chicken SKU"},
    {"brand_name": "Sava", "store_label": "SAVA Supermarket, Jumeirah Beach Residence - JBR", "line_no": 31,
     "action": "missing", "note": "No whole chicken SKU"},
    {"brand_name": "Viva", "store_label": "VIVA Supermarket, Arjan 2", "line_no": 30, "action": "slug",
     "product_slug": "tender-chicken-breast-uae-400g/s/112320", "note": "D'Pollo tender breast"},
    {"brand_name": "Viva", "store_label": "VIVA Supermarket, Arjan 2", "line_no": 31, "action": "missing",
     "note": "D'Pollo whole chicken not in Arjan catalog"},
    {"brand_name": "Viva", "store_label": "VIVA Supermarket, Arjan 2", "line_no": 32, "action": "missing",
     "note": "Beef mince not in Arjan catalog"},
    # L28 watermelon
    {"brand_name": "Carrefour", "store_label": "Carrefour, Century Mall", "line_no": 28, "action": "slug",
     "product_slug": "watermelon-5kg/s/1674816_2141846000002"},
    {"brand_name": "GALA", "store_label": "Gala Supermarket Karama", "line_no": 28, "action": "slug",
     "product_slug": "watermelon-approx-3kg-4kg/s/9100009196825"},
    {"brand_name": "GALA", "store_label": "Gala Supermarket, Al Barari", "line_no": 28, "action": "slug",
     "product_slug": "watermelon-approx-3kg-4kg/s/9100009196825"},
    {"brand_name": "Lulu", "store_label": "LuLu Hypermarket, Dubai Motor City", "line_no": 28, "action": "slug",
     "product_slug": "watermelon-kiran-india-1kg/s/1411290_KG"},
    {"brand_name": "Lulu", "store_label": "Lulu Hypermarket, Muhaisnah 4", "line_no": 28, "action": "slug",
     "product_slug": "watermelon-kiran-india-1kg/s/1411290_KG"},
    {"brand_name": "Sava", "store_label": "SAVA Supermarket, Al Muteena", "line_no": 28, "action": "missing",
     "note": "No whole watermelon SKU in catalog"},
    {"brand_name": "Sava", "store_label": "SAVA Supermarket, Jumeirah Beach Residence - JBR", "line_no": 28,
     "action": "missing", "note": "No whole watermelon SKU in catalog"},
    # L31 whole chicken
    {"brand_name": "Lulu", "store_label": "Lulu Hypermarket, Muhaisnah 4", "line_no": 31, "action": "slug",
     "product_slug": "lulu-fresh-whole-chicken-1kg/s/423891_EA"},
    {"brand_name": "Carrefour", "store_label": "Carrefour, Century Mall", "line_no": 31, "action": "slug",
     "product_slug": "al-ain-farms-fresh-chicken-whole-900g/s/268584_6291056400577", "note": "Tanmiah proxy: Al Ain whole"},
    {"brand_name": "GALA", "store_label": "Gala Supermarket Karama", "line_no": 31, "action": "slug",
     "product_slug": "fresh-chicken-whole-1kg/s/9490942"},
    # L32 beef mince
    {"brand_name": "Carrefour", "store_label": "Carrefour, Century Mall", "line_no": 32, "action": "slug",
     "product_slug": "beef-mince-brazil-500g/s/909662_2132020000000"},
    {"brand_name": "Sava", "store_label": "SAVA Supermarket, Al Muteena", "line_no": 32, "action": "slug",
     "product_slug": "butchero-chilled-beef-mince-500g/s/101940_6294017518427"},
    {"brand_name": "Sava", "store_label": "SAVA Supermarket, Jumeirah Beach Residence - JBR", "line_no": 32,
     "action": "slug", "product_slug": "butchero-chilled-beef-mince-500g/s/101940_6294017518427"},
]


def _compact(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def find_brand_slug(store_label: str, brand_token: str, must: list[str], forbid: list[str]) -> str | None:
    path = catalog_path_for_store(store_label)
    if not path.is_file():
        return None
    data = load_catalog_file(store_label)
    bt = _compact(brand_token)
    for cat in data.get("categories") or []:
        for sub in cat.get("subcategories") or []:
            for prod in sub.get("products") or []:
                url = str(prod.get("url") or "")
                if "/product/" not in url:
                    continue
                slug = url.split("/product/", 1)[1].split("?", 1)[0]
                blob = (str(prod.get("product_name") or "") + " " + slug).lower()
                if bt and bt not in _compact(blob):
                    continue
                if must and not all(x in blob for x in must):
                    continue
                if forbid and any(x in blob for x in forbid):
                    continue
                return slug
    return None


def build_viva_entries() -> list[dict]:
    entries: list[dict] = []
    for line_no, stores in VIVA_CORRECTIONS.items():
        for store_label, product_key in stores.items():
            if product_key is None:
                entries.append(
                    {
                        "brand_name": "Viva",
                        "store_label": store_label,
                        "line_no": line_no,
                        "action": "missing",
                        "note": f"Viva L{line_no} explicit missing",
                    }
                )
            else:
                slug = VIVA_PRODUCTS.get(str(product_key), "")
                entries.append(
                    {
                        "brand_name": "Viva",
                        "store_label": store_label,
                        "line_no": line_no,
                        "action": "slug",
                        "product_slug": slug,
                        "note": f"Migrated from apply_viva_url_corrections L{line_no}",
                    }
                )
    return entries


def audit_suggestions(conn: sqlite3.Connection) -> list[str]:
    lines: list[str] = []
    rows = conn.execute(
        """
        SELECT b.brand_name, s.store_label, bi.line_no, m.brand_token
        FROM basket_item_brand_map m
        JOIN basket_items bi ON bi.basket_item_id = m.basket_item_id
        JOIN brands b ON b.brand_id = m.brand_id
        JOIN stores s ON s.brand_id = b.brand_id
        WHERE bi.line_no IN (12,13,21,28,31,32)
        ORDER BY bi.line_no, b.brand_name
        """
    ).fetchall()
    override_keys = {
        (e["brand_name"].lower(), e["store_label"].lower(), int(e["line_no"]))
        for e in build_viva_entries() + CHAIN_OVERRIDES
    }
    for r in rows:
        key = (str(r["brand_name"]).lower(), str(r["store_label"]).lower(), int(r["line_no"]))
        if key in override_keys:
            continue
        bt = str(r["brand_token"] or "")
        ln = int(r["line_no"])
        label = str(r["store_label"])
        if ln in (12, 13):
            slug = find_brand_slug(
                label, bt, ["uht", "milk"], ["juice", "yogurt", "chocolate", "strawberry"]
            )
        elif ln == 21:
            slug = find_brand_slug(label, bt, ["whole", "chicken"], ["breast"])
        elif ln == 28:
            slug = find_brand_slug(
                label, "", ["watermelon"], ["jelly", "juice", "candy", "gum", "ring", "slice", "chewing"]
            )
        elif ln == 31:
            slug = find_brand_slug(label, bt, ["whole", "chicken"], ["breast", "frozen"])
        elif ln == 32:
            slug = find_brand_slug(label, bt, ["mince", "beef"], [])
        else:
            slug = None
        if slug:
            lines.append(f"SUGGEST slug L{ln} {r['brand_name']} {label}: {slug}")
        else:
            lines.append(f"SUGGEST missing L{ln} {r['brand_name']} {label} (no identity slug)")
    return lines


def write_overrides_json() -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    entries = build_viva_entries() + CHAIN_OVERRIDES
    out = CONFIG_DIR / "match_overrides.json"
    out.write_text(
        json.dumps({"version": 1, "entries": entries}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out


def main() -> int:
    write_overrides_json()
    print(f"Wrote {CONFIG_DIR / 'match_overrides.json'} ({len(build_viva_entries()) + len(CHAIN_OVERRIDES)} entries)")

    db = ROOT / "data" / "viva_tracker.db"
    if db.is_file():
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        for line in audit_suggestions(conn):
            print(line)
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
