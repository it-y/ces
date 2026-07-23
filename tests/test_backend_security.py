import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core import security
from app.main import app
from app.media import routes as media_routes
from app.system.providers import public_provider


def test_public_provider_never_returns_full_volcengine_credentials():
    result = public_provider({
        "id": "volcengine",
        "volcengine_access_key_id": "AK-EXAMPLE-SECRET",
        "volcengine_secret_access_key": "SK-EXAMPLE-SECRET",
    })

    assert "volcengine_access_key_id" not in result
    assert "volcengine_secret_access_key" not in result
    assert result["has_volcengine_access_key"] is True
    assert result["has_volcengine_secret_key"] is True


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "http://127.0.0.1:3000/health",
    "http://localhost/private",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.1/internal",
    "http://192.168.1.1/internal",
])
def test_validate_remote_url_rejects_non_public_targets(url):
    with pytest.raises(HTTPException) as exc:
        security.validate_remote_url(url)
    assert exc.value.status_code == 400


def test_download_output_serves_scoped_local_file(tmp_path, monkeypatch):
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "result.png").write_bytes(b"image-bytes")
    monkeypatch.setattr(media_routes, "OUTPUT_DIR", outputs)
    client = TestClient(app)

    response = client.get("/api/download-output", params={"url": "/output/result.png"})

    assert response.status_code == 200
    assert response.content == b"image-bytes"
