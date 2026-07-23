from pathlib import Path

import asyncio

import pytest

from app.canvas import manager


@pytest.fixture
def canvas_storage(tmp_path, monkeypatch):
    active = tmp_path / "canvases"
    trash = tmp_path / "canvas-trash"
    resources = tmp_path / "canvas-files"
    projects = tmp_path / "projects.json"
    for directory in (active, trash, resources):
        directory.mkdir()
    monkeypatch.setattr(manager, "CANVAS_DIR", active)
    monkeypatch.setattr(manager, "CANVAS_TRASH_DIR", trash, raising=False)
    monkeypatch.setattr(manager, "CANVAS_FILES_DIR", resources)
    monkeypatch.setattr(manager, "PROJECTS_PATH", projects)
    manager._canvas_locks.clear()
    return active, trash, resources


@pytest.mark.anyio
async def test_delete_moves_json_to_trash_and_keeps_resources(canvas_storage):
    active, trash, resources = canvas_storage
    canvas = await manager.create_canvas("demo")
    active_file = next(f for f in active.glob("*.json") if f.name != ".index.json")
    resource_dir = resources / active_file.stem
    marker = resource_dir / "outputs" / "keep.png"
    marker.write_bytes(b"image")

    await manager.delete_canvas(canvas["id"])

    assert not active_file.exists()
    assert (trash / active_file.name).exists()
    assert marker.exists()


@pytest.mark.anyio
async def test_restore_moves_json_back_to_active_directory(canvas_storage):
    active, trash, _ = canvas_storage
    canvas = await manager.create_canvas("demo")
    filename = next(f for f in active.glob("*.json") if f.name != ".index.json").name
    await manager.delete_canvas(canvas["id"])

    restored = await manager.restore_canvas(canvas["id"])

    assert restored["deleted_at"] is None
    assert (active / filename).exists()
    assert not (trash / filename).exists()


@pytest.mark.anyio
async def test_purge_deletes_only_trash_json_and_preserves_resources(canvas_storage):
    active, trash, resources = canvas_storage
    canvas = await manager.create_canvas("demo")
    filename = next(f for f in active.glob("*.json") if f.name != ".index.json").name
    resource_dir = resources / Path(filename).stem
    await manager.delete_canvas(canvas["id"])

    await manager.purge_canvas(canvas["id"])

    assert not (trash / filename).exists()
    assert resource_dir.exists()


@pytest.mark.anyio
async def test_load_canvas_any_can_read_canvas_from_trash(canvas_storage):
    _, _, _ = canvas_storage
    canvas = await manager.create_canvas("demo")
    await manager.delete_canvas(canvas["id"])

    loaded = await manager.load_canvas_any(canvas["id"])

    assert loaded["id"] == canvas["id"]
    assert loaded["deleted_at"] is not None


@pytest.mark.anyio
async def test_purge_does_not_delete_active_canvas(canvas_storage):
    active, _, _ = canvas_storage
    canvas = await manager.create_canvas("demo")
    active_file = next(f for f in active.glob("*.json") if f.name != ".index.json")

    await manager.purge_canvas(canvas["id"])

    assert active_file.exists()


@pytest.mark.anyio
async def test_meta_title_rename_keeps_json_and_resource_directory_in_sync(canvas_storage):
    active, _, resources = canvas_storage
    canvas = await manager.create_canvas("before")
    old_json = next(f for f in active.glob("*.json") if f.name != ".index.json")
    old_resource = resources / old_json.stem
    marker = old_resource / "outputs" / "keep.png"
    marker.write_bytes(b"image")

    updated = await manager.update_canvas_meta(canvas["id"], title="after")

    new_json = next(f for f in active.glob("*.json") if f.name != ".index.json")
    new_resource = resources / new_json.stem
    assert updated["title"] == "after"
    assert new_json.name.startswith("after_")
    assert not old_json.exists()
    assert new_resource.exists()
    assert (new_resource / "outputs" / "keep.png").read_bytes() == b"image"


@pytest.mark.anyio
async def test_active_and_trash_lists_scan_their_matching_directories(canvas_storage):
    active, trash, _ = canvas_storage
    kept = await manager.create_canvas("kept")
    deleted = await manager.create_canvas("deleted")

    await manager.delete_canvas(deleted["id"])
    active_items = await manager.list_canvases()
    trash_items = await manager.list_deleted_canvases()

    assert {item["id"] for item in active_items} == {kept["id"]}
    assert {item["id"] for item in trash_items} == {deleted["id"]}
    assert any(f.name != ".index.json" for f in active.glob(f"*_{kept['id'][:8]}.json"))
    assert any(trash.glob(f"*_{deleted['id'][:8]}.json"))


@pytest.mark.anyio
async def test_canvas_list_uses_metadata_index_after_create(canvas_storage, monkeypatch):
    active, _, _ = canvas_storage
    canvas = await manager.create_canvas("indexed")
    assert (active / ".index.json").exists()

    original_read_json = manager.read_json

    async def fail_on_canvas_json(path):
        if path.parent == active and path.name != ".index.json":
            raise AssertionError("????????????? JSON")
        return await original_read_json(path)

    monkeypatch.setattr(manager, "read_json", fail_on_canvas_json)
    items = await manager.list_canvases()

    assert [item["id"] for item in items] == [canvas["id"]]


@pytest.mark.anyio
async def test_append_canvas_nodes_is_atomic_under_concurrency(canvas_storage):
    canvas = await manager.create_canvas("append")

    await asyncio.gather(
        manager.append_canvas_nodes(canvas["id"], [{"id": "a"}]),
        manager.append_canvas_nodes(canvas["id"], [{"id": "b"}]),
    )

    loaded = await manager.load_canvas(canvas["id"])
    assert {node["id"] for node in loaded["nodes"]} == {"a", "b"}


@pytest.mark.anyio
async def test_title_rename_rewrites_canvas_file_urls(canvas_storage):
    active, _, _ = canvas_storage
    canvas = await manager.create_canvas("before")
    old_stem = next(path for path in active.glob("*.json") if path.name != ".index.json").stem
    saved = await manager.save_canvas(
        canvas["id"],
        nodes=[{"id": "n1", "data": {"url": f"/cfiles/{old_stem}/outputs/image.png"}}],
        title="after",
    )
    new_file = next(path for path in active.glob("*.json") if path.name != ".index.json")

    assert saved["nodes"][0]["data"]["url"] == f"/cfiles/{new_file.stem}/outputs/image.png"


@pytest.mark.anyio
async def test_title_rename_rolls_back_resource_move_when_json_write_fails(canvas_storage, monkeypatch):
    active, _, resources = canvas_storage
    canvas = await manager.create_canvas("before")
    old_json = next(path for path in active.glob("*.json") if path.name != ".index.json")
    old_resource = resources / old_json.stem
    original_write = manager.write_atomic

    async def fail_new_file(path, data):
        if path.parent == active and path != old_json and path.name != ".index.json":
            raise OSError("disk full")
        await original_write(path, data)

    monkeypatch.setattr(manager, "write_atomic", fail_new_file)

    with pytest.raises(OSError, match="disk full"):
        await manager.update_canvas_meta(canvas["id"], title="after")

    assert old_json.exists()
    assert old_resource.exists()
    assert not any(path.name.startswith("after_") for path in active.glob("*.json") if path.name != ".index.json")
    assert not any(path.name.startswith("after_") for path in resources.iterdir() if path.is_dir())
