import sqlite3

from paystream.database.migrations import apply_all
from paystream.users.search import SEARCH_SQL


def test_user_search_uses_name_index():
    conn = sqlite3.connect(":memory:")
    apply_all(conn)
    plan = " ".join(row[-1] for row in conn.execute("EXPLAIN QUERY PLAN " + SEARCH_SQL, ("Ada",)))
    assert "ix_users_name" in plan
