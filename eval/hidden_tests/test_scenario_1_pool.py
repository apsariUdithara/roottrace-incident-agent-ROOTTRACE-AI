from paystream.database import config


def test_total_pool_fits_database_limit():
    assert config.DB_POOL_SIZE * config.INSTANCES <= config.MAX_CONNECTIONS
