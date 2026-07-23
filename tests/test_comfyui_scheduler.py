import asyncio
import json

import pytest
from fastapi import HTTPException

from app.comfyui import routes
from app.comfyui.scheduler import ComfyUIScheduler, ComfyUIUnavailableError


def test_workflow_names_reject_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr("app.comfyui.scheduler.WORKFLOW_DIR", tmp_path)
    scheduler = ComfyUIScheduler()

    for name in ("../secret.json", r"..\secret.json", "/absolute.json", r"C:\secret.json"):
        with pytest.raises(HTTPException) as exc:
            scheduler.save_workflow_file(name, "{}")
        assert exc.value.status_code == 400


def test_workflow_save_is_validated_json_and_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr("app.comfyui.scheduler.WORKFLOW_DIR", tmp_path)
    scheduler = ComfyUIScheduler()

    with pytest.raises(ValueError):
        scheduler.save_workflow_file("broken.json", "not-json")
    assert not (tmp_path / "broken.json").exists()

    scheduler.save_workflow_file("valid.json", '{"1": {"inputs": {}}}')
    assert json.loads((tmp_path / "valid.json").read_text(encoding="utf-8")) == {"1": {"inputs": {}}}
    assert not list(tmp_path.glob(".tmp_*"))


@pytest.mark.anyio
async def test_named_workflow_loads_json_before_submit(monkeypatch):
    loaded = {"1": {"inputs": {"seed": 1}}}
    observed = {}

    monkeypatch.setattr(routes.scheduler, "load_workflow", lambda name: loaded)

    async def fake_submit(workflow, params=None):
        observed["workflow"] = workflow
        observed["params"] = params
        return "prompt-1"

    monkeypatch.setattr(routes.scheduler, "submit_workflow", fake_submit)

    result = await routes.run_named_workflow("demo.json", {"params": {"1": {"seed": 2}}})

    assert result == {"prompt_id": "prompt-1", "status": "submitted"}
    assert observed == {"workflow": loaded, "params": {"1": {"seed": 2}}}


@pytest.mark.anyio
async def test_all_backends_unavailable_raises(monkeypatch):
    scheduler = ComfyUIScheduler()
    await scheduler.update_instances(["a:1", "b:2"])

    async def unavailable(_addr):
        raise OSError("offline")

    monkeypatch.setattr(scheduler, "_query_queue", unavailable)

    with pytest.raises(ComfyUIUnavailableError):
        await scheduler.get_best_backend()

    assert scheduler._load == {"a:1": 0, "b:2": 0}


@pytest.mark.anyio
async def test_backend_probes_run_concurrently(monkeypatch):
    scheduler = ComfyUIScheduler()
    await scheduler.update_instances(["a:1", "b:2"])
    started = set()
    both_started = asyncio.Event()

    async def query(addr):
        started.add(addr)
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=0.2)
        return {"queue_running": [], "queue_pending": []}

    monkeypatch.setattr(scheduler, "_query_queue", query)
    chosen = await scheduler.get_best_backend()

    assert chosen in {"a:1", "b:2"}
    assert started == {"a:1", "b:2"}


@pytest.mark.anyio
async def test_update_instances_preserves_matching_load():
    scheduler = ComfyUIScheduler()
    await scheduler.update_instances(["a:1", "b:2"])
    scheduler._load["b:2"] = 3

    result = await scheduler.update_instances(["b:2", "c:3", "b:2", ""])

    assert result == ["b:2", "c:3"]
    assert scheduler.instances == ["b:2", "c:3"]
    assert scheduler._load == {"b:2": 3, "c:3": 0}
