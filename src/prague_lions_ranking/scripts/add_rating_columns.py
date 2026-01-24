"""Add rating columns to the users table.

Run this script once to migrate the database to support TrueSkill ratings.
"""

import sqlite3
from pathlib import Path


def migrate():
    # Database is in the project root (where the app is run from)
    db_path = Path.cwd() / "rankings.db"

    if not db_path.exists():
        print(f"Database not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get existing columns
    cursor.execute("PRAGMA table_info(users)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    columns_to_add = [
        ("mu", "REAL"),
        ("sigma", "REAL"),
        ("true_skill", "REAL"),
        ("number_of_practices", "INTEGER"),
        ("number_of_games", "INTEGER"),
        ("ratings_updated_at", "DATETIME"),
    ]

    for column_name, column_type in columns_to_add:
        if column_name not in existing_columns:
            print(f"Adding column: {column_name}")
            cursor.execute(f"ALTER TABLE users ADD COLUMN {column_name} {column_type}")
        else:
            print(f"Column already exists: {column_name}")

    conn.commit()
    conn.close()
    print("Migration complete!")


if __name__ == "__main__":
    migrate()
