# Infinite Canvas 全量整改交接文档

更新时间：2026-07-13

项目目录：E:\项目\项目\inf\image

## 1. 总体状态

| 项目 | 当前状态 |
|---|---|
| 架构文档与重构资料 | 已阅读并按其约束执行 |
| 初始测试基线 | 28 passed |
| 完成路径与导入安全后 | 48 passed |
| 完成 ComfyUI 修复后最后一次全绿 | 54 passed |
| 当前测试状态 | 55 passed, 3 failed，共 58 项 |
| Python 编译检查 | 当前通过 |
| Git 状态 | 当前目录不是 Git 仓库，无 commit/worktree |
| 全量整改 | 尚未完成 |

当前 3 个失败测试是画布下一批修复的 TDD 红灯测试，不是随机故障：

1. 画布列表应使用 .index.json 元数据索引。
2. append_canvas_nodes 应在并发下原子追加节点。
3. 标题重命名后应重写 /cfiles/ 资源 URL。

## 2. 已完成的整改

| 模块 | 完成内容 | 主要文件 | 测试 |
|---|---|---|---|
| 测试基础 | 新增 pytest.ini，仅收集 tests，修复导入路径 | pytest.ini | pytest 与 python -m pytest 可统一收集 |
| 路径安全 | safe_path_join、ZIP 成员校验、简单文件名校验、本地媒体 URL 解析 | app/core/paths.py、app/core/security.py | tests/test_paths.py，14 项 |
| 画布导入导出 | JSON/ZIP 导入、ZIP bomb 限制、成员路径校验、资产与工作流打包解析、同步 ZIP 操作转线程 | app/canvas/import_export.py、app/canvas/routes.py、app/canvas/models.py | tests/test_canvas_import_export.py |
| 画布导入事务基础 | 新 ID、临时资源目录、资源提交失败清理、URL 重写 | app/canvas/manager.py | 已有导入测试通过 |
| 本地安全边界 | CORS 限定 localhost/127.0.0.1；写 API 增加同源校验；无 Origin 的本地脚本保持兼容 | app/main.py | tests/test_local_security_boundary.py |
| ComfyUI | 工作流名称防穿越、JSON 校验、原子保存、路由同步 IO 转线程、命名工作流先加载再提交、并发探测后端、全不可用报错、实例负载同步、真实队列快照 | app/comfyui/scheduler.py、app/comfyui/routes.py、app/core/io.py | tests/test_comfyui_scheduler.py，6 项 |
| 审查与计划 | 全量整改设计和实施计划 | docs/superpowers/specs/2026-07-13-infinite-canvas-full-remediation-design.md；docs/superpowers/plans/2026-07-13-infinite-canvas-full-remediation.md | 已确认 |

## 3. 当前工作区特别注意事项

### 3.1 必须先修复的 UTF-8 文本问题

一次使用 PowerShell here-string 管道到 python - 的操作把非 ASCII 字符替换成问号。

已修复：

- app/comfyui/scheduler.py
- app/core/io.py

尚未修复：

- app/comfyui/routes.py

该文件目前能编译，测试也可能通过，但中文错误消息已变成问号。接手后第一步必须用 Node REPL 文件 API、apply_patch，或真正 UTF-8 的脚本文件重写；不要再用 PowerShell Get-Content/Set-Content 或 Unicode here-string 管道改写中文源码。

错误已记录在 .learnings/ERRORS.md。

### 3.2 画布测试处于预期 RED 状态

新增测试位于 tests/test_canvas_storage.py，当前失败：

| 测试 | 预期实现 |
|---|---|
| test_canvas_list_uses_metadata_index_after_create | 创建/保存/删除/恢复时维护 CANVAS_DIR/.index.json 与回收站索引；列表只读索引 |
| test_append_canvas_nodes_is_atomic_under_concurrency | 在 canvas_id 对应 asyncio.Lock 内读、追加、原子写 |
| test_title_rename_rewrites_canvas_file_urls | 资源目录重命名时递归重写 /cfiles/{旧目录}/ 为新目录 |

重命名失败回滚测试当前已通过，但实现仍是旧逻辑，原因是注入的失败点没有完全覆盖旧流程；完成事务实现后应重新核对测试是否真正验证资源目录、旧 JSON、新 JSON 三者回滚。

## 4. 下一步建议顺序

| 顺序 | 模块 | 具体任务 | 完成标准 |
|---:|---|---|---|
| 1 | ComfyUI | 恢复 app/comfyui/routes.py 中文文本，重新跑 ComfyUI 测试 | 无问号损坏；相关测试全绿 |
| 2 | 画布 | 实现活动/回收站元数据索引、索引重建、项目计数读索引 | 列表热路径不读完整画布 JSON |
| 3 | 画布 | 实现 append_canvas_nodes；生成模块改用它 | 并发追加不丢节点 |
| 4 | 画布 | 标题重命名事务：资源移动、写新 JSON、删旧 JSON、失败逆序回滚；重写 /cfiles/ URL | JSON、资源目录、URL 始终一致 |
| 5 | 模块边界 | 新建 app/canvas/context.py，迁移 last_opened/client binding；generation/modelscope/canvas 不再反向导入 upload.routes | 消除跨模块反向依赖 |
| 6 | 上传/媒体 | 上传流式限额、Base64 严格校验、本地导入白名单、远程 URL SSRF 与逐跳重定向校验、PIL/媒体上限 | 路径、内存、SSRF 测试通过 |
| 7 | 生成 | 任务绝对路径、lifespan 加载、原子持久化、中断状态、TTL/数量清理、日志脱敏、正确分发顺序 | 重启后无永久 running；协议顺序测试通过 |
| 8 | 资产/JSON | 损坏 JSON 隔离、Prompt 稳定 ID、资产增量索引、批量一次锁一次写、热路径异步化 | 无锁内同步 IO；损坏文件可诊断 |
| 9 | WebSocket/系统 | 连接状态锁、广播超时、真实队列、真实探测、无实现功能返回 501 | 不再返回假成功/假队列 |
| 10 | 更新/回滚 | 完整下载、staging 校验、排除 data、备份 manifest、原子安装、失败回滚、合法 backup ID、真实重启 | 更新/回滚事务测试通过 |
| 11 | 最终验证 | compileall、pytest、python -m pytest、同步 IO 与路径审计 | 两种 pytest 结果一致且全绿 |

## 5. 尚未完成的高优先级问题

### P0/P1 安全与数据一致性

- app/generation/gateways/openai.py 本地 URL 仍可路径穿越读取。
- orchestrator、OpenAI、Gemini、upload.import_urls 存在 SSRF 与无界下载。
- 多个上传端点先完整读入内存，缺少单文件、文件数、总量限制。
- 本地图片导入未限制到注册共享目录。
- app/config.py 的 LOCAL_IMAGE_IMPORT_EXTS 与 Path.suffix 比较格式不一致。
- 生成下载、历史保存、画布追加失败可能被静默吞掉，任务仍显示成功。
- AI 分发顺序当前是 Gemini 与 Volcengine 颠倒。
- Gemini API Key 放在查询参数，可能进入日志。

### P0 系统更新

- api_update 只下载到 staging，没有安装、备份或重启。
- api_rollback 没有恢复 BASE_DIR，却可能返回成功。
- GitHub/ModelScope 文件列表只取前 50 个，失败被吞掉。
- 更新路径未严格限制，必要文件未校验。
- _update_lock 未使用，更新和回滚可并发。
- 重启逻辑找不到当前 run.bat/uvicorn 入口。
- 更新、备份、回滚响应格式与前端契约不一致。

### 系统假端点

- /api/queue-status 固定返回空数据，且字段与前端 total/position 不一致。
- /api/update-connectivity 方法与前端不一致，probe 永远返回成功。
- /api/providers/probe-async 永远返回 queued/ok，没有真实探测。
- 普通 provider test 对六种协议统一请求 /v1/models，协议不正确。

## 6. 更新/回滚实现要点

接手者应将业务放在 app/system/updater.py，routes.py 保持薄层：

1. 更新与回滚共用一个 asyncio.Lock；占用时返回 409。
2. 下载完整清单，不允许 50 文件截断。
3. 只允许明确发布路径，永久排除 data/** 和用户配置。
4. 远端条目必须通过相对路径校验，拒绝 ..、绝对路径和盘符路径。
5. staging 必须包含 VERSION、app/main.py、static/index.html。
6. 任一文件下载失败则整批失败；fallback=true 才切换备用源。
7. 安装前创建备份与 manifest.json。
8. 文件替换采用同目录临时文件加 os.replace。
9. 中途失败按逆序恢复备份，并删除此次新增文件。
10. Windows 重启优先 run.bat；无启动器时使用 python -m uvicorn app.main:app --host 127.0.0.1 --port 3000。
11. 严格返回前端现有字段，不得假成功。

## 7. 生成、上传和媒体审查补充

- OpenAI 本地素材解析必须统一调用 resolve_local_media_url/safe_path_join。
- 远程下载统一做公网 URL 校验、流式累计限额、重定向逐跳复验。
- upload_file、upload_ai_reference、upload_base64、upload_local_assets、upload_cloud_video、import_urls 都需限额。
- PIL 打开、解码、缩放、转换、保存整个流程放入 asyncio.to_thread。
- media preview width 建议限制 16 到 2048；设置最大像素并处理解压炸弹。
- /cfiles/ 必须在预览、JPEG 转换、文件查看三个接口中一致支持。
- 有 canvas_id 时，素材落盘或画布写入失败应让任务失败，不能回退远程 URL 后报成功。
- 任务持久化路径必须基于 DATA_DIR；启动时 queued/running 转 interrupted；增加保留数量/TTL。

## 8. 当前验证命令

在项目根目录执行：

    python -m compileall -q app tests
    pytest -q
    python -m pytest -q

当前实际结果：

    compileall: passed
    pytest: 55 passed, 3 failed

失败均来自新增的画布 RED 测试。修复画布后再要求全绿。

## 9. 关键文件清单

| 类型 | 绝对路径 |
|---|---|
| 架构文档 | E:\项目\项目\idea\Infinite-Canvas-main\.codebase-memory\architecture-design.md |
| 重构分析 | E:\项目\项目\idea\Infinite-Canvas-main\.codebase-memory\refactor-plan.md |
| 重构蓝图 | E:\项目\项目\idea\Infinite-Canvas-main\.codebase-memory\refactor-blueprint.md |
| 整改设计 | E:\项目\项目\inf\image\docs\superpowers\specs\2026-07-13-infinite-canvas-full-remediation-design.md |
| 实施计划 | E:\项目\项目\inf\image\docs\superpowers\plans\2026-07-13-infinite-canvas-full-remediation.md |
| 本交接文档 | E:\项目\项目\inf\image\docs\superpowers\handoff\2026-07-13-infinite-canvas-remediation-handoff.md |

## 10. 交接结论

当前已完成安全路径、画布 ZIP 导入导出基础、本地同源边界和 ComfyUI 调度核心修复；全量整改约处于前半段之前，画布索引/事务正在 TDD 开发中。工作区可编译，但不是全绿状态，并且 app/comfyui/routes.py 存在需要立即恢复的中文问号损坏。接手时不要从“全部通过”假设开始，应先修复该文件，再完成 3 个画布 RED 测试，然后继续上传、生成、系统更新等剩余模块。
