"""GPT verify / pick for basket-first matching (v2)."""

from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI

from .basket_match_spec import BasketMatchSpec
from .openai_usage import log_completion
from .pack_normalize import format_title_pack_display
from .settings import OPENAI_DEFAULT_MODEL


def _catalog_row_summary(row: dict[str, str], *, with_pack: bool = False) -> dict[str, str]:
    summary = {
        "ref": str(row.get("ref") or ""),
        "product_name": str(row.get("product_name") or ""),
        "subcategory_path": str(row.get("subcategory_path") or ""),
        "price": str(row.get("discounted_price") or row.get("price") or ""),
    }
    if with_pack:
        summary["catalog_pack_text"] = format_title_pack_display(summary["product_name"])
    return summary


def verify_pick(
    client: OpenAI,
    model: str,
    spec: BasketMatchSpec,
    row: dict[str, str],
    *,
    pack_flexible: bool = False,
) -> dict[str, Any]:
    """Return {accept: bool, reason: str}."""
    if pack_flexible:
        system = (
            "You verify grocery catalog matches for price tracking. "
            'Return strict JSON: {"accept": boolean, "reason": string}. '
            "This is a loose fresh item (fresh produce/fruit/meat) sold by weight; "
            "pack-size differences are acceptable because prices are compared per kg. "
            "Accept when the catalog product is the same fresh item type. "
            "Reject wrong product form (e.g. juice vs fresh fruit) or processed lookalikes "
            "(e.g. pickled, canned, dried, jerky, sausage)."
        )
    else:
        system = (
            "You verify grocery catalog matches for price tracking. "
            'Return strict JSON: {"accept": boolean, "reason": string}. '
            "Accept only when the catalog product is the same basket item (type + pack). "
            "Reject wrong product form (e.g. juice vs fresh fruit), wrong pack, or processed lookalikes."
        )
    user = json.dumps(
        {
            "basket_label": spec.basket_label,
            "basket_tokens": list(spec.basket_tokens),
            "pack": f"{spec.pack_qty} {spec.pack_unit}".strip(),
            "line_role": spec.line_role,
            "category": spec.category,
            "candidate": _catalog_row_summary(row),
        },
        ensure_ascii=False,
    )
    resp = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    log_completion(resp, operation="match_verify", model=model, context={"line_no": spec.line_no})
    raw = resp.choices[0].message.content or "{}"
    data = json.loads(raw)
    return {
        "accept": bool(data.get("accept")),
        "reason": str(data.get("reason") or "").strip(),
    }


def pick_from_candidates(
    client: OpenAI,
    model: str,
    spec: BasketMatchSpec,
    candidates: list[dict[str, str]],
    *,
    pack_flexible: bool = False,
    intent_pick: bool = False,
    pack_tolerance_pct: float = 12.0,
    min_confidence: float = 0.55,
) -> dict[str, Any]:
    """Return {ref: str|None, reason: str, confidence: float}.

    In ``intent_pick`` mode candidates are Round 2 near matches: GPT picks by
    customer intent (same category/need, pack within ``pack_tolerance_pct``),
    and picks below ``min_confidence`` are treated as no match.
    """
    if not candidates:
        return {"ref": None, "reason": "No candidates", "confidence": 0.0}
    if intent_pick:
        system = (
            "You pick the best Talabat catalog product for a basket line from a list of "
            "NEAR matches (they failed strict matching). "
            'Return strict JSON: {"ref": string|null, "confidence": float, "reason": string}. '
            "Use ref from the candidate list. Pick the product that serves the SAME customer "
            "intent: same product category and need, and a comparable pack size "
            f"(within +/-{pack_tolerance_pct:.0f}% of the basket pack for packaged goods). "
            "When several candidates equally satisfy intent and pack, pick the LOWEST price "
            "(this is for competitive price tracking). "
            "Reject different product forms or unrelated needs (e.g. a soap/handwash 'bar' for "
            "a nut/cereal bar, juice for fresh fruit, a sauce for a spice). "
            "Pick null when no candidate fits the same intent."
        )
        if pack_flexible:
            system += (
                " This is a loose fresh item sold by weight; pack-size differences are fine "
                "(prices compared per kg)."
            )
        operation = "match_pick_near"
    else:
        system = (
            "You pick the best Talabat catalog product for a basket line. "
            'Return strict JSON: {"ref": string|null, "confidence": float, "reason": string}. '
            "Use ref from the candidate list. Pick null only when none fit. "
            "When multiple candidates match the basket intent and pack, pick the LOWEST price."
        )
        if pack_flexible:
            system += (
                " This is a loose fresh item sold by weight; pack-size differences are fine "
                "(prices compared per kg). Match on fresh product type; reject processed lookalikes."
            )
        operation = "match_pick"
    payload: dict[str, Any] = {
        "basket_label": spec.basket_label,
        "basket_tokens": list(spec.basket_tokens),
        "pack": f"{spec.pack_qty} {spec.pack_unit}".strip(),
        "line_role": spec.line_role,
        "category": spec.category,
        "candidates": [_catalog_row_summary(r, with_pack=intent_pick) for r in candidates],
        "pick_cheapest_when_equivalent": True,
    }
    if intent_pick:
        payload["match_group"] = spec.match_group
        payload["pack_tolerance_pct"] = pack_tolerance_pct
    user = json.dumps(payload, ensure_ascii=False)
    resp = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    log_completion(
        resp,
        operation=operation,
        model=model,
        context={"line_no": spec.line_no, "candidates": len(candidates)},
    )
    raw = resp.choices[0].message.content or "{}"
    data = json.loads(raw)
    ref = data.get("ref")
    confidence = float(data.get("confidence") or 0.0)
    reason = str(data.get("reason") or "").strip()
    if ref in (None, ""):
        return {"ref": None, "reason": reason, "confidence": confidence}
    if intent_pick and confidence < min_confidence:
        return {
            "ref": None,
            "reason": reason or f"Confidence {confidence:.2f} below {min_confidence:.2f}",
            "confidence": confidence,
        }
    return {
        "ref": str(ref).strip(),
        "reason": reason,
        "confidence": confidence,
    }


def openai_client() -> OpenAI | None:
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    return OpenAI()
