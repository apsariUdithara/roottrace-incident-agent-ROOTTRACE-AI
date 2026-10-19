from alembic import command
from alembic.config import Config


def test_models_match_migrations(alembic_cfg: Config) -> None:
    """Fails when a model changed without a migration: run `alembic revision --autogenerate`."""
    command.check(alembic_cfg)


def test_migrations_downgrade_and_upgrade_cleanly(alembic_cfg: Config) -> None:
    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")
