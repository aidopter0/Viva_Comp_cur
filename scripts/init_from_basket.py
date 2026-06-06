from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from viva_tracker.db import connect_db, init_db
from viva_tracker.jobs import initialize_from_csv
from viva_tracker.settings import BASKET_CSV_PATH


def main() -> None:
    conn = connect_db()
    init_db(conn)
    initialize_from_csv(conn, BASKET_CSV_PATH)
    print(f"Initialized DB from basket CSV: {BASKET_CSV_PATH}")


if __name__ == "__main__":
    main()
