from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_api_stats_endpoint():
    response = client.get("/api/stats")
    assert response.status_code == 200
    data = response.json()
    assert "total_uploads" in data
    assert "total_views" in data
    assert "total_storage_mb" in data

def test_api_nonexistent_image():
    response = client.get("/api/image/nonexistent_slug")
    assert response.status_code == 404
