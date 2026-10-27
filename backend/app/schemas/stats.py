"""Response body for /stats/dashboard."""

from pydantic import BaseModel

from app.schemas.incident import IncidentOut
from app.schemas.investigation import InvestigationOut


class DashboardStats(BaseModel):
    """Averages are None until an investigation has finished."""

    investigations: int
    avg_confidence: float | None
    avg_duration_ms: int | None
    avg_cost_usd: float | None
    patch_success_rate: float | None  # verified patches / proposed patches
    recent_investigations: list[InvestigationOut]  # newest first
    recent_incidents: list[IncidentOut]  # newest first
