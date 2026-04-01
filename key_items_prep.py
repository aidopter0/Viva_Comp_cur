"""
Ensure config/key_items_prepared_gemini.json is up to date with config/key_items.txt.

Uses config/.key_items_source.sha256 to skip regeneration when the source file is unchanged.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

KEY_ITEMS_TXT = Path("config/key_items.txt")
HASH_FILE = Path("config/.key_items_source.sha256")
GEMINI_JSON = Path("config/key_items_prepared_gemini.json")


def load_dotenv_if_present() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = Path(__file__).resolve().parent
    for name in (".env", "env"):
        p = root / name
        if p.is_file():
            load_dotenv(p)
            break


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def read_stored_hash() -> str | None:
    if not HASH_FILE.is_file():
        return None
    return HASH_FILE.read_text(encoding="utf-8").strip()


def needs_gemini_prep(*, force: bool) -> bool:
    if force:
        return True
    if not KEY_ITEMS_TXT.is_file():
        return False
    if not GEMINI_JSON.is_file():
        return True
    stored = read_stored_hash()
    if not stored:
        return True
    return sha256_file(KEY_ITEMS_TXT) != stored


def write_stored_hash() -> None:
    if not KEY_ITEMS_TXT.is_file():
        return
    HASH_FILE.parent.mkdir(parents=True, exist_ok=True)
    HASH_FILE.write_text(sha256_file(KEY_ITEMS_TXT), encoding="utf-8")


def ensure_key_items_gemini_json(
    *,
    skip: bool = False,
    force: bool = False,
    model: str | None = None,
) -> None:
    """
    Regenerate key_items_prepared_gemini.json when key_items.txt changed or output missing.
    Exits the process on missing API key when prep is required.
    """
    load_dotenv_if_present()
    if skip:
        return
    if not KEY_ITEMS_TXT.is_file():
        print(f"File not found: {KEY_ITEMS_TXT}", file=sys.stderr)
        sys.exit(1)
    # Migration: JSON existed before hash tracking — stamp hash without calling Gemini.
    if not force and GEMINI_JSON.is_file() and not HASH_FILE.is_file():
        write_stored_hash()
        print(f"Recorded {HASH_FILE} for existing {GEMINI_JSON} (migration).", flush=True)
        return
    if not needs_gemini_prep(force=force):
        return

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print(
            "Key items need regeneration (config/key_items.txt changed or "
            f"{GEMINI_JSON} missing) but no API key is set.\n"
            "Set GOOGLE_API_KEY or GEMINI_API_KEY in env/.env, or run:\n"
            "  python prepare_key_items_gemini.py\n"
            "Or pass --skip-key-items-prep if the Gemini JSON is already correct.",
            file=sys.stderr,
        )
        sys.exit(1)

    from prepare_key_items_gemini import run_gemini_prepare

    m = model or os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
    run_gemini_prepare(KEY_ITEMS_TXT, GEMINI_JSON, model=m, dry_run=False)
    write_stored_hash()
    print(f"Updated {GEMINI_JSON} and {HASH_FILE}.", flush=True)
