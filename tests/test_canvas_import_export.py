import io
import json
import zipfile

import pytest
from fastapi import HTTPException

from app.canvas import manager
from app.canvas.import_export import import_canvas_file, pack_canvas_assets


@pytest.mark.anyio
async def test_import_canvas_json_uses_new_id_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "CANVAS_DIR", tmp_path / "canvases")
    monkeypatch.setattr(manager, "CANVAS_FILES_DIR", tmp_path / "files")
    monkeypatch.setattr(manager, "PROJECTS_PATH", tmp_path / "projects.json")
    source = {"id": "old", "title": "demo", "nodes": [], "connections": []}

    result = await import_canvas_file(json.dumps(source).encode(), "demo.json")

    assert result["id"] != "old"
    assert list((tmp_path / "canvases").glob("*.json"))
    assert list((tmp_path / "files").iterdir())


@pytest.mark.anyio
async def test_import_canvas_zip_rejects_manifest_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "CANVAS_DIR", tmp_path / "canvases")
    monkeypatch.setattr(manager, "CANVAS_FILES_DIR", tmp_path / "files")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("canvas.json", json.dumps({"id": "old", "title": "demo"}))
        archive.writestr("resources-manifest.json", json.dumps({
            "resources": [{"url": "/assets/a.png", "file": "../outside.txt"}]
        }))
        archive.writestr("../outside.txt", b"secret")

    with pytest.raises(HTTPException) as exc:
        await import_canvas_file(buffer.getvalue(), "demo.zip")
    assert exc.value.status_code == 400
    assert not (tmp_path / "outside.txt").exists()


@pytest.mark.anyio
async def test_pack_canvas_assets_uses_item_name_and_requested_filename(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "image.png").write_bytes(b"png")

    content, filename = await pack_canvas_assets(
        [{"url": "/assets/image.png", "name": "friendly.png"}],
        "my-assets.zip",
        upload_dir=uploads,
        output_dir=tmp_path / "outputs",
        canvas_files_dir=tmp_path / "cfiles",
    )

    assert filename == "my-assets.zip"
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert archive.namelist() == ["friendly.png"]
        assert archive.read("friendly.png") == b"png"
