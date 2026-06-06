"""Apply known Viva URL master corrections for cross-store price alignment.

Prefer config/match_overrides.json wired into match_engine (match_method=override).
This script remains for one-off Mura URL normalization.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from viva_tracker.db import connect_db, init_db
from viva_tracker.pack_normalize import format_title_pack_display, pack_matches_target
from viva_tracker.repository import list_basket_items, upsert_item_url_master
from viva_tracker.storefront import resolve_url_master_from_product_url

ARJAN_STORE_ID = 11
MURA_STORE_ID = 6
ARJAN_LABEL = "VIVA Supermarket, Arjan 2"
MURA_LABEL = "Viva Supermarket, Muraqqabat"
ARJAN_GROCERY = "710208"
MURA_GROCERY = "746833"
ARJAN_AID = "6474"
MURA_AID = "1169"

# Canonical product paths (slug/s/sku) shared across Viva branches.
PRODUCTS = {
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


def build_url(product_key: str, *, grocery_id: str, aid: str) -> str:
    path = PRODUCTS[product_key]
    return (
        f"https://www.talabat.com/uae/grocery/{grocery_id}/viva-supermarket/product/{path}?aid={aid}"
    )


def normalize_mura_url(url: str) -> str:
    if not url:
        return url
    out = re.sub(r"/grocery/\d+/", f"/grocery/{MURA_GROCERY}/", url)
    if "aid=" in out:
        out = re.sub(r"aid=\d+", f"aid={MURA_AID}", out)
    else:
        out += ("&" if "?" in out else "?") + f"aid={MURA_AID}"
    return out


# line_no -> {store_id: product_key | None (clear URL)}
CORRECTIONS: dict[int, dict[int, str | None]] = {
    4: {ARJAN_STORE_ID: "sweet_corn_340g", MURA_STORE_ID: "sweet_corn_340g"},
    6: {ARJAN_STORE_ID: "milba_butter_unsalted"},
    8: {MURA_STORE_ID: "milba_yoghurt_1kg"},
    11: {MURA_STORE_ID: "chakki_atta_5kg"},
    16: {MURA_STORE_ID: "ama_basmati_5kg"},
    21: {ARJAN_STORE_ID: "qualiko_frozen_chicken_1kg", MURA_STORE_ID: "qualiko_frozen_chicken_1kg"},
    26: {MURA_STORE_ID: "lemon_bag_1kg"},
    27: {MURA_STORE_ID: "orange_500g"},
    28: {MURA_STORE_ID: None},
    29: {ARJAN_STORE_ID: "cipri_orange_juice_1l"},
    30: {MURA_STORE_ID: "tender_chicken_400g"},
    31: {ARJAN_STORE_ID: "dpollo_whole_chicken_900g", MURA_STORE_ID: "dpollo_whole_chicken_900g"},
    32: {ARJAN_STORE_ID: "beef_mince_500g", MURA_STORE_ID: "beef_mince_500g"},
    34: {MURA_STORE_ID: "white_cabbage_1kg"},
    35: {MURA_STORE_ID: "onion_pink_500g"},
    37: {ARJAN_STORE_ID: "potato_premium_1kg"},
    38: {MURA_STORE_ID: "carrot_bag_1kg"},
    39: {ARJAN_STORE_ID: "tomato_500g", MURA_STORE_ID: "tomato_500g"},
}


def store_grocery_aid(store_id: int) -> tuple[str, str]:
    if store_id == ARJAN_STORE_ID:
        return ARJAN_GROCERY, ARJAN_AID
    if store_id == MURA_STORE_ID:
        return MURA_GROCERY, MURA_AID
    raise ValueError(f"Unknown Viva store_id {store_id}")


def _catalog_index(store_label: str) -> dict[str, dict]:
    path = ROOT / "catalogs" / f"{store_label}.json"
    if not path.exists():
        return {}
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for cat in data.get("categories", []):
        for sub in cat.get("subcategories", []):
            for p in sub.get("products", []) or []:
                url = str(p.get("url") or "")
                if "/product/" not in url:
                    continue
                slug_sku = url.split("/product/", 1)[1].split("?", 1)[0]
                out[slug_sku] = p
    return out


_CATALOGS: dict[str, dict[str, dict]] = {}


def catalog_meta(store_id: int, product_key: str) -> dict[str, str] | None:
    global _CATALOGS
    label = ARJAN_LABEL if store_id == ARJAN_STORE_ID else MURA_LABEL
    if label not in _CATALOGS:
        _CATALOGS[label] = _catalog_index(label)
    path = PRODUCTS[product_key]
    hit = _CATALOGS[label].get(path)
    if not hit:
        return None
    return {
        "source_url": str(hit["url"]),
        "item_id": str(hit["item_id"]),
        "slug": path.split("/s/", 1)[0],
        "item_title": str(hit.get("product_name") or ""),
    }


def existing_db_meta(conn, store_id: int, line_no: int) -> dict[str, str] | None:
    row = conn.execute(
        """
        SELECT u.source_url, u.item_id, u.slug, u.item_title
        FROM item_url_master u
        JOIN basket_items bi ON bi.basket_item_id = u.basket_item_id
        WHERE u.store_id = ? AND bi.line_no = ?
        """,
        (store_id, line_no),
    ).fetchone()
    if not row or not str(row["source_url"] or "").strip():
        return None
    return {
        "source_url": str(row["source_url"]),
        "item_id": str(row["item_id"] or ""),
        "slug": str(row["slug"] or ""),
        "item_title": str(row["item_title"] or ""),
    }


def resolve_product_meta(conn, store_id: int, product_key: str, line_no: int) -> dict[str, str]:
    grocery_id, aid = store_grocery_aid(store_id)
    url = build_url(product_key, grocery_id=grocery_id, aid=aid)
    try:
        return resolve_url_master_from_product_url(url)
    except Exception:
        pass
    alt_store = MURA_STORE_ID if store_id == ARJAN_STORE_ID else ARJAN_STORE_ID
    alt_grocery, alt_aid = store_grocery_aid(alt_store)
    alt_url = build_url(product_key, grocery_id=alt_grocery, aid=alt_aid)
    try:
        meta = resolve_url_master_from_product_url(alt_url)
        meta["source_url"] = url
        return meta
    except Exception:
        pass
    cat = catalog_meta(store_id, product_key)
    if cat:
        cat["source_url"] = url
        return cat
    cat_alt = catalog_meta(alt_store, product_key)
    if cat_alt:
        cat_alt["source_url"] = url
        return cat_alt
    peer = existing_db_meta(conn, alt_store, line_no)
    if peer and PRODUCTS[product_key] in peer["source_url"]:
        peer = dict(peer)
        peer["source_url"] = url
        return peer
    raise ValueError(f"Could not resolve {product_key} for store_id={store_id}")


def apply_correction(
    conn,
    *,
    line_no: int,
    store_id: int,
    product_key: str | None,
    pack_lookup: dict[int, tuple[str, str]],
) -> str:
    bid = pack_lookup["_bid"][line_no]
    pack_qty, pack_unit = pack_lookup["pack"].get(line_no, ("", ""))

    if product_key is None:
        upsert_item_url_master(
            conn,
            store_id=store_id,
            basket_item_id=bid,
            item_id=None,
            source_url=None,
            slug=None,
            item_title=None,
            status="missing",
            error=None,
            match_method="manual",
            match_confidence=None,
            match_reason="Cleared wrong Viva URL (apply_viva_url_corrections)",
            pack_match="unknown",
            catalog_pack_text=None,
        )
        return "cleared"

    meta = resolve_product_meta(conn, store_id, product_key, line_no)
    title = meta["item_title"]
    pack_match = pack_matches_target(title, pack_qty, pack_unit)
    status = "ok" if pack_match in {"exact", "close", "unknown"} else "pack_mismatch"
    upsert_item_url_master(
        conn,
        store_id=store_id,
        basket_item_id=bid,
        item_id=meta["item_id"],
        source_url=meta["source_url"],
        slug=meta["slug"] or None,
        item_title=title,
        status=status,
        error=None,
        match_method="manual",
        match_confidence=None,
        match_reason="Viva cross-store URL correction",
        pack_match=pack_match,
        catalog_pack_text=format_title_pack_display(title) if title else None,
    )
    return f"{status}/{pack_match} -> {title[:50]}"


def normalize_mura_cross_store_urls(conn) -> int:
    rows = conn.execute(
        """
        SELECT store_id, basket_item_id, source_url
        FROM item_url_master
        WHERE store_id = ? AND TRIM(COALESCE(source_url, '')) != ''
        """,
        (MURA_STORE_ID,),
    ).fetchall()
    changed = 0
    for row in rows:
        url = str(row["source_url"])
        fixed = normalize_mura_url(url)
        if fixed == url:
            continue
        try:
            meta = resolve_url_master_from_product_url(fixed)
        except Exception:
            continue
        conn.execute(
            """
            UPDATE item_url_master
            SET source_url = ?, item_id = ?, slug = ?, item_title = ?, last_verified_at = datetime('now')
            WHERE store_id = ? AND basket_item_id = ?
            """,
            (
                meta["source_url"],
                meta["item_id"],
                meta["slug"],
                meta["item_title"],
                MURA_STORE_ID,
                int(row["basket_item_id"]),
            ),
        )
        changed += 1
    conn.commit()
    return changed


def main() -> None:
    conn = connect_db()
    init_db(conn)

    items = list_basket_items(conn)
    line_to_bid = {int(r["line_no"]): int(r["basket_item_id"]) for r in items}
    pack_by_line: dict[int, tuple[str, str]] = {}
    for r in conn.execute(
        """
        SELECT bi.line_no, IFNULL(m.pack_qty, '') AS pack_qty, IFNULL(m.pack_unit, '') AS pack_unit
        FROM basket_items bi
        JOIN brands b ON LOWER(b.brand_name) = 'viva'
        LEFT JOIN basket_item_brand_map m
          ON m.basket_item_id = bi.basket_item_id AND m.brand_id = b.brand_id
        ORDER BY bi.line_no
        """
    ):
        pack_by_line[int(r["line_no"])] = (str(r["pack_qty"]), str(r["pack_unit"]))

    pack_lookup = {"_bid": line_to_bid, "pack": pack_by_line}

    print("Applying explicit Viva URL corrections...")
    failures: list[str] = []
    for line_no in sorted(CORRECTIONS):
        for store_id, product_key in CORRECTIONS[line_no].items():
            store_label = "Arjan" if store_id == ARJAN_STORE_ID else "Muraqqabat"
            try:
                result = apply_correction(
                    conn,
                    line_no=line_no,
                    store_id=store_id,
                    product_key=product_key,
                    pack_lookup=pack_lookup,
                )
                print(f"  L{line_no:2d} {store_label:12s}: {result}")
            except Exception as exc:  # noqa: BLE001
                msg = f"L{line_no} {store_label}: {exc}"
                failures.append(msg)
                print(f"  SKIP {msg}")

    print("\nNormalizing Muraqqabat URLs copied from Arjan store id...")
    n = normalize_mura_cross_store_urls(conn)
    print(f"  Updated {n} Muraqqabat row(s) to grocery/{MURA_GROCERY}")

    conn.close()
    if failures:
        print(f"\n{len(failures)} correction(s) skipped.")
    print("\nDone. Run extraction to refresh prices.")


if __name__ == "__main__":
    main()
