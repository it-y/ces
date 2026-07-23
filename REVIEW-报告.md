# Infinite Canvas 重构代码审查报告

> **审查范围**：重构代码 `E:/项目/项目/image/app/` vs 源代码 `E:/新建文件夹 (2)/Infinite-Canvas-main/main.py`
> **审查方式**：逐行阅读重构代码 + 对照源码关键段落 + 路由全量对比
> **状态**：本报告第一版。核心层 / 画布模块 / 路由缺失统计 / 存储结构变更为**已确认**；generation / assets / chat / comfyui / system / media / modelscope / upload 模块的逐行审查待后续补充（标 ⏳）。
> **原则**：只审查，不改代码。

---

## 〇、一句话结论

这次"重构"**不是 1:1 的等价重构，而是一次大幅删减 + 存储结构重做**。重构代码只有 **5876 行 / 92 个路由**，源代码是 **15131 行 / 148 个路由**，**缺失 78 个路由（53%）**。同时画布/资产/历史/配置的存储路径和文件格式全部改动，与 CLAUDE.md 明令"前端零改动、API 路径和响应格式必须一致、画布文件可携特性必须保留"的要求**大面积冲突**。以当前状态，把重构版后端接上原前端后，**大部分功能会 404 或行为异常**，无法直接替代源码。

---

## 一、规模与结构对比

| 指标                        | 源代码                | 重构代码                    | 差异                  |
| ------------------------- | ------------------ | ----------------------- | ------------------- |
| 后端总行数                     | 15131 行（单 main.py） | 5876 行（55 个 .py）        | **缩减 61%**          |
| 路由数                       | 148                | 92                      | **缺失 78（53%）**      |
| 最大单文件                     | main.py 15131 行    | canvas/manager.py 547 行 | 拆分达标                |
| threading.Lock            | 10 个               | 0 个                     | ✅ 已全部改 asyncio.Lock |
| 同步 HTTP (urllib/requests) | 13+ 处              | 待全量核对                   | ⏳                   |
| 启动 migrate 函数             | 3 个                | 0 个                     | ❌ 缺失                |
| 前端 static/                | 完整                 | 直接复用                    | ✅                   |

**结构上的正面**：模块化拆分本身合理（app/canvas、app/generation、app/assets 等），锁改成了 asyncio.Lock，httpx 客户端做了池化复用，JSON 写入加了原子写（临时文件 + os.replace + fsync）。这些是改进。

**结构上的负面**：功能大面积缺失，存储布局被重做导致与老数据不兼容，多处 async 函数内部仍是同步 IO（伪异步）。

---

## 二、🔴 严重问题（会导致功能错误 / 崩溃 / 数据丢失 / 前端不可用）

### 2.1 【致命】缺失 78 个路由（源码 53% 的 API 没了）

经全量对比，以下源码路由在重构版中**完全不存在**。前端调用必然 404。

**资产库管理（几乎全缺）**

- `POST/PATCH/DELETE /api/asset-library/categories`（分类 CRUD）
- `POST/PATCH/DELETE /api/asset-library/libraries`（库 CRUD）
- `PATCH /api/asset-library/items/{item_id}`（修改单项）
- `POST /api/asset-library/items/batch`（批量导入）
- `POST /api/asset-library/items/classify`（AI 分类）
- `POST /api/asset-library/items/crop`（裁剪）
- `POST /api/asset-library/items/delete`（批量删除）
- `POST /api/asset-library/items/move`（移动）
- `POST /api/asset-library/items/{item_id}/register-avatar`（数字人注册）
- `POST /api/asset-library/items/{item_id}/avatar-status`（数字人状态）
- `POST /api/asset-library/workflows/upload`（工作流上传到资产库）

**提示词库（全缺）**

- `PATCH/DELETE /api/prompt-libraries/{library_id}`
- `POST/DELETE/PATCH /api/prompt-libraries/categories[/{id}]`
- `POST /api/prompt-libraries/items`、`POST /api/prompt-libraries/items/delete`
- `PATCH/DELETE /api/prompt-libraries/items/{item_id}`

**本地资产 local-assets（几乎全缺，源码 12 个路由）**

- `POST /api/local-assets/upload`、`POST /api/local-assets/import-urls`
- `POST/PATCH /api/local-assets/folders`、`PATCH /api/local-assets/items`
- `POST /api/local-assets/delete`、`POST /api/local-assets/move`
- `POST/PATCH /api/local-assets/caption`、`POST /api/local-assets/classify`

**共享文件夹（仅留 GET 列表，其余缺）**

- `POST /api/shared-folders`、`DELETE /api/shared-folders/{folder_id}`
- `GET /api/shared-folders/{folder_id}/tree`、`GET /api/shared-folders/{folder_id}/file`
- `POST /api/shared-folders/import`

**画布工作流导入导出（全缺）**

- `POST /api/canvas-workflows/export`、`/export-to-library`、`/import`
- `POST /api/smart-canvas/group-export`、`GET /api/smart-canvas/prompt-templates`

**对话管理（部分缺）**

- `GET /api/conversations/{conversation_id}`、`DELETE /api/conversations/{conversation_id}`
- `POST /api/chat/agent`（agent 模式）

**更新机制（部分缺）**

- `GET /api/update-connectivity`、`GET /api/update-connectivity/probe`（连通性探测）

**供应商（部分缺）**

- `POST /api/providers/probe-async`、`GET /api/providers/{provider_id}/fetch-models`
- `GET /api/models`（旧版模型列表）

**RunningHub（路径全改，前端不兼容）**

- 源码：`/api/runninghub/submit`、`/workflow-submit`、`/query`、`/upload-asset`、`/workflow-info`、`/workflows/fetch`、`/workflows/{id:path}`(GET/PUT/DELETE)、`/app-info`
- 重构：`/api/runninghub/apps/submit`、`/workflows/submit`、`/task/query`、`/apps`、`/models`、`/workflows/{workflow_id}` —— **路径全部对不上**

**其它**

- `POST /generate`（旧版生成入口，源码 L14115）
- `POST /api/angle/generate`、`POST /api/angle/poll_status`（360 全景图）
- `POST /api/temp-sh/upload`、`POST /api/cloud-video/upload`
- `POST /api/comfyui/upload-base64`
- `GET /api/view`、`GET /api/image-params`
- `POST /api/projects/{project_id}`（源码用 POST 改名，重构用 PUT）
- `GET/DELETE/PUT/POST /api/workflows/{name:path}`（源码用 `:path` 通配，重构用 `{name}` 不支持斜杠）

> **结论**：这是本次审查最严重的问题。不是个别 bug，是系统性功能缺失。

---

### 2.2 【致命】存储路径与文件格式全部改动，老数据不可用

CLAUDE.md 明确要求"画布文件可携特性必须保留""`data/canvases/*.json` 直接拷贝迁移"。但重构版把存储布局整个重做了：

| 数据      | 源码路径                                          | 重构路径                                                             | 影响            |
| ------- | --------------------------------------------- | ---------------------------------------------------------------- | ------------- |
| 画布 JSON | `data/canvases/{canvas_id}.json`（文件名=完整id）    | `data/canvases/{title}_{date}_{id前8位}.json`                      | ❌ 文件名含标题，改名即变 |
| 画布资源    | `assets/output/`、`assets/input/`（全局共享）        | `data/canvas-files/{title}_{date}_{id8}/inputs\|outputs/`（每画布独立） | ❌ 结构不同，无法互迁   |
| 对话      | `data/conversations/{user_id}/{conv_id}.json` | `data/conversations/`（扁平？待确认）                                    | ⏳             |
| 资产库     | `data/asset_library.json`                     | `data/library/index.json`                                        | ❌ 路径变         |
| 提示词库    | `data/prompt_libraries.json`                  | `data/library/prompts.json`                                      | ❌ 路径变         |
| 项目      | `data/projects.json`                          | `data/library/projects.json`                                     | ❌ 路径变         |
| 供应商配置   | `data/api_providers.json`                     | `data/config/providers.json`                                     | ❌ 路径变         |
| 共享文件夹   | `data/shared_folders.json`                    | `data/config/shared-folders.json`                                | ❌ 路径变         |
| 历史      | `history.json`（根目录）                           | `data/history/history.json`                                      | ❌ 路径变         |
| API env | `API/.env`                                    | `data/config/env`                                                | ❌ 路径变         |
| 输出      | `output/`                                     | `data/outputs/`                                                  | ❌ 路径变         |
| 上传      | `assets/uploads/`                             | `data/uploads/`                                                  | ❌ 路径变         |
| 静态挂载    | `/static` `/output` `/assets`                 | 多挂了 `/cfiles`                                                    | 新增挂载          |

**直接后果**：

1. 老用户的 `data/` 目录直接拿来用，**所有路径都找不到**，画布/资产/配置全部"丢失"（实际还在老路径）。
2. 前端引用的 `/output/xxx`、`/assets/xxx` 图片 URL，在重构版里文件实际在 `data/outputs/`、`data/uploads/`，**静态挂载虽然还叫 `/output` `/assets`，但指向的物理目录变了**，老图片 URL 全部 404。
3. 画布 JSON 文件名从"纯 id"变成"标题+日期+短id"，破坏了"直接拷贝迁移"的核心卖点——拷贝过去文件名对不上、资源文件夹也要配套拷贝。

---

### 2.3 【致命】`/api/app-info` 响应格式完全不同

源码（L1639-1664）返回更新机制所需的全部信息：

```json
{
  "version": "...",
  "repo_url": "...", "version_url": "...", "tree_url": "...",
  "sources": { "github": {...}, "modelscope": {...} },
  "update_notes": "..."
}
```

重构版（main.py:106）返回：

```json
{ "app": "...", "version": "...", "status": "running", "features": [...], "data_dir": "..." }
```

**前端读 `repo_url`/`sources`/`update_notes` 的字段全部 undefined**，更新检查、GitHub/ModelScope 双源切换、更新日志展示全部失效。这是前端首屏就会触发的接口，影响面极大。

---

### 2.4 【高】画布按 id 查找退化为 O(N) 全表扫描

源码：`data/canvases/{canvas_id}.json`，文件名即完整 id，`load_canvas` 直接 `open(path)`，**O(1)**。

重构版（canvas/manager.py:55-63 `_find_canvas_file`）：用 `canvas_id[:8]` 去 `CANVAS_DIR.glob("*_{short}.json")`，然后逐个读 JSON 比对 `id` 字段。**每次加载/保存/删除画布都要 glob + 读多个文件**，O(N)。

**衍生问题**：

1. **短 id 碰撞风险**：uuid 前 8 位只有 32 bit，画布多了（几百上千）碰撞概率非可忽略；碰撞时 glob 会命中多个文件，逐个读 JSON 比对，正确性靠运气（实际能比对出来，但性能更差）。
2. **`list_canvases` / `list_projects` 每次都全量扫 + 读所有画布 JSON**（manager.py:345、487），且 `list_canvases` 还会先调 `_cleanup_trash`（又一次全量扫 + 读）。源码也有类似扫描，但源码是单文件直接读；重构版因为文件名不含完整信息，必须读内容。画布多时明显卡顿。
3. **`save_canvas` 改标题时重命名文件**（manager.py:194-211）：先写新文件名 `write_atomic(new)`，再 `path.unlink()` 删旧文件。在这两步之间，如果有并发请求用旧 shortid glob，会同时命中新旧两个文件（都含相同 shortid 后缀），`_find_canvas_file` 取第一个返回，行为不确定。存在竞态。

---

### 2.5 【高】`update_canvas_meta` 改标题不重命名文件，导致状态不一致

`update_canvas_meta`（manager.py:218-242）允许改 `title`，但**只改 JSON 内容里的 title 字段，不重命名文件**。而 `save_canvas` 改 title 时**依赖文件名含 title**（manager.py:195-198 重新 `_make_filename`）。

后果：

- 用户通过 meta 接口改了标题 → 文件名还是旧标题 → 之后 `save_canvas` 保存时 `_make_filename(title,...)` 算出的新文件名 ≠ 当前文件名 → 触发重命名分支 → 又走"写新删旧"的竞态路径。
- 文件名和内容长期不一致，`_find_canvas_file` 靠 shortid 还能找到，但任何依赖文件名解析 title/date 的逻辑都会拿到旧值。

源码没有这个问题（文件名就是 id，title 只在内容里）。

---

### 2.6 【高】协议分发用硬编码 provider id，破坏"单模型覆盖协议"机制

源码 `effective_protocol(provider, model)`（L3795-3806）支持：某个 provider 配了 `model_protocols`，可以让**指定模型**走不同协议（例如 OpenAI 协议的 provider，某个模型走 gemini）。这是 `normalize_provider` schema 里的 `model_protocols` 字段。

重构版 orchestrator.py:93-109 的 `_dispatch`：

```python
if pid == "modelscope":          # ← 硬编码 id
    gw = ModelScopeGateway(provider)
elif is_jimeng_provider(provider):
    ...
```

用 `pid == "modelscope"` 判断，而不是 `proto == "modelscope"`。如果用户把某个自定义 provider 的 id 起成别的名字但协议是 modelscope，或者反过来，分发就错了。**`effective_protocol` 的返回值 `proto` 只在 `gemini` 那一支用到**，其它分支都绕过了它。这违反了 CLAUDE.md "6 种协议分发顺序必须一致"的要求。

---

### 2.7 【高】生成图片下载逻辑产生孤儿文件 + 同步 IO 阻塞

orchestrator.py:116-187 `_download_or_keep`：

1. **写两份**：先 `write_bytes` 到 `OUTPUT_DIR`（行175），有 canvas_id 时再 `write_bytes` 到 canvas outputs 目录（行182），但**返回的是 `/cfiles/...` 路径**（行184）。结果 `OUTPUT_DIR` 里那份成了无人引用的孤儿文件，浪费磁盘且永远不会被清理。
2. **同步写阻塞**：`write_bytes` 是同步阻塞调用，在 async 函数里直接用，大文件（视频）会阻塞 event loop。蓝图要求用 `asyncio.to_thread` 或 aiofiles。
3. **下载重试无指数退避上限**：`asyncio.sleep(2 ** attempt)`，attempt 0/1/2，还行，但 3 次都失败直接返回原 URL，前端拿到外部 URL 可能跨域无法显示。

---

### 2.8 【高】多处 async 函数内部是同步 IO（伪异步）

CLAUDE.md 要求"同步阻塞改异步"。但重构版多处 `async def` 里直接用同步 IO：

| 位置                                                | 问题                                            |
| ------------------------------------------------- | --------------------------------------------- |
| `core/errors.py:21` `read_json`                   | `async def` 但用 `path.read_text()` 同步读         |
| `core/errors.py:31` `write_atomic`                | `async def` 但 `json.dump` + `os.fsync` 同步写    |
| `orchestrator.py:175/182`                         | `write_bytes` 同步                              |
| `orchestrator.py:244-273` `_save_history`         | 锁内 `read_text` + `json.dump` + `os.fsync` 全同步 |
| `canvas/manager.py` 多处 `read_json`/`write_atomic` | 同上                                            |

虽然加了 `asyncio.Lock` 防竞态，但**IO 本身仍阻塞 event loop**——锁只保证不并发，不保证不阻塞。蓝图明确要求 aiofiles。这是"形似异步、实为同步"。

---

## 三、🟡 功能缺失 / 行为偏差（待逐模块确认，⏳ 表示需进一步核对）

### 3.1 启动迁移函数全部缺失

源码 startup_event（L179-198）调用 3 个 migrate：

- `migrate_asset_library_into_dirs`（资产库分组）
- `migrate_double_extension_uploads`（修复双扩展名）
- `migrate_mislabeled_image_extensions`（纠正错误扩展名）

重构版 `main.py` lifespan（行30-46）只做了 `ensure_directories()` + `setup_logging()`，**3 个 migrate 全无**。老数据迁移、文件名修复功能丢失。

### 3.2 `schedule_self_restart` 跨平台重启 ⏳

源码 L1955-2027，更新后自动重启。重构版 `system/updater.py`（184行）待确认是否完整实现 Windows `_self_restart.bat` + Unix `_self_restart.sh`。

### 3.3 火山引擎 V4 签名 ⏳

源码 L6806-6849 的 HMAC 签名链。重构版 `generation/gateways/volcengine.py`（104行）待核对是否完整。104 行要装下完整 V4 签名偏紧。

### 3.4 RunningHub 7 字段类型推断 + seed 随机化 ⏳

源码 L8152-8308（约 150 行）+ L7050-8631（整体约 1500 行）。重构版 `gateways/runninghub.py` 只有 207 行，**大概率大幅简化或缺失**。需核对。

### 3.5 Jimeng 7 种视频模式 + WSL + 长驻 login ⏳

源码 Jimeng 部分约 750 行（L3872-4623）。重构版 jimeng/process.py(131) + image.py(57) + video.py(93) + wsl.py(42) = 323 行。WSL 兼容、长驻 login 进程、QR 码解析、7 种视频模式参数映射，**容量上勉强，需核对完整性**。

### 3.6 Apimart TLS 重试 ⏳

源码 `is_transient_tls_error`（L6522-6553）。重构版待确认是否保留。

### 3.7 `download-output` Range header 支持 ⏳

源码 L9054-9100，视频 seek 必需。重构版 `media/routes.py`（213行）待核对。

### 3.8 `reload_env_globals` 配置热重载 ⏳

源码 L570-596，PUT /api/providers 后 7 个全局变量立即生效。重构版 `system/providers.py`（290行）待确认。

### 3.9 `normalize_provider` 完整 schema ⏳

源码 L1146-1193，28 字段。重构版待确认是否完整（尤其 rh_apps/rh_workflows/ms_loras/volcengine_*）。

### 3.10 ModelScope 4 处无 timeout 下载 ⏳

源码 L13981/L14071/L14169/L14265 已知无 timeout。重构版 `modelscope/routes.py` + `gateways/modelscope.py` 待确认是否补了 timeout。

---

## 四、🟠 代码质量问题

### 4.1 存储工具放在 errors.py（职责错位）

`read_json`/`write_atomic`/`now_ms` 定义在 `core/errors.py`（行21-48）。这些是存储/时间工具，不是错误处理。蓝图本意是集中到 storage.py。canvas/manager、assets/library、orchestrator 都从 `core.errors` 导入这些工具，依赖方向混乱（业务模块依赖错误处理模块做 IO）。

### 4.2 `now_ms()` 用 `__import__("time")` 炫技

`core/errors.py:48`：`return int(__import__("time").time() * 1000)`。应直接模块级 `import time`。无功能影响，但可读性差、不符合"代码读起来像周围代码"的原则。

### 4.3 httpx 客户端池不关闭

`core/http_client.py` 缓存了 4 个 client + 1 个 upload client，但 `main.py` lifespan 关闭时**没调用 `close_clients()`**（http_client.py:47 提供了该方法但无人调用）。进程退出时 OS 回收，但不规范，且 lifespan 里明确有 shutdown 段却没接上。

### 4.4 WebSocket 异常处理过简

`main.py:82-93`：`except Exception: pass` + `finally: disconnect`。源码区分 `WebSocketDisconnect` 和通用 Exception 并打印日志（`print(f"WS Error: {e}")`）。重构版吞掉所有异常无日志，调试困难。finally 兜底了 disconnect，功能上不致命。

### 4.5 `CanvasSaveRequest` 默认值可能误覆盖

`canvas/models.py:34-44`：`nodes: list = Field(default_factory=list)`。当前端只改 viewport 却没传 nodes 时，Pydantic 会用默认 `[]`，`save_canvas` 里 `if nodes is not None` 为 True（空 list 不是 None），**会把画布 nodes 清空**。源码 `save_canvas(canvas)` 接收整个对象，不存在此问题。需确认前端是否每次都传完整 nodes（若是则无碍，但接口语义变脆弱）。

### 4.6 `QuietAccessLogFilter` 过滤条件可能误伤

`core/logging.py:21-29`：用 `"GET /api/canvases/ " in message and "/meta" in message` 这种字符串匹配。依赖 uvicorn 日志格式，格式一变就失效。源码也是类似实现，影响不大。

### 4.7 `_canvas_locks` 字典无清理

`canvas/manager.py:29`：`_canvas_locks: dict[str, asyncio.Lock] = {}`，每个画布文件名一个锁，**永不删除**。画布多了内存增长（每个锁对象很小，但仍是泄漏）。purge_canvas 时应清理对应锁。

### 4.8 画布乐观锁重试在生成场景下可能丢节点

`orchestrator.py:202-237` `_add_image_nodes_to_canvas`：读画布 → 追加节点 → save（带乐观锁）→ 冲突则重读重试。但重试时**重新读最新 canvas 后 append 自己的节点**，如果两个生成任务并发，都可能 append 成功（顺序），但如果重试 3 次都冲突就**静默放弃**（行237 `return`），生成的图片节点没加到画布，用户无感知。源码行为待对照。

---

## 五、✅ 做得好的地方（客观记录）

1. **模块化拆分**：从 15131 行单文件拆成 55 个文件，职责边界清晰，远比源码易维护。
2. **锁全部 asyncio.Lock**：源码 10 个 threading.Lock 在 async handler 里阻塞 event loop，重构版全部改 asyncio.Lock（虽然 IO 仍同步，但锁本身不阻塞了）。
3. **JSON 原子写**：`write_atomic` 用临时文件 + `os.fsync` + `os.replace`，比源码裸写 `json.dump` 抗崩溃。
4. **资产库加锁**：`assets/library.py` 的 `_asset_lock` 修复了源码 `save_asset_library` 无锁的已知 race condition。
5. **httpx 客户端池化**：`http_client.py` 按 preset 复用连接，避免源码每次 `httpx.AsyncClient()` 新建的开销。
6. **乐观锁保留**：`save_canvas` 的 `base_updated_at` + `CanvasConflictError` → 409 机制保留了。
7. **meta 不刷 updated_at**：`update_canvas_meta` 明确不更新 `updated_at`，保留了源码"打标签不顶到最前"的设计意图。
8. **WebSocket 协议保留**：4 种消息类型 + ping/pong 格式与源码一致，`broadcast_canvas_updated` 的 `exclude_client_id` 逻辑正确。

---

## 六、待办（后续补充）

以下模块尚未逐行审查，需在额度允许时补充：

- [ ] `generation/` 全模块：orchestrator 已读，6 个 gateway + 3 个 routes 待逐行核对（火山签名 / RH 7字段 / Jimeng 7模式 / Apimart TLS / 图片提取多格式）
- [ ] `assets/routes.py` + `chat/` + `comfyui/`：已实现路由的响应格式核对、chat SSE 流式、ComfyUI scheduler 轮询、/api/generate 异步化
- [ ] `system/` 全模块：app-info 已确认不符，providers/updater/routes 待核对（normalize_provider / reload_env_globals / 更新双源 / self_restart）
- [ ] `media/` + `modelscope/` + `upload/`：download-output Range、MS timeout、上传路径、CSRF 使用情况
- [ ] 抽查源码 `generate_ai_image`（L8632-8802）6 主分支 + 6 sub 分支，对照重构 `_dispatch`
- [ ] 抽查源码 canvas-video（L11797-12341）5 协议分发 + 错误中文化

---

## 七、优先级建议（仅供参考，非修改建议）

如果后续要修复，按影响排序：

1. **P0**：补齐 78 个缺失路由（否则前端大面积 404）
2. **P0**：存储路径回退到源码布局（或写迁移脚本），否则老用户数据失效
3. **P0**：`/api/app-info` 响应格式对齐源码
4. **P1**：画布文件名改回纯 id（解决 O(N) 查找 + 重命名竞态 + meta 不一致）
5. **P1**：协议分发改回 `effective_protocol` 驱动，去掉 `pid == "modelscope"` 硬编码
6. **P1**：async 函数内的同步 IO 改 aiofiles / to_thread
7. **P2**：补 3 个 migrate 函数、self_restart、火山 V4 签名等关键功能
8. **P2**：RunningHub / Jimeng 网关完整性核对

---

*报告生成时间：2026-07-10。第一版覆盖：核心层、画布模块、路由全量对比、存储结构、orchestrator。其余模块待补。*

储存是我让这样改的，但是还是以json储存的这个是没问题，前端我也测试过来，就是目前生成图片的时候一直说拿不到id然后给我放不到对应json名文件夹的out文件夹里，只能放到外层的总储存文件，但是导入可以？这个没搞懂，昨天聊的是说点击生成的时候是可以直接进行获取当前画布id的但是好像也不行，也没给我改好。

已工作 3 分 47 秒

前端**确实传了** `canvas_id: canvas?.id || ''`（行9972、10068）。注意是 `canvas?.id || ''`——如果 `canvas` 对象存在但 `id` 为空，或 `canvas` 为 undefined，就传空字符串 `''`。

关键来了：后端 `OnlineImageRequest.canvas_id: Optional[str] = None`。前端传的是 `''`（空串），Pydantic v2 对 `Optional[str]` 字段收到空串 `''`，**会保留为 `''` 而非 None**。那 orchestrator 里 `if canvas_id:` 对空串为 False，走"无 canvas_id"分支，图片就只存到外层。

但等等——这还不能完全解释。我需要确认 `canvas.id` 到底有没有值。让我看 `canvas` 这个变量怎么来的，以及 `createCanvasImageTask` 怎么发请求。关键是看 `canvas?.id` 在生成那一刻是不是空的。

让我读 `createCanvasImageTask` 函数和 `canvas` 变量的定义。

探索

·1 搜索, 1 文件

继续读

已工作 3 分 59 秒

OK，如果 URL 是远程 http URL，下载成功后 content 不为 None，走行170之后：

行171 `filename = f"gen_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}{ext}"` 行173-175：`OUTPUT_DIR.mkdir` → `write_bytes` 行178-186：`canvas_id` 有值 → `canvas_output_dir(canvas_id)` → mkdir → write_bytes → return `/cfiles/...`

**这一路走完如果没异常，应该是 OK 的**。如果静默失败，那只有一种可能：`canvas_output_dir(canvas_id)` 里 `_find_canvas_file` 在运行时找不到画布。

让我看一个关键时间点——`canvas_output_dir` 和 `load_canvas` 用的是同一个 `_find_canvas_file`。如果 `_add_image_nodes_to_canvas` 行208的 `load_canvas` 也能找到画布（否则它会 return，但图片已经显示了），那说明在 `_download_or_keep` 执行时画布确实存在。

**等等！我漏看了一个关键问题。** `_download_or_keep` 的行 178 判断是 `if canvas_id:`。如果 canvas_id 是**空字符串 `''`**，`bool('')` 是 `False`，就会跳过 per-canvas 写入。

前端传的是 `canvas_id: canvas?.id || ''`——如果 `canvas.id` 有值就传它，**没有就传空串 `''`**。Pydantic `Optional[str]` 收到 `''` 后保持为 `''`（不是 None）。`orchestrator.py` 行42 `canvas_id: str | None = None`，前端传的 `''` 不是 None。

然后 orchestrator.py 行58：`if canvas_id and local_urls:` — **空串 `''` 是 falsy，跳过**。行178也同理。

**所以根因就是：前端传了 `''` 而不是 `None` 或真实 id，导致后端把空串当"无 canvas_id"处理。**

但前端 `canvas?.id || ''` 在 canvas 对象存在时有 id……除非调用生成的时候 canvas 对象还没初始化。让我看看是否有"在 canvas 页面加载完之前就触发生成"的情况——比如 `resumeCanvasImageTasks`（行1933）。
