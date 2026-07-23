from fastapi.testclient import TestClient

from app.generation import routes_jimeng
from app.main import app


def test_jimeng_login_missing_cli_returns_stable_api_error(monkeypatch):
    async def missing_cli():
        raise FileNotFoundError("jimeng-cli")

    monkeypatch.setattr(routes_jimeng.jimeng_subprocess, "start_login", missing_cli)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/api/jimeng/login/start")

    assert response.status_code == 503
    assert "CLI" in response.json()["detail"]
