from config import DB_POOL_SIZE, INSTANCES, MAX_CONNECTIONS


def test_pool_fits_database_limit():
    assert DB_POOL_SIZE * INSTANCES <= MAX_CONNECTIONS


def test_pool_is_positive():
    assert DB_POOL_SIZE > 0
