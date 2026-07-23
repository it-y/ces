from app.main import app
from fastapi.testclient import TestClient


def route_methods():
    return {
        (method, route.path)
        for route in app.routes
        for method in (getattr(route, "methods", None) or set())
    }


def test_current_frontend_backend_contract_routes_exist():
    routes = route_methods()
    required = {
        ("POST", "/api/angle/generate"),
        ("GET", "/api/runninghub/workflow-info"),
        ("POST", "/api/shared-folders"),
        ("DELETE", "/api/shared-folders/{folder_id}"),
        ("GET", "/api/shared-folders/{folder_id}/tree"),
        ("GET", "/api/shared-folders/{folder_id}/file"),
        ("POST", "/api/workflows/{name}/run"),
    }

    assert required <= routes


def test_removed_page_capabilities_are_not_advertised():
    routes = route_methods()
    assert ("GET", "/api/test") not in routes

    response = TestClient(app).get("/api/app-info")
    assert response.status_code == 200
    assert "chat" not in response.json()["features"]


def test_canvas_assets_returns_frontend_contract():
    response = TestClient(app).get("/api/canvas-assets")

    assert response.status_code == 200
    assert set(response.json()) == {"categories", "canvases", "items"}
