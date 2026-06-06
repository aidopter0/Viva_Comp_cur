#!/usr/bin/env python3
"""Deprecated: name splits no longer drive matching (basket-first v2)."""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "backfill_name_splits.py is deprecated. "
        "Matching uses basket_label + pack only; brand_token/generic are reference data.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
