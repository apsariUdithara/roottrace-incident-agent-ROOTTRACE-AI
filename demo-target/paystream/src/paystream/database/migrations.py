"""Schema migrations, applied in order.

Kept as plain SQL so they run on SQLite in tests and on Postgres in production.
"""
import sqlite3

MIGRATIONS: list[tuple[str, str]] = [
    ("0001_create_users", """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""),
    ("0002_create_payments", """
        CREATE TABLE payments (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            amount TEXT NOT NULL,
            currency TEXT NOT NULL,
            idempotency_key TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""),
    ("0003_index_users_name", "CREATE INDEX ix_users_name ON users (name)"),
    ("0004_index_payments_user", "CREATE INDEX ix_payments_user_id ON payments (user_id)"),
]


def apply_all(conn: sqlite3.Connection) -> None:
    for _, sql in MIGRATIONS:
        conn.execute(sql)
    conn.commit()
