from __future__ import annotations

import csv
import re
from pathlib import Path


def _clean(s: str | None) -> str:
    return (s or "").strip()


def _normalize_query(text: str) -> str:
    s = _clean(text).lower()
    s = re.sub(r"[^\w\s\.]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_basket_csv(path: Path) -> tuple[list[str], list[dict]]:
    """
    Parse basket CSV with expected shape:
      category, product_id, [brand_name, qty, unit] x N brands
    """
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, [])
        if len(header) < 7:
            raise ValueError("Basket CSV header is not in expected format.")

        # CSV schema:
        # 0:#, 1:category, 2:product_id, 3:basket_name,
        # then repeating brand triplets from col 4:
        #   <Brand> Product, <measurement-like>, <pq-like>
        brands: list[str] = []
        brand_specs: list[tuple[str, int, int, int]] = []
        i = 4
        while i + 2 < len(header):
            raw_brand_col = _clean(header[i])
            if not raw_brand_col:
                i += 3
                continue
            brand = re.sub(r"\s*product\s*$", "", raw_brand_col, flags=re.IGNORECASE).strip()
            if not brand:
                i += 3
                continue
            brands.append(brand)
            brand_specs.append((brand, i, i + 1, i + 2))
            i += 3

        rows: list[dict] = []
        line_no = 0
        for cells in reader:
            if not any(_clean(c) for c in cells):
                continue
            line_no += 1
            # Use explicit CSV positions:
            # 0:#, 1:CATEGORY 2, 2:Product ID, 3:Basket
            serial = _clean(cells[0] if len(cells) > 0 else "")
            if serial.isdigit():
                line_no = int(serial)
            category = _clean(cells[1] if len(cells) > 1 else "")
            product_id = _clean(cells[2] if len(cells) > 2 else "")
            basket_label = _clean(cells[3] if len(cells) > 3 else "")
            by_brand: dict[str, dict] = {}
            for brand, name_idx, qty_idx, unit_idx in brand_specs:
                name = _clean(cells[name_idx] if name_idx < len(cells) else "")
                # Keep the two pack cells in positional order from CSV.
                # Header labels differ across brands (Meas'ment/PQ vs PQ/Meas'ment),
                # but values are still quantity + unit-like fields.
                qty = _clean(cells[qty_idx] if qty_idx < len(cells) else "")
                unit = _clean(cells[unit_idx] if unit_idx < len(cells) else "")
                by_brand[brand] = {
                    "name": name,
                    "qty": qty,
                    "unit": unit,
                    "search_query": _normalize_query(name),
                }
            viva_name = _clean((by_brand.get("Viva") or {}).get("name") or "")
            if not viva_name and not basket_label:
                continue
            rows.append(
                {
                    "line_no": line_no,
                    "category": category,
                    "product_id": product_id,
                    "basket_label": basket_label,
                    "viva_name": viva_name or basket_label,
                    "brands": by_brand,
                }
            )
    return brands, rows
