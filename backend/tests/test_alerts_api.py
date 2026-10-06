
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

def test_list_alerts_empty():
    response = client.get("/api/alerts")
    assert response.status_code == 200
    assert response.json() == []

def test_get_alert_not_found():
    response = client.get("/api/alerts/nonexistent")
    assert response.status_code == 404

def test_acknowledge_not_found():
    response = client.post("/api/alerts/nonexistent/acknowledge")
    assert response.status_code == 404
