"""Add match_type column to the matches table.

Run this script once to migrate an existing database.
"""

import sqlite3
from pathlib import Path


def migrate():
    db_path = Path.cwd() / "rankings.db"

    if not db_path.exists():
        print(f"Database not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(matches)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    if "match_type" not in existing_columns:
        print("Adding column: match_type")
        cursor.execute("ALTER TABLE matches ADD COLUMN match_type TEXT")
    else:
        print("Column already exists: match_type")

    conn.commit()
    conn.close()
    print("Migration complete!")


if __name__ == "__main__":
    migrate()
