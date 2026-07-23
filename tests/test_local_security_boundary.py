from fastapi.testclient import TestClient

from app.canvas import manager as canvas_manager
from app.main import app


def _isolate_canvas_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(canvas_manager, "CANVAS_DIR", tmp_path / "canvases")
    monkeypatch.setattr(canvas_manager, "CANVAS_FILES_DIR", tmp_path / "canvas-files")
    monkeypatch.setattr(canvas_manager, "PROJECTS_PATH", tmp_path / "projects.json")


def test_cross_origin_mutation_is_rejected(tmp_path, monkeypatch):
    _isolate_canvas_storage(tmp_path, monkeypatch)
    response = TestClient(app).post(
        "/api/canvases",
        headers={"Origin": "https://evil.example"},
        json={"title": "blocked"},
    )
    assert response.status_code == 403


def test_local_origin_mutation_is_allowed(tmp_path, monkeypatch):
    _isolate_canvas_storage(tmp_path, monkeypatch)
    response = TestClient(app).post(
        "/api/canvases",
        headers={"Origin": "http://127.0.0.1:3000", "Host": "127.0.0.1:3000"},
        json={"title": "allowed"},
    )
    assert response.status_code == 200


def test_cors_preflight_does_not_allow_arbitrary_origin():
    response = TestClient(app).options(
        "/api/canvases",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.headers.get("access-control-allow-origin") is None
