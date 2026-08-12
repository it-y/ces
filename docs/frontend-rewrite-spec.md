# Infinite Canvas 前端重写规范

> 本文档是给 AI 的提示词/规范，用它来指导前端重写。
> 后端 Python FastAPI 已稳定，**后端不改，只重写前端**。

---

## 一、项目概况

Infinite Canvas 是一个 AI 绘画工作流管理工具，核心功能：

- **无限画布**：节点式可视化编程，拖拽图片/提示词/生成器节点，连线构建 AI 工作流
- **智能画布**：自由画布 + 顶部对话式生成面板，prompt 驱动生图/视频
- **素材库**：图片/视频/工作流的分类管理
- **6 种 AI 协议分发**：ModelScope → 即梦 → RunningHub → Gemini → 火山引擎 → OpenAI
- **桌面应用**：Electron 打包，内嵌 Chromium

**当前前端现状**：
- 62,000 行纯手写 JS/CSS/HTML
- 零框架、零模块化、零测试
- canvas.js（14,681 行）和 smart-canvas.js（16,540 行）是两个独立实现
- 所有变量全局挂载，函数互相牵扯

---

## 二、技术选型建议

### 推荐方案：React + React Flow (xyflow)

| 需求 | React Flow 能力 | 节省代码量 |
|------|----------------|-----------|
| 无限画布（zoom/pan） | 内置，自动 GPU 加速 | ~2000 行 |
| 节点拖拽 | 内置，含吸附/对齐 | ~3000 行 |
| SVG 连线（贝塞尔曲线） | 内置，自动路由 | ~2000 行 |
| 小地图 | 内置 MiniMap 组件 | ~500 行 |
| 选中/多选 | 内置 | ~500 行 |
| 撤销/重做 | 配合 zustand 轻松实现 | ~1000 行 |

**其他推荐库**：
- **zustand** — 轻量状态管理，替代全局变量
- **@tanstack/react-query** — API 请求缓存，替代手动 fetch
- **tailwindcss** — 已有 CDN 依赖，继续用
- **lucide-react** — 已有 lucide 图标，换 React 版本

**不需要的**：
- 不需要 Next.js（纯 SPA，Electron 内嵌）
- 不需要 TypeScript（可选，但建议至少 JS + JSDoc）

### 构建工具

Vite — 快、简单、和 Electron 兼容好。

---

## 三、后端 API 完整参考

> 后端 155 个路由，以下按模块分列。**路由路径、参数名、响应格式必须严格一致。**

### 3.1 画布管理 (canvas)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/canvases` | 列出画布 `?trash=false` |
| GET | `/api/canvases/trash` | 回收站列表 |
| POST | `/api/canvases` | 创建画布 `{title, icon, kind, project, board_x, board_y}` |
| POST | `/api/canvases/import` | 导入画布（multipart file） |
| GET | `/api/canvases/{id}` | 获取画布详情 `{canvas: {id, title, nodes, connections, viewport, logs, settings, updated_at}}` |
| PUT | `/api/canvases/{id}` | 保存画布 `{title?, nodes?, connections?, viewport?, logs?, settings?, client_id?, base_updated_at?}` → 409 表示冲突（乐观锁） |
| GET | `/api/canvases/{id}/meta` | 获取元数据 |
| POST | `/api/canvases/{id}/meta` | 更新元数据 `{title?, icon?, owner?, color?, pinned?, project?, board_x?, board_y?}` |
| DELETE | `/api/canvases/{id}` | 软删除 |
| POST | `/api/canvases/{id}/restore` | 恢复 |
| DELETE | `/api/canvases/{id}/purge` | 永久删除 |
| POST | `/api/canvases/{id}/touch` | 更新时间戳 |
| GET | `/api/canvases/{id}/assets` | 画布关联素材 |

### 3.2 项目 (projects)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/projects` | 列出项目 |
| POST | `/api/projects` | 创建 `{name}` |
| POST | `/api/projects/{id}` | 更新 `{name?, order?}` |
| DELETE | `/api/projects/{id}` | 删除 |

### 3.3 AI 生成 (generation)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/online-image` | 文生图 `{prompt, provider_id, model, size, n, reference_images[], canvas_id?, client_id?}` → `{images: [urls], type, timestamp}` |
| POST | `/api/canvas-image-tasks` | 创建图片任务（同上参数）→ `{task_id, status}` |
| GET | `/api/canvas-image-tasks/{task_id}` | 查询任务 `{id, status, result?, error?}` |
| POST | `/api/canvas-video` | 文生视频 `{prompt, provider_id, model, duration, aspect_ratio, images[], videos[], audios[], ...}` |
| POST | `/api/canvas-llm` | LLM 对话 `{message, system_prompt, model, messages[], images[], videos[], canvas_id?}` → `{content, model}` |
| POST | `/api/canvas-comfy-tasks` | ComfyUI 生成 `{prompt, width, height, workflow_json, params, client_id?}` → `{task_id}` |
| GET | `/api/canvas-comfy-tasks/{task_id}` | 查询 ComfyUI 任务 |
| POST | `/api/generate` | ComfyUI 生成（旧路径） |
| POST | `/api/ai/generate` | OpenAI 兼容生成 |
| POST | `/api/image-task-query` | 查询第三方任务 `{provider_id, task_id}` |

### 3.4 ModelScope

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/ms/generate` | ModelScope 生图 `{api_key?, model, prompt, loras?, image_urls?, width?, height?}` → `{images, type, timestamp}` |
| POST | `/api/angle/generate` | 角度生图（同上，默认 Qwen-Image-Edit） |

### 3.5 即梦 (Jimeng)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/jimeng/status` | CLI 状态 `{status, version?, hint?}` |
| GET | `/api/jimeng/credit` | 积分余额 |
| POST | `/api/jimeng/login` | 登录 |
| GET | `/api/jimeng/login/text` | 获取登录文本/二维码 |
| POST | `/api/jimeng/query-media` | 查询生成结果 `{submit_id, kind}` |
| POST | `/api/jimeng/help` | CLI 帮助 |
| POST | `/api/jimeng/logout` | 注销 |
| GET | `/api/jimeng/version` | 版本 |
| POST | `/api/jimeng/login/start` | 启动登录 |
| GET | `/api/jimeng/login/status` | 登录状态 |

### 3.6 RunningHub

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/runninghub/workflows` | 工作流列表 |
| GET | `/api/runninghub/workflows/{id}` | 工作流详情 |
| POST | `/api/runninghub/workflows/submit` | 提交工作流 |
| POST | `/api/runninghub/apps/submit` | 提交应用 |
| POST | `/api/runninghub/task/query` | 查询任务 |
| GET | `/api/runninghub/models` | 模型列表 |
| POST | `/api/runninghub/upload-asset` | 上传素材 |
| GET | `/api/runninghub/app-info` | 应用详情 `?webappId=` |
| GET | `/api/runninghub/workflow-info` | 工作流信息 `?workflowId=` |
| GET | `/api/runninghub/query` | 查询 `?taskId=` |

### 3.7 ComfyUI

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/workflows` | 工作流列表 |
| POST | `/api/workflows` | 保存工作流 `{name, workflow}` |
| GET | `/api/workflows/{name}` | 获取工作流 |
| PUT | `/api/workflows/{name}/config` | 配置 |
| DELETE | `/api/workflows/{name}` | 删除 |
| POST | `/api/workflows/run` | 运行 `{workflow_id, params, client_id?}` |
| GET/PUT | `/api/comfyui/instances` | 实例管理 |

### 3.8 素材库 (asset-library)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/asset-library` | 获取完整素材库 |
| POST/DELETE | `/api/asset-library/libraries[/{id}]` | 库 CRUD |
| POST/PATCH/DELETE | `/api/asset-library/categories[/{id}]` | 分类 CRUD |
| POST | `/api/asset-library/items/upload` | 上传素材（multipart） |
| GET | `/api/asset-library/items/{id}/content` | 文件内容 |
| GET | `/api/asset-library/items/{id}/resolve` | 解析素材 URL |
| POST/PATCH/DELETE | `/api/asset-library/items[/{id}]` | 素材 CRUD |
| POST | `/api/asset-library/items/batch` | 批量导入 |
| POST | `/api/asset-library/items/delete` | 批量删除 `{ids}` |
| POST | `/api/asset-library/items/move` | 移动 `{ids, target_category_id}` |
| POST | `/api/asset-library/items/classify` | AI 分类 |
| POST | `/api/asset-library/workflows/upload` | 上传工作流 |
| GET | `/api/asset-library/workflows/{id}/content` | 工作流内容 |
| POST | `/api/asset-library/workflows/{id}/install` | 安装工作流 |

### 3.9 提示词库 (prompt-libraries)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/prompt-libraries` | 获取提示词库 |
| POST/PATCH/DELETE | `/api/prompt-libraries[/{id}]` | 库 CRUD |
| POST/PATCH/DELETE | `/api/prompt-libraries/categories[/{id}]` | 分类 CRUD |
| POST/PATCH/DELETE | `/api/prompt-libraries/items[/{id}]` | 条目 CRUD |
| POST | `/api/prompt-libraries/items/delete` | 批量删除 |
| GET | `/api/smart-canvas/prompt-templates` | 智能画布模板 |

### 3.10 上传/本地素材

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/upload` | 上传文件（multipart）→ `{url, filename, size}` |
| POST | `/api/ai/upload` | 批量上传 `{files}` → `{files: [{url, name, kind, mime}]}` |
| POST | `/api/ai/import-local-image` | 导入本地图片 |
| POST | `/api/ai/upload-base64` | base64 上传 |
| GET | `/api/local-assets` | 本地素材列表 |
| GET | `/api/local-assets/files/{path}` | 文件内容 |
| POST | `/api/local-assets/upload` | 上传到本地素材 |
| POST | `/api/local-assets/delete` | 批量删除 |
| PATCH | `/api/local-assets/items` | 重命名 |
| POST | `/api/local-assets/folders` | 创建文件夹 |
| POST | `/api/local-assets/caption` | 标注管理 |
| POST | `/api/local-assets/classify` | AI 分类 |
| POST | `/api/local-assets/move` | 移动 |
| POST | `/api/local-assets/import-urls` | 从 URL 导入 |

### 3.11 媒体工具

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/media-preview` | 缩略图生成 `?w=480&url=` → JPEG，有磁盘缓存 |
| GET | `/api/image-jpeg` | 转 JPEG |
| GET | `/api/download-output` | 下载输出文件 |
| GET | `/api/view` | 预览文件 |

### 3.12 系统/配置

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/PUT | `/api/providers` | 供应商配置 |
| POST | `/api/providers/test-connection` | 测试连接 |
| POST | `/api/providers/fetch-models` | 拉取模型列表 |
| GET | `/api/config` | 获取配置 |
| GET | `/api/config/token` | Token 状态 |
| GET/POST | `/api/settings/github-token` | GitHub Token |
| GET | `/api/check-update` | 检查更新 |
| POST | `/api/update-from-github` | 执行更新 |
| GET/POST | `/api/queue_status` | 队列状态 |
| GET | `/api/history` | 历史记录 |
| POST | `/api/history/delete` | 删除历史 |
| GET/POST/DELETE | `/api/shared-folders[/{id}]` | 共享文件夹管理 |

### 3.13 静态文件挂载

| 路径前缀 | 物理目录 |
|---------|---------|
| `/static/*` | `static/` |
| `/output/*` | `data/outputs/` |
| `/assets/*` | `data/uploads/` |
| `/cfiles/*` | `data/canvas-files/` |

---

## 四、WebSocket 协议

**连接地址**：`ws://{host}/ws/stats?client_id={clientId}`

### 4.1 客户端 → 服务器

```
发送文本: "ping"    （心跳保活）
```

### 4.2 服务器 → 客户端

**`stats`** — 在线人数
```json
{"type": "stats", "online_count": 3}
```

**`new_image`** — 生成完成通知
```json
{
  "type": "new_image",
  "data": {
    "images": ["/cfiles/.../gen_xxx.png"],
    "prompt": "a cat",
    "model": "gpt-image-2",
    "canvas_id": "abc123"
  }
}
```

**`canvas_updated`** — 画布被其他客户端修改
```json
{
  "type": "canvas_updated",
  "canvas_id": "abc123",
  "updated_at": 1722928800000,
  "client_id": "canvas_xyz"
}
```
→ 收到后判断 `client_id !== 自己的ID` 且 `updated_at > 本地时间` → 拉取最新画布

**`asset_library_updated`** — 素材库有变动
```json
{"type": "asset_library_updated", "updated_at": 1722928800000}
```
→ 刷新素材库列表

**`pong`** — 心跳回复
```json
{"type": "pong"}
```

### 4.3 客户端 ID 生成

```js
const clientId = `canvas_${Math.random().toString(36).slice(2, 10)}`;
// smart-canvas 用: `canvas_smart_${random8}${timestamp}`
```

---

## 五、跨上下文通信

除了 WebSocket，还需要通过以下方式同步状态：

1. **BroadcastChannel** (`studio-api`) — 同浏览器不同标签页间通信
   - 转发：`canvas_updated`, `asset_library_updated`, `providers-changed`

2. **window.postMessage** — iframe 内外通信
   - 转发：`canvas_updated`, `asset_library_updated`, `studio-theme`, `studio-lang`

---

## 六、关键业务逻辑（必须实现）

### 6.1 乐观锁

保存画布时必须传 `base_updated_at`：
```js
// 获取时记录 updated_at
const canvas = await fetchCanvas(id);
localUpdatedAt = canvas.updated_at;

// 保存时带上
const resp = await fetch(`/api/canvases/${id}`, {
  method: 'PUT',
  body: JSON.stringify({ nodes, connections, base_updated_at: localUpdatedAt })
});
if (resp.status === 409) {
  // 冲突 → 重新拉取 → 合并 → 再保存
}
```

### 6.2 6 种 AI 协议分发顺序

```
ModelScope → Jimeng → RunningHub → Gemini → Volcengine → OpenAI
```
按这个顺序匹配 providers 配置，找到第一个可用且支持请求模型的供应商。

### 6.3 节点类型

经典画布节点：`image`, `prompt`, `loop`, `llm`, `generator`, `comfy`, `ltxDirector`, `rh`, `msgen`, `video`, `output`, `group`, `promptGroup`

智能画布节点：`image`, `prompt`, `loop`, `group`

---

## 七、分阶段实施计划

### Phase 1：基础设施（建议先做）
- Vite + React 项目初始化
- React Flow 集成，实现基础画布（zoom/pan + 空白节点）
- API 调用层封装（fetch + react-query）
- WebSocket 连接管理
- 主题系统（亮/暗模式）
- i18n 框架（中/英）
- Electron 集成（保持和原版一样）

### Phase 2：经典画布核心
- 节点系统：image, prompt, loop 节点
- 连线系统：端口 + SVG 边
- 画布列表页
- 画布保存/加载（含乐观锁）
- 跨标签页同步（BroadcastChannel + WS）

### Phase 3：生成集成
- AI 图片生成（online-image）
- ModelScope 生成
- ComfyUI 集成
- RunningHub 集成
- 视频生成
- LLM 对话

### Phase 4：素材库
- 素材库列表/浏览
- 素材上传
- 分类管理
- 提示词库

### Phase 5：高级功能
- 图片编辑器（裁剪/遮罩/画笔）
- 工作流导入/导出
- 撤销/重做
- 小地图
- 键盘快捷键
- 缩略图预览缓存

### Phase 6：智能画布
- 独立画布 + 对话面板
- prompt 驱动生成
- 全景查看器
- 比较模式

---

## 八、重要约束

1. **API 路径不能改** — 后端 155 个路由全部保持原样
2. **响应格式不能改** — JSON 字段名必须和现有 API 一致
3. **不要改后端** — 只改 `static/` 目录下的前端文件
4. **Electron 兼容** — 所有请求用相对路径，不要硬编码 localhost
5. **保持两套画布** — 经典画布和智能画布是两个独立页面
6. **GPU 性能** — 记得加 `will-change: transform`、`content-visibility: auto`
7. **缩略图尺寸** — 画布内 480px，点开展示原图
8. **拖拽 rAF 节流** — 画布拖拽用 requestAnimationFrame，不要每次 mousemove 都更新

---

## 九、当前前端文件对照表

| 原文件 | 行数 | 对应功能 | 重写为 |
|--------|------|---------|--------|
| `js/canvas.js` | 14,681 | 经典画布 | `src/canvas/` |
| `js/smart-canvas.js` | 16,540 | 智能画布 | `src/smart-canvas/` |
| `js/asset-manager.js` | 4,511 | 素材库 | `src/asset-manager/` |
| `js/ltx-director-timeline.js` | 4,111 | 时间线 | `src/timeline/` |
| `js/api-settings.js` | 3,464 | API 设置 | `src/settings/` |
| `js/comfyui-settings.js` | 1,417 | ComfyUI 设置 | `src/comfyui-settings/` |
| `js/canvas-list.js` | 1,121 | 画布列表 | `src/canvas-list/` |
| `js/theme.js` | 191 | 主题 | `src/theme/` |
| `js/i18n.js` + `i18n/` | ~1,100 | 国际化 | `src/i18n/` |
| `css/*.css` | ~10,900 | 样式 | Tailwind + CSS modules |

---

## 十、使用指南

复制本文档 + 后端 API 代码（`app/` 目录）给 AI，分 Phase 逐个实现。

每个 Phase 的 prompt 模板：

```
你是一个前端工程师。请根据以下规范实现 Infinite Canvas 的 Phase N。

[粘贴本文档对应 Phase 的章节 + API 参考]

要求：
1. 使用 React + React Flow + zustand + @tanstack/react-query + tailwindcss
2. 构建工具用 Vite
3. API 路径和响应格式严格对照规范，不能改
4. 只写前端代码，不碰后端
5. 输出 src/ 目录下的文件结构和完整代码
```
