"""
Smoke test for v4 library API — tests all critical functions.
"""

import asyncio, json, sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app.assets.library import (
    load_asset_library, store_asset, resolve_asset,
    create_library, rename_library, delete_library,
    create_category, update_category, delete_category,
    add_url_item, update_item, move_asset,
    batch_add_items, batch_delete_items,
    store_workflow, resolve_workflow,
)


async def test():
    # 1. Load — initial state
    lib = await load_asset_library()
    print("=== GET /api/asset-library ===")
    print(f"  version: {lib['version']}")
    print(f"  libraries: {len(lib['libraries'])}")
    for l in lib['libraries']:
        print(f"    '{l['name']}': {len(l['categories'])} cats, {sum(len(c['items']) for c in l['categories'])} items")

    # 2. Create library
    r = await create_library("测试用库")
    lib_id = r['id']
    print(f"\n=== POST /api/asset-library/libraries ===")
    print(f"  created: {r}")

    # 3. Create category
    r2 = await create_category(lib_id, "测试图片", "image")
    cat_id = r2['id']
    print(f"\n=== POST /api/asset-library/categories ===")
    print(f"  created: {r2}")

    # 4. Add URL item
    item = await add_url_item(cat_id, {"name": "test.png", "url": "/assets/test.png", "kind": "image"})
    print(f"\n=== POST /api/asset-library/items ===")
    print(f"  created: id={item['id']}, name={item['name']}")

    # 5. Resolve
    resolved = await resolve_asset(item['id'])
    assert resolved is not None
    print(f"\n=== GET /api/asset-library/items/{item['id']}/resolve ===")
    print(f"  name={resolved['name']}, url={resolved['url']}")

    # 6. Update item
    await update_item(item['id'], {"name": "renamed.png", "tags": ["test"]})
    import time as ttime
    ttime.sleep(0.5)  # wait for async write to complete
    lib_after = await load_asset_library()
    # Find the item in the library
    for l in lib_after['libraries']:
        for c in l['categories']:
            for i in c['items']:
                if i['id'] == item['id']:
                    print(f"  item name in load_asset_library: {i['name']}")
                    break
    resolved2 = await resolve_asset(item['id'])
    print(f"  resolved name: {resolved2['name'] if resolved2 else 'None'}")
    assert resolved2 and resolved2['name'] == 'renamed.png'
    print(f"\n=== PATCH /api/asset-library/items/{item['id']} ===")
    print(f"  updated name: {resolved2['name']}")

    # 7. Move item
    r3 = await create_category(lib_id, "测试分类2", "image")
    cat2_id = r3['id']
    await move_asset(item['id'], target_category_id=cat2_id)
    resolved3 = await resolve_asset(item['id'])
    assert resolved3['category_id'] == cat2_id
    print(f"\n=== POST /api/asset-library/items/move ===")
    print(f"  moved to category: {resolved3['category_id']}")

    # 8. Delete item
    await batch_delete_items([item['id']])
    assert await resolve_asset(item['id']) is None
    print(f"\n=== DELETE /api/asset-library/items/{item['id']} ===")
    print(f"  deleted OK")

    # 9. Rename library
    await rename_library(lib_id, "测试用库-改名")
    lib_after = await load_asset_library()
    for l in lib_after['libraries']:
        print(f"  library: {l['name']} (id={l['id']})")
    found = any(l['name'] == '测试用库-改名' for l in lib_after['libraries'])
    print(f"  looking for name '测试用库-改名' with id {lib_id}: found={found}")
    assert found
    print(f"\n=== PATCH /api/asset-library/libraries/{lib_id} ===")
    print(f"  renamed: {found}")

    # 10. Cleanup - delete library
    await delete_library(lib_id)
    lib_after = await load_asset_library()
    found = any(l['id'] == lib_id for l in lib_after['libraries'])
    assert not found
    print(f"\n=== DELETE /api/asset-library/libraries/{lib_id} ===")
    print(f"  deleted: {not found}")

    print("\n=== ALL TESTS PASSED ===")


if __name__ == "__main__":
    asyncio.run(test())
