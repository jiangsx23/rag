"""health check 测试"""
from fastapi.testclient import TestClient
from app.api import app

client = TestClient(app)


def test_health_returns_ok():
    """测试 /health 接口返回 200 + status ok"""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
