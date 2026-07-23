# Infinite Canvas Full Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保持现有 API、前端和本地 JSON 存储兼容的前提下，修复审查确认的全部安全、功能、数据一致性、性能和维护性问题。

**Architecture:** 保留现有功能域结构，将路径与远程素材校验收口到 core，将画布导入导出、资产索引、Prompt 持久化等放回所属功能域。所有文件写入使用域内锁与原子替换，索引是可重建缓存，外部协议通过 gateway 隔离。

**Tech Stack:** Python 3.13、FastAPI、Pydantic、httpx、asyncio、pytest/anyio、本地 JSON/ZIP/PIL。

---

## 文件结构

- Create `app/core/paths.py`: 本地根目录安全拼接与本地媒体 URL 解析。
- Create `app/core/remote_assets.py`: SSRF 防护、流式受限下载和 data URI 解码。
- Create `app/canvas/import_export.py`: 画布 JSON/ZIP 导入、资产检查、下载和工作流打包。
- Create `app/canvas/index.py`: 可重建的画布元数据索引。
- Create `app/canvas/context.py`: 客户端与最近画布上下文。
- Modify `app/canvas/manager.py`: canvas_id 锁、事务重命名、原子追加节点、索引维护。
- Modify `app/assets/library.py`: 增量索引、Prompt 稳定 ID 和锁外响应构造。
- Modify `app/upload/routes.py`: 流式上传、共享目录边界、原子写。
- Modify `app/media/routes.py`: 统一素材加载、尺寸和像素上限。
- Modify `app/generation/routes.py`: 任务仓储生命周期、真实状态和 gateway 查询。
- Modify `app/generation/orchestrator.py`: 分发顺序和原子追加。
- Modify `app/generation/gateways/*.py`: 安全参考素材与鉴权。
- Modify `app/comfyui/scheduler.py`: 安全工作流文件、多实例调度和实例更新。
- Modify `app/core/websocket.py`: 并发广播和连接状态锁。
- Rewrite `app/system/updater.py`: 完整更新、备份、应用与回滚事务。
- Modify `app/system/routes.py`: 更新状态、真实队列、占位端点语义。
- Modify `app/main.py`: CORS、安全 token、lifespan 初始化与清理。
- Create/modify `tests/test_*.py`: 所有风险的回归测试。
- Create `pytest.ini`: 稳定测试收集。

### Task 1: 测试工程与安全路径基础

- [ ] 新增路径测试，覆盖绝对路径、`..`、反斜杠、驱动器路径和合法子路径。
- [ ] 运行 `python -m pytest tests/test_paths.py -q`，确认因模块不存在而失败。
- [ ] 实现 `safe_path_join()`、`resolve_local_media_url()`、ZIP 成员路径校验。
- [ ] 新增远程地址测试，覆盖 localhost、私网、保留地址、HTTP/HTTPS 和重定向目标。
- [ ] 实现 `validate_remote_url()` 与受限流式下载接口。
- [ ] 添加 `pytest.ini`，确认 `pytest -q` 只收集 `tests/`。

### Task 2: 画布导入、导出和资产契约

- [ ] 写端点测试复现 JSON/ZIP 导入 NameError、ZIP 穿越和资产下载空 ZIP。
- [ ] 将导入导出移动到 `canvas/import_export.py`，路由只调用公开函数。
- [ ] ZIP 导入预检条目、大小和压缩比，所有文件写入使用安全路径和线程执行。
- [ ] 修正 `CanvasAssetCheckItem` 的 `name` 字段及下载 filename 行为。
- [ ] 统一资产 URL 到本地文件解析，移除路由裸路径拼接。
- [ ] 运行画布导入导出测试和现有画布测试。

### Task 3: 浏览器本地安全边界

- [ ] 写 CORS 和敏感端点测试，验证非本地 Origin 被拒绝。
- [ ] 收紧 CORS 到 localhost 两种 origin。
- [ ] 将同源校验改为统一依赖；对文件写入、配置、更新、工作流和进程端点启用。
- [ ] 增加进程级随机 token 和同源 bootstrap；保留无浏览器 Origin 的本地脚本兼容策略。
- [ ] 运行路由安全测试。

### Task 4: ComfyUI 工作流和调度

- [ ] 写工作流名称穿越、命名运行、多实例全部失败和实例更新测试。
- [ ] 工作流文件名只允许纯 `.json` 文件名并原子写入。
- [ ] 命名运行先加载 dict，再提交；`submit_workflow` 只接收 dict 或明确 JSON 内容。
- [ ] 锁外并发探测后端，全部失败返回可识别错误。
- [ ] 增加 `update_instances()` 并同步负载映射。
- [ ] 运行 ComfyUI 测试。

### Task 5: 更新和回滚事务

- [ ] 使用临时目录写更新成功、下载缺失、提交失败自动回滚、非法备份 ID 测试。
- [ ] 完整下载文件清单，验证 HTTP 状态、相对路径、数量和总大小。
- [ ] 排除 `data/`、缓存、虚拟环境、版本控制目录和本地配置。
- [ ] 创建备份 manifest，使用临时文件和 `os.replace` 提交允许更新文件。
- [ ] 提交失败按日志逆序恢复；成功写 update-state。
- [ ] 回滚只接受服务端备份 ID，并执行同样事务。
- [ ] 路由调用真实 `apply_update()`/`rollback_update()`，成功后才调度重启。
- [ ] 运行更新器测试。

### Task 6: 画布事务、上下文和索引

- [ ] 写同 canvas_id 并发锁、重命名失败回滚、原子追加节点和索引重建测试。
- [ ] 锁键统一为 canvas_id，禁止路由访问私有锁。
- [ ] 实现事务重命名和 `append_canvas_nodes()`。
- [ ] 新建画布上下文模块，移除其他功能域对 `upload.routes` 的反向导入。
- [ ] 实现 `.index.json`，在创建、保存、删除、恢复、清除时增量维护。
- [ ] 列表与项目统计优先读索引；损坏时从完整 JSON 重建。
- [ ] 将回收站清理移出请求热路径。
- [ ] 运行画布并发和索引测试。

### Task 7: JSON 错误语义、资产与 Prompt

- [ ] 写损坏 JSON 不被默认值覆盖、Prompt 同名不覆盖和批量索引只提交一次测试。
- [ ] 将 JSON 不存在、损坏和 IO 错误分开处理；损坏文件保留 `.corrupt-*` 副本。
- [ ] Prompt 使用稳定 ID 文件名并迁移旧格式。
- [ ] 资产 CRUD 增量维护索引，批量操作一次加锁和写入。
- [ ] 响应拼装避免在全局锁内同步读取多个文件。
- [ ] 运行资产和配置测试。

### Task 8: 上传、媒体和远程素材

- [ ] 写扩展名、共享目录越界、Base64 超限、远程 SSRF、重定向和图片像素上限测试。
- [ ] 修正扩展名集合并将本地导入限制到已注册共享目录。
- [ ] 上传、Base64 和远程导入流式写临时文件并在过程中限额。
- [ ] 媒体代理和 OpenAI/Gemini 引用统一使用安全素材加载器。
- [ ] 限制缩略图宽度和 PIL 最大像素，CPU/文件 IO 使用 `to_thread`。
- [ ] 运行上传和媒体测试。

### Task 9: 生成任务、网关和即梦进程

- [ ] 写任务重启恢复、TTL、分发顺序、查询鉴权、节点追加失败状态和子进程超时测试。
- [ ] 任务仓储改用 `DATA_DIR`，lifespan 初始化，原子保存和状态修复。
- [ ] 修正六协议顺序，任务查询移动到 gateway 并添加鉴权。
- [ ] 生成结果使用 canvas manager 原子追加，失败写明确任务警告并广播。
- [ ] 即梦进程超时 terminate 后 kill，shutdown 清理。
- [ ] 运行生成模块测试。

### Task 10: WebSocket 和真实系统状态

- [ ] 写慢客户端、重复 client ID、批量断线和队列状态测试。
- [ ] 使用连接状态锁、快照、`gather` 和单连接超时广播。
- [ ] 重复 client ID 关闭旧连接；失败连接批量清理后只广播一次统计。
- [ ] 系统 queue_status 聚合真实任务状态；update connectivity 做真实并发探测。
- [ ] 无协议依据的占位端点返回 501，前端处理明确错误。
- [ ] 运行 WebSocket 和 system 测试。

### Task 11: 模块收口和全量验证

- [ ] 将超大路由中的剩余 ZIP、文件遍历和业务逻辑移回所属域模块。
- [ ] 提取重复 URL 深度扫描函数，清理未使用锁、导入和错误日志正文。
- [ ] 确认所有同步大文件 IO/ZIP/PIL/目录复制均不直接阻塞事件循环。
- [ ] 运行 `python -m compileall -q app tests`，预期退出码 0。
- [ ] 运行 `pytest -q`，预期全部通过。
- [ ] 运行 `python -m pytest -q`，预期与 `pytest -q` 结果一致。
- [ ] 逐项复核设计文档和审查报告，记录仍依赖真实外部服务才能验证的项目。
