from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from app.models import Alert

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

@router.get("", response_model=list[Alert])
def list_alerts(request: Request, entity_id: Optional[str] = None, active: bool = False):
    """List all alerts, optionally filtered."""
    db = request.app.state.database
    return db.list_alerts(entity_id=entity_id, only_active=active)

@router.get("/active", response_model=list[Alert])
def list_active_alerts(request: Request):
    """List active alerts."""
    db = request.app.state.database
    return db.list_alerts(only_active=True)

@router.get("/{alert_id}", response_model=Alert)
def get_alert(alert_id: str, request: Request):
    """Retrieve an alert."""
    db = request.app.state.database
    alert = db.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert

@router.post("/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: str, request: Request):
    """Acknowledge an alert."""
    db = request.app.state.database
    if not db.get_alert(alert_id):
        raise HTTPException(status_code=404, detail="Alert not found")
    db.acknowledge_alert(alert_id)
    return {"status": "ok"}
