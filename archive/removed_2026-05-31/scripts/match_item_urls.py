from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from viva_tracker.db import connect_db, init_db
from viva_tracker.match_engine import match_all_stores


def main() -> None:
    conn = connect_db()
    init_db(conn)
    results = match_all_stores(conn)
    for r in results:
        print(r)


if __name__ == "__main__":
    main()
