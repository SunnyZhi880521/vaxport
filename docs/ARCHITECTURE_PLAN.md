# vaxport 架构升级实施规划 (v2)

## Go Bubble Tea TUI + Python FastAPI 后端

---

## 一、第一性原理回顾

在进入细节之前，先回答几个根本问题：

| 问题 | 答案 |
|------|------|
| **为什么要换？** | Textual 渲染性能差（O(n²) Markdown）、刷新闪烁、Python GIL 阻塞 UI |
| **什么不能动？** | 30+ Python 业务文件 — agent/orchestrator/tools/db/charts/anomaly/statistics/reports/prediction/signal_detection/image_analysis/web_search/documents/compliance/regulations/alerts/monitoring/concurrent_executor + config/session/memory/skills |
| **最小改动是什么？** | 新增 `api/` 包（4 个文件），新增 `tui/` Go 项目（~15 个文件），不改任何现有 .py |
| **Go TUI 的职责边界？** | 纯展示 + 输入捕获 + SSE 事件消费。不包含：SQL 生成、Agent 逻辑、DB 连接、LLM 调用、图表生成 |
| **和现有 CLI 模式的关系？** | 共存。`vaxport` 命令仍可用 `--query` 一次性模式。`vaxport-tui` 是新二进制 |

---

## 二、架构总览

```
┌──────────────────────────────────────────────────────────┐
│                 Go TUI 进程 (Bubble Tea)                   │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌────────────────────────┐ │
│  │ Header   │  │ Sidebar  │  │  Chat Viewport          │ │
│  │ Bar      │  │ (Tree)   │  │  (glamour MD 渲染)      │ │
│  └──────────┘  │          │  │                        │ │
│                │ 数据库表 │  │  - 用户消息 (▸ 紫色)    │ │
│  ┌──────────┐  │ SKILL    │  │  - Agent 回答 (MD)     │ │
│  │ Input    │  │ 列表     │  │  - 工具调用概要         │ │
│  │ Info Bar │  │          │  │  - 计划确认提示         │ │
│  └──────────┘  │ 快捷键   │  └────────────────────────┘ │
│                └──────────┘                              │
│  ┌────────────────────────────────────────────────────┐  │
│  │  Input Area (多行 + 历史 + /命令补全)                │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  REST Client ──── HTTP ────►  SSE Client ──── SSE ───►  │
└──────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────┐
│              Python FastAPI 后端 (localhost:8931)          │
│                                                          │
│  ┌─────────────────┐    ┌────────────────────────────┐  │
│  │  api/ (新增)     │    │  现有代码 (零修改)          │  │
│  │  - server.py     │───▶│  - orchestrator.py         │  │
│  │  - routes.py     │    │  - agent.py                │  │
│  │  - sse.py        │    │  - tools.py / db.py        │  │
│  └─────────────────┘    │  - cli.py (App 类复用)     │  │
│                         │  - config.py / session.py   │  │
│                         │  - 所有其他 .py              │  │
│                         └────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

### 核心原则

1. **Python 代码零修改** — 所有现有 `.py` 文件不变。`cli.py` 的 `App` 类被 API 层 import 复用
2. **Go TUI 只做展示** — 不包含业务逻辑、Agent 逻辑、数据库连接
3. **FastAPI 是薄封装** — 把 `Orchestrator.run()` 和 `App.setup()` 暴露为 HTTP/SSE
4. **双进程，松耦合** — Go TUI 启动时检查后端是否在线；不在线则提示用户启动（v1.0 不自动管理子进程，用 `make dev` 脚本）

---

## 三、文件清单

### 3.1 新增文件

```
src/vaxport/api/          # Python — FastAPI 后端 (4 个文件)
├── __init__.py              # 包初始化
├── server.py                # FastAPI app 创建 + 生命周期 + 全局状态
├── routes.py                # 所有 REST 端点 + Pydantic schemas
└── sse.py                   # SSE 流式端点 + plan_confirm 状态管理

tui/                         # Go — Bubble Tea 前端 (~15 个文件)
├── go.mod
├── go.sum
├── main.go                  # 入口, Bubble Tea 程序初始化
├── model.go                 # Model 定义 + Init()
├── update.go                # Update 函数 (SSE 事件 → 状态变更)
├── view.go                  # View 函数 (布局 + 渲染)
├── styles.go                # Lipgloss 样式 (Dracula 主题)
├── components/
│   ├── header.go            # 头部栏
│   ├── sidebar.go           # 侧边栏 (数据库表树 / SKILL / 快捷键)
│   ├── chat.go              # 对话视口 (消息列表 + glamour MD 渲染)
│   ├── input.go             # 输入区 (多行 + 历史 + /补全)
│   ├── statusbar.go         # 状态栏 (空闲信息 / 执行状态)
│   ├── toollog.go           # 工具调用日志 (可折叠概要行)
│   └── modals.go            # 弹窗 (模型选择/数据库选择/决策选择)
└── client/
    ├── api.go               # HTTP REST 客户端
    └── sse.go               # SSE 事件流客户端 (含自动重连)
```

### 3.2 修改文件

| 文件 | 修改内容 |
|------|---------|
| `pyproject.toml` | 添加 `fastapi`, `uvicorn[standard]`, `sse-starlette` |
| `README.md` | 添加 Go TUI 编译/启动说明，保留 CLI 模式文档 |

### 3.3 保留但不再使用

| 文件 | 原因 |
|------|------|
| `src/vaxport/tui/app.py` | Textual TUI，新架构不再需要 |
| `src/vaxport/tui/style.tcss` | Textual 样式文件 |
| `src/vaxport/tui/__init__.py` | Textual 包 init |

---

## 四、FastAPI 后端设计

### 4.1 API 端点清单

```
# ── 核心 (SSE 流式) ──
POST   /api/chat/stream         SSE 流式 — Agent 执行 (核心)
POST   /api/chat/confirm        计划确认 (plan_ready 后回传)
POST   /api/chat/cancel         取消正在执行的查询

# ── 查询 (非流式) ──
POST   /api/chat/classify       意图分类
GET    /api/status              后端状态 (模型/后端/DB连接/版本)
GET    /api/tools               已注册工具列表
GET    /api/schemas             数据库 schema 树 (供侧边栏)
GET    /api/skills              SKILL 列表
GET    /api/models              可用模型列表 (所有后端)
POST   /api/models/switch       切换全局模型/后端
POST   /api/models/agent        设置单个 Agent 偏好模型

# ── 会话 ──
GET    /api/session/status      当前会话信息
GET    /api/session/history     对话历史
POST   /api/session/resume      恢复已保存会话
POST   /api/session/clear       清空会话
POST   /api/session/save        保存会话

# ── 管理操作 ──
POST   /api/schema/refresh      重新扫描数据库 schema + 更新工具注册
GET    /api/config              当前配置 (脱敏)
POST   /api/debug/toggle        切换调试模式
POST   /api/feedback            提交用户纠正反馈
POST   /api/shutdown            关闭后端 (Go TUI 退出时调用)
```

### 4.2 全局状态管理 (server.py)

```python
# FastAPI app 生命周期内的全局单例
_app: App | None = None           # cli.py 的 App 实例

# plan_confirm 状态 (key = request_id, 跨 /stream 和 /confirm)
_pending_plans: dict[str, threading.Event] = {}
_pending_results: dict[str, dict] = {}    # request_id → {confirmed, feedback}

# 执行取消状态
_active_executions: dict[str, threading.Event] = {}  # request_id → cancel_event
```

### 4.3 SSE 事件流 (核心)

端点: `POST /api/chat/stream`

**请求体**:
```json
{
  "query": "用户输入文本",
  "plan_mode": false,
  "history": [{"role": "user", "content": "..."}]
}
```

**响应**: `text/event-stream`

**SSE 事件类型**:

| 事件 | 数据 | 触发时机 |
|------|------|---------|
| `meta` | `{"request_id": "uuid", "agent_type": "analyze_reporter", "agent_label": "分析报告"}` | 流开始 (第一条事件) |
| `status` | `{"text": "📊 分析报告 Agent 思考中..."}` | 状态变更 |
| `plan_chunk` | `{"text": "### 一、任务理解\n..."}` | 规划阶段流式文本 |
| `plan_ready` | `{"plan_text": "...", "has_decisions": true, "decisions": [...]}` | 计划完成 → 暂停，等待 /confirm |
| `text_chunk` | `{"text": "分析结果显示..."}` | 回答流式文本 (当前策略: 仅累积不流式展示) |
| `tool_call` | `{"name": "query_xxx", "args": {...}}` | 工具调用开始 |
| `tool_result` | `{"row_count": 50, "truncated": false}` | 工具调用结束 |
| `answer` | `{"answer": "...", "agent_chain": [...], "turns": 5, "tokens_used": 12345, "token_pct": 40, "sql_queries": [...]}` | 执行完成 |
| `error` | `{"message": "错误描述"}` | 错误 (流继续或终止) |
| `done` | `{}` | 流结束 |

### 4.4 plan_confirm 双请求协调 (关键设计)

**问题**: SSE 流 (`/api/chat/stream`) 和 HTTP 确认 (`/api/chat/confirm`) 是两个独立请求，如何关联同一个执行会话？

**方案**: request_id 桥接

```
Go TUI                              FastAPI
  │                                    │
  ├── POST /api/chat/stream ──────────►│ 生成 request_id = uuid4()
  │                                    │ 存入 _active_executions[request_id] = cancel_event
  │◄─── SSE: meta(request_id) ────────┤ 返回 request_id 给客户端
  │◄─── SSE: plan_chunk... ──────────┤ 流式输出计划
  │◄─── SSE: plan_ready ──────────────┤ 计划完成
  │                                    │ 创建 event = threading.Event()
  │                                    │ 存入 _pending_plans[request_id] = event
  │                                    │ 存入 _pending_results[request_id] = {}
  │                                    │ event.wait(timeout=300)  ← 阻塞等待
  │                                    │
  │  (用户确认/取消/选择决策)            │
  │                                    │
  ├── POST /api/chat/confirm ─────────►│ body: {request_id, confirmed, feedback}
  │                                    │ _pending_results[request_id] = {confirmed, feedback}
  │                                    │ _pending_plans[request_id].set()  ← 解除阻塞
  │◄─── 200 {"status": "ok"} ─────────┤
  │                                    │ Agent 继续执行
  │◄─── SSE: text_chunk/answer ───────┤
  │◄─── SSE: done ────────────────────┤
  │                                    │ 清理 _pending_plans, _pending_results, _active_executions
```

**取消流程**:
```
  │ (用户按 Esc)                       │
  ├── POST /api/chat/cancel ──────────►│ body: {request_id}
  │                                    │ 如果 _pending_plans[request_id] 存在:
  │                                    │   _pending_results[request_id] = {confirmed: false}
  │                                    │   _pending_plans[request_id].set()  ← 解除 plan_confirm 阻塞
  │                                    │ 如果 _active_executions[request_id] 存在:
  │                                    │   _active_executions[request_id].set()  ← 触发 cancel_event
  │◄─── 200 {"status": "cancelled"} ───┤
```

### 4.5 各端点详细设计

#### `POST /api/chat/confirm`
```json
// 请求
{"request_id": "uuid-from-meta-event", "confirmed": true, "feedback": "1A; 补充意见..."}
// 响应
{"status": "ok"}
```

#### `POST /api/chat/cancel`
```json
// 请求
{"request_id": "uuid-from-meta-event"}
// 响应
{"status": "cancelled"}
```

#### `POST /api/models/switch`
```json
// 请求 — 全局切换
{"backend": "aliyun", "model": "deepseek-v4-pro"}
// 响应
{"status": "ok", "active_backend": "aliyun", "active_model": "deepseek-v4-pro"}
```

#### `POST /api/models/agent`
```json
// 请求 — 单个 Agent 偏好
{"agent_name": "analyze_reporter", "model": "deepseek-v4-pro"}
// model 为 null 时恢复继承全局
{"agent_name": "analyze_reporter", "model": null}
// 响应
{"status": "ok"}
```

#### `POST /api/session/resume`
```json
// 请求
{"session_ref": "2026-05-29-143000"}
// 响应
{"status": "ok", "session_id": "...", "message_count": 42}
```

#### `GET /api/config`
```json
// 响应 (API key 脱敏为前4后4)
{
  "model": "deepseek-v4-pro",
  "backend": "aliyun",
  "pg_host": "localhost",
  "pg_database": "myappdb",
  "pg_user": "vlm_reader",
  "api_key_redacted": "sk-0****b27",
  "db_names": ["myappdb"],
  "active_db": "myappdb",
  "skills_count": 5,
  "auto_plan": true,
  "plan_confirm": true
}
```

#### `POST /api/debug/toggle`
```json
// 请求: {}
// 响应: {"debug_mode": true}
```

#### `POST /api/schema/refresh`
```json
// 请求: {}
// 响应: {"status": "ok", "tool_count": 42}
// 说明: 重新扫描数据库 schema, 更新工具注册, 同步 orchestrator db_context
// 对应 CLI 命令 /refresh-schema
```

### 4.6 错误处理规范

| 场景 | HTTP 状态码 | 响应体 | Go TUI 表现 |
|------|-----------|--------|------------|
| 后端未启动 | 连接拒绝 | — | 全屏提示 "后端未启动，请运行: make api" + 重试按钮 (r 键) |
| LLM 调用失败 | SSE `error` 事件 | `{"message": "LLM 超时"}` | 对话区显示红色错误信息，状态栏恢复空闲 |
| DB 连接断开 | 200 (非致命) | 正常返回，`pg_status: "未连接"` | 侧边栏显示 "未连接"，输入区仍可用 (聊天模式) |
| plan_confirm 超时 | SSE `error` + `done` | `{"message": "确认超时(5分钟)"}` | 对话区显示超时提示 |
| SSE 连接断开 | 连接关闭 | — | 状态栏显示 "连接断开，正在重连..." (3次重试，间隔1s/2s/4s)，3次失败后提示用户 |
| 500 内部错误 | 500 | `{"error": "..."}` | 对话区显示 "内部错误: ..." |

---

## 五、Go TUI 设计

### 5.1 布局

```
┌──────────────────────────────────────────────┬─────────────┐
│  Header: 疫苗企业数据分析终端 vX.X.X           │             │
├──────────────────────────────────────────────┤  Sidebar    │
│  Chat Viewport (可滚动)                       │             │
│                                              │  PostgreSQL │
│  # 欢迎信息 (Markdown)                        │  ├─ myappdb*│
│  - 模型: deepseek-v4-pro @ 阿里百炼            │  │  ├─ analog│
│  ...                                         │  │  │  ├─ 表 │
│  ───────────────────────────────────────     │  │  ├─ ...   │
│  ▸ 用户查询                                  │             │
│  ───────────────────────────────────────     │  ────────── │
│  > 🤖 **通用 Agent**                         │  快捷键     │
│                                              │  ctrl+p 模型│
│  ## 回答标题                                 │  ctrl+d 库  │
│  回答内容 (Markdown 渲染)                     │  ctrl+t 规划│
│                                              │  ctrl+s 侧栏│
│  ┌──────────────────────────────────────┐    │  ctrl+o 日志│
│  │ ⚙ 共 3 次查询 | query_xxx → 50行     │    │  ctrl+q 退出│
│  └──────────────────────────────────────┘    │             │
│  ───────────────────────────────────────     │             │
├──────────────────────────────────────────────┤             │
│  Info Bar: 执行 · 通用 Agent | deepseek...    │             │
├──────────────────────────────────────────────┤             │
│  Input Area                                  │             │
│  Enter 发送  Ctrl+N 换行  ↑↓ 历史  / 命令     │             │
└──────────────────────────────────────────────┴─────────────┘
```

### 5.2 Model 结构

```go
type Model struct {
    // 子组件
    header     header.Model
    sidebar    sidebar.Model
    chat       chat.Model
    statusBar  statusbar.Model
    inputArea  inputarea.Model

    // API 客户端
    api  *client.APIClient
    sse  *client.SSEClient

    // 终端尺寸
    width  int
    height int

    // ── 运行时状态 ──
    busy          bool          // 是否有正在执行的查询
    planMode      bool          // 规划/执行模式
    debugMode     bool          // 调试模式
    sidebarTab    string        // "tables" | "skills"
    toolLogExpanded bool

    // ── 消息历史 ──
    messages      []chat.Message  // {role, content, agentType, toolCalls}

    // ── 输入历史 ──
    inputHistory  []string
    historyPos    int

    // ── 当前活跃请求 ──
    activeRequestID string       // 当前 SSE 流的 request_id
    // plan_confirm 状态
    waitingConfirm  bool
    planText        string
    planDecisions   []Decision
    planFeedback    string

    // ── 弹窗 (nil = 无弹窗) ──
    modal          tea.Model

    // ── 连接状态 ──
    backendOnline   bool
    sseReconnecting bool
}
```

### 5.3 消息类型

```go
// ── SSE 事件消息 ──
type MetaMsg struct {
    RequestID string
    AgentType string
    AgentLabel string
}
type StatusMsg struct { Text string }
type PlanChunkMsg struct { Text string }
type PlanReadyMsg struct {
    PlanText     string
    HasDecisions bool
    Decisions    []Decision
}
type TextChunkMsg struct { Text string }
type ToolCallMsg struct {
    Name string
    Args map[string]interface{}
}
type ToolResultMsg struct {
    RowCount  int
    Truncated bool
}
type AnswerMsg struct {
    Answer     string
    AgentChain []string
    Turns      int
    TokensUsed int
    TokenPct   int
    SQLQueries []string
}
type ErrorMsg struct {
    Message  string
    Fatal    bool    // true=流终止
}
type DoneMsg struct{}

// ── 用户操作消息 ──
type UserSubmitMsg struct{ Text string }
type PlanConfirmMsg struct{ Confirmed bool; Feedback string }
type BackendConnectedMsg struct{}
type BackendDisconnectedMsg struct{}
type SSEReconnectFailedMsg struct{}
```

### 5.4 各组件实现要点

#### Header
- 一行，紫色粗体 "疫苗企业数据分析终端 v{version}"
- 从 `GET /api/status` 获取版本

#### Sidebar
- **数据库表树**: `GET /api/schemas` → 递归树渲染。当前活跃库标注 `*`
- **SKILL 列表**: Ctrl+S 切换，`GET /api/skills`
- **快捷键面板**: 底部固定，灰色文字
- 实现: `viewport` + 手写树渲染（Bubble Tea 无内置 Tree widget）

#### Chat Viewport
- 消息列表，视口自动滚底
- 消息类型:
  - 欢迎消息 (首次启动，Markdown)
  - `▸ 用户消息` (紫色)
  - Agent 标签行 `> 🤖 **通用 Agent**` (灰色斜体)
  - Agent 回答 (glamour 渲染 Markdown, Dracula 主题)
  - 工具调用概要 (灰色，默认折叠)
  - 分隔线 `──` (暗灰色)
  - 错误消息 (红色)
  - 计划确认提示 `[Enter] 确认 [Esc] 取消`
- **Markdown 渲染**: 直接在 chat.go 中内联使用 glamour，一行调用 `glamour.Render(mdText, "dracula")`，不单独包装文件

#### Status Bar (Info Bar)
- 空闲: `执行 · 分析报告 Agent | deepseek-v4-pro | myappdb@localhost | Context ████░░░░░░ 40% | 轮次 3`
- 执行中: SSE `status` 事件驱动实时更新 (见下表)

| SSE 事件/阶段 | 状态栏文字 |
|-------------|-----------|
| meta | `🔍 分类中...` → `🤖 {Agent名} Agent 思考中...` |
| plan_chunk | `📋 正在生成执行计划...` |
| plan_ready | `⏳ 请确认执行计划 [Enter] 确认 [Esc] 取消` |
| tool_call | `⚙ 执行: {工具名}` |
| tool_result | `   ↳ {N} 行结果` |
| answer | `📝 正在生成回答...` |
| error | `❌ {错误信息}` |
| done → 空闲 | `执行 · {Agent名} Agent | {model} | {db} | Context {bar} {pct}% | 轮次 {n}` |
| Esc 取消 | `⏹ 执行已取消` |
| SSE 断开 | `⏳ 连接断开，正在重连... (第{N}次)` |

#### Input Area
- Textarea 组件，3-12 行自适应
- **Enter**: 提交文本。如果 `waitingConfirm`，Enter=确认执行
- **Ctrl+N**: 强制换行
- **↑↓**: 浏览输入历史
- **/**: 触发命令补全 (下拉提示)
- 命令: `/exit /quit /help /model /status /skills /tools /clear /history /debug /save /copy /export /refresh-schema`
- **Ctrl+Y**: 复制最后回答
- **Ctrl+Shift+C**: 复制全部对话 (终端原生快捷键也可用)

#### Tool Log
- 概要行: `⚙ 共 {N} 次查询 | 最近: {工具名} → {M} 行 [Ctrl+O 展开]`
- Ctrl+O 展开/折叠详情列表
- 详情每行显示: `⚙ {工具名}({参数摘要})` / `  ↳ {N} 行 | {耗时}`

#### Modals
1. **模型选择器** (Ctrl+P): 两级 — Agent 列表 → 模型列表
2. **数据库选择器** (Ctrl+D): 数据库列表
3. **决策选择器**: 计划中的决策项，逐项 A/B/C/D + 自定义

### 5.5 后端连接管理

```
Go TUI 启动
  → GET /api/status (health check)
  → 成功: backendOnline = true, 加载侧边栏数据
  → 失败: 显示 "后端未启动" 全屏提示
      "请在新终端运行: make api"
      "启动后按 [r] 重新连接"

SSE 连接断开 (运行时)
  → sseReconnecting = true
  → 自动重连 (3次, 间隔 1s/2s/4s)
  → 成功: sseReconnecting = false, 恢复
  → 3次失败: 显示 "连接丢失，按 [r] 重新连接"
```

---

## 六、流式输出策略

与当前 vaxport 保持一致（基于之前的优化）：

| 内容类型 | 展示方式 | 说明 |
|---------|---------|------|
| 规划文本 (plan_chunk) | **流式** | glamour 实时渲染，逐 chunk 更新 Markdown widget |
| 工具调用 (tool_call/result) | **仅状态栏** | 不在对话区展示，概要行实时更新 |
| 正式回答 (text_chunk→answer) | **一次性渲染** | text_chunk 仅累积，收到 answer 事件后一次性 glamour 渲染 |
| 错误 (error) | **即时** | 直接显示在对话区 |

---

## 七、Dracula 主题

```go
var Dracula = struct {
    Bg, Line, Sel, Fg, Comment lipgloss.Color
    Cyan, Green, Orange, Pink, Purple, Red, Yellow lipgloss.Color
}{
    Bg: "#282A36", Line: "#44475A", Sel: "#44475A", Fg: "#F8F8F2",
    Comment: "#6272A4", Cyan: "#8BE9FD", Green: "#50FA7B",
    Orange: "#FFB86C", Pink: "#FF79C6", Purple: "#BD93F9",
    Red: "#FF5555", Yellow: "#F1FA8C",
}
```

Glamour 使用内置 `dracula` 主题（与上述色值一致）。

---

## 八、保留的全部现有功能

| 当前 TUI 功能 | Go TUI 实现 | 备注 |
|-------------|------------|------|
| 欢迎信息 (Markdown) | chat 第一条消息 | |
| 用户消息 `▸ text` | 紫色前缀 | |
| Agent 回答 (Markdown) | glamour 渲染 | |
| Agent 标签 `> 🤖 **通用 Agent**` | 注入回答顶部 | |
| 工具调用概要 `⚙ 共 N 次` | 可折叠概要行 | |
| Ctrl+O 展开/折叠工具日志 | 同 | |
| 头部栏 | 同 | |
| 状态栏 (prompt-info) | 底部 Info Bar | |
| 输入区 (多行 + 历史 + /补全) | 同 | |
| 侧边栏: 数据库表树 | 同 | |
| 侧边栏: SKILL 列表 | Ctrl+S 切换 | |
| 侧边栏: 快捷键面板 | 同 | |
| Ctrl+P 模型选择器 (两级) | Modal | |
| Ctrl+D 数据库选择器 | Modal | |
| 决策选择器 (计划中) | Modal | |
| 计划确认 `[Enter] 确认 [Esc] 取消` | 同 | |
| Esc 取消执行 | POST /api/chat/cancel | |
| Ctrl+T 规划/执行切换 | 同 | |
| Ctrl+E/W 展开/折叠树 | 同 | |
| Ctrl+Y 复制最后回答 | 同 | |
| Ctrl+Shift+C 复制全部对话 | 同 | |
| / 命令补全提示 | 同 | |
| 调试模式信息展示 | 同 | |
| 轮次分隔线 `──` | 同 | |
| 反馈记忆自动提取 | 同 (通过 /api/feedback) | |
| 会话自动保存 | 同 (通过 /api/session/save) | |
| 审计日志 | Python 侧不变 | |
| 图表生成 (PNG) | MD `![](path)` 引用 | 可选: 加 `Ctrl+G` 用系统应用打开最新图表 |
| 多数据库切换 | 同 (Ctrl+D) | |

---

## 九、实施阶段

### 阶段 1: FastAPI 后端 (5-6 天)

**目标**: 所有端点可用，curl/httpie 可完整测试

| # | 任务 | 产出 |
|---|------|------|
| 1.1 | `api/__init__.py` + `api/server.py` (框架) | FastAPI app, CORS, 生命周期, 全局状态 |
| 1.2 | `api/routes.py` — 查询类端点 | status, tools, schemas, skills, models, models/switch, models/agent, classify, config |
| 1.3 | `api/routes.py` — 会话类端点 | session/status, history, resume, clear, save |
| 1.4 | `api/routes.py` — 操作类端点 | debug/toggle, schema/refresh, feedback, shutdown |
| 1.5 | `api/sse.py` — `/api/chat/stream` | SSE 流式端点 + TUICallbacks 等价回调 → SSE 事件转换 |
| 1.6 | `api/sse.py` — plan_confirm 机制 | request_id 桥接, `/api/chat/confirm`, `/api/chat/cancel` |
| 1.7 | 集成测试 | curl 测试所有端点, httpie 测试 SSE 流 |

### 阶段 2: Go 骨架 + 开发环境 (2 天)

**目标**: Go 可编译，`make dev` 可用，静态布局可见

| # | 任务 | 产出 |
|---|------|------|
| 2.1 | `go.mod` + 依赖安装 | bubbletea, lipgloss, glamour, bubbles, yaml.v3 |
| 2.2 | `main.go` + `model.go` + `view.go` | Bubble Tea 程序入口，基础布局渲染 |
| 2.3 | `styles.go` | Dracula 主题 |
| 2.4 | `components/header.go` | 静态头部栏 |
| 2.5 | `components/sidebar.go` | 静态侧边栏 (手写 Tree 渲染) |
| 2.6 | `components/input.go` | Textarea + Enter/Ctrl+N/↑↓ |
| 2.7 | `components/statusbar.go` | 静态状态栏 |
| 2.8 | `Makefile` + `make dev` 脚本 | `make dev` 一键启动后端+Go TUI |

### 阶段 3: Go 客户端 + 动态数据 (1-2 天)

**目标**: Go TUI 连接后端，侧边栏/状态栏显示真实数据

| # | 任务 | 产出 |
|---|------|------|
| 3.1 | `client/api.go` | REST 客户端 (所有端点) |
| 3.2 | `client/sse.go` | SSE 客户端 + 事件解析 + 自动重连 |
| 3.3 | 启动流程 | health check → 加载侧边栏/状态栏数据 |
| 3.4 | `update.go` (基础) | SSE 事件 → Model 状态更新 |

### 阶段 4: 对话功能 (3-4 天)

**目标**: 完整对话流程可用

| # | 任务 | 产出 |
|---|------|------|
| 4.1 | `components/chat.go` | 消息列表 + glamour MD 渲染 + 自动滚底 |
| 4.2 | 用户输入 → SSE 流 | 提交 → POST /stream → 消费事件 → 渲染消息 |
| 4.3 | plan_confirm 流程 | plan_ready → 用户确认/取消 → POST /confirm → 继续/终止 |
| 4.4 | Esc 取消 | POST /api/chat/cancel |
| 4.5 | `components/toollog.go` | 概要行 + Ctrl+O 展开/折叠 |
| 4.6 | 命令处理 | / 命令 + 裸词命令 (help/clear/status 等) |
| 4.7 | 会话管理 | /save /clear /history → API 调用 |

### 阶段 5: 弹窗 + 高级交互 (2 天)

**目标**: 全部交互可用

| # | 任务 | 产出 |
|---|------|------|
| 5.1 | `components/modals.go` — 模型选择器 | Ctrl+P 两级选择 |
| 5.2 | `components/modals.go` — 数据库选择器 | Ctrl+D |
| 5.3 | `components/modals.go` — 决策选择器 | plan 中逐项选择 |
| 5.4 | 侧边栏交互 | Ctrl+S/E/W 切换/展开/折叠 |
| 5.5 | 复制功能 | Ctrl+Y / Ctrl+Shift+C |
| 5.6 | 图表文件打开 | Ctrl+G 用系统应用打开最新图表 (可选) |

### 阶段 6: 联调 + 错误处理 + 打包 (2 天)

**目标**: 生产可用

| # | 任务 | 产出 |
|---|------|------|
| 6.1 | 后端不可用时的 UI | 全屏提示 + 重连 |
| 6.2 | SSE 断开重连 | 自动重试 + 失败提示 |
| 6.3 | 错误场景覆盖 | 超时/500/LLM 失败 等场景 UI 表现 |
| 6.4 | Go 静态编译 | `CGO_ENABLED=0 go build` |
| 6.5 | README 更新 | Go TUI 编译/启动文档 |
| 6.6 | 保留 CLI 文本模式 | `vaxport --query "..."` 不变 |

---

## 十、依赖清单

### Go
```
github.com/charmbracelet/bubbletea  v1.x
github.com/charmbracelet/lipgloss   v0.x
github.com/charmbracelet/glamour    v0.x
github.com/charmbracelet/bubbles    v0.x    (textarea, viewport)
gopkg.in/yaml.v3                    v3      (读取 ~/.vaxport/config.yaml)
```

### Python (新增)
```
fastapi >= 0.110.0
uvicorn[standard] >= 0.27.0
sse-starlette >= 1.8.0
```

---

## 十一、工作量

| 阶段 | 内容 | 人天 |
|------|------|------|
| 1 | FastAPI 后端 | 5-6 |
| 2 | Go 骨架 + 开发环境 | 2 |
| 3 | Go 客户端 + 动态数据 | 1-2 |
| 4 | 对话功能 | 3-4 |
| 5 | 弹窗 + 高级交互 | 2 |
| 6 | 联调 + 错误处理 + 打包 | 2 |
| **总计** | | **15-18** |

---

## 十二、风险

| 风险 | 等级 | 缓解 |
|------|------|------|
| glamour 对中文表格渲染效果差 | 中 | 阶段 2.3 用真实中文 Markdown 表格验证；不行则切 `chroma` 高亮 + 手写表格渲染 |
| plan_confirm 双请求状态竞态 | 中 | request_id 桥接 + `threading.Event` 方案已在当前 Textual TUI 中验证可行；API 层加超时兜底 |
| Bubble Tea 无 Tree widget | 低 | 手写递归树渲染（<200 行），参考 OpenCode 实现 |
| Python 进程管理跨平台 | 低 | v1.0 不自动管理子进程，`make dev` 分别启动 |
| Textual Markdown widget → glamour 迁移差异 | 低 | glamour 渲染为纯字符串后直接输出，无布局兼容问题 |

---

## 十三、最终目录结构

```
vaxport/
├── README.md
├── TROUBLESHOOTING.md
├── ARCHITECTURE_PLAN.md
├── Makefile                         # 新增
├── pyproject.toml                   # 修改 (加依赖)
├── myappdb_full.dump
├── src/vaxport/
│   ├── __init__.py
│   ├── agent.py                     # 不变
│   ├── cli.py                       # 不变
│   ├── config.py                    # 不变
│   ├── db.py                        # 不变
│   ├── tools.py                     # 不变
│   ├── orchestrator.py              # 不变
│   ├── charts.py / statistics.py / anomaly.py / ...
│   ├── tui/                         # 保留不删
│   │   ├── app.py
│   │   └── style.tcss
│   └── api/                         # 新增
│       ├── __init__.py
│       ├── server.py
│       ├── routes.py
│       └── sse.py
├── tui/                             # 新增 Go 项目
│   ├── go.mod / go.sum
│   ├── main.go / model.go / update.go / view.go / styles.go
│   ├── components/
│   │   ├── header.go / sidebar.go / chat.go / input.go
│   │   ├── statusbar.go / toollog.go / modals.go
│   └── client/
│       ├── api.go / sse.go
└── tests/
```