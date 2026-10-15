import sqlite3

from paystream.database import config
from paystream.database.migrations import apply_all
from paystream.users.search import search_by_name


def test_pool_size_is_positive():
    assert config.DB_POOL_SIZE > 0


def test_migrations_create_tables_and_search_works():
    conn = sqlite3.connect(":memory:")
    apply_all(conn)
    conn.execute("INSERT INTO users VALUES (1, 'ada@example.com', 'Ada', '2026-01-01')")
    assert search_by_name(conn, "Ada") == [(1, "ada@example.com", "Ada")]
