from pathlib import Path
import sys
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from viva_tracker.db import connect_db, init_db
from viva_tracker.jobs import refresh_store_uuids


def main() -> None:
    conn = connect_db()
    init_db(conn)
    rows = refresh_store_uuids(conn)
    df = pd.DataFrame(rows)
    if df.empty:
        print("No stores found.")
        return
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
