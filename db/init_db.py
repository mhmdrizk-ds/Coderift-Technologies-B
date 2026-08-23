"""
init_db.py — builds db/coderift.db from schema.sql + seed.sql.

Run directly: python db/init_db.py
Always drops and rebuilds, so every demo run starts from the same fixed
seed state.
"""

import sqlite3
from pathlib import Path

DB_DIR = Path(__file__).resolve().parent
# The db file lives in its own subdirectory (db/data/), separate from
# schema.sql/seed.sql/migrations/init_db.py which stay directly in db/.
# This is required for Docker: a named volume mounted onto a single FILE
# path is unreliable across Docker Engine versions/storage drivers (fails
# with "source ... is not directory"). Mounting a named volume onto a
# whole directory is reliable, so db/data/ exists purely to be that mount
# target -- see docker-compose.yml's db_data volume.
DATA_DIR = DB_DIR / "data"
DB_PATH = DATA_DIR / "coderift.db"
SCHEMA_PATH = DB_DIR / "schema.sql"
SEED_PATH = DB_DIR / "seed.sql"


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.executescript(SEED_PATH.read_text(encoding="utf-8"))
    conn.commit()
    conn.close()

    print(f"Built {DB_PATH} from schema.sql + seed.sql")


if __name__ == "__main__":
    main()
