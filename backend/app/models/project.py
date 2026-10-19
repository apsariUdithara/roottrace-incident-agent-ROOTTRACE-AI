import uuid
from enum import StrEnum

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin, enum_check, enum_type


class Project(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Every row in the knowledge and investigation tables belongs to one project."""

    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("owner_id", "name"),)

    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")


class SandboxStatus(StrEnum):
    UNKNOWN = "unknown"
    READY = "ready"
    UNTESTABLE = "untestable"  # tests can't run in the sandbox: investigation-only mode


class Repository(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint("project_id", "url"),
        enum_check("sandbox_status", SandboxStatus),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(32), default="github", server_default="github")
    url: Mapped[str] = mapped_column(String(500))
    default_branch: Mapped[str] = mapped_column(String(255), default="main", server_default="main")
    local_path: Mapped[str | None] = mapped_column(String(500))
    encrypted_token: Mapped[str | None] = mapped_column(Text)  # Fernet; never sent to the client
    last_indexed_sha: Mapped[str | None] = mapped_column(String(64))
    # NULL means auto-detect: `pip install -r requirements.txt` or `pip install -e .`
    setup_command: Mapped[str | None] = mapped_column(Text)
    test_command: Mapped[str] = mapped_column(Text, default="pytest -q", server_default="pytest -q")
    sandbox_status: Mapped[SandboxStatus] = mapped_column(
        enum_type(SandboxStatus),
        default=SandboxStatus.UNKNOWN,
        server_default=SandboxStatus.UNKNOWN.value,
    )
