"""Numbers for the Dashboard."""

import uuid

from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.core.errors import not_implemented
from app.schemas.stats import DashboardStats

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/dashboard")
async def dashboard(user: CurrentUser, project_id: uuid.UUID | None = None) -> DashboardStats:
    """Across all your projects, or one with `project_id` (404 if it isn't yours)."""
    raise not_implemented("B-804")
