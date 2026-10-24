"""The Fix Generator's output: patches and the sandbox test runs that verify them."""

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    EMPTY_JSON_ARRAY,
    Base,
    CreatedAtMixin,
    UUIDPrimaryKeyMixin,
    enum_check,
    enum_type,
)


class PatchStatus(StrEnum):
    PROPOSED = "proposed"
    TESTS_FAILED = "tests_failed"
    VERIFIED = "verified"
    REJECTED = "rejected"
    UNTESTED = "untested"  # investigation-only mode: checked against evidence, not tests


class Patch(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "patches"
    __table_args__ = (
        UniqueConstraint("investigation_id", "attempt"),
        enum_check("status", PatchStatus),
    )

    investigation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE")
    )
    attempt: Mapped[int]  # 1, or 2 after a revision
    diff: Mapped[str] = mapped_column(Text)  # unified diff
    # [{"path": "...", "explanation": "..."}], for the per-file view in the Patch tab
    files_changed: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=EMPTY_JSON_ARRAY
    )
    explanation: Mapped[str | None] = mapped_column(Text)
    status: Mapped[PatchStatus] = mapped_column(
        enum_type(PatchStatus),
        default=PatchStatus.PROPOSED,
        server_default=PatchStatus.PROPOSED.value,
    )
    pr_title: Mapped[str | None] = mapped_column(String(300))
    pr_body: Mapped[str | None] = mapped_column(Text)
    pr_url: Mapped[str | None] = mapped_column(String(500))


class TestPhase(StrEnum):
    __test__ = False  # not a pytest test class

    BASELINE = "baseline"  # expected to fail: bug reproduced
    PATCHED = "patched"  # expected to pass


class TestRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __test__ = False  # not a pytest test class
    __tablename__ = "test_runs"
    __table_args__ = (enum_check("phase", TestPhase),)

    patch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("patches.id", ondelete="CASCADE"), index=True
    )
    phase: Mapped[TestPhase] = mapped_column(enum_type(TestPhase))
    command: Mapped[str] = mapped_column(Text)
    exit_code: Mapped[int | None]  # NULL if the run timed out
    stdout: Mapped[str] = mapped_column(Text, default="", server_default="")
    stderr: Mapped[str] = mapped_column(Text, default="", server_default="")
    passed: Mapped[int | None]
    failed: Mapped[int | None]
    duration_ms: Mapped[int | None]
    sandbox_image_id: Mapped[str | None] = mapped_column(String(200))  # proof it ran on Nebius
