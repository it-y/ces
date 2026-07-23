import base64

import pytest

from app.canvas import manager
from app.generation import orchestrator


@pytest.fixture
def generation_storage(tmp_path, monkeypatch):
    active = tmp_path / "canvases"
    trash = tmp_path / "canvas-trash"
    resources = tmp_path / "canvas-files"
    outputs = tmp_path / "outputs"
    for directory in (active, trash, resources, outputs):
        directory.mkdir()
    monkeypatch.setattr(manager, "CANVAS_DIR", active)
    monkeypatch.setattr(manager, "CANVAS_TRASH_DIR", trash, raising=False)
    monkeypatch.setattr(manager, "CANVAS_FILES_DIR", resources)
    monkeypatch.setattr(manager, "PROJECTS_PATH", tmp_path / "projects.json")
    monkeypatch.setattr(orchestrator, "CANVAS_FILES_DIR", resources)
    monkeypatch.setattr(orchestrator, "OUTPUT_DIR", outputs)
    manager._canvas_locks.clear()
    return active, resources, outputs


def data_url(content=b"generated"):
    return "data:image/png;base64," + base64.b64encode(content).decode("ascii")


@pytest.mark.anyio
async def test_canvas_output_is_stored_only_in_matching_canvas(generation_storage):
    _, resources, outputs = generation_storage
    first = await manager.create_canvas("first")
    second = await manager.create_canvas("second")

    url = await orchestrator._download_or_keep(data_url(), first["id"])

    first_files = list((await manager.canvas_output_dir(first["id"])).glob("*"))
    second_files = list((await manager.canvas_output_dir(second["id"])).glob("*"))
    assert url.startswith("/cfiles/")
    assert len(first_files) == 1
    assert second_files == []
    assert list(outputs.glob("*")) == []


@pytest.mark.anyio
async def test_invalid_canvas_id_does_not_fall_back_to_global_output(generation_storage):
    _, _, outputs = generation_storage

    with pytest.raises(FileNotFoundError):
        await orchestrator._download_or_keep(data_url(), "missing-canvas-id")

    assert list(outputs.glob("*")) == []


@pytest.mark.anyio
async def test_unscoped_output_uses_global_directory(generation_storage):
    _, _, outputs = generation_storage

    url = await orchestrator._download_or_keep(data_url(), None)

    assert url.startswith("/output/")
    assert len(list(outputs.glob("*"))) == 1
