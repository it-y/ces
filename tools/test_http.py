"""
Full HTTP end-to-end test for all asset library API endpoints.
Run against a running server on port 8799.
"""

import http.client
import json
import sys

HOST = "127.0.0.1"
PORT = 8799


def api(method, path, body=None):
    conn = http.client.HTTPConnection(HOST, PORT, timeout=10)
    headers = {"Content-Type": "application/json"}
    data = json.dumps(body).encode() if body else None
    conn.request(method, path, data, headers)
    r = conn.getresponse()
    return r.status, json.loads(r.read())


def test():
    # 1. GET asset library
    status, data = api("GET", "/api/asset-library")
    lib = data.get("library", {})
    print(f"1. GET /api/asset-library -> {status} ({len(lib.get('libraries', []))} libraries)")
    assert status == 200

    # 2. Create library
    status, data = api("POST", "/api/asset-library/libraries", {"name": "HTTP测试库"})
    assert status == 200
    lib_id = data.get("asset_library", {}).get("id", "")
    assert lib_id
    print(f"2. POST create library -> {status} (id={lib_id})")

    # 3. Create category
    status, data = api("POST", "/api/asset-library/categories", {"library_id": lib_id, "name": "图片"})
    assert status == 200
    cat_id = data.get("category", {}).get("id", "")
    assert cat_id
    print(f"3. POST create category -> {status} (id={cat_id})")

    # 4. Add URL item
    status, data = api("POST", "/api/asset-library/items", {
        "category_id": cat_id, "name": "http-test.png", "url": "/test.png", "kind": "image",
    })
    assert status == 200
    item_id = data.get("item", {}).get("id", "")
    assert item_id
    print(f"4. POST add item -> {status} (id={item_id})")

    # 5. Resolve item
    status, data = api("GET", f"/api/asset-library/items/{item_id}/resolve")
    assert status == 200
    assert data["item"]["name"] == "http-test.png"
    print(f"5. GET resolve -> {status} (name={data['item']['name']})")

    # 6. Update item
    status, data = api("PATCH", f"/api/asset-library/items/{item_id}", {"name": "renamed-http.png"})
    assert status == 200
    status, data = api("GET", f"/api/asset-library/items/{item_id}/resolve")
    assert data["item"]["name"] == "renamed-http.png"
    print(f"6. PATCH update -> {status} (name={data['item']['name']})")

    # 7. Move item
    status, data = api("POST", "/api/asset-library/categories", {"library_id": lib_id, "name": "分类2"})
    cat2_id = data["category"]["id"]
    status, data = api("POST", "/api/asset-library/items/move", {
        "ids": [item_id], "target_category_id": cat2_id,
    })
    assert status == 200
    assert data.get("moved", 0) == 1
    print(f"7. POST move -> {status} (moved={data.get('moved', 0)})")

    # 8. Classify item
    status, data = api("POST", "/api/asset-library/items/classify", {"names": [item_id]})
    assert status == 200
    assert data.get("count", 0) == 1
    print(f"8. POST classify -> {status} (count={data.get('count', 0)})")

    # 9. Delete item
    status, data = api("DELETE", f"/api/asset-library/items/{item_id}")
    assert status == 200
    status, data = api("GET", f"/api/asset-library/items/{item_id}/resolve")
    assert status == 404
    print(f"9. DELETE item -> 200, verify deleted -> 404 OK")

    # 10. Batch add items
    status, data = api("POST", "/api/asset-library/items/batch", {
        "category_id": cat_id,
        "items": [
            {"name": "batch1.png", "url": "/b1.png", "kind": "image"},
            {"name": "batch2.png", "url": "/b2.png", "kind": "image"},
        ],
    })
    assert status == 200
    assert len(data.get("items", [])) == 2
    print(f"10. POST batch items -> {status} ({len(data.get('items', []))} items)")

    # 11. Batch delete
    ids = [i["id"] for i in data["items"]]
    status, data = api("POST", "/api/asset-library/items/delete", {"ids": ids})
    assert status == 200
    assert data.get("removed", 0) == 2
    print(f"11. POST batch delete -> {status} (removed={data.get('removed', 0)})")

    # 12. Rename library
    status, data = api("PATCH", f"/api/asset-library/libraries/{lib_id}", {"name": "HTTP测试库-已改名"})
    assert status == 200
    status, data = api("GET", "/api/asset-library")
    found = any(l["name"] == "HTTP测试库-已改名" for l in data["library"]["libraries"])
    assert found
    print(f"12. PATCH rename library -> {status} (found renamed: {found})")

    # 13. Delete library
    status, data = api("DELETE", f"/api/asset-library/libraries/{lib_id}")
    assert status == 200
    status, data = api("GET", "/api/asset-library")
    found = any(l["id"] == lib_id for l in data["library"]["libraries"])
    assert not found
    print(f"13. DELETE library -> {status} (gone: {not found})")

    # 14. Prompt library
    status, data = api("GET", "/api/prompt-libraries")
    assert status == 200
    print(f"14. GET /api/prompt-libraries -> {status} ({len(data['library']['libraries'])} libs)")

    print("\n=== ALL HTTP TESTS PASSED ===")


if __name__ == "__main__":
    test()
