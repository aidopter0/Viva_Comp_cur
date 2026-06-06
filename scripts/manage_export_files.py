from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from viva_tracker.extraction_exports import list_run_export_dirs, prune_run_exports
from viva_tracker.settings import MAX_RUN_EXPORT_RETENTION, RUNS_EXPORT_DIR


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List and prune versioned extraction export folders."
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List retained run export folders (newest first)",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Delete run export folders beyond the retention limit",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=MAX_RUN_EXPORT_RETENTION,
        help=f"Maximum run folders to keep (default: {MAX_RUN_EXPORT_RETENTION})",
    )
    args = parser.parse_args()

    if not args.list and not args.prune:
        args.list = True

    entries = list_run_export_dirs(RUNS_EXPORT_DIR)
    if args.list:
        if not entries:
            print(f"No run export folders under {RUNS_EXPORT_DIR}")
        else:
            print(f"Run export folders under {RUNS_EXPORT_DIR} (newest first):")
            for idx, (run_id, label, path) in enumerate(entries, start=1):
                print(f"  {idx}. run_id={run_id} label={label} path={path}")

    if args.prune:
        deleted = prune_run_exports(RUNS_EXPORT_DIR, keep=args.keep)
        if deleted:
            print(f"Pruned {len(deleted)} folder(s) (keep={args.keep}):")
            for path in deleted:
                print(f"  removed {path}")
        else:
            print(f"No folders to prune (keep={args.keep}).")


if __name__ == "__main__":
    main()
