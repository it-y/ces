# Errors

Command failures and integration errors.

---

## [ERR-20260713-001] PowerShell text rewrite encoding corruption

**Logged**: 2026-07-13T14:22:00+08:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
Using PowerShell Get-Content/Set-Content to rewrite a UTF-8 Python source file corrupted Chinese string literals and caused a SyntaxError.

### Error
`SyntaxError: unterminated string literal` in `app/canvas/manager.py`.

### Context
A small replacement was performed through PowerShell text decoding. The original file contains UTF-8 Chinese strings and the shell decoded/re-encoded it incorrectly.

### Suggested Fix
For source rewrites, use Python `Path.read_text(encoding="utf-8")` and `write_text(encoding="utf-8")`; restore from the verified workspace backup before reapplying changes.

### Metadata
- Reproducible: yes
- Related Files: app/canvas/manager.py

---
## [ERR-20260713-002] invalid import in new canvas module

**Logged**: 2026-07-13T14:27:00+08:00
**Priority**: low
**Status**: resolved
**Area**: backend

### Summary
A malformed conditional expression was accidentally left in an import statement while creating `canvas/import_export.py`.

### Error
`SyntaxError: invalid syntax` at the `core.paths` import.

### Suggested Fix
Keep imports explicit and run `py_compile` immediately after creating each module.

---
## [ERR-20260713-003] route section replacement anchor mismatch

**Logged**: 2026-07-13T14:31:00+08:00
**Priority**: low
**Status**: resolved
**Area**: backend

### Summary
A multi-section source rewrite used an overly exact newline/comment anchor and stopped before writing the route file.

### Error
`ValueError: substring not found`.

### Suggested Fix
Locate sections by decorator/function anchors independently and assert each index before writing.

---

## [ERR-20260713-02] PowerShell pipeline corrupted UTF-8 source

**Logged**: 2026-07-13
**Status**: resolved
**Area**: source editing

Used a PowerShell here-string piped to `python -` to rewrite Python files containing Chinese text. Windows pipeline encoding replaced non-ASCII literals with `?`, corrupting user-facing messages. Use Node REPL filesystem APIs or a UTF-8 script file/base64 payload for existing UTF-8 source; never pipe Unicode source through Windows PowerShell stdin.

## 2026-07-13：本地诊断脚本对 providers.json 顶层结构判断错误

- **任务**：读取 `data/config/providers.json`，对自定义中转站执行无计费的 `/v1/models` 稳定性探测。
- **错误**：脚本先调用 `cfg.get(...)`，但当前配置文件顶层实际是数组，触发 `AttributeError: 'list' object has no attribute 'get'`。
- **原因**：虽然前一个脚本兼容了字典/数组两种结构，新脚本没有复用该判断。
- **改进**：读取配置后先用 `isinstance(cfg, dict)`/`isinstance(cfg, list)` 分支标准化，再访问供应商列表；本地含中文绝对路径的 Python heredoc 应优先使用工作目录相对路径，避免 Windows stdin 编码导致路径变成问号。
- **状态**：已定位，待用兼容脚本重试。
