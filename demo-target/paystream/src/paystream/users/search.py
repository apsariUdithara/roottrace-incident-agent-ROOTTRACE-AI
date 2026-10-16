"""User search by name. Relies on the ix_users_name index (migration 0003)."""
import sqlite3

SEARCH_SQL = "SELECT id, email, name FROM users WHERE name = ? ORDER BY id LIMIT 50"


def search_by_name(conn: sqlite3.Connection, name: str) -> list[tuple]:
    return conn.execute(SEARCH_SQL, (name,)).fetchall()
