"""Database connection settings.

Each API instance keeps its own connection pool, so the total number of
connections opened against Postgres is DB_POOL_SIZE * INSTANCES. That total
must stay within the server's max_connections (see docs/database.md).
"""
import os

MAX_CONNECTIONS = int(os.getenv("PAYSTREAM_DB_MAX_CONNECTIONS", "100"))
INSTANCES = int(os.getenv("PAYSTREAM_INSTANCES", "5"))
DB_POOL_SIZE = int(os.getenv("PAYSTREAM_DB_POOL_SIZE", "20"))
DB_POOL_TIMEOUT_SECONDS = 5
