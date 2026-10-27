"""repository ingest status

Revision ID: 0cb00b6a6297
Revises: d6e15ab9f67a
Create Date: 2026-10-28 03:53:20.830095

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0cb00b6a6297"
down_revision: str | Sequence[str] | None = "d6e15ab9f67a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column(
            "ingest_status",
            sa.Enum(
                "not_indexed",
                "queued",
                "running",
                "indexed",
                "failed",
                name="ingeststatus",
                native_enum=False,
                length=32,
            ),
            server_default="not_indexed",
            nullable=False,
        ),
    )
    op.add_column("repositories", sa.Column("ingest_error", sa.Text(), nullable=True))
    op.add_column(
        "repositories", sa.Column("last_indexed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_repositories_ingest_status"),
        "repositories",
        "ingest_status IN ('not_indexed', 'queued', 'running', 'indexed', 'failed')",
    )
    # Repositories indexed before these columns existed (by scripts/seed_demo.py): their files
    # are already in the knowledge base.
    op.execute(
        """
        UPDATE repositories AS r
        SET ingest_status = 'indexed',
            last_indexed_at = indexed.at,
            last_indexed_sha = coalesce(r.last_indexed_sha, indexed.sha)
        FROM (
            SELECT project_id, max(created_at) AS at, max(commit_sha) AS sha
            FROM documents
            WHERE source_type IN ('doc', 'code')
            GROUP BY project_id
        ) AS indexed
        WHERE indexed.project_id = r.project_id
        """
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_repositories_ingest_status"), "repositories", type_="check")
    op.drop_column("repositories", "last_indexed_at")
    op.drop_column("repositories", "ingest_error")
    op.drop_column("repositories", "ingest_status")
