"""Create the SQLite fixture (test.db) used by the examples and tests.

The users table includes JSON columns (profile, settings) for nested,
document-style field paths such as ``profile.address.city``.
"""

from __future__ import annotations
import json
import sqlite3
from pathlib import Path


DB_PATH = Path(__file__).parent / "test.db"


def create_database() -> None:
    """Create (or recreate) the test database with sample data."""
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    # --- users table (v0.4: adds profile and settings JSON columns) ---
    cursor.execute("""
        CREATE TABLE users (
            id       INTEGER PRIMARY KEY,
            name     TEXT    NOT NULL,
            age      INTEGER NOT NULL,
            email    TEXT    NOT NULL,
            city     TEXT    NOT NULL,
            has_license INTEGER,
            status   TEXT,
            profile  TEXT,
            settings TEXT
        )
    """)

    users_data = [
        (1, "Alice",    30, "alice@example.com",    "Singapore", 1, "active",
         json.dumps({"address": {"city": "Singapore", "country": "Singapore"}, "bio": "Engineer"}),
         json.dumps({"notifications": {"email": True, "sms": False}})),
        (2, "Bob",      17, "bob@example.com",      "Jakarta", 0, "inactive",
         json.dumps({"address": {"city": "Jakarta", "country": "Indonesia"}, "bio": "Student"}),
         json.dumps({"notifications": {"email": True, "sms": True}})),
        (3, "Charlie",  22, "charlie@example.com",  "Singapore", 1, "active",
         json.dumps({"address": {"city": "Singapore", "country": "Singapore"}, "bio": "Designer"}),
         json.dumps({"notifications": {"email": False, "sms": False}})),
        (4, "Diana",    45, "diana@example.com",    "Kuala Lumpur", 1, "active",
         json.dumps({"address": {"city": "Kuala Lumpur", "country": "Malaysia"}, "bio": "Manager"}),
         json.dumps({"notifications": {"email": True, "sms": True}})),
        (5, "Eve",      15, "eve@example.com",      "Bangkok", 0, "inactive",
         json.dumps({"address": {"city": "Bangkok", "country": "Thailand"}, "bio": "Student"}),
         json.dumps({"notifications": {"email": False, "sms": False}})),
        (6, "Frank",    19, "frank@example.com",    "Singapore", 1, "active",
         json.dumps({"address": {"city": "Singapore", "country": "Singapore"}, "bio": "Developer"}),
         json.dumps({"notifications": {"email": True, "sms": True}})),
    ]
    cursor.executemany(
        "INSERT INTO users (id, name, age, email, city, has_license, status, profile, settings) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        users_data,
    )

    # --- orders table ---
    cursor.execute("""
        CREATE TABLE orders (
            id       INTEGER PRIMARY KEY,
            user_id  INTEGER NOT NULL,
            product  TEXT    NOT NULL,
            amount   REAL    NOT NULL,
            quantity INTEGER NOT NULL
        )
    """)

    orders_data = [
        (1,  1, "Widget",    19.99,  2),
        (2,  1, "Gadget",    49.99,  1),
        (3,  1, "Gizmo",     199.99, 1),
        (4,  3, "Widget",    19.99,  5),
        (5,  3, "Gadget",    49.99,  3),
        (6,  4, "Gizmo",     99.99,  1),
        (7,  4, "Widget",    19.99,  3),
        (8,  4, "Premium",   299.99, 1),
        (9,  6, "Gadget",    49.99,  2),
        (10, 6, "Widget",    19.99,  4),
    ]
    cursor.executemany(
        "INSERT INTO orders (id, user_id, product, amount, quantity) VALUES (?, ?, ?, ?, ?)",
        orders_data,
    )

    conn.commit()
    conn.close()

    print(f"Created database: {DB_PATH}")
    print("  Table: users    (6 rows, with name/age/email/city/has_license/status/profile/settings)")
    print("  Table: orders   (10 rows, with product/amount/quantity)")


if __name__ == "__main__":
    create_database()
