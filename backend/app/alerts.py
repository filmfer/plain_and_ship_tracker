"""Alert persistence and resolution."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.anomalies import AlertState, AnomalyType


class Alert(BaseModel):
    """Normalized alert for anomaly findings."""
    id: str
    entity_id: str
    timestamp: datetime
    type: AnomalyType
    score: int
    severity: AlertState
    description: str
    detected_at: datetime
    resolved_at: Optional[datetime] = None
    acknowledged: bool = False
