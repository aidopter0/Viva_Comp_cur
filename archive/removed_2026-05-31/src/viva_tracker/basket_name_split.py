"""GPT-assisted split of chain product names into brand token + generic description."""

from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI

from .catalog_match import _norm, distinctive_name_tokens
from .match_groups import MATCH_GROUP_PACKAGED, MATCH_GROUP_PRODUCE
from .openai_usage import log_completion
from .settings import OPENAI_DEFAULT_MODEL


def normalize_brand_token(
    *,
    match_group: str,
    mapped_name: str,
    chain_name: str,
    raw_token: str,
) -> str:
    token = str(raw_token or "").strip()
    if match_group == MATCH_GROUP_PRODUCE:
        return ""
    if token:
        return token
    chain = str(chain_name or "").strip()
    if chain and chain.lower() in _norm(mapped_name):
        return chain
    return token


def deterministic_name_split(
    mapped_name: str,
    brand: str,
    *,
    match_group: str = MATCH_GROUP_PACKAGED,
) -> tuple[str, str]:
    mapped = str(mapped_name or "").strip()
    brand_name = str(brand or "").strip()
    if match_group == MATCH_GROUP_PRODUCE:
        return "", mapped
    tokens = distinctive_name_tokens(mapped)
    brand_norm = _norm(brand_name)
    rest = [t for t in tokens if _norm(t) != brand_norm and brand_norm not in _norm(t)]
    generic = " ".join(rest) if rest else mapped
    token = brand_name if brand_norm and brand_norm in _norm(mapped) else ""
    return token, generic


def split_chain_product_names(
    entries: list[dict[str, str]],
    *,
    model: str | None = None,
) -> list[dict[str, str]]:
    """
    Split mapped chain product names for one basket line.

    Each entry: {brand, mapped_name, pack}
    Returns: {brand, brand_token, generic_description}
    """
    usable = [
        e
        for e in entries
        if str(e.get("mapped_name") or "").strip() and str(e.get("brand") or "").strip()
    ]
    if not usable:
        return []

    model_name = model or os.environ.get("OPENAI_MODEL", OPENAI_DEFAULT_MODEL)
    if not os.environ.get("OPENAI_API_KEY"):
        return []

    client = OpenAI()
    system = (
        "You parse grocery product titles into brand token and generic product description. "
        'Return strict JSON: {"splits": [{"brand": string, "brand_token": string, '
        '"generic_description": string}]}. '
        "brand_token is the manufacturer/brand on the package (e.g. Freshly Pick, Milba, Carrefour). "
        "Use empty brand_token for unbranded fresh produce (carrot, tomato, onion) with no supplier on pack. "
        "For chain own-label products (Carrefour …, Lulu …), set brand_token to that chain/manufacturer name. "
        "generic_description is the product type without brand (e.g. Sweet Corn, Full cream milk). "
        "Keep pack size out of generic_description. Echo brand exactly as input."
    )
    user = json.dumps({"products": usable}, ensure_ascii=False)
    resp = client.chat.completions.create(
        model=model_name,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    log_completion(
        resp,
        operation="name_split",
        model=model_name,
        context={"products": len(usable)},
    )
    raw = resp.choices[0].message.content or "{}"
    data = json.loads(raw)
    out: list[dict[str, str]] = []
    for row in data.get("splits") or []:
        if not isinstance(row, dict):
            continue
        brand = str(row.get("brand") or "").strip()
        if not brand:
            continue
        out.append(
            {
                "brand": brand,
                "brand_token": str(row.get("brand_token") or "").strip(),
                "generic_description": str(row.get("generic_description") or "").strip(),
            }
        )
    return out


def apply_name_splits_to_line(
    conn,
    *,
    basket_item_id: int,
    brand_ids: dict[str, int],
    entries: list[dict[str, str]],
    match_group: str = MATCH_GROUP_PACKAGED,
) -> None:
    from .repository import update_basket_map_name_split

    splits = split_chain_product_names(entries)
    by_brand = {s["brand"]: s for s in splits}
    for entry in entries:
        brand = str(entry.get("brand") or "").strip()
        mapped = str(entry.get("mapped_name") or "").strip()
        if not brand or not mapped:
            continue
        split = by_brand.get(brand)
        if split:
            raw_token = split["brand_token"]
            generic = split["generic_description"]
        else:
            raw_token, generic = deterministic_name_split(
                mapped, brand, match_group=match_group
            )
        brand_token = normalize_brand_token(
            match_group=match_group,
            mapped_name=mapped,
            chain_name=brand,
            raw_token=raw_token,
        )
        if not brand_token and not generic:
            _, generic = deterministic_name_split(mapped, brand, match_group=match_group)
        brand_id = brand_ids.get(brand)
        if brand_id is None:
            continue
        update_basket_map_name_split(
            conn,
            basket_item_id=basket_item_id,
            brand_id=brand_id,
            brand_token=brand_token,
            generic_description=generic,
        )
