"""
v4 资产库测试 — 使用 HTTP 客户端（集成测试）。
等效于 tools/test_http.py 但运行在 pytest 框架下。
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    """每个测试一个干净的临时库目录"""
    monkeypatch.setattr(config, "LIBRARY_DIR", tmp_path / "library")
    monkeypatch.setattr(config, "ASSET_DIR", tmp_path / "library" / "assets")
    monkeypatch.setattr(config, "ASSET_INDEX_PATH", tmp_path / "library" / "assets" / ".index.json")
    monkeypatch.setattr(config, "LOCAL_DIR", tmp_path / "library" / "local")
    monkeypatch.setattr(config, "LOCAL_INDEX_PATH", tmp_path / "library" / "local" / ".index.json")
    monkeypatch.setattr(config, "WORKFLOW_LIBRARY_DIR", tmp_path / "library" / "workflows")
    monkeypatch.setattr(config, "WORKFLOW_LIBRARY_INDEX_PATH", tmp_path / "library" / "workflows" / ".index.json")
    monkeypatch.setattr(config, "TRASH_DIR", tmp_path / "library" / "trash")
    config.ensure_directories()
    return TestClient(app)


def test_full_crud_lifecycle(client):
    """创建库→分类→素材→更新→移动→删除全流程"""
    # 1. 获取空库
    r = client.get("/api/asset-library")
    assert r.status_code == 200

    # 2. 创建库
    r = client.post("/api/asset-library/libraries", json={"name": "测试库"})
    assert r.status_code == 200
    lib_id = r.json()["asset_library"]["id"]
    assert lib_id

    # 3. 创建分类
    r = client.post("/api/asset-library/categories", json={"library_id": lib_id, "name": "图片"})
    assert r.status_code == 200
    cat_id = r.json()["category"]["id"]

    # 4. 添加 URL 素材
    r = client.post("/api/asset-library/items", json={
        "category_id": cat_id, "name": "test.png", "url": "/test.png", "kind": "image",
    })
    assert r.status_code == 200
    item_id = r.json()["item"]["id"]

    # 5. 解析素材
    r = client.get(f"/api/asset-library/items/{item_id}/resolve")
    assert r.status_code == 200
    assert r.json()["item"]["name"] == "test.png"

    # 6. 更新素材名
    r = client.patch(f"/api/asset-library/items/{item_id}", json={"name": "renamed.png"})
    assert r.status_code == 200
    r = client.get(f"/api/asset-library/items/{item_id}/resolve")
    assert r.json()["item"]["name"] == "renamed.png"

    # 7. 移动到新分类
    r = client.post("/api/asset-library/categories", json={"library_id": lib_id, "name": "分类2"})
    cat2_id = r.json()["category"]["id"]
    r = client.post("/api/asset-library/items/move", json={"ids": [item_id], "target_category_id": cat2_id})
    assert r.status_code == 200
    assert r.json()["moved"] == 1

    # 8. 删除素材
    r = client.delete(f"/api/asset-library/items/{item_id}")
    assert r.status_code == 200
    r = client.get(f"/api/asset-library/items/{item_id}/resolve")
    assert r.status_code == 404

    # 9. 删除库
    r = client.delete(f"/api/asset-library/libraries/{lib_id}")
    assert r.status_code == 200
    r = client.get("/api/asset-library")
    assert all(l["id"] != lib_id for l in r.json()["library"]["libraries"])


def test_local_asset_upload(client, monkeypatch):
    """上传本地素材"""
    monkeypatch.setattr("app.upload.routes.LOCAL_DIR", config.LOCAL_DIR)
    monkeypatch.setattr("app.upload.routes.LOCAL_INDEX_PATH", config.LOCAL_INDEX_PATH)
    r = client.post("/api/local-assets/upload", data={"folder": "参考图"}, files={"files": ("ref.png", b"image", "image/png")})
    assert r.status_code == 200
    item = r.json()["files"][0]
    assert item["url"].startswith("/api/local-assets/files/")
    assert (config.LOCAL_DIR / item["path"]).read_bytes() == b"image"


def test_local_asset_folder_crud(client):
    """本地素材文件夹的创建、移动、重命名、删除"""
    client.post("/api/local-assets/folders", json={"path": "A"})
    uploaded = client.post("/api/local-assets/upload", data={"folder": "A"}, files={"files": ("x.png", b"x", "image/png")}).json()["files"][0]

    moved = client.post("/api/local-assets/move", json={"names": [uploaded["path"]], "folder": "B"})
    assert moved.status_code == 200

    renamed = client.patch("/api/local-assets/items", json={"path": "B/x.png", "name": "y.png"})
    assert renamed.status_code == 200

    deleted = client.post("/api/local-assets/delete", json={"names": ["B/y.png"]})
    assert deleted.status_code == 200
