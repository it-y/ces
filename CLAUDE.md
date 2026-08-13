# Infinite Canvas 重构规范

> 本文件是 Claude Code 在 `image/` 目录下编写代码时必须遵守的规则。

---

## 一、架构文档

**编写任何代码前，必须先看架构设计文档**：

`E:\项目\项目\idea\Infinite-Canvas-main\.codebase-memory\architecture-design.md`

目录结构、模块职责、存储格式、数据流都在那里面。一切以架构文档为准。

---

## 二、原则

### 2.1 禁止照抄

**绝对不能从原 `main.py` 直接复制粘贴代码。**

正确做法：
1. 先看懂原代码在做什么（功能目的、输入输出、边界情况）
2. 理解为什么要那样设计（设计意图）
3. 用新的、干净的代码实现同样的行为

可以借鉴原代码里的**逻辑和算法**（比如火山 V4 签名的 HMAC 链、即梦 7 种视频模式的参数映射），但不能复制粘贴函数体。

### 2.2 理解优先

遇到不懂的原代码逻辑，先去 `.codebase-memory/refactor-plan.md` 里查对应的分析。那个文档已经把 15,131 行的每个函数、每个锁、每个路由都梳理过了。

### 2.3 按功能模块写，不按技术层写

```
✅ app/canvas/manager.py    ← 画布的所有业务逻辑在这里
✅ app/generation/gateways/openai.py  ← OpenAI 协议适配在这里

❌ app/services/canvas_service.py     ← 不要这种横切的服务层
❌ app/storage/json_store.py          ← 不要通用的存储抽象层
```

### 2.4 简单优于抽象

- 不需要 ABC 抽象基类（只有一个实现的接口是过度设计）
- 不需要 `ServiceResult` 包装层
- 不需要 `app.state` 依赖注入
- 模块级单例够用：`manager = ConnectionManager()`

---

## 三、代码规范

### 3.1 文件组织

每个功能模块内部结构：
```
功能名/
├── routes.py      ← API 路由（薄层，参数校验 → 调 manager → 返回）
├── models.py      ← Pydantic 模型
└── manager.py     ← 业务逻辑（如果逻辑少可以和 routes 合并）
```

### 3.2 路由函数

```python
# 好的写法
@router.get("/api/canvases")
async def list_canvases():
    return await get_canvas_list()

# 坏的写法
@router.get("/api/canvases")
async def list_canvases():
    # 50 行文件读取 + 排序 + 过滤逻辑全写在这里
    ...
```

### 3.3 并发

- 所有锁用 `asyncio.Lock`
- 锁定义在所属模块的 manager 文件顶部
- JSON 写入用 `tempfile + os.replace()` 原子写
- 同步 IO/CPU 密集操作用 `asyncio.to_thread()` 包装

### 3.4 命名

- 文件名：小写下划线（`routes_runninghub.py`）
- 函数名：动词开头（`save_canvas` `list_canvases` `generate_image`）
- 变量名：描述性（`canvas_id` 不是 `cid`）

### 3.5 导入

```python
# app 内部导入用相对路径
from .manager import save_canvas
from ..core.websocket import manager
from ..config import DATA_DIR
```

---

## 四、与原项目的关系

### 必须保持一致的东西

1. **API 路径和响应格式** — 前端零改动，所有路由的 URL、方法、请求体、响应体必须和原来一样
2. **WebSocket 消息格式** — `stats` `new_image` `canvas_updated` `asset_library_updated` 四种类型，字段名不能改
3. **画布乐观锁机制** — `base_updated_at` + 409
4. **6 种 AI 协议分发顺序** — ModelScope → Jimeng → RunningHub → Gemini → Volcengine → OpenAI
5. **文件可携特性** — 画布 JSON + 资源文件夹拷贝迁移

### 可以改进的东西

- 同步阻塞改异步（`threading.Lock` → `asyncio.Lock`、`requests` → `httpx`）
- 文件写入加原子写
- 无锁的文件加锁
- 错误处理更规范
- 代码结构更清晰

---

## 五、原项目参考文件

| 文件 | 用途 |
|------|------|
| `../main.py` | 原版完整代码，需要理解某个功能的原始实现时查阅 |
| `../.codebase-memory/refactor-plan.md` | 原代码的逐行分析，查函数行号、锁位置、路由分布 |
| `../.codebase-memory/refactor-blueprint.md` | 原重构蓝图，有详细的模块边界和各功能细节 |
| `../.codebase-memory/architecture-design.md` | **新架构设计文档，本文档的上级参考** |
| `../static/` | 前端文件，直接迁移过来，不动 |

---

## 六、版本号规则（必须遵守）

**格式**：`YYYY.MM.DD.N`

- `YYYY.MM.DD` = **当天的日期**（不是旧日期）
- `N` = 今天第几次推送更新（从 1 开始，每次 push 前 +1）

**更新范围**：
- `VERSION` 文件
- `static/*.html` 中所有 `?v=YYYY.MM.DD.N` 引用

> `upload/` 目录已废弃（Electron 桌面打包目录，已脱节不维护），**不要**再同步它的版本号或代码。

**每次 push 代码前，必须先改版本号。不推送不带版本号的代码。**

---

## 七、开始写代码前检查

- [ ] 看过架构设计文档里的目录结构了吗？
- [ ] 知道这个功能属于哪个模块了吗？
- [ ] 看过原代码的实现逻辑和设计意图了吗？
- [ ] 确认了 API 路径和响应格式不变了吗？
- [ ] 用了 `asyncio.Lock` 而不是 `threading.Lock` 吗？
- [ ] 没有复制粘贴原代码吗？
- [ ] 版本号更新为今天的日期了吗？（§六）
