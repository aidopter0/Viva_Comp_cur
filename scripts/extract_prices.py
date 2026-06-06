from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from viva_tracker.db import connect_db, init_db
from viva_tracker.jobs import extract_prices


def main() -> None:
    conn = connect_db()
    init_db(conn)
    result = extract_prices(conn, triggered_by="cli")
    print(f"Extraction completed. run_id={result.run_id}")
    if result.export_label:
        print(f"Export label: {result.export_label}")
        print(f"Export dir: {result.export_dir}")
        if result.pruned_dirs:
            print(f"Pruned {len(result.pruned_dirs)} older export folder(s).")


if __name__ == "__main__":
    main()
