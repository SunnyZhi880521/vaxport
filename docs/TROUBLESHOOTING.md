# TROUBLESHOOTING

## 2026-05-30: iTerm2 中文 IME 输入完全不工作

### 背景
在 iTerm2 中运行 vaxport TUI，中文 IME 输入无法写入 TextArea。macOS Terminal.app 正常，Ghostty 需补丁后正常，但 iTerm2 完全不工作——按中文输入法打字后 TextArea 无任何字符出现，RichLog 诊断 app 也没有 Key 事件。粘贴中文可以正常输入。

### 根因
Textual 启动时向终端发送 `\x1b[>25u`（Kitty 键盘协议 flags=1+8+16），请求 `disambiguate_escape_codes` + `report_all_keys` + `report_associated_text` 三个增强。

iTerm2 启用 `report_all_keys` (flag 8) 后存在 bug：IME 中文输入的 committed text **完全不发送任何字节**。既不作为 UTF-8 直传，也不包装为 CSI-u 序列，直接被吞掉。诊断日志确认：

```
# iTerm2 Kitty 全模式 (flags=25) 下中文 IME 输入：
RAW: b'\x1b[32;;32u'   ← 空格正常（CSI-u）
RAW: b'\x1b[113;5u'     ← Ctrl+Q 正常（CSI-u）
# 但中文 IME：零字节，完全没有 RAW 输出
```

对比降级后 (flags=1)：

```
RAW: b'\xe4\xbd\xa0'    ← IME 中文 "你" 作为 UTF-8 直传！
DECODED: '你'
EVENT: Key(key='你', character='你', is_printable=True)  ← 正常生成 Key 事件
```

### 修复

**1. iTerm2 Kitty 协议降级补丁** (`app.py` 模块级)

检测 iTerm2 环境（`LC_TERMINAL=iTerm2` 或 `TERM_PROGRAM=iTerm.app`），将 Kitty 协议从 flags=25 降级为 flags=1（只保留 `disambiguate_escape_codes`）。IME 文字回归 UTF-8 直传，与 macOS Terminal 行为一致。

```python
if _IS_ITERM2:
    from textual.drivers.linux_driver import LinuxDriver
    _ORIG_START_APP_MODE = LinuxDriver.start_application_mode

    def _iterm2_start_app_mode(self):
        _ORIG_START_APP_MODE(self)
        self.write("\x1b[<u")   # pop 当前键盘模式 (flags=25)
        self.write("\x1b[>1u")  # push 只 disambiguate (flag=1)
        self.flush()

    LinuxDriver.start_application_mode = _iterm2_start_app_mode
```

**2. Ghostty 多字 IME 补丁** (`app.py` 模块级，已有)

Ghostty 用完整 Kitty 协议发送 IME，text 字段含冒号分隔的多码点（如 `20320:22909` = "你好"）。Textual 原始 regex 不匹配冒号，补丁扩展 regex 支持多码点解析。

**3. 删除 Ctrl+I workaround**

iTerm2 降级后 IME 直接输入正常，Ctrl+I 原生输入模式不再需要。已删除 Binding、快捷键列表、action_native_input 方法和欢迎消息中的提示。

### 各终端中文输入状态
| 终端 | 状态 | 机制 |
|------|------|------|
| macOS Terminal.app | 正常 | 无 Kitty 协议，IME UTF-8 直传 |
| iTerm2 | 正常（需降级补丁） | Kitty flags=1，IME UTF-8 直传 |
| Ghostty | 正常（需 IME regex 补丁） | Kitty flags=25，IME CSI-u 多码点 |
| Ubuntu/GNOME Terminal | 正常 | 无 Kitty 协议，IME UTF-8 直传 |

### 涉及文件
- `src/vaxport/tui/app.py`: iTerm2 降级补丁、Kitty IME regex 补丁、Ctrl+I 删除、`import os` 添加
- `src/vaxport/orchestrator.py`: `C:\Users` → `C:\\Users` (SyntaxError 修复)
- `src/vaxport/cli.py`: `C:\` → `C:\\` (SyntaxWarning 修复)
- `src/vaxport/agent.py`: `C:\` → `C:\\` (SyntaxWarning 修复)

---

## 2026-05-30: Kitty IME 补丁误拦截普通按键 + Kitty 协议 Backspace 双事件

### 现象
输入框中按 Backspace 删除字符时，经常一次删除 2 个字符而非 1 个（中英文输入均有此问题，iTerm2 和 Ghostty 均出现）。

### 根因（两层）

**第一层：Kitty IME 补丁正则过于宽泛**
Kitty IME 补丁的 regex `\x1b\[(\d*);(\d*);([\d:]+)([u~ABCDEFHPQRS])` 也会匹配普通 3 字段 CSI-u 序列（如 Backspace 按下事件 `\x1b[127;5;1u`），产生错误的 `\x01` 事件。

**第二层：Kitty keyboard protocol `report_all_keys` (flag 8) 缺陷**
修复第一层后问题依旧。进一步排查发现 Textual 8.2.7 的 Kitty protocol flag 8 模式下，Backspace 被底层双重处理——CSI-u 序列和原始字节均产生事件。将 Kitty 协议全局降级为 flags=1（仅 disambiguate）仍无效，说明双事件发生在更低层（可能是终端驱动或 Textual 内部）。

### 修复

**1. IME 补丁加 `:` 守卫**（`app.py` 模块级）
```python
if ":" in text_str:  # 只处理真正的多码点 IME 组合
```

**2. Kitty 协议全局降级**（`app.py` 模块级）
将原本仅 iTerm2 的 Kitty 降级扩展为全局所有终端，移除 `report_all_keys` 和 `report_associated_text`。

**3. ChatInput Backspace 防抖兜底**（`app.py` ChatInput 类）
```python
_last_backspace_time: float = 0.0

def action_delete_left(self) -> None:
    """删除光标左侧字符（带 50ms 防抖）"""
    import time
    now = time.monotonic()
    if now - self._last_backspace_time < 0.05:
        return  # 50ms 内重复事件，丢弃
    self._last_backspace_time = now
    super().action_delete_left()
```
无论根因在哪一层，50ms 内的重复 Backspace 直接丢弃。最终方案。

### 涉及文件
- `src/vaxport/tui/app.py`: `_patched_seq_to_keys()` 加守卫、Kitty 全局降级、ChatInput 防抖

---

## 2026-05-30: TaskAssigner 路由误判 + 跨平台路径兼容 + Python 转义语法错误

### 背景

三个关联问题在同一次排查中发现并修复：

**问题 1：TaskAssigner 将统计分析查询误路由到 GeneralAgent**
用户提问"A、B、C 三个班组，哪个的成品效价最稳定？对比一下均值、标准差和变异系数。"——这涉及多组对比+统计计算（均值/标准差/变异系数），应路由到 `analyze_reporter`，但被误判为 `general`。GeneralAgent 没有统计分析能力和领域知识，无法正确处理。

**问题 2：agent.py SyntaxError — `\U` Unicode 转义**
`REVIEW_PROMPT` 中 `C:\Users\...` 的单反斜杠 `\U` 被 Python 解析为 Unicode 转义序列 `\UXXXXXXXX`，`sers` 不是合法的 8 位十六进制字符，导致整个文件无法导入。

**问题 3：跨平台路径假设**
vaxport 实际运行在企业管理者本地电脑（Windows/macOS/Linux），远程连 PostgreSQL。但 prompt 和代码中多处硬编码"Unix 路径是对的，Windows 是错的"，在 Windows 上会导致路径处理错误。

### 根因

**TaskAssigner 三个缺陷**：
1. **无对话历史**：`_task_assign()` 只传当前 query，不传历史。追问"列出这些 CAPA"无法知道"这些"指代什么，只能当纯列表查询
2. **互斥规则过于激进**：`纯列表/排序/筛选/计数类查询...即使涉及质量领域术语...只要不加分析判断，一律归 general`——但"对比均值、标准差和变异系数"明明是统计分析，却被"对比"字面归类为简单查询
3. **错误静默降级**：LLM 调用异常、JSON 解析失败、target 无效 → 全静默回退 `general`，无任何日志

**`\U` 转义**：Python 普通字符串中 `\U` 被解析为 Unicode 转义序列 `\UXXXXXXXX`（8 位十六进制），`C:\Users` 中的 `\U` + `sers\...` 不符合格式 → SyntaxError

**跨平台假设**：`Path.home() / ".vaxport"` 本身已通过 `pathlib` 自动适配各 OS（Win→`C:\Users\xxx`，macOS→`/Users/xxx`，Linux→`/home/xxx`）。但 prompt 和 `_fix_windows_paths()` 仍假定运行环境必定是 Unix。

### 修复

**1. TaskAssigner 路由强化** (`orchestrator.py`)

- `_task_assign()` / `_route()` / `classify()` / `run()` 全链路传入对话历史（最近 6 条）
- 新增路由决策日志：
  - 成功：`TaskAssigner 路由: <query> → <target>（原因: <reason>）`
  - 失败：`warning` 级别记录异常原因
- 优化分类规则：
  - 新增：统计计算（均值/标准差/变异系数/Cpk/相关性等）→ `analyze_reporter`
  - 新增：多组对比（"哪个更稳定""组间差异"）→ `analyze_reporter`
  - 新增：趋势分析/异常检测/参数漂移 → `analyze_reporter`
  - 缩小互斥规则：仅纯浏览/排序/筛选（"显示所有表""最近 10 条记录"）→ `general`
- 添加 `import logging` + `logger = logging.getLogger(__name__)`

**2. TUI 侧传入历史** (`app.py`)

- `classify()` 调用前提前构造 history（摘要 + 最近 20 条），复用于路由和执行

**3. `\U` 转义修复** (`agent.py`)

- `C:\Users\...` → `C:\\Users\\...`（双反斜杠转义）

**4. 跨平台兼容** (4 个文件)

| 修改 | 文件 | 内容 |
|------|------|------|
| `_fix_windows_paths` 加 OS 判断 | `app.py` | `platform.system() == "Windows"` 时跳过修复 |
| REVIEW_PROMPT 路径检查 | `agent.py` | "检查是否与 generate_chart 返回完全一致"（平台中立） |
| FIX_PROMPT | `agent.py` | "禁止任何格式转换"（平台中立） |
| ANALYSIS_PROMPT | `agent.py` | 同上 |
| GENERAL_SYSTEM_PROMPT | `orchestrator.py` | 同上 |
| generate_chart 工具描述 | `cli.py` | "当前系统的绝对路径"（平台中立） |

### 涉及文件

- `src/vaxport/orchestrator.py`: TaskAssigner 历史+日志+分类规则优化+跨平台 prompt
- `src/vaxport/tui/app.py`: classify 传历史+`_fix_windows_paths` OS 判断
- `src/vaxport/agent.py`: `\U` 转义修复+跨平台 prompt
- `src/vaxport/cli.py`: 跨平台 prompt

---

## 2026-05-30: LLM 将图片路径改写为 Windows 格式 → 图片不显示

### 背景
用户在 Ghostty (macOS) 中测试，报告"有一个图片没有生成"。检查发现图表文件实际已生成（`~/.vaxport/charts/chart_comparison_1780125170634.png`，17KB），但 LLM 在 Markdown 中把路径写成了 `C:\Users\zhixiaoguang\.vaxport\charts\...`（Windows 格式），而非工具返回的 `/Users/zhixiaoguang/.vaxport/charts/...`（Unix 格式）。

### 根因
`generate_chart` 工具返回正确的 Unix 绝对路径，prompt 中也多处强调"必须原样使用 file_path"。但部分 LLM 模型训练数据偏向 Windows，看到 `/Users/xxx` 这类绝对路径会自动"修正"为 `C:\Users\xxx`，忽略工具实际返回值。

与 2026-05-28 的"相对路径改写"问题同根：LLM 对路径有"美化/规范化"倾向，不受 prompt 指令约束。

### 修复
4 处 prompt 强化，明确禁止 Windows 格式：

| 文件 | 位置 | 修改 |
|------|------|------|
| `cli.py` | `generate_chart` 工具描述 | 标注 file_path 是 **Unix 绝对路径**，严禁添加 `C:\` 前缀 |
| `orchestrator.py` | 输出格式规范 | 加"禁止改为 Windows 格式（如 C:\Users\...）" |
| `agent.py` | 可视化要求 | 加"禁止对路径做任何修改：禁止添加 C:\ 前缀、禁止将 /Users/ 或 /home/ 改为 Windows 格式" |
| `agent.py` | QC 修复规则 | 加"禁止改为 Windows 格式" |

### 后续考虑 → 已落实
**Prompt 强化未能完全阻止**（2026-05-30 二次测试仍出现 `C:\Users\...`），已增加代码层正则兜底：
`_fix_windows_paths()` — 在 `_show_agent_result` 中自动将 `C:\Users\xxx\.vaxport\` 替换为 `/Users/xxx/.vaxport/`。

### 涉及文件
- `src/vaxport/cli.py` — prompt 强化
- `src/vaxport/orchestrator.py` — prompt 强化
- `src/vaxport/agent.py` — prompt 强化
- `src/vaxport/tui/app.py` — `_fix_windows_paths()` 代码层兜底

---

## 2026-05-30: Go+Bubble Tea TUI 方案失败，回归 Textual

### 背景
为解决 Textual TUI 流式输出性能问题（内容增长后变慢），尝试用 Go+Bubble Tea 重写前端 TUI。Bubble Tea 的 Elm 架构（同步单线程事件循环）在理论上有更好的可控性。

### 失败原因
Bubble Tea 的同步事件循环架构与高频 SSE 流式事件根本性不兼容：

1. **事件循环饥饿**：SSE 每秒数百个事件直接注入 Bubble Tea 事件循环，UI 事件（键盘/鼠标）永远排不上队
2. **逐事件 yield 方案**（30ms/事件）：将吞吐压到 ~33 事件/秒，100 事件 = 3 秒延迟
3. **批次排空 + yield 方案**（50ms/批）：速度提升但仍不够，复杂规划→执行→展示流程卡死
4. **定时器驱动批次方案**（50ms tick 驱动排空）：架构上正确，但整体复杂度已超过收益
5. **viewport.SetContent() O(n) 换行计算**：每次刷新重新计算全部内容换行，内容增长后单次调用可达 100ms+

### 关键教训
- **Bubble Tea/Elm 架构不适合高频数据流场景**：事件循环设计用于 UI 交互（按键/鼠标），不是数据推送
- **Textual 的 async 架构天然更适合**：Widget 异步更新 + 独立渲染管线，不会因数据事件阻塞 UI
- **框架选型应先验证最薄弱环节**：流式输出是 vaxport 核心场景，应在选型阶段先用原型验证

### 涉及文件
- `tui/` — 已删除（Go Bubble Tea 代码）
- `vaxport-tui` — 已删除（Go 二进制）
- `pyproject.toml` — `vaxport-tui` 入口点改回 `vaxport.cli:main`
- `Makefile` — 移除 Go 相关 target
- `src/vaxport/tui_launcher.py` — 简化为调用 `cli:main`

### 回退后状态
- `vaxport` 和 `vaxport-tui` 均启动 Textual TUI
- Textual 版本保留在 `src/vaxport/tui/app.py`（83621 bytes，含双 widget 轮转优化）

---

## 2026-05-29: 流式输出性能优化 — 双 widget 轮转架构

### 背景
流式输出越来越慢：简单问题 + 规划阶段流式正常，但 ReAct 正式分析阶段随着输出内容增长，Markdown widget 全量重渲染造成 O(n²) 累积卡顿。

### 根因
`Markdown.update(full_text)` 每次重解析+重渲染整个文档。内容从 0 → 20000+ 字符，单次 update 从 1ms 增长到 500ms+，在50ms节流间隔下完全跟不上。

### 修复（方案 B'：双 widget 轮转）
**核心思路**：用两个 Markdown widget（stable + active），active 始终 < 3000 字符（更新快），超过阈值合并到 stable（更新频率低）。

1. `TUICallbacks.__init__`：`_live_widget` → `_stable_widget` + `_active_widget` + `_stable_text` + `ACTIVE_THRESHOLD = 3000`
2. `set_answer_widgets(stable, active)`：替代 `set_live_widget(widget)`
3. `_flush()`：active_text < 3K → `_active_widget.update(active_text)`（快）；≥3K → `_stable_widget.update(full_text)` + `_active_widget.update("")`（低频）
4. `_create_stream_widgets()`：创建 stable_w + active_w 两个 Markdown widget
5. `_show_agent_result()`：最终合并 → `_stable_widget.update(display_text)` + `_active_widget.remove()`

### 性能对比（20K 字符文档）
| | 更新次数 | 单次成本 | 总渲染工作量 |
|---|---|---|---|
| 旧方案（单 widget） | ~200 次 | 平均 O(10K) = ~200ms | ~40 秒 CPU |
| 新方案（双 widget） | active: ~200 次 + stable: ~6 次 | active: O(400) = ~2ms, stable: O(10K) = ~200ms | ~1.6 秒 CPU |

**约 25 倍提升**。视觉效果 100% 一致（相同 Markdown widget + 相同 CSS）。

### 涉及文件
- `src/vaxport/tui/app.py`：TUICallbacks 类 + _create_stream_widgets + _show_agent_result

## 2026-05-29: 流式输出视觉效果修复（RichLog → Markdown）

### 背景
RichLog 流式方案中，两个 RichLog widget（plan + answer）被嵌入对话区后显示为两个小滚动框（约5行高度），用户无法看到完整内容的全局视图，视觉效果差。

### 根因
RichLog 自带内部滚动和 `max_lines` 高度限制，被嵌在 VerticalScroll 对话区内时形成嵌套滚动 + 固定高度小窗口。

### 修复（参考 Claude Code 设计）
Claude Code 流式输出时直接在对话区渐进更新 Markdown widget，不使用中间 widget 类型。

1. `_create_stream_widgets()` — RichLog 改为 Markdown("")
2. `_flush()` / `_flush_plan()` — `widget.write(new_text)` 改为 `widget.update(full_text)`（全量刷新 Markdown）
3. `_show_agent_result()` — 不再 remove RichLog + mount Markdown，改为原地 `widget.update(display_text)`
4. 节流参数调整：50ms / 5 chunks（Markdown 全量刷新比 RichLog 增量写稍重，适度放宽节流）
5. 清理：SKIP_PLAN 残留用 `widget.remove()` 替代 `widget.clear()`（Markdown 没有 clear 方法）

### 效果
- 内容在对话区自然展开，与最终渲染完全一致的视觉效果
- 流式期间和完成后是同一个 Markdown widget，无切换闪烁
- CSS 已有 `#conversation Markdown { height: auto; }`，无需额外样式

## 2026-05-29: comparison 柱状图缺少柱子标签

### 背景
用户报告 comparison 类型图表中，每组有 7 个柱子但没有图例标明每个柱子代表什么指标，信息不完整。

### 根因
`_draw_comparison()` 中，柱子按 group 着色（同组内所有柱子同色），图例只显示 group 名。数据结构 `{"groups": {"组A": [v1..v7], "组B": [v1..v7]}}` 中没有字段用于描述每个柱子代表什么。

### 修复
1. `charts.py` — `_draw_comparison()`: 新增 `bar_labels` 可选字段。当提供时，柱子按指标（位置）着色，图例显示指标名；未提供时保持旧行为（按 group 着色）
2. `charts.py` — `generate_chart()` docstring: comparison 格式增加 `bar_labels`
3. `cli.py` — 工具描述: comparison 示例增加 `bar_labels`
4. `agent.py` — 提示词: comparison 图指引强调"每组多柱子时必须提供 bar_labels"

### 验证
- 带 bar_labels 的 2组×7柱 comparison 图 → 图例显示 7 个指标名 ✓
- 不带 bar_labels → 旧行为（按 group 着色）不变 ✓

## 2026-05-29: 审核流程加固 + 上下文连贯性修复 + 跨会话记忆

### 背景
三个关联问题一次性修复：

**问题 1：审核发现问题后不自动修复**
用户测试"冷藏车故障放行评估"后追问"按质检发现问题补充运输温度影响分析"——审核（原"质检"）发现了问题但 `_run_fix` 返回 `fix_count=0`，带 ⚠️ 标记的未修复答案直接输出给用户。术语"质检"在疫苗行业有特定含义（QC 检测），与 Agent 的输出检查混淆。

**问题 2：追问上下文割裂**
用户追问"补充XX"后 Agent 重新规划→执行→只输出补充章节，没有和原始报告合并。`_generate_plan()` 不接收对话历史，每次查询都当新任务。与 Claude Code 的自然对话体验差距明显。

**问题 3：缺少跨会话记忆**
用户纠正过的行为（如"零值月份要展示"）跨会话丢失。Claude Code 有 auto-memory，vaxport 没有。

### Claude Code vs vaxport 对比分析

```
Claude Code:  User → [完整上下文 + 对话导向 prompt] → 自然回复
vaxport:  User → TaskAssigner → [规划防火墙, 无历史] → 确认 → SQL批量 → 分析 → 审核 → 修复 → 输出
```

6 个缺口：
1. `_generate_plan()` 看不到历史 — 无法识别追问
2. 没有"对话模式" — 追问也走完整管道
3. System Prompt 任务导向 — 缺少对话连续性指令
4. 没有"任务完成"意识 — 输出补丁就算完成
5. 追问输出不合并 — 只输出补充章节
6. 缺少渐进式交互 — 只有确认/取消，不能反问澄清

### 修复

**1. 审核→FIX→re-QC 循环（agent.py）**
- `QC_PROMPT` → `REVIEW_PROMPT`，"质检" → "审核"
- `_run_qc` → `_run_review`，`_auto_qc` → `_auto_review`
- POST-HOOK 改为最多 3 轮审核-修复循环：审核 → 有问题 → 修复 → 再审核 → 通过 → 输出
- `_run_fix` 加固：短答案自动重试（追加纠正提示），改进 `fix_count` 检测（同时匹配 `- 🔴` 和 `🔴`）
- 3 轮后仍未通过 → 简短提示，不 dump 审核清单
- 超时：`total_timeout` 600→900，`single_round_timeout` 120→300

**2. 上下文连贯性（agent.py + orchestrator.py）**
- `_generate_plan(self, user_query, history=None)`：接收对话历史，注入最近 3 轮交互
- `PLAN_PROMPT` 加对话连续性判断：追问 → `SKIP_PLAN`，全新话题 → 正常规划
- `Agent.run()` SKIP_PLAN 分支：跳过规划管道，注入 `FOLLOWUP_MODE_PROMPT`，进入 ReAct（保留工具调用）
- `FOLLOWUP_MODE_PROMPT`：输出完整性铁律 + 自然反问 + 工具按需调用
- `Agent.SYSTEM_PROMPT` 加"对话连续性"+"输出完整性"
- `GENERAL_SYSTEM_PROMPT` 同步追加
- `ANALYSIS_PROMPT` 加"追问合并规则"
- `PLAN_PROMPT`"七、待用户决策"增"需澄清"类型：默认值继续 + 标注

**3. 跨会话反馈记忆（memory.py 新文件 + tui/app.py）**
- `FeedbackMemory` 类：加载/存储 `~/.vaxport/memory/feedback.json`
- `needs_extraction()`：检测用户消息是否含纠正关键词（"不对""应该是""记住""以后"等）
- `extract_and_store()`：轻量 LLM 调用提取规则，去重存储
- `build_system_prompt_section()`：构建"用户历史反馈"段落
- TUI 启动时加载并注入到 Orchestrator → 所有 Agent
- 每次查询后异步检测纠正 → 提取规则 → 显示"📝 已记住: ..."

### 涉及文件
- `src/vaxport/agent.py`: REVIEW_PROMPT, FOLLOWUP_MODE_PROMPT, PLAN_PROMPT(门控+需澄清), SYSTEM_PROMPT(连续性+完整性), ANALYSIS_PROMPT(合并规则), _generate_plan(history), Agent.run(SKIP_PLAN分支), _run_review, _run_fix(加固), set_memory_context()
- `src/vaxport/orchestrator.py`: GENERAL_SYSTEM_PROMPT(连续性+完整性), __init__(auto_review+超时), set_memory_context()
- `src/vaxport/config.py`: auto_qc→auto_review
- `src/vaxport/cli.py`: auto_qc→auto_review, 超时同步
- `src/vaxport/memory.py`: 新文件 — FeedbackMemory 类
- `src/vaxport/tui/app.py`: 加载 memory, 纠正检测, _extract_feedback(), on_mount 注入

### 流程变化

```
追问"补充XX" → _generate_plan(history) → SKIP_PLAN
→ 跳过规划管道 → 注入对话模式 prompt → ReAct(可调工具)
→ 输出完整融合报告

用户纠正"不对，应该是XX" → needs_extraction() 检测
→ extract_and_store() LLM提取规则 → 存储 feedback.json
→ 下次启动自动注入 system prompt
```

---

## 2026-05-29: Agent 产出不完整答案 — 零值缺失 + 让用户自己去查数据

### 背景
用户提问"仓库温湿度报警按月统计，有没有季节性规律"，Agent 只返回了 7 月和 8 月（有报警的月份），然后建议"查询更多月份的监控数据（如 1~6 月、9~12 月）以完整绘制全年报警趋势图"。

### 真实数据
`analog_warehouse.warehouse_monitoring` 有 13,126 条记录，覆盖 2024-01 到 2026-06 共 30 个月。只有 2024-07（91次）和 2024-08（15次）有报警。数据是完整的——但 Agent 没有展示零报警的月份，导致答案看起来不完整。

### 根因（两层）
1. **SQL 层面**：`WHERE alarm_flag=true GROUP BY month` 只返回有报警的月份，零值分组被过滤掉。LLM 没有意识到需要补零
2. **LLM 推理层面**：Agent 看到"只有 2 个月有数据"，错误推断"数据不完整"，然后建议用户去获取更多数据——而不是验证数据覆盖范围或直接得出结论

### 更深层问题 — 第一性原则违背
Agent 把"建议用户补充数据"当作可接受的输出，而不是把它当作自己应该完成的工作。如同一个医生看了你的化验单，发现少了一项，不开化验单却让你自己去化验科。

### 修复（3 层加固）
1. **SQL_GEN_PROMPT**：新增 GROUP BY 零值规则——必须包含无 WHERE 过滤的验证查询，或使用 `COUNT(*) FILTER (WHERE condition)` 保留零值分组
2. **ANALYSIS_PROMPT**：新增"数据完整性第一原则"——零是有效数据；禁止建议用户自行获取数据；需要的数据自己直接查
3. **GENERAL_SYSTEM_PROMPT**：新增"数据完整性铁律"——零值分组必须展示；禁止建议用户查询更多数据
4. **QC_PROMPT**：新增零值检查 + "建议用户自查"检测（标记为 🔴 严重缺陷）
5. **FIX_PROMPT**：新增规则 0（最高优先级）——自动查询缺失数据、补零、重绘图表、删除"建议用户"表述

### 涉及文件
- `src/vaxport/agent.py`: SQL_GEN_PROMPT, ANALYSIS_PROMPT, QC_PROMPT, FIX_PROMPT
- `src/vaxport/orchestrator.py`: GENERAL_SYSTEM_PROMPT

---

## 2026-05-28: TaskAssigner 语义路由 + 按 Agent 配置模型 + 数据领域知识注入

### 背景
Prompt Handoff (`[HANDOFF:target]`) 在 deepseek-v4-flash 上实测失效：GeneralAgent 忽略 Handoff 指令，自行处理复杂分析任务。同时 Agent 缺乏数据领域知识，不知道特定数据在哪张表。

### 变更内容
- **TaskAssigner 语义路由**: 1 次无工具 LLM 调用（TASK_ASSIGNER_PROMPT），输出 `{target, reason, hints}` JSON，替代 Prompt Handoff 作为主路由
- **LLMClient 模型覆盖**: `chat_completion()` 新增 `model` 参数，Agent 传入 `preferred_model` 覆盖全局默认
- **Config 持久化**: `agent_models` 字典，key=Agent名，value=model_id；`get_agent_model()` / `set_agent_model()` 方法
- **Ctrl+P 改造**: ModelPickerScreen → AgentModelPickerScreen，两级界面（Agent 列表 → 模型选择），支持"(继承全局)"
- **数据领域知识**: 3 个 System Prompt 追加数据库表位置/含义/关联知识
- **Handoff 段删除**: GENERAL_SYSTEM_PROMPT 中 ~50 行 Handoff 指令段删除，Handoff 降级为 Agent 内部二次转交

### 根因
1. deepseek-v4-flash 不遵循 `[HANDOFF:target]` 输出格式，LLM 倾向于"帮忙"而非委派
2. Agent 缺乏对数据库 schema 的领域理解（如不知道 `cold_chain_break` 是偏差类型），导致 SQL 查询遗漏关键数据

### 涉及文件
- `src/vaxport/orchestrator.py`: TASK_ASSIGNER_PROMPT + _task_assign() + _route() 重写 + Handoff 段删除 + 数据知识注入 + update_agent_model()
- `src/vaxport/llm/__init__.py`: chat_completion() model 参数覆盖
- `src/vaxport/agent.py`: preferred_model 参数 + 5 处 chat_completion + 5 处 active_model 引用更新
- `src/vaxport/config.py`: agent_models 配置 + get/set_agent_model()
- `src/vaxport/cli.py`: Orchestrator 构造传入 config
- `src/vaxport/tui/app.py`: AgentModelPickerScreen（替换 ModelPickerScreen）
- `~/.vaxport/config.yaml`: agent_models 字段

### 模型默认值（推荐）
| Agent | 推荐模型 | 说明 |
|-------|---------|------|
| TaskAssigner | deepseek-v4-pro | 需要强语义理解能力 |
| general | glm-5.1 | 简单查询，成本优先 |
| analyze_reporter | deepseek-v4-pro | 复杂分析需强推理 |
| quality_supervision | glm-5.1 | 合规判断，平衡成本 |
| document_search | deepseek-v4-flash | RAG 检索，速度优先 |

---

## 2026-05-28: Agent 架构 v2 — 4 Agent 精简 + 关键词路由删除 + Stats+Report 合并

### 背景
在 GeneralAgent 统一入口架构基础上，进一步优化 Agent 数量和路由机制。

### 变更内容
- **删除门控**: GATING_PROMPT + _gating_check() + conversational_mode（~55 行），GeneralAgent auto_plan=False 替代
- **删除关键词路由**: 6 组关键词列表（STATS/REPORT/MULTI_STEP/COMPLIANCE/DOC_SEARCH/ALERT_MONITOR）+ `_route()` 关键词匹配逻辑（~100 行）
- **修复 Handoff 策略**: GeneralAgent Handoff 指令从"先尝试自己解决"改为"先判断后行动"，新增立即 Handoff 触发条件（多维度分析/放行决策/风险评估）
- **合并 Agent**: StatsAgent + ReportAgent → AnalyzeReporter（detect_anomaly + generate_report），删除 `_run_sequential()`
- **合并 Agent**: ComplianceAgent + AlertMonitorAgent → QualitySupervisionAgent（纯 System Prompt 差异）
- **新增 System Prompts**: GENERAL_SYSTEM_PROMPT（~80行）+ ANALYZER_SYSTEM_PROMPT（~150行）+ QUALITY_SUPERVISION_SYSTEM_PROMPT（~100行）
- **Agent 数量**: 6 → 4（GeneralAgent, AnalyzeReporter, QualitySupervisionAgent, DocSearchAgent）
- **停止注册工具**: match_regulation/root_cause_analysis/classify_deviation/check_capa_closure/check_alerts/get_alert_summary/run_statistics

### 根因
1. 门控二元分类无法区分"简单 DB 查询"和"复杂分析"
2. 关键词路由随 Agent 增多 O(n) 膨胀，无法覆盖表达变体
3. StatsAgent 和 ReportAgent 各只有一个独特工具（detect_anomaly vs generate_report），pipeline 完全相同，单独成立 Agent 的理由薄弱
4. 分析→报告是自然工作流连续性，合并后一次 Handoff 完成

### 涉及文件
- `src/vaxport/agent.py`: 删除 GATING_PROMPT/_gating_check/conversational_mode
- `src/vaxport/orchestrator.py`: 新增 3 个 System Prompt + TOOL_FILTERS + Agent 重配置；删除关键词列表 + _run_sequential + 旧配置
- `src/vaxport/tui/app.py`: Agent 数量 6→4，标签更新
- 源文件保留不删: compliance.py, alerts.py, monitoring.py, statistics.py

---

## 2026-05-28: 门控三次迭代失败 → GeneralAgent 统一入口架构

### 背景
Agent 需要对用户问题做"简单 vs 复杂"分类：简单问题（查表、对话、基础统计）直接回答，复杂问题走 plan→SQL batch→ReAct→QC pipeline。

### 迭代一：合并门控（方案 K）— 失败
- **方案**: 门控指令嵌入 PLAN_PROMPT 末尾，PLAN_PROMPT 先让 LLM 判断 CHAT/ANALYZE 再写规划
- **现象**: "你好，你能干什么"也触发 plan→SQL→分析 pipeline
- **根因**: GATING_INSTRUCTION 放在 90 行 PLAN_PROMPT 末尾，LLM 被前 80 行模板（任务理解/数据需求/执行步骤/输出章节/风险点）prime 成规划模式后已无法正确判断。Prompt 顺序决定了 LLM 的"思维惯性"
- **教训**: 门控和规划必须在不同的 LLM 调用中完成，prompt 位置解决不了 priming 问题

### 迭代二：独立门控（方案 E）— 失败
- **方案**: 新增独立的 `GATING_PROMPT`（~18 行短 prompt）+ `_gating_check()` 方法，一次轻量 LLM 调用返回 CHAT/ANALYZE。与 PLAN_PROMPT 完全分离
- **现象（Bug 1 修复后测试）**: 
  - "你好，你能干什么" → 正确分类为 CHAT ✅
  - "用 ISO 9001:2015 和 ICH Q10 框架评估质量体系成熟度" → 分类为 CHAT ❌（明显是 ANALYZE）
- **根因**: 分类维度本身就是错的。CHAT=不需要数据库 vs ANALYZE=需要数据库。但"简单查库"和"复杂分析"都需要数据库——在 CHAT/ANALYZE 维度上无法区分。二元分类解决不了"简单 vs 复杂"的问题
- **教训**: 门控的正确维度不是"是否需要数据库"，而是"当前 Agent 能否独立完成"

### 迭代三：GeneralAgent 统一入口（最终方案）
- **方案**: 废弃门控。所有查询统一进入 GeneralAgent，通过**工具硬约束**控制行为：
  - GeneralAgent 只有 `query_*` + `generate_chart`，`auto_plan=False`
  - 简单任务：SQL（利用 PG 内置 AVG/CORR/REGR_*/PERCENTILE 等 200+ 函数计算）→ 翻译 → 直接回答
  - 复杂任务：需要 `detect_anomaly`/`generate_report` 等专业工具时 → `[HANDOFF:target]` → Orchestrator 路由到专业 Agent（完整 pipeline）
- **核心洞察**: "SQL 本身是计算引擎，LLM 是交互界面 + SQL 作者 + 结果翻译。不要让 LLM 做 SQL 能做的事"
- **具体改动**: 见 `orchestrator.py` GeneralAgent 配置和 `agent.py` 门控代码删除

### 涉及文件
- `src/vaxport/agent.py`: 删除 GATING_PROMPT、_gating_check()、conversational_mode 变量
- `src/vaxport/orchestrator.py`: 新增 GENERAL_SYSTEM_PROMPT、TOOL_FILTERS["general"]、简化 _route()、配置 GeneralAgent

## 2026-05-28: 门控失效 + Esc取消 + 上下文记忆 — 三个修复

### Bug 1: 门控失效（独立门控 方案 E）
- **现象**: "你好，你能干什么"也触发 plan→SQL→分析 pipeline。合并门控（方案 K）中门控指令在 PLAN_PROMPT 末尾，LLM 被 90 行模板 prime 成规划模式后已无法正确判断
- **修复**:
  - 新增 `GATING_PROMPT`（独立短 prompt ~15 行）与 PLAN_PROMPT 完全分离
  - 新增 `_gating_check()` 方法 — 一次轻量 LLM 调用，返回 CHAT/ANALYZE
  - PLAN_PROMPT 恢复纯净（去除门控指令和 GATING_INSTRUCTION）
  - `_generate_plan()` 恢复接受 `user_query`（不再含门控逻辑）
  - `run()` 先调 `_gating_check()` → CHAT 走对话模式，ANALYZE 走 `_generate_plan()` + 完整 pipeline

### Bug 2: Esc 不能停止执行（方案 A+）
- **现象**: 执行阶段按 Esc 无效，只能等 Agent 完成
- **根因**: `_on_key` 中 Esc 仅在计划确认阶段有效（`_plan_confirm_event` 非空时），执行阶段无取消通道
- **修复**:
  - `App.__init__` 新增 `_execution_cancel: threading.Event`
  - `_on_key` Esc 且 busy 时 set 该 Event
  - `Agent.run()` / `_single_llm_turn()` / `Orchestrator.run()` 新增 `cancel_event` 参数
  - ReAct 轮间 + stream chunk 间检查 `cancel_event.is_set()`，命中后关闭流返回 `⏸️ 执行已取消`
  - `_do_run()` 执行前 clear Event，传入 orchestrator

### 上下文记忆改进
- **背景**: `session.messages[-6:]` 硬截断，超过 3 轮的消息完全丢失
- **修复**:
  - `Session` 新增 `summary` + `_summary_msg_count` 字段，支持增量摘要
  - `save()`/`load()` 持久化摘要
  - `needs_summary_update()` — 超过 20 条消息且新增 ≥10 条时触发
  - `_do_run()` 传 `summary` + 最近 20 条消息作为历史
  - `_update_session_summary()` — LLM 增量合并旧摘要+新消息（失败不阻塞）
- **关键文件**: `agent.py`, `orchestrator.py`, `tui/app.py`, `session.py`

## 2026-05-28: 自适应对话模式 — 方案K 实现

- **背景**: Agent 每次查询都是全新对话（无历史），且强制走 plan→SQL→分析→QC pipeline
- **方案**: 独立门控 + 对话模式轻量分支 + Esc 取消 + 增量摘要
- **改动**: 见上方三个修复

## 2026-05-28: 决策选择器不弹窗 — App._on_key 未调用 super()

## 2026-05-28: 决策选择器不弹窗 — App._on_key 未调用 super()

- **现象**: 计划中包含待决策事项，但决策选择器不再逐项弹窗让用户选择
- **根因**: `VaxportApp._on_key` 只处理 Escape，但所有其他按键都直接 return 没调 `super()._on_key(event)`，阻断了 Textual 内部的按键传播机制
- **修复** (`app.py:582-590`): 添加 `else: super()._on_key(event)` 分支，确保非 Escape 键正常传递给底层 Textual

## 2026-05-28: Ctrl+Y 复制失效 — TextArea redo 绑定冲突

- **现象**: 按 Ctrl+Y 无反应（期望复制全部对话内容），实际被 TextArea 的 `ctrl+y → redo` 拦截
- **根因**: ChatInput 继承自 Textual 的 TextArea，TextArea 有默认绑定 `ctrl+y → redo`，优先级高于 App 层 `ctrl+y → copy_last_answer`
- **修复** (`app.py:27-29, 61-62, 154-155, 991-1015`):
  - ChatInput.BINDINGS 新增 `ctrl+y → trigger_copy`，通过子类绑定覆盖父类 TextArea 的 redo
  - ChatInput 新增 `CopyRequested` 消息类 + `action_trigger_copy` 方法
  - VaxportApp 新增 `on_chat_input_copy_requested` 处理器 + `_get_conversation_text` 方法
  - 移除 App.BINDINGS 中冗余的 `ctrl+y` 绑定
  - Ctrl+Y 改为复制**全部对话内容**（而非仅最后一条回答）

## 2026-05-28: 质检发现图表路径占位符但不自动修复

- **现象**: LLM 调用 generate_chart 后，Markdown 中写 `![标题](file_path)` 而非实际文件路径。质检检测到路径不存在，但只追加"⚠️ 质检发现问题"标记，不自动修复
- **根因**: `_run_qc` 设计为检测+追加式（QC_PROMPT 明确指示"禁止输出修正后的答案"），没有修复阶段
- **修复** (`agent.py:125-140, 357-405, 769-793`):
  - 新增 `FIX_PROMPT` — 修复阶段提示词，要求 LLM 重新调用工具并只修复问题
  - 新增 `_run_fix` 方法 — 最多 3 轮 ReAct 修复循环，可调用工具。带安全底线（修复结果长度不足原答案 30% 视为失败）
  - POST-HOOK 改为"质检 → 修复"两步：QC 发现问题后自动触发 `_run_fix`，成功则追加"✅ 已自动修复 N 项"

## 2025-05-26: LLM 主动设小 _limit 导致漏数据

- **现象**: LLM 调 `query_xxx(_limit=50)` 返回 50 行"已截断", 实际表有 53 行, 漏了 3 行。同类问题: LLM 拍脑袋传 `_limit=20/50/100` 导致大量假截断
- **根因**: `_limit` 参数暴露给 LLM, 它不知道数据量却自作主张设小值
- **最终修复**: 删除 `_limit` 参数, LLM 不可见。固定 5000 行硬上限, 查询用 `LIMIT 5001` 多取一行检测真截断, 截断时返回 `warning` 字段引导 LLM 加过滤条件或聚合
- **涉及文件**: `src/vaxport/tools.py`

## 2025-05-26: 查询返回空结果 (row_count=0)

- **现象**: LLM 调用表查询工具始终 row_count=0, 数据库实际有数据
- **根因**: `tools.py:32` `register()` 把所有表列标记为 `required`, LLM 被迫传空字符串 → `WHERE col=''` → 0 行
- **修复**:
  - `register()` 增加 `required` 参数(默认 None = 全部 required)
  - `_register_table_tool()` 传 `required=[]` 使所有过滤可选
  - handler 增加空字符串过滤: `v is not None and v != ""`
- **涉及文件**: `src/vaxport/tools.py`

## 2025-05-26: Agent 循环检测过激误中断

- **现象**: LLM 查询多张表时触发 `连续使用相同参数调用 3 次，已中断`
- **根因**: `agent.py` 检测 #3 用累计计数器, 跨轮不重置, 3 次阈值太低
- **修复**: 改为**连续**检测(穿插其他工具后重置), 阈值提到 5;
  `AgentLoopState` 新增 `_last_tool_call_sig` + `_consecutive_same_count`
- **涉及文件**: `src/vaxport/agent.py`

## 2026-05-26: Agent 检测 #1 误中断合法多步骤查询

- **现象**: 统计分析任务（如2024 vs 2025年度对比）触发 `检测到重复工具调用，已中断。请尝试换一种方式提问`
- **失败尝试 1**: 阈值 1→2（连续 2 次相同回合才中断）。无效，仍然拦截。
- **失败尝试 2**: 归因于上下文压缩后 LLM 遗忘导致重查。用户确认无压缩发生，诊断错误。
- **根因**: 检测 #1 的基本假设是错的——"连续两轮相同工具调用=死循环"。现实中正常的多步骤分析（查询→统计→下钻→查询→统计）会产生合法重复，Python 脚本同理。用 LLM 记忆来判断逻辑正确性是走错了方向。
- **最终修复**: **删除检测 #1**。保留检测 #2（乒乓 A→B→A→B→A）和检测 #3（连续 5 次完全相同的单次调用）捕获真正的病态模式。`max_rounds=100` + `total_timeout=600` 已是硬约束。
- **涉及文件**: `src/vaxport/agent.py`

## 2026-05-26: Ctrl+O 展开工具日志导致崩溃 (MarkupError)

- **现象**: 执行多次查询后按 Ctrl+O 展开工具调用日志，vaxport 崩溃报 `MarkupError: Expected markup value`
- **根因**: 工具参数中包含 `[` 字符（如 `data=[532.78, ...]`），Textual `Static` 默认解析 Rich markup，把 `[` 当标签起始符，内部 `=` 被误解析为属性赋值
- **修复**: `ToolCallLog.add_call()/add_result()` 存储前用 `\[` 转义 `[`；`_refresh()` 中手动拼接的 markup 标签不受影响
- **涉及文件**: `src/vaxport/tui/app.py`

## 2026-05-27: LLM 回答不完整/跳章节 — 缺少规划与自检机制

- **现象**: 
  - deepseek-v4-flash 回答 FDA 飞行检查报告从"五、完整追溯报告"开始，跳过前四章
  - deepseek-v4-pro 同一问题输出完整（模型能力差异导致的鲁棒性问题）
- **根因**: ReAct 循环只有"调工具→输出答案"，缺少两个关键阶段：
  1. **执行前规划**：LLM 直接动手，没有显式列出输出章节和步骤，容易遗漏
  2. **执行后自检**：LLM 没有回头检查"我是不是跳号了/漏内容了"
  弱模型（flash）对此更敏感
- **解决方案**：在 Agent.run() 中强制注入 PRE-HOOK + POST-HOOK，代码级强制执行，非 prompt 建议：
  - **PRE-HOOK** (auto_plan)：执行前用不含工具的独立 LLM 调用生成结构化计划（任务理解/数据需求/执行步骤/输出章节/风险点）
  - **POST-HOOK** (auto_qc)：执行后用不含工具的独立 LLM 调用对照清单质检（结构检查/内容检查/数据检查），发现问题直接修正
  - **plan_confirm**：计划生成后暂停，用户可确认/取消（TUI 中用 threading.Event 实现阻塞等待）
  - 配置开关：`config.yaml` → `agent.auto_plan`, `agent.plan_confirm`, `agent.auto_qc`
  - 成本：每次查询额外 ~1300 tokens（plan ~500 + qc ~800）
- **涉及文件**: `src/vaxport/agent.py`, `src/vaxport/orchestrator.py`, `src/vaxport/config.py`, `src/vaxport/cli.py`, `src/vaxport/tui/app.py`（5 文件联动）

## 2026-05-27: 乒乓检测误中断合法多步分析 + 计划驱动批量执行

- **现象**: 复杂多步分析（如"所有产品线效价按月汇总画趋势图→分析季节效应→关联仓储湿度→关联原料批次"）触发 `检测到工具调用在 A→B→A 模式间切换，已中断`
- **根因**: 乒乓检测有两个缺陷：
  1. 只比对工具名（不比对参数），导致 `run_query(SQL_A)` → `思考` → `run_query(SQL_B)` → `思考` → `run_query(SQL_C)` 被误判为 A→B→A 死循环，实际上每次 SQL 不同，在做有用功
  2. 阈值 5 太小，合法多步分析轻易触及
- **更深层根因**: ReAct 循环本身就是逐轮摸索，PRE-HOOK 规划产出的文本计划没有结构化利用。规划阶段已分析了需要哪些表/条件/逻辑关系，但执行阶段照样一步步试，浪费轮次
- **修复**:
  1. **签名比对升级**: 乒乓检测比对完整 `(工具名, 参数)` 签名而非仅工具名，参数不同意味着在做有用功
  2. **阈值三级跳**: 5→7→10，最终需 5 个完整交替周期（10 步）才触发
  3. **计划驱动批量执行 v1** (GATHER_PROMPT): PRE-HOOK 确认后新增数据采集阶段，LLM 最多 3 轮批量调用查询工具，再切分析模式。用 prompt 引导批量，但仍是 ReAct 行为
  4. **代码重构**: 提取 `_single_llm_turn()` 和 `_append_tool_results()` helper
- **涉及文件**: `src/vaxport/agent.py`

## 2026-05-27: 计划驱动批量执行 v2 — 结构化 SQL 生成

- **现象**: v1 的 GATHER_PROMPT 方案仍是 ReAct（prompt 建议"一次多调"但 LLM 不一定照做），flash 模型可能每次只调 1 个查询，3 轮采集上限也限制利用率
- **第一性原理分析**: 计划→SQL 是确定性转换——计划中已列出表名/条件/目的，数据库概况中有列信息。不需要 LLM 在 ReAct 循环中"边试边查"，1 次 LLM 调用输出结构化 JSON 即可
- **修复**:
  1. **SQL_GEN_PROMPT** 替换 GATHER_PROMPT: LLM 输出 `{"queries": [{"sql": "SELECT ...", "purpose": "..."}]}` JSON，不调工具
  2. **`_generate_sql_queries()`**: 1 次非流式 LLM 调用，解析 JSON，失败返回空列表回退 ReAct
  3. **`_execute_sql_batch()`**: 代码直接通过 `db.execute_query()` 批量执行全部 SQL，输出汇总+详细数据注入 messages。0 次 LLM 调用
  4. **流程简化**: 原 3 轮 ReAct 采集 → 现 1 次 LLM 调用 + 代码批量执行。LLM 调用从「2+3 轮」降至「3 次」（plan + sql_gen + analysis）
  5. **安全**: 验证 SQL 以 SELECT 开头，PG 用户 vlm_reader 本身只读，结果经 truncate_tool_result 截断
- **涉及文件**: `src/vaxport/agent.py`

## 2026-05-27: Ctrl+O 工具日志不显示批量查询 + 缺少分析/思考进度

- **现象**:
  1. v2 结构化 SQL 方案上线后，Ctrl+O 展开为空——批量 SQL 执行绕过了 `on_tool_call` 回调，ToolCallLog 收不到任何条目
  2. 即便 v2 之前，Ctrl+O 展开也只显示 `⚙ query_xxx → ↳ N行` 的查询记录，两轮查询之间 LLM 在"分析结果/思考下一步/组织答案"的阶段完全不可见
- **根因**:
  1. `_execute_sql_batch()` 直接调 `db.execute_query()`，只触发 `on_sql` 和 `on_tool_result`，未触发 `on_tool_call`
  2. `ToolCallLog` 只有 `add_call`/`add_result` 两种条目，无不调用工具的"思考/分析"状态展示能力
  3. `ProgressCallbacks.on_thinking` 定义后从未被调用
- **修复**:
  1. **`_execute_sql_batch()`**：每条 SQL 执行前调用 `callbacks.on_tool_call("batch_sql", {"sql": ..., "purpose": ...})`
  2. **`ProgressCallbacks.on_thinking(description)`**：加 `description` 参数，Agent 在关键阶段节点调用：
     - `_execute_sql_batch()` 入口 → `"📋 批量数据采集 (N 条查询)"`
     - ANALYSIS_PROMPT 后 → `"📊 分析阶段"`
     - ReAct 循环每轮执行完工具后 → `"分析查询结果..."`
  3. **`ToolCallLog`**：`_calls`/`_results` 双数组 → `_entries: list[(type, text)]` 有序列表，支持 `"call"`/`"result"`/`"thinking"` 三种类型，新增 `add_thinking()`，`_refresh()` 按时间线顺序渲染
  4. **`_add_to_tool_log()`**：增加 `thinking_text` 参数
  5. **`TUICallbacks.on_thinking`**：调 `_add_to_tool_log(thinking_text=...)`

## 2026-05-27: ToolCallLog 完全不显示 + Ctrl+O 无反应 — Textual 8.x widget 更新机制缺陷

- **现象**:
  - 计划确认后 Ctrl+O 工具日志不显示任何内容，按 Ctrl+O 无反应
  - 执行过程中页面有闪动但无 toolcalllog 提示
  - 几分钟后结果直接输出，中间无进度反馈
  - 同时复杂查询触发 `总时长超过限制` 超时中断
- **根因**:
  1. **Textual 8.2.7 `Static.update()` 对已挂载 widget 不可靠**：`update()` 内部设置 `__visual` 并调用 `refresh(layout=True)`，但布局系统未正确重绘已挂载 widget。这是贯穿所有失败尝试的根本原因
  2. **`start_time` 计时过早**：`start_time` 在 SQL 生成和批量执行之前就开始计时，这些阶段（LLM 调用 30-60s + 批量 SQL 执行）消耗了 ReAct 循环的 600s 配额，导致复杂查询超时
- **失败尝试（共 8 种方案）**:
  1. `Static.update()` 直接更新 → 内容不可见
  2. 覆盖 `render()` 方法返回动态内容 → 不触发重渲染
  3. 改用 `RichLog.write()`/`clear()` → 同源问题，不可见
  4. `remove()` + `mount()` 同一 widget → 能显示但每 300ms 闪烁
  5. 创建新 widget 替换旧 widget + `before=` → 同闪烁
  6. `refresh(layout=True)` + 父容器刷新 → 无效果
  7. CSS `min-height: 1` + `height: auto` → 无效果
  8. 纯文本（去除 Rich markup）→ 排除 markup 问题，仍无效
- **最终方案 — 独立 widget + display 属性**:
  - **核心思路**：放弃"更新已挂载 widget"这条路。每条进度创建一个全新的 Static widget 并 `mount()`（天然可靠，与 `_add_info` 同原理），通过 widget 的 `display` 属性控制折叠
  - **数据结构**：
    - `_tool_summary: Static | None` — 概要行 widget（每次更新时 remove 旧的 mount 新的）
    - `_tool_details: list[Static]` — 详情行 widget 列表（默认 `display = False` 隐藏）
    - `_tool_entries: list[tuple[str, str]]` — 条目数据 `[(type, text), ...]`，用于概要行计数
    - `_tool_expanded: bool` — 展开/折叠状态
  - **概要行**：始终可见，显示 "⚙ 共 N 次查询 | 最近: ... | Ctrl+O 展开详情"。每次更新时创建新 widget 替换旧 widget（仅 1 个 widget，开销可忽略）
  - **详情行**：每个条目（call/result/thinking）对应一个独立 Static widget，创建时设 `display = self._tool_expanded`，默认折叠时不可见
  - **Ctrl+O**：遍历 `_tool_details` 切换每个 widget 的 `display` 属性，重建概要行更新提示文字（"展开详情" ↔ "收起详情"）
  - **为什么能 work**：每个 widget 都是新建的，从不更新已挂载 widget 的内容，完全绕开了 Textual 8.2.7 的缺陷
- **超时修复**：`start_time = time.time()` 从 SQL 生成/批量执行之前移到 ReAct 循环入口。规划和数据采集阶段（LLM 调用 + SQL 批量执行）不再占用分析阶段的时间配额
- **涉及文件**: `src/vaxport/tui/app.py`, `src/vaxport/agent.py`, `src/vaxport/tui/style.tcss`

## 2026-05-27: ToolCallLog 概要行 MarkupError 崩溃

- **现象**: 执行查询后崩溃报 `MarkupError: Expected markup value (found ': "PEDV Pot..)[/] | \\[Ctrl+O 展开详情]')`
- **根因**: `_add_to_tool_log()` 把工具参数直接插入 Rich markup f-string（如 `f"[#6272A4]⚙ {call_text}[/]"`），参数中包含 `[`、`:` 等 Rich 特殊字符，被 Rich parser 误解析为标签
- **修复**: 新增 `_escape_markup()` 方法，`call_text`/`result_text`/`thinking_text` 统一 `replace("[", "\\[")` 后存入 `_tool_entries`，后续 `_update_tool_summary()` 读取时已是安全文本
- **与之前同类问题对比**: 2026-05-26 的 `ToolCallLog` 有 `_escape_markup()`，但独立 widget 重构时遗漏了该方法
- **涉及文件**: `src/vaxport/tui/app.py`

## 2026-05-27: POST-HOOK QC 虚构图表引用 + generate_chart 结果被截断

- **现象**:
  1. 用户提问"评估交叉污染风险，给出风险热力图"，LLM 第一次回答纯文字无图
  2. 追问"为什么没有生成图"后，POST-HOOK QC 输出虚构的 `heatmap_warehouse.png` 引用（文件不存在），且话题漂移到"仓库环境监控"
- **根因（三层）**:
  1. **QC 设计缺陷**：QC_PROMPT 要求"输出修正后的完整答案"但禁止调工具 → LLM 发现缺图但无法调用 `generate_chart`，只能编造
  2. **图表结果不可用**：`generate_chart` 返回 base64（~100K tokens），被 `truncate_tool_result`（6000 tokens 上限）截断，LLM 即使调用了也收不到可用数据
  3. **prompt 引导弱**：ANALYSIS_PROMPT 只说"可以继续调用图表生成"，LLM 不认为是必须的
- **修复**:
  1. **`generate_chart` 改为存盘返回路径**：PNG 保存到 `~/.vaxport/charts/chart_{type}_{timestamp}.png`，返回 `{"file_path": "..."}` 而非 `{"image_base64": "..."}`，路径 ~50 字符不触发截断，LLM 用 `![标题](路径)` 引用
  2. **`QC_PROMPT` 改为 inspection-only**：删除"请直接输出修正后的完整答案"，改为"**禁止输出修正后的答案，只输出问题清单**"
  3. **`_run_qc()` 改为追加式**：QC 发现问题时追加 `⚠️ 质检发现问题` 章节到原答案末尾，不替换原答案，杜绝幻觉覆盖正确答案
  4. **`ANALYSIS_PROMPT` 强化图表引导**：增加 5 种图表类型→场景映射表，明确要求"必须调用 generate_chart"
  5. **`PLAN_PROMPT` 增加可视化需求章节**：模板新增"五、可视化需求"表格，规划阶段就列出图表需求
- **涉及文件**: `src/vaxport/charts.py`, `src/vaxport/agent.py`, `src/vaxport/cli.py`

## 2026-05-27: 规划阶段 LLM 发现模糊参数但不主动询问 — 缺少交互式决策机制

- **现象**:
  1. 用户提问"评估交叉污染风险，给出风险热力图"，LLM 生成的热力图基于编造的评分（0.75~0.95），图表无意义
  2. LLM 在规划的"风险点"中已识别到"热力图需定义风险量化规则"，但没有主动暂停询问用户如何定义
  3. 用户期望：LLM 发现模糊参数时，列出方案选项（🥇→🥉 排序+解释），让用户选择后再执行
- **根因**: plan_confirm 机制是二元的（Enter 确认 / Esc 取消），没有"用户输入反馈完善计划"的能力。LLM 虽然被 prompt 引导列出风险点，但没有机制将模糊点转化为交互式选项
- **修复**:
  1. **`PLAN_PROMPT` 增加"七、待用户决策的关键事项"**：引导 LLM 列出模糊参数及排序方案（🥇🥈🥉），写清推荐理由/适用场景/预期结果
  2. **`ProgressCallbacks` 增加 `plan_feedback`**：在基类中增加空字符串属性，TUI 子类写入用户反馈
  3. **`_show_plan_for_confirm()` 检测决策项**：如果计划含"待用户决策"且非"无需用户决策"，显示不同提示（"输入选择后按 Enter，或直接 Enter 使用推荐方案"）
  4. **`on_chat_input_submitted()` 捕获反馈**：计划确认模式下，用户输入的非空文字作为决策反馈存入 `_plan_feedback`
  5. **`TUICallbacks.on_plan()` 传回反馈**：`event.wait()` 返回后将 `_plan_feedback` 复制到 `self.plan_feedback`
  6. **`agent.run()` 注入反馈**：plan 确认后，若 `callbacks.plan_feedback` 非空，追加到 plan_text 作为"用户决策"章节
- **涉及文件**: `src/vaxport/agent.py`, `src/vaxport/tui/app.py`

## 2026-05-27: 决策选择器交互升级 — 逐项交互 + 上下键选择

- **现象**: 第一版决策机制要求用户手动输入选择（如 "1A, 2B"），多个决策项一股脑展示，交互不便
- **用户需求**: 一个一个展示决策项，上下键选择方案，增加方案 D（自定义输入）
- **修复**:
  1. **`_parse_plan_decisions()`**: 正则解析计划文本中的 `**决策项 N: ...**` 和 🥇🥈🥉 选项，返回结构化列表
  2. **`DecisionPickerScreen(ModalScreen[list | None])`**: ModalScreen 弹窗，逐项展示决策，OptionList 提供 A/B/C/D 四个选项，D 触发 CustomInputModal
  3. **`CustomInputModal(ModalScreen[str | None])`**: 带 Input 的简单弹窗，Enter 提交自定义方案，Esc 取消回到选项列表
  4. **`_format_decision_feedback()`**: 将用户选择结果格式化为"用户决策"文本，注入 plan
  5. **`TUICallbacks.on_plan()`** 分叉：有决策项 → `_start_decision_picker()` 推 DecisionPickerScreen；无决策项 → 传统 Enter/Esc 确认
  6. **CSS**: 新增 `DecisionPickerScreen`、`#decision-container`、`#decision-title`、`#decision-question`、`#decision-list`、`CustomInputModal`、`#custom-input-container`、`#custom-input-title`、`#custom-input-field` 样式
- **涉及文件**: `src/vaxport/tui/app.py`, `src/vaxport/tui/style.tcss`

## 2026-05-27: 决策选择器出现空选项行 — 解析器未过滤空标签

- **现象**: 决策选择器在有些决策项中显示一个空行（可上下移动选中，按 Enter 进入下一问题），出现在 LLM 只写了 1 个方案时
- **根因**: LLM 输出格式 `- 🥇 : 描述文字`（方案名留空，只有一个冒号），`_parse_plan_decisions()` 解析出 `label=""`，OptionList 添加了一个只有 emoji+冒号的空选项
- **修复**: `_parse_plan_decisions()` 增加 `if not label: continue`，label 为空字符串或仅空白时跳过该选项
- **涉及文件**: `src/vaxport/tui/app.py`

## 2026-05-27: generate_chart 图表无数据 — 工具描述缺失 + 空数据静默通过 + 全 Agent 可用性

- **现象**: 
  1. 首次运行：用户提问"给出风险热力图"，报告纯文字无图 → `generate_chart` 只在 `report` Agent 中，查询被路由到 `compliance` Agent
  2. 修复后运行：LLM 调了 `generate_chart` 生成 heatmap + comparison，但两张 PNG 97.8% 白色像素——只有坐标轴和刻度，中间无数据图形。LLM 无感知，继续用 `![图示](路径)` 引用空白图
  3. 再修复后运行：成功生成有实际数据的热力图
- **根因（四层）**:
  1. **Agent 路由 + 工具缺失**: 查询含"交叉污染" → 匹配 `COMPLIANCE_KEYWORDS` → `compliance` Agent 没有 `generate_chart`（只在 `report` 中）
  2. **工具描述太简略**: description 仅 "data 为对应格式JSON"，LLM 不知道每种图表类型的键名和数据结构
  3. **绘图函数不够容错**: `_draw_heatmap` 只认 `matrix`/`xlabels`/`ylabels`，LLM 传 `data`/`columns`/`rows` 就匹配不到空数据
  4. **空数据静默生成空白图**: LLM 传入空 `matrix: []` 或空 `groups: {}` 时，matplotlib 创建空白坐标轴返回 `{"chart_type": ..., "file_path": ...}`（成功），LLM 以为图表已生成
- **修复**:
  1. **`generate_chart` 全 Agent 可用**: `Orchestrator.TOOL_FILTERS` 5 个 Agent 全部加入 `generate_chart`
  2. **工具描述补全 5 种格式**: description 列出 trend/control/pareto/heatmap/comparison 各类型的完整 JSON 示例
  3. **绘图函数容错**: `_draw_heatmap` 增加 `data`/`values`/`columns`/`cols`/`rows` 回退键名；`_draw_comparison` 增加 `categories`/`data` 回退
  4. **预校验拦截空白图**: 新增 `_validate_chart_data()` 在创建 figure 前校验核心数据非空，为空返回 `{"error": "xxx 图缺少数据: 请提供 {...} 格式"}`，LLM 可据此重试
- **涉及文件**: `src/vaxport/orchestrator.py`, `src/vaxport/tools.py`, `src/vaxport/cli.py`, `src/vaxport/charts.py`

## 2026-05-28: Markdown 图片路径被 LLM 改写为相对路径 → 图片不显示

- **现象**: AEFI 药物警戒信号检测报告，LLM 调了 `generate_chart`（3 张图均生成成功有数据），但报告中写成 `![...](./charts/chart_xxx.png)` 相对路径。TUI Markdown 从 CWD 解析相对路径找不到 `./charts/`，用户看到图片不显示。追问后 LLM 给方案分析但不重新调 `generate_chart`
- **根因**: `generate_chart` 返回 `{"file_path": "/home/sunny/.vaxport/charts/chart_xxx.png"}`（绝对路径），LLM 自作主张改写为 `./charts/chart_xxx.png`。LLM 习惯性"美化"路径，改了就不显示
- **修复**:
  1. **`generate_chart` 工具描述**: 增加 **重要** 标注 —— "返回的 file_path 是绝对路径，Markdown 引用时必须原样使用，禁止改为相对路径"
  2. **`ANALYSIS_PROMPT`**: 增加 "必须使用 generate_chart 返回结果中的 file_path 原样引用，禁止改为相对路径"
- **涉及文件**: `src/vaxport/cli.py`, `src/vaxport/agent.py`

## 2026-05-29: 流式输出 — 规划和分析阶段实时显示

### 背景
vaxport 输出是"整理好之后全部内容一下子出来"，与 Claude Code / OpenCode 的逐字流式体验差距大。用户期望看到连续工作的样子，而非等待后一次性显示。

### 分析
- Agent 的 ReAct 主循环已经在用 `stream=True`（`agent.py:620`），API 层已流式
- 但 TUI 的 `TUICallbacks.on_text_chunk()` 只累积 chunk 到 `_answer_parts`，不做任何 UI 更新
- 最终在 `_show_agent_result()` 一次性 `mount(Markdown(text))`
- 规划阶段的 `_generate_plan()` 用 `stream=False`，完全不流式
- **结论**：流式管道已铺好，就差 TUI 层打开水龙头

### Token 消耗
**零增长。** 流式改变的是传输方式（逐 token vs 整段），不改变生成内容。输入/输出 token 数完全一致。

### 修复

**1. ReAct 分析阶段流式（app.py TUICallbacks）**
- `on_text_chunk()`: 不再只累积，改为调用 `_flush()` 实时更新 live widget
- `_flush()`: 节流策略 — 每 150ms 或累积 20 chunks 更新一次 Markdown widget
- `_do_flush()`: 通过 `call_from_thread` 在 TUI 线程执行 `widget.update(content)` + `scroll_end`
- `_agent_worker()`: 在 orchestrator.run() 之前创建空的 Markdown widget 作为流式目标
- `_show_agent_result()`: 接受 callbacks 参数，流式完成后仅做最终更新（加 Agent 标签），不再重复挂载

**2. 规划阶段流式（agent.py + app.py）**
- `ProgressCallbacks`: 新增 `on_plan_chunk(text)` 方法
- `_generate_plan()`: `stream=False` → `stream=True`，逐 chunk 调用 `callbacks.on_plan_chunk()`
- `Agent.run()`: 将 callbacks 传入 `_generate_plan()`
- `TUICallbacks`: 新增 `_plan_widget` + `_plan_parts` + `_flush_plan()`，与 answer widget 独立
- `on_plan()`: 计划已流式显示时，仅追加确认提示，不重复挂载 Markdown

**3. SKIP_PLAN 残留清理**
- `_show_agent_result()`: 检测 plan widget 内容仅为 "SKIP_PLAN" 时自动清空

### 架构变化

```
之前:
  _generate_plan(stream=False) → 等完 → on_plan 挂载 Markdown
  → SQL 批量 → ReAct(stream=True 但 chunk 丢弃)
  → _show_agent_result mount(完整 Markdown)

之后:
  _generate_plan(stream=True) → plan widget 实时更新 → on_plan 确认提示
  → SQL 批量(工具日志实时) → ReAct(stream=True) → answer widget 实时更新
  → _show_agent_result 最终更新(加 Agent 标签)
```

### 涉及文件
- `src/vaxport/agent.py`: `ProgressCallbacks.on_plan_chunk`, `_generate_plan` 改为流式
- `src/vaxport/tui/app.py`: `TUICallbacks` 重写（+`_flush`/`_flush_plan`/`_live_widget`/`_plan_widget`），`_agent_worker` widget 创建，`_show_agent_result` 流式更新

## 2026-05-29: 补充基础工具 — 日期/统计/文件/环境

### 背景
LLM 生成文档时日期错误——它没有获取当前日期的工具。进一步审计发现 `statistics.py` 实现了完整的统计功能但从未注册为工具，加上文件读写和环境信息工具也缺失。

### 新增工具 (4+1)

| 工具 | 用途 | 安全限制 |
|------|------|----------|
| `get_current_time` | 返回 ISO 日期时间 + Unix 时间戳 | 无 |
| `run_statistics` | 7 种统计操作 (stats/cpk/trend/outlier/correlation/compare/control_limits) | 无 |
| `read_file` | 读取文件内容 | 限制在当前工作目录内，最多 50KB |
| `write_file` | 写入内容到文件 | 限制在当前工作目录内 |
| `get_env_info` | Python 版本/平台/cwd/数据库连接状态 | 无 |

### TOOL_FILTERS 更新
- 新增 `BASIC_TOOLS` 集合，4 个基础工具对所有 Agent 可见
- `run_statistics` 对 analyze_reporter 和 quality_supervision 可见
- `detect_anomaly` 对 analyze_reporter 可见（已有）
- 使用集合合并 `| BASIC_TOOLS` 避免重复维护

### 涉及文件
- `src/vaxport/cli.py`: 新增 `_register_basic_tools()`，`_register_phase3_tools()` 中注册 `run_statistics`
- `src/vaxport/orchestrator.py`: `BASIC_TOOLS` + `TOOL_FILTERS` 更新
- `src/vaxport/statistics.py`: 已有实现，无需修改

## 2026-05-29: query_* 工具支持范围查询 + 隐藏思考过程 + 取消回答流式

### 背景
1. `query_*` 工具只支持等值过滤，LLM 无法写 BETWEEN/范围条件
2. 规划通过后的 ReAct 思考过程（工具调用/思考文本）直接展示在对话区，内容杂乱
3. 正式回答流式展示太慢，用户要求取消流式，最终一次性渲染

### 修改

**1. 范围查询支持**
- `db.py` `execute_safe_select`: filters 值支持 `(operator, value)` 元组格式，支持 `>=`, `<=`, `>`, `<`, `!=`, `LIKE`, `ILIKE`
- `tools.py` `_register_table_tool`: 对日期/时间/数值类型列自动生成 `_from`/`_to` 参数
- `tools.py` handler: 检测 `_from`/`_to` 后缀参数，转换为 `(>=`, value)`/`(`<=`, value)` 过滤器

**2. 隐藏思考过程**
- `app.py` TUICallbacks: `on_tool_call`/`on_tool_result`/`on_thinking` 不再调用 `_add_to_tool_log`，仅更新状态栏

**3. 取消回答流式**
- `app.py` TUICallbacks: `on_text_chunk` 仅累积文本，不再调用 `_flush()`；最终由 `_show_agent_result` 一次性渲染到 stable widget

### 涉及文件
- `src/vaxport/db.py`: `execute_safe_select` 操作符支持
- `src/vaxport/tools.py`: `_register_table_tool` 范围参数 + handler 范围检测
- `src/vaxport/tui/app.py`: TUICallbacks 三个方法修改

## 2026-05-29: 计划确认后状态栏实时更新

### 背景
输出完成后，对话区底部仍显示 "[Enter] 确认执行 | [Esc] 取消" 和 "计划已确认，开始执行..." 等过期消息，状态栏没有实时反映执行阶段变化。

### 修改
- `_add_info` 返回 Static widget 引用，支持后续移除
- 计划确认后自动移除 "[Enter] 确认执行 | [Esc] 取消" 提示 widget
- "计划已确认，开始执行..." 改为写入状态栏 (`_set_agent_status`) 而非对话区 (`_add_info`)，执行完成后自动清除
- 仅在 `_plan_widget` 流式显示路径需要管理确认提示 widget，`_show_plan_for_confirm` 路径的提示嵌入 Markdown 中，无需单独移除

### 涉及文件
- `src/vaxport/tui/app.py`: `_add_info` 返回值修改，`on_chat_input_submitted` 状态栏替代，`on_plan` 确认提示移除，`_plan_confirm_widget` 初始化

## 2026-05-30: ReAct 思考文本与最终答案混在同一 widget，无法视觉区分

### 背景
用户提问后，问题下方出现 ReAct 中间回合的思考文本（如"让我先确认一下数据库中所有产品线的完整情况..."），这些文本和最终答案混在同一个 answer widget 中，以相同的样式（白色正文）显示，用户无法区分哪些是思考过程、哪些是最终答案。

### 根因
ReAct 循环中每次 LLM 调用（无论中间回合还是最终答案）都通过 `callbacks.on_text_chunk()` 流式输出到同一个 answer widget。中间回合的 `collected_content`（LLM 在调用工具前的推理文字）和最终答案没有分流。

### 修改
1. **`agent.py`**
   - `ProgressCallbacks` 新增 `on_thinking_text(text)` 回调（非流式，整段思考一次性交付）
   - `_single_llm_turn` 新增 `stream_content` 参数（默认 `True` 保持兼容），`False` 时只收集不流式输出
   - ReAct 循环改为 `stream_content=False`：中间回合 → `callbacks.on_thinking_text(collected_content)`，最终答案 → 20 字符分批模拟流式输出 `callbacks.on_text_chunk`

2. **`app.py`**
   - `TUICallbacks.on_thinking_text`：将思考文本按行格式化为 `- ` 无序列表，通过 `_show_thinking_text` 挂载到 `.thinking-content` 样式 widget
   - 新增 `_show_thinking_text` 方法：创建带 `classes="thinking-content"` 的 Markdown widget 展示思考文本

3. **`style.tcss`**
   - 新增 `.thinking-content` 样式：浅色文字 `#8888A0`（正文为 `#F8F8F2`）+ 左侧竖线边框 `#44475A` + 缩进 padding

### 效果
- 思考文本 → 浅色 + `- ` 无序编号 + 左侧缩进线，出现在问题下方、答案上方
- 最终答案 → 正常白色样式，流式输出到 answer widget
- 同时优化了 plan 阶段展示：plan widget 也用 `.thinking-content` 样式，`### 一、` 标题转为 `- **一、**` 无序列表

### 涉及文件
- `src/vaxport/agent.py`: `ProgressCallbacks` + `on_thinking_text`，`_single_llm_turn` + `stream_content` 参数，ReAct 循环路由逻辑
- `src/vaxport/tui/app.py`: `TUICallbacks.on_thinking_text`，`_show_thinking_text` 方法，plan widget `classes="thinking-content"`
- `src/vaxport/tui/style.tcss`: `.thinking-content` 选择器 + `MarkdownBlock` 子样式

## 2026-05-30: 双 MarkdownStream 架构 — 思考文本与答案彻底分流

### 背景
上一次修复（`on_thinking_text` + `mark_answer_start`）存在三个问题：
1. 思考文本先出现在 answer widget 中（非降级样式），之后才通过 `on_thinking_text` 进入 thinking widget —— 时序错误
2. answer widget 上方残留未降级展示的思考语句，最后才被 `_show_agent_result` 清理
3. `stream_content=False` 导致答案也无法流式输出

### 根因
所有 ReAct 文本（无论思考还是答案）共用同一个 answer widget 的 MarkdownStream。思考文本在流式阶段进入 answer widget，只能等回合结束后通过 `on_thinking_text` 补充到 thinking widget，期间两处同时展示、样式不一致。

### 修改
**架构变更：双 MarkdownStream —— 思考流 + 答案流，彻底分离**

1. **`agent.py`**
   - `ProgressCallbacks` 新增 `on_thinking_chunk(text)` 回调（思考流式块）
   - `_single_llm_turn`: 流式阶段改为调用 `callbacks.on_thinking_chunk(delta.content)`（不再调用 `on_text_chunk`）
   - ReAct 最终答案回合：将 `collected_content` 按 3 字符分批回放到 `callbacks.on_text_chunk`，实现答案流式展示

2. **`app.py`**
   - `TUICallbacks` 新增三个字段：`_thinking_widget`、`_thinking_queue`、`_thinking_stream`
   - `TUICallbacks.on_thinking_chunk(text)`: 将 chunk 放入 thinking queue
   - `App._setup_thinking_stream(callbacks, widget)`: 初始化思考 MarkdownStream
   - `App._run_thinking_stream(queue, stream)`: `@work` 异步 worker，消费 thinking queue
   - `_create_stream_widgets`: 同时创建 thinking widget（`.thinking-content`）+ answer widget
   - `_show_agent_result`: 最终答案展示后移除 thinking widget，只保留 clean answer

3. **`style.tcss`**（已有，复用）
   - `.thinking-content`: 浅色 `#8888A0` + 左侧竖线 `#44475A`

### 数据流
```
ReAct turn → on_thinking_chunk → thinking queue → thinking widget (.thinking-content)
                                ↓ (如果是最终答案)
                           on_text_chunk (3-char 回放) → answer queue → answer widget (正常)
最终: thinking widget 移除，只剩 answer widget
```

### 涉及文件
- `src/vaxport/agent.py`: `ProgressCallbacks.on_thinking_chunk`，`_single_llm_turn` 回调切换，ReAct 答案回放
- `src/vaxport/tui/app.py`: `TUICallbacks.on_thinking_chunk`，`_setup_thinking_stream`，`_run_thinking_stream`，`_create_stream_widgets` 双 widget 创建，`_show_agent_result` thinking widget 清理

---

## 修复 4: 答案双展示（thinking widget 架构缺陷）

**日期**: 2026-05-30（四轮迭代修复）

**现象**: 
- 查询完成后，相同答案内容在对话区出现两次
- 一条有左侧边框（`.thinking-content` 样式），一条无边框

**最终根因**:
双 widget 架构（thinking widget + answer widget）导致同一份 LLM 输出被展示两次：
1. 流式阶段通过 `on_thinking_chunk` → thinking widget
2. 最终答案通过 `on_text_chunk` 回放 → answer widget
3. `clear_thinking()` 从 worker 线程操作 widget（Textual 不允许跨线程）→ 清空失败
4. 即使改用哨兵机制，thinking 和 answer 两个 widget 的时序竞态无法根除

**最终修复（第四轮）**:
取消 thinking widget，统一使用单一 answer widget：
- `_single_llm_turn`: `on_thinking_chunk` → `on_text_chunk`，LLM 输出直接进 answer widget
- ReAct `else` 分支：删除答案回放和 `clear_thinking()` 调用
- 删除 `_setup_thinking_stream`、`_run_thinking_stream`、`_thinking_*` 字段
- `_create_stream_widgets` 只创建 `plan_w` + `answer_w`

**涉及文件**:
- `src/vaxport/agent.py`: 流式输出改用 `on_text_chunk`，简化 `else` 分支
- `src/vaxport/tui/app.py`: 删除 thinking widget 相关所有代码

---

## 修复 5: Ctrl+P 命令面板冲突导致上下键失效和 ScreenStackError 崩溃

**日期**: 2026-05-30

**现象**:
- Ctrl+P 打开的菜单中，上下键无法逐个选择选项
- 选择 Screenshot、Theme、Select model 等命令后立即崩溃退出
- 错误: `ScreenStackError: Can't pop screen; there must be at least one screen on the stack`

**根因**:
Textual 在 App 初始化时（`__init__` 中）检查 `ENABLE_COMMAND_PALETTE`（默认为 True）。
若已注册的 bindings 中没有 action 为 `command_palette` 或 `app.command_palette` 的绑定，
则自动添加 `Ctrl+P → command_palette`，且 `priority=True`。

VaxportApp 的 `BINDINGS` 已绑定 `Ctrl+P → show_model_picker`（自定义模型选择器），
但此绑定的 action 不是 `command_palette`，触发 Textual 自动添加第二个 `Ctrl+P` 绑定。

结果：Ctrl+P 同时触发 `show_model_picker` 和 `command_palette`，两个 Screen 竞争，
命令面板的 OptionList 失去焦点 → 上下键无法导航；
选中选项调用 `dismiss()` → `pop_screen()` 时 Screen 栈已损坏 → `ScreenStackError`.

**修复**:
1. `VaxportApp.ENABLE_COMMAND_PALETTE = False` — 禁止 Textual 自动添加 `Ctrl+P` 绑定
2. `_on_key` (已废弃) → `on_key` — 避免废弃 API 干扰事件传递

**涉及文件**:
- `src/vaxport/tui/app.py`: 新增 `ENABLE_COMMAND_PALETTE = False`，`_on_key` → `on_key`
- `src/vaxport/tui/style.tcss`: `.thinking-content`（复用）

---

## 功能: SSH 隧道支持（远程网络环境连接内网数据库）

**日期**: 2026-05-30

**场景**: 用户从非局域网环境（如外部网络）连接到内网 PostgreSQL 数据库，需要通过 SSH 跳板机建立端口转发隧道。

**实现**:

1. **Config 新增 `pg.ssh_tunnel` 配置项** (`config.py`):
   - `enabled`: 是否启用隧道（默认 False）
   - `jump_host`: SSH 跳板机地址，如 `"user@host"`
   - `jump_port`: SSH 端口（默认 22）
   - `db_host`: 数据库在跳板机后的主机名（如数据库和跳板机是同一台，填 `"localhost"`）
   - `db_port`: 数据库端口（默认 5432）
   - `local_port`: 本地转发端口（默认 5433，避免与本地 PG 端口冲突）

2. **Database 类新增隧道管理** (`db.py`):
   - `_start_tunnel()`: 启动 `ssh -N -L` 子进程，等待端口就绪后返回 `("localhost", local_port)`
   - `_stop_tunnel()`: 发送 SIGTERM 到隧道进程组，超时则 SIGKILL
   - `connect()`: `ssh_tunnel.enabled` 为 True 且未通过 overrides 覆盖 host 时，自动建立隧道
   - `close()`: 关闭连接池同时清理隧道（共享隧道除外）
   - `_tunnel_shared`: 标记隧道是否由 MultiDatabase 共享管理

3. **MultiDatabase 隧道共享** (`db.py`):
   - `connect_all()`: SSH 隧道建立一次，所有数据库连接通过 `localhost:local_port` 共享复用
   - `close_all()`: 在所有数据库关闭后手动清理共享隧道进程

4. **Config 文件已更新** (`~/.vaxport/config.yaml`):
   ```yaml
   pg:
     ssh_tunnel:
       enabled: true
       jump_host: "sunny@10.21.134.109"
       jump_port: 22
       db_host: "localhost"
       db_port: 5432
       local_port: 5433
   ```

**涉及文件**:
- `src/vaxport/config.py`: DEFAULT_CONFIG 新增 `ssh_tunnel` 节，新增 `ssh_tunnel_*` 属性
- `src/vaxport/db.py`: Database 新增 `_start_tunnel/_stop_tunnel/_tunnel_shared`，MultiDatabase 隧道共享

---

## 修复 6: 计划 Widget 残留导致"部分内容二次展示"

**日期**: 2026-05-30

**现象**:
- 查询"按产品类型分组统计批次"时，结果区域出现部分内容重复
- 不是完整重复，而是计划中描述的策略（如"按产品类型分组"）和最终答案的内容重叠

**根因**:
执行流程中有 THREE 个流式 widget：`plan_w`（计划）、`think_w`（ReAct 思考）、`answer_w`（答案）。
修复 4 只清空了 `think_w`，但 `plan_w` 在执行完成后仍然保留。
计划内容包含查询策略描述（如"查询 2024 年生产批次，按产品类型分组统计"），
而最终答案也包含相似的分析过程和结论，两者重叠 → 用户感知为"部分内容二次展示"。

**修复**:
`_show_agent_result` 中，不仅移除 SKIP_PLAN，对正常计划也在执行完成后调用 `update("")` 清空内容。

**涉及文件**:
- `src/vaxport/tui/app.py`: `_show_agent_result` → plan widget 执行完成后清空
- `~/.vaxport/config.yaml`: 用户配置文件已添加 SSH 隧道配置

---

## 修复 7: 0 条消息会话记录污染会话列表

**日期**: 2026-06-03

**现象**:
- GUI 左侧栏"会话历史"出现大量"0 条消息"的无效会话记录
- 每次 SSE 连接建立都会触发 `session.save()`，即使用户未发送任何消息

**根因**:
`Session._write()` 未检查消息列表是否为空，`sse.py` 中 SSE 流结束时无条件调用 `session.save()`，
导致空会话被持久化到磁盘。`list_sessions()` 也未过滤空会话。

**修复**:
1. `Session._write()` 增加空消息检查，`messages` 为空时直接 return
2. `Session.list_sessions()` 过滤 `message_count == 0` 的会话文件

**涉及文件**:
- `src/vaxport/session.py`: `_write()` 空消息守卫 + `list_sessions()` 过滤逻辑

---

## 修复 8: 图表生成功能未打通（GUI 右侧栏图表面板空白）

**日期**: 2026-06-03

**现象**:
- GUI 右侧栏"图表预览"面板始终显示占位符，无法展示实际图表
- 后端 `generate_chart` 工具正常生成 matplotlib 图片，但前端无法接收

**根因**:
图表数据从后端到前端的完整链路未打通：
1. Agent 没有 `on_chart` 回调机制
2. SSE 层未定义 `chart` 事件类型
3. 前端未处理 chart 事件
4. ChartPreview 组件未接入实际数据

**修复**:
1. `agent.py` 新增 `on_chart` 回调属性，工具结果处理中检测 chart 数据并触发回调
2. `sse.py` 实现 `on_chart()` 回调，将 matplotlib Figure 编码为 base64 PNG 并通过 SSE 发送 `chart` 事件
3. `sse.ts` 新增 `chart` 事件类型和 `onChart` 回调
4. `chatStore.ts` / `appStore.ts` 新增 `ChartItem` 接口和 charts 状态管理
5. `App.tsx` 接入 `onChart` 回调，将图表数据写入 store
6. `ChartPreview.tsx` 完整重写，从 store 读取图表数据并渲染 `<img>` 标签

**涉及文件**:
- `src/vaxport/agent.py`: `on_chart` 回调 + 图表检测逻辑
- `src/vaxport/api/sse.py`: `on_chart()` base64 编码 + SSE chart 事件
- `Vaxport-GUI/src/lib/sse.ts`: chart 事件类型 + onChart 回调
- `Vaxport-GUI/src/stores/chatStore.ts`: ChartItem + charts 状态
- `Vaxport-GUI/src/stores/appStore.ts`: ChartItem + charts 状态
- `Vaxport-GUI/src/App.tsx`: onChart 回调接入
- `Vaxport-GUI/src/components/panels/ChartPreview.tsx`: 完整重写

---

## 修复 9: TUI Ctrl+P 模型列表展示过多无关模型

**日期**: 2026-06-03

**现象**:
- TUI 中按 Ctrl+P 弹出的模型选择器展示 API 返回的所有模型（数十个），大部分不适用于当前场景

**根因**:
模型选择器直接展示 `/v1/models` API 返回的完整列表，未做过滤。

**修复**:
模型列表过滤为仅展示 4 个指定模型：`deepseek-v4-pro`、`deepseek-v4-flash`、`qwen3.7-max`、`qwen-max`。

**涉及文件**:
- `src/vaxport/tui/app.py`: 模型选择器过滤逻辑（约 line 1476-1478）

---

## 修复 10: Temperature 从全局改为按 Agent 分别设置

**日期**: 2026-06-03

**背景**:
不同 Agent 对 temperature 的需求差异大：任务分配需要 0.0（确定性路由），分析报告需要 0.3（一定创造性），
全局统一 temperature 无法满足精细化控制需求。

**修复**:
1. **后端**: `config.py` 将 `temperature` 改为 `agent_temperatures` dict，新增 `get_agent_temperature()` / `set_agent_temperature()` 方法
2. **后端**: `orchestrator.py` 初始化每个 Agent 时读取对应的 temperature 配置
3. **API**: `routes.py` 的 `TemperatureRequest` 新增 `agent_name` 字段，`/api/temperature` 端点支持按 Agent 设置
4. **TUI**: 重构 Ctrl+P 选择器，展示每个 Agent 的当前 temperature，支持独立调整
5. **GUI**: `ModelSettings.tsx` 展示 5 个 Agent 的独立 temperature 输入（上下箭头步进 0.1），附带推荐值说明
6. **GUI**: `api.ts` 的 `setTemperature()` 新增 `agentName` 参数

**涉及文件**:
- `src/vaxport/config.py`: `agent_temperatures` dict + getter/setter
- `src/vaxport/orchestrator.py`: 按 Agent 读取 temperature
- `src/vaxport/api/routes.py`: per-agent temperature 端点
- `src/vaxport/tui/app.py`: per-agent temperature UI
- `Vaxport-GUI/src/components/settings/ModelSettings.tsx`: per-agent temperature 输入
- `Vaxport-GUI/src/lib/api.ts`: setTemperature(agentName, temperature)

---

## 修复 11: 规划输出包含"确认以上计划后，我将立即执行"等多余文本

**日期**: 2026-06-03

**现象**:
- Agent 生成的执行计划末尾出现"确认以上计划后，我将立即执行"等对话式文本
- 影响计划的结构化展示

**根因**:
PLAN_PROMPT 中未明确禁止 LLM 输出确认性对话文本，LLM 习惯性地添加"等待用户确认"的礼貌性回复。

**修复**:
在 `PLAN_PROMPT` 中新增规则：禁止输出"确认后执行"等对话性文本，计划以最后一个章节结束。

**涉及文件**:
- `src/vaxport/agent.py`: PLAN_PROMPT 新增禁止规则（约 line 208-209）

---

## 修复 12: 相似调用检测误判合法批量处理

**日期**: 2026-06-03

**现象**:
- 用户查询"E. coli 灭活疫苗用了五个菌株的灭活验证数据"时，Agent 对 5 个菌株分别执行相同工具（不同参数），被误判为"重复调用"
- 触发 LLM 随机性缓解机制，阻止了合法的批量查询

**根因**:
原有的相似调用检测仅比较工具名和参数相似度，未区分"无意义重复"和"有意义的批量处理"。
五个菌株的查询虽然工具名相同，但参数（菌株名）各不相同，属于合法的批量操作。

**修复**:
1. 实现语义相似度计算：`_levenshtein_distance()`、`_string_similarity()`、`_value_similarity()`、`_args_similarity()`
2. 新增批量处理检测：`_is_diverse_batch()` — 计算近期同类调用的参数多样性
3. 多样性阈值（默认 0.6）：如果参数间平均相似度 < 0.6，判定为合法批量处理，不触发限制
4. 上下文注入：LLM 调用前注入历史调用摘要，引导 LLM 避免真正的重复

**涉及文件**:
- `src/vaxport/agent.py`: 5 个辅助函数 + 重写相似检测逻辑 + 上下文注入（lines 25-110, 1423-1443, 1601-1628）

---

## 修复 13: `_append_tool_results` 中 state 参数可能为 None

**日期**: 2026-06-03

**现象**:
- 特定条件下 `AttributeError: 'NoneType' object has no attribute 'xxx'`

**根因**:
`_append_tool_results` 方法的 `state` 参数默认值为 None，但方法内部未做 None 检查直接访问 state 属性。

**修复**:
1. 在 `_append_tool_results` 中所有访问 state 的位置增加 `if state` 守卫
2. 主 ReAct 循环调用时显式传入 state 参数

**涉及文件**:
- `src/vaxport/agent.py`: lines 788, 802, 834 (None 守卫) + line 1463 (传入 state)

---

## 修复 14: GUI 导出 Markdown 不包含图表图片

**日期**: 2026-06-03

**现象**:
- GUI 导出的 Markdown 文件中图片路径为绝对路径（`/Users/.../.vaxport/charts/chart_xxx.png`），在其他设备或分享后无法显示
- TUI 导出的 Markdown 包含 `images/` 子目录，图片可正常显示

**根因**:
GUI 导出逻辑直接调用 Tauri `writeTextFile` 写入 markdown 内容，未处理图片引用：
1. 没有复制 `~/.vaxport/charts/` 下的图片文件
2. 没有将绝对路径重写为相对路径

TUI 的 `_cmd_export()` 完整实现了图片复制 + 路径重写，GUI 侧缺失此逻辑。

**修复**:
1. 后端新增 `POST /api/export/markdown` 端点，复用 TUI 的导出逻辑：创建子目录、复制图表到 `images/`、重写路径为相对路径
2. 前端 `Message.tsx` 的 `handleExport` 改为调用后端端点，不再直接写文件
3. `api.ts` 新增 `exportMarkdown()` 方法

**涉及文件**:
- `src/vaxport/api/routes.py`: 新增 `ExportRequest` 模型 + `/api/export/markdown` 端点
- `Vaxport-GUI/src/lib/api.ts`: 新增 `exportMarkdown()` 方法
- `Vaxport-GUI/src/components/chat/Message.tsx`: `handleExport` 改为调用后端端点

---

## 修复 15: GUI 规划确认弹窗字体大小不一

**日期**: 2026-06-03

**现象**:
- PlanConfirm 弹窗中的规划内容字体大小参差不齐，标题和正文大小区分不清晰

**根因**:
1. PlanConfirm 容器使用 `text-sm`（14px）作为基准，所有标题 em 单位相对 14px 计算，导致标题偏小
2. CSS 仅定义 h1-h3 的样式，h4-h6 无规则，回退到浏览器默认大小（与容器不一致）
3. 标题层级间的尺寸差距不够明显（1.5em / 1.25em / 1.1em）

**修复**:
1. PlanConfirm 容器移除 `text-sm`，改用默认 16px 基准
2. `index.css` 新增 `.markdown-content` 基准规则（`font-size: 1em; line-height: 1.6`）
3. 扩展标题覆盖 h1-h6，调整尺寸层级：h1=1.6em / h2=1.35em / h3=1.15em / h4-h6=1em
4. 增加 `line-height: 1.3` 确保标题行距合理

**涉及文件**:
- `Vaxport-GUI/src/components/chat/PlanConfirm.tsx`: 移除 `text-sm`
- `Vaxport-GUI/src/index.css`: 重写 `.markdown-content` 标题层级

---

## 修复 16: GUI 对比分析输出缺少排名表格

**日期**: 2026-06-03

**现象**:
- TUI 输出的对比分析报告包含完整的对比表格（含排名列），图表与文字结合好
- GUI 输出的同类报告对比表格较少，表格内无排序/排名列，不够直观

**根因**:
LLM 输出存在随机性，两次独立运行的结构可能不同。`ANALYSIS_PROMPT` 中未明确要求对比分析时必须使用排名表格。

**修复**:
在 `ANALYSIS_PROMPT` 中新增"对比分析输出规范"：
- 多对象对比时必须使用 Markdown 表格，禁止纯文字叙述
- 表格必须包含排名列或按关键指标排序
- 每个对比维度各一张表 + 一张图
- 结论章节使用综合排名表汇总所有维度

**涉及文件**:
- `src/vaxport/agent.py`: `ANALYSIS_PROMPT` 新增"对比分析输出规范"段落

---

## 修复 17: TUI/GUI 设置不持久化（每次启动回到默认值）

**日期**: 2026-06-03

**现象**:
- TUI 和 GUI 中设置大模型、temperature 等参数后，下次启动全部回到默认值
- 用户每次使用都需要重新配置，不符合使用习惯

**根因**:

**TUI**：
- 全局模型切换（Ctrl+P 选择 global 模型）只调用了 `_llm.set_model()`，未持久化到 config.yaml
- per-agent 模型选择调用了 `set_agent_model()` 但未调用 `save()`

**GUI**：
- `ModelSettings.tsx` 所有设置项使用硬编码 `useState` 默认值（backend="aliyun"、model="deepseek-v4-pro"、apiKey="sk-0abc****defg" 等）
- 组件挂载时只加载了 `temperatures`，未加载 backend/model/apiKey/baseUrl/ollamaUrl/ollamaModel
- 用户修改 backend/model/apiKey/baseUrl 等不写回后端
- 只有 temperature 会调用 `api.setTemperature()` 持久化

**修复**:

**后端**：
- 新增 `POST /api/config/update` 端点，接受部分更新字段（api_key/base_url/ollama_url/ollama_model/backend/model/agent_model/auto_plan/plan_confirm/auto_qc），写入 config.yaml

**TUI**：
- 全局模型选择后增加 `_cfg.set("agent", "primary_backend", backend)` + `_cfg.set("api", "aliyun_model", model_id)` + `_cfg.save()`
- per-agent 模型选择后 `set_agent_model()` 内部已有 `save()`，无需额外处理

**GUI**：
- `api.ts` 新增 `updateConfig()` 方法
- `ModelSettings.tsx` 完全重写：
  - `useEffect` 挂载时从 `api.getConfig()` 加载真实配置填充所有 state
  - backend/model 变更立即调用 `api.updateConfig()`
  - apiKey 使用 blur 事件保存（避免每次按键都发请求）
  - baseUrl/ollamaUrl/ollamaModel 使用 800ms debounce 保存
  - per-agent model select 变更时调用 `api.updateConfig({ agent_model: {...} })`
  - auto_plan/plan_confirm/auto_qc 变更时调用 `api.updateConfig()`

**涉及文件**:
- `src/vaxport/api/routes.py`: 新增 `ConfigUpdateRequest` 模型 + `/api/config/update` 端点
- `src/vaxport/tui/app.py`: 全局模型选择持久化（line 484-502）
- `Vaxport-GUI/src/lib/api.ts`: 新增 `updateConfig()` 方法
- `Vaxport-GUI/src/components/settings/ModelSettings.tsx`: 完全重写，加载+持久化所有设置

---

## 修复 18: 规划弹窗字体过大 + 输出章节数量硬编码 + TUI 重复内容 + GUI 图片不显示

**日期**: 2026-06-03

**现象**:
1. PlanConfirm 弹窗中"四、输出章节"内容字体过大，与其他正文不协调
2. 每次规划输出章节数固定为 4 章，无法根据任务复杂度调整
3. TUI 交互页面内容结果出现两次，但导出 markdown 正常（仅一次）
4. GUI 交互页面和导出 markdown 中图片不可见，但右侧栏图表面板可正常浏览

**根因**:

**问题 1**：PlanConfirm 使用 `markdown-content` 类，其中 h3 为 1.15em（16px × 1.15 = 18.4px），视觉上偏大

**问题 2**：`PLAN_PROMPT` 中"四、输出章节"模板示例给出 `一、二、...`，LLM 解释为固定 4 章

**问题 3**：`_show_agent_result` 在流式完成后调用 `answer_widget.update(display_text)`，Textual Markdown 的 `update()` 可能导致流式内容被重复渲染

**问题 4**：LLM 生成的图片引用为纯文件名（如 `chart_xxx.png`）而非完整路径（如 `/Users/.../.vaxport/charts/chart_xxx.png`），`resolveImageSrc` 仅处理含 `.vaxport` 的完整路径，无法解析纯文件名

**修复**:

**问题 1**：PlanConfirm 改用独立 CSS 类 `plan-content`，所有标题统一 1em 字体

**问题 2**：`PLAN_PROMPT` 改为"根据任务复杂度确定章节数量，简单任务 2-3 章，复杂分析 5-7 章或更多"

**问题 3**：`_show_agent_result` 中增加判断 — 如果流式内容与 answer 一致，仅追加 agent 标签 widget，不再调用 `answer_widget.update()` 避免重复渲染

**问题 4**：
- `Message.tsx` 和 `StreamingBlock.tsx` 的 `resolveImageSrc` 增加纯文件名分支：`/api/files/charts/{filename}`
- 导出端点增加纯文件名处理：从 `~/.vaxport/charts/` 查找并复制

**涉及文件**:
- `Vaxport-GUI/src/index.css`: 新增 `.plan-content` 样式
- `Vaxport-GUI/src/components/chat/PlanConfirm.tsx`: 使用 `plan-content` 类
- `src/vaxport/agent.py`: `PLAN_PROMPT` 章节数量说明
- `src/vaxport/tui/app.py`: `_show_agent_result` 去重逻辑
- `Vaxport-GUI/src/components/chat/Message.tsx`: `resolveImageSrc` 纯文件名处理
- `Vaxport-GUI/src/components/chat/StreamingBlock.tsx`: `resolveImageSrc` 纯文件名处理
- `src/vaxport/api/routes.py`: 导出端点相对路径处理

---

## 修复 19: 计划内容标题字体过大（补充修复 18） + EAR 反馈统计始终为 0

**日期**: 2026-06-03

### 问题 A: 计划内容字体修复不完整

**现象**: 修复 18 仅处理了 PlanConfirm 弹窗的计划字体，但流式展示（StreamingBlock）和聊天消息（Message）中的计划内容仍然显示大号标题字体。

**根因**: 计划内容在三个位置渲染，修复 18 只改了 `PlanConfirm.tsx`：
1. `PlanConfirm.tsx` — 确认弹窗 ✅ 已修复
2. `StreamingBlock.tsx` — 流式展示 ❌ 未修复
3. `Message.tsx` — 计划作为聊天消息插入 ❌ 未修复

LLM 输出的计划内容如 `## 一、疫苗批签发概况分析` 为 h2 标题，在 `.markdown-content` 下渲染为 `1.35em`，而非正文大小。

**修复**:
- `StreamingBlock.tsx` 第 110 行：`markdown-content` → `markdown-content plan-content`
- `Message.tsx` 第 117 行：为计划消息类型增加 `plan-content` 类
- `chat.ts`：`messageType` 增加 `"plan"` 类型
- `App.tsx`：计划消息设置 `messageType: "plan"`
- `index.css`：新增 `.markdown-content.plan-content h1/h2/h3 { font-size: 1em; }` 高优先级规则，确保无论 CSS 层叠顺序如何都能覆盖

**涉及文件**:
- `Vaxport-GUI/src/components/chat/StreamingBlock.tsx`
- `Vaxport-GUI/src/components/chat/Message.tsx`
- `Vaxport-GUI/src/types/chat.ts`
- `Vaxport-GUI/src/App.tsx`
- `Vaxport-GUI/src/index.css`

### 问题 B: EAR 反馈统计始终为 0

**现象**: 前端 EAR 面板中"用户反馈"统计（总反馈数/显式反馈/满意/不满意）始终为 0。

**根因**: 数据链路断裂：
1. `orchestrator.py:648` 在 result 中放入 `task_id` ✅
2. `sse.py:291-303` 构造 `answer` SSE 事件时，**未将 `task_id` 传给前端** ❌
3. 前端 `App.tsx` 解 `data.task_id` 为空 → `finalizeAnswer` 收到空字符串
4. `Message.tsx:159` 判断 `message.taskId` 为空 → 👍👎 反馈按钮不渲染
5. 用户永远看不到反馈按钮 → 无法提交反馈 → 统计永远为 0

**修复**: `sse.py` answer 事件 data 中增加 `"task_id": result.get("task_id", "")`

**涉及文件**:
- `src/vaxport/api/sse.py`

---

## 修复 20: 新机器首次安装 DMG 后设置保存失败（"保存失败，请重试"）

**日期**: 2026-06-05

**现象**: 新机器（ARM64 macOS）首次安装 vaxport.dmg 后，设置页中数据库和模型参数无法保存，点击保存后显示"保存失败，请重试"。`~/.vaxport/config.yaml` 存在但所有值为空。开发者本机反复安装均正常，因为 `.vaxport/config.yaml` 已存在且包含有效值。

**根因**: 三个 bug 叠加导致 API 服务完全无法启动，8931 端口从未监听。

**崩溃链**:
```
create_multi_database() → connect_all()
  ↓ tunnel_db 未初始化 (db.py:449) → UnboundLocalError
  ↓ 被 cli.py:66 except 捕获
create_database() → psycopg2 连接失败（新机器无 PostgreSQL）
  ↓ 被 cli.py:71 except 捕获
ui.print_warning("数据库连接失败: ...")  ← 输出中文触发 rich
  ↓ rich._unicode_data.unicode17-0-0 未打包 → ModuleNotFoundError
  ↓ 未被捕获，直接传播到 FastAPI lifespan
Application startup failed. Exiting.
```

**问题 A: PyInstaller 打包遗漏 rich._unicode_data 模块（致命）**

`rich/_unicode_data/__init__.py:90` 使用 `importlib.import_module()` 动态加载 `unicode17-0-0.py` 等版本数据文件，PyInstaller 的静态分析完全无法追踪这种动态导入。任何中文文本输出（`rich.console.print` 计算字符宽度时触发）都会导致 `ModuleNotFoundError` 崩溃。

**问题 B: db.py connect_all() 中 tunnel_db 变量未初始化**

`connect_all()` 中 `tunnel_db` 仅在 `ssh_tunnel_enabled` 为 True 时赋值，但循环体内 `cli.py:449` 无条件引用 `if tunnel_db and tunnel_db._tunnel_process`，当 SSH 隧道未启用时触发 `UnboundLocalError`。

**问题 C: cli.py DB 失败路径缺少防御性异常处理**

DB 连接失败后调用 `ui.print_warning()` 输出中文警告。即使 rich 修好了，如果该调用因其他原因失败，异常会穿透 `except` 块传播到 FastAPI lifespan，导致 API 服务无法启动。

**修复**:

**问题 A**: `vaxport.spec` 的 `hiddenimports` 从 19 个扩展到 48 个，新增：
- `rich._unicode_data` + `_versions` + 6 个 unicode 版本数据模块（unicode13-0-0 至 unicode17-0-0）
- `sqlparse.sql`、`sqlparse.tokens`、`sqlparse.keywords`（子模块遗漏）
- `sse_starlette`、`sse_starlette.sse`（SSE 流式输出）
- `textual` 子模块（`_xterm_parser`、`widgets._option_list`、`binding`、`command`、`containers`、`drivers.linux_driver`、`keys`、`message`、`screen`）
- `rich` 子模块显式声明（`box`、`console`、`panel`、`rule`、`text`）
- `psycopg2` 子模块（`extras`、`pool`、`sql`）
- `matplotlib` 子模块（`font_manager`、`pyplot`、`ticker`）
- `uvicorn`、`starlette`、`fastapi`、`fastapi.middleware.cors`、`fastapi.responses` 显式声明
- `datas` 新增 `tui/style.tcss`（textual CSS 数据文件）

**问题 B**: `db.py:438` 在循环前初始化 `tunnel_db = None`

**问题 C**: `cli.py` 的 `setup()` 中：
- 在 try 块前显式设置 `self.db = None` 和 `self.mdb = None`
- `ui.print_warning()` 调用包裹在 `try/except: pass` 中，确保 UI 输出失败不阻止启动

**验证**: 清空 `config.yaml` 模拟新机器环境，新构建的 sidecar 正常启动，8931 端口监听成功，`pg_status: "未连接"` 优雅降级。

**涉及文件**:
- `vaxport.spec`: hiddenimports 扩展 + datas 添加 style.tcss
- `src/vaxport/db.py:438`: tunnel_db 初始化
- `src/vaxport/cli.py:60-75`: DB 失败路径防御性异常处理

---

## 修复 21: 旧 sidecar 残留、Orchestrator LLM 引用失效、Config 浅拷贝污染

**日期**: 2026-06-05

**现象**: 修复 20 解决后，新机器首次安装 DMG 仍存在多个问题：
1. 卸载旧 DMG 后重新安装，设置页自动加载旧的 API Key 和数据库配置（看似"信息泄露"）
2. 模型下拉列表为空，`/api/models` 返回 `{"models": []}`
3. 设置保存成功后，对话时报"没有可用的LLM后端，请先配置API key或本地模型"

**根因**: 四个独立 bug 叠加。

**问题 A: 旧 sidecar 进程残留（导致"信息泄露"假象）**

卸载旧 DMG 时 Tauri 未 kill sidecar 进程。新 DMG 安装后启动 sidecar 失败（端口 8931 被旧进程占用），但前端未报错，仍连接旧 sidecar。用户看到的"自动加载"的旧配置实际来自内存中的旧进程，而非文件。

**问题 B: Orchestrator LLM 引用未更新**

`_reinit_after_config_change()` 中 `app.orchestrator.llm_client = app.llm` 只是给 orchestrator 创建了一个无用的新属性。orchestrator 的 `self._llm` 和各 Agent 的 `self.llm` 仍指向旧的 LLM 客户端。多次 config 更新后旧客户端状态不一致，chat 时报"没有可用的LLM后端"。

**问题 C: Config 浅拷贝污染全局状态**

`Config.__init__` 中 `self._data = DEFAULT_CONFIG.copy()` 是浅拷贝，嵌套的 dict/list 仍指向全局 `DEFAULT_CONFIG` 的引用。多次 `load_config()` 后全局默认值被污染。

**问题 D: 模型列表为空（PyInstaller 环境下 OpenAI SDK 异常）**

OpenAI SDK 的 `client.models.list()` 在 PyInstaller 打包环境中可能静默失败（httpx/certifi 兼容问题）。原代码的 fallback 依赖 `info.get("model")`，但 `aliyun_model` 为空时返回空字符串，导致 models 列表为空。

**修复**:

**问题 A**: `lib.rs` 的 `start_backend()` 中新增 `kill_old_sidecar_on_port()` 函数，启动前检查端口 8931 是否被占用，如果是则 kill 旧进程（macOS 平台使用 `lsof -ti:8931` + `kill -9`）。

**问题 B**: `orchestrator.py` 新增 `set_llm_client()` 方法，同步更新 `self._llm` 和所有 Agent 的 `self.llm`。`routes.py` 中 `app.orchestrator.llm_client = app.llm` → `app.orchestrator.set_llm_client(app.llm)`。

**问题 C**: `config.py` 中 `DEFAULT_CONFIG.copy()` → `copy.deepcopy(DEFAULT_CONFIG)`，新增 `import copy`。

**问题 D**: `llm/__init__.py` 的 `list_models()` 新增三层 fallback：
1. OpenAI SDK `client.models.list()`
2. httpx 直接调用 `/v1/models` API（绕过 OpenAI SDK，解决 PyInstaller 兼容问题）
3. 静态常用模型列表（qwen3-max, qwen3-plus 等）

`routes.py` 的 `/api/models` 路由和 `_reinit_after_config_change()` 新增文件日志，写入 `~/.vaxport/api.log`，便于诊断。

**验证**: 新机器清空 `~/.vaxport`，安装最新 DMG，手动填入 API Key 和数据库配置，保存成功，模型下拉显示 211 个模型，对话正常输出。

**涉及文件**:
- `Vaxport-GUI/src-tauri/src/lib.rs`: kill_old_sidecar_on_port() 函数
- `src/vaxport/orchestrator.py:789-794`: set_llm_client() 方法
- `src/vaxport/api/routes.py:417`: app.orchestrator.set_llm_client(app.llm)
- `src/vaxport/config.py:3`: import copy
- `src/vaxport/config.py:59`: copy.deepcopy(DEFAULT_CONFIG)
- `src/vaxport/llm/__init__.py:118-168`: list_models() 三层 fallback
- `src/vaxport/api/routes.py:153-180`: /api/models 文件日志
- `src/vaxport/api/routes.py:423-430`: _reinit_after_config_change 文件日志

---

## 2026-06-08: SKILL 一致性问题 — per-section 限图机制 + Deep Research + Semantic Memory

### 背景

同一问题+同一SKILL，H21 测试 Run1 生成 9 张图（正常），Run2 生成 32+ 张图直到崩溃（20分钟卡死）。根因是 SKILL 只约束"做什么"（分析框架）但缺乏"做多少"（数据范围/分析深度/图表数量）的约束，导致 Agent 在不同运行中进入不同程度的图表循环发散。

### 根因分析

三层原因叠加：

1. **SKILL 缺乏收敛约束**: 偏差分析 SKILL 的"必须生成的图表"只有"最少"没有"最多"，Agent 可以无限细分维度生成图表
2. **无 per-section 限图机制**: GuardRails 只有全局步数预算（max_total_steps=20）和死循环检测，无法追踪每个分析步骤的图表数量
3. **串行 ReAct 无结构约束**: Agent 逐条查表，每轮发现"新角度"就生成新图表，没有全局视野感知"够了"

### 修复方案：三层协同约束

**第一层：SKILL per-section 收敛约束（方案 B）**

generate_chart 工具新增 `section` 参数（当前分析步骤名称），GuardRails 按 section 聚合计数图表数量：

```
generate_chart(data="...", chart_type="pareto", section="帕累托分析")
                    ↓
StepRecord(tool_name="generate_chart", section="帕累托分析")
                    ↓
GuardRails.monitor_trajectory() → Counter(section) → per-section 上限检查
                    ↓
章节"帕累托分析"已生成6张图 > 5 → break_loop: "请停止为该章节生成新图表"
```

- 每章节最多 5 张图表（`count > max_charts_per_section` 触发）
- section 为空时不计入 per-section，只计入全局 15 张兜底
- 3 个 SKILL.md 增加 section 参数映射（第一步→"偏差总览"、第二步→"帕累托分析"等）

**第二层：Deep Research 结构约束**

将串行 ReAct 逐条查表改为三阶段流水线：
- scan: auto_plan 增强，输出结构化数据定位计划
- 聚合采集: 并发执行 GROUP BY + COUNT/AVG SQL，返回 5 行摘要而非 5000 行原始数据
- 跨表综合: Agent 基于摘要做判断，按需回查缓存详细数据

结构上限制 Agent 分析维度——收到 5 行摘要就不可能对 5000 行数据逐条展开。

**第三层：GuardRails 全局兜底**

保留全局 15 张图表上限作为安全兜底，per-section 限图在全局之前检查。

### 代码改动

**1. StepRecord 增加 section 字段**

`src/vaxport/ear/guard_rails.py` StepRecord dataclass:

```python
@dataclass
class StepRecord:
    tool_name: str
    arguments: dict
    success: bool
    token_usage: int = 0
    timestamp: float = field(default_factory=lambda: __import__("time").time())
    section: str = ""  # generate_chart 的章节归属
```

**2. GuardRails per-section 限图逻辑**

`src/vaxport/ear/guard_rails.py` GuardRails class:

```python
def __init__(self, ..., max_charts_per_section: int = 5):
    self.max_charts_per_section = max_charts_per_section

def monitor_trajectory(self, history):
    # 1.5 per-section 限图（在全局15张之前检查）
    chart_sections = Counter(s.section for s in history if s.tool_name == "generate_chart" and s.section)
    for section, count in chart_sections.items():
        if count > self.max_charts_per_section:
            return RegulationAction(action="break_loop", message=f"章节'{section}'已生成{count}张图表...")
    # 1.6 全局15张兜底（保留原有逻辑）
```

**3. generate_chart section 参数**

`src/vaxport/cli.py` generate_chart 注册:

```python
parameters={
    "data": {...}, "chart_type": {...}, "options": {...},
    "section": {"type": "string", "description": "当前分析步骤名称，如'偏差总览'、'帕累托分析'..."},
},
required=["data", "chart_type"],  # section 为可选
```

**4. agent.py StepRecord 构造提取 section**

```python
section = arguments.get("section", "") if isinstance(arguments, dict) else ""
self._trajectory_history.append(StepRecord(tool_name=..., arguments=..., success=..., section=section))
```

**5. SKILL.md section 映射**

每个 SKILL 的"必须生成的图表"章节增加 section 参数说明：

```markdown
调用 generate_chart 时必须传入 **section** 参数，值为当前分析步骤名称：
- 第一步"偏差总览" → section="偏差总览"
- 第二步"帕累托分析" → section="帕累托分析"
...
每个 section 最多 5 张图表。
```

**6. Database.execute_simple 新增**

`src/vaxport/db.py` 新增 DDL/INSERT 执行方法，解决 SemanticMemory 和 documents.py 的 RAG schema 创建问题：

```python
def execute_simple(self, query_template: str, params: tuple = (), timeout_ms: int = 60000) -> None:
    """执行 DDL/INSERT 等无返回行的语句"""
    conn = self._pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = %s", (str(timeout_ms),))
            cur.execute(query_template, params)
        conn.commit()
    finally:
        self._pool.putconn(conn)
```

注意：必须显式 `conn.commit()`，否则 DDL 在连接归还连接池后事务回滚，表实际不会被创建。

**7. agent.py logging 修复**

agent.py 缺少 `import logging` 和 `logger = logging.getLogger(__name__)`，导致 `_try_deep_research()` 中 `logger.info/warning` 抛出 NameError。

### 伴随 Bug 修复

- **pgvector 不可用**: PostgreSQL 未安装 vector 扩展时，SemanticMemory 优雅降级（返回空字符串，不影响主流程），日志打印 "RAG schema 创建失败" 为预期行为
- **Deep Research SQL 生成问题**: LLM 生成的 plan_text 中包含中文描述（"或"、"与"）导致聚合 SQL 语法错误，DeepResearchCollector 的 build_aggregate_sql 已按英文逻辑处理，但 LLM 生成的 table 名仍可能包含中文连接词——目前通过 execute_query 错误容忍处理

### 涉及文件

- `src/vaxport/ear/guard_rails.py`: StepRecord section 字段 + per-section 限图逻辑
- `src/vaxport/cli.py`: generate_chart section 参数注册
- `src/vaxport/agent.py`: logging 导入 + StepRecord section 提取
- `src/vaxport/db.py`: execute_simple 方法新增
- `src/vaxport/skills/deviation-analysis/skill.md`: section 映射 + 收敛约束
- `src/vaxport/skills/process-capability/skill.md`: section 映射 + 收敛约束
- `src/vaxport/skills/stability-assessment/skill.md`: section 映射 + 收敛约束
- `src/vaxport/deep_research.py`: Deep Research 三阶段流水线（新增）
- `src/vaxport/semantic_memory.py`: Semantic Memory 语义召回层（新增）
- `src/vaxport/orchestrator.py`: Deep Research + Semantic Memory 集成
- `src/vaxport/tools.py`: deep_research_collect 工具注册
- `tests/test_guardrails_section.py`: per-section 限图单元测试（8 cases）
- `tests/test_deep_research.py`: Deep Research 单元测试（22 cases）
- `tests/test_semantic_memory.py`: Semantic Memory 单元测试（11 cases）

---

## 2026-06-09: pgvector 安装 + 权限配置 + execute_simple commit 修复

### 背景

v1.4.0 的 SemanticMemory 和 documents.py 的 RAG 功能依赖 pgvector 扩展。初始测试环境 PostgreSQL 18.4 (Homebrew) 未安装 pgvector，导致所有语义检索功能降级（返回空字符串不阻断主流程）。

### pgvector 安装

```bash
# macOS (Homebrew)
brew install pgvector

# Ubuntu/Debian — 从源码编译
git clone --branch v0.8.2 https://github.com/pgvector/pgvector.git
cd pgvector && make && sudo make install
```

在 myappdb 中启用扩展（需要 postgres 超级用户）：

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

### vaxport_rag 权限配置

vlm_reader 对业务 schema（analog_*）保持只读，仅对 vaxport_rag schema 授权写入：

```sql
-- 创建语义记忆存储 schema
CREATE SCHEMA IF NOT EXISTS vaxport_rag;
GRANT USAGE ON SCHEMA vaxport_rag TO vlm_reader;
GRANT CREATE ON SCHEMA vaxport_rag TO vlm_reader;
GRANT ALL ON ALL TABLES IN SCHEMA vaxport_rag TO vlm_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA vaxport_rag
    GRANT ALL ON TABLES TO vlm_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA vaxport_rag
    GRANT ALL ON SEQUENCES TO vlm_reader;
GRANT CREATE ON DATABASE myappdb TO vlm_reader;
```

权限模型验证：
- vlm_reader INSERT vaxport_rag.analysis_cases: ✅ 成功
- vlm_reader INSERT analog_quality.deviations: ❌ InsufficientPrivilege（正确阻止）
- pgvector cosine similarity: 0.915（语义搜索正常工作）

### execute_simple commit 修复

原始 `execute_simple` 使用 `self.cursor()` 上下文管理器，DDL 执行后连接归还连接池但事务未提交，导致表实际不被创建。

修复：改为手动获取连接，执行后显式 `conn.commit()`：

```python
def execute_simple(self, query_template: str, params: tuple = (), timeout_ms: int = 60000) -> None:
    """执行 DDL/INSERT 等无返回行的语句"""
    conn = self._pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = %s", (str(timeout_ms),))
            cur.execute(query_template, params)
        conn.commit()
    finally:
        self._pool.putconn(conn)
```

### 涉及文件

- `src/vaxport/db.py`: execute_simple 方法（conn.commit 修复）
- `scripts/generate_quality_supplement.py`: 6张新表数据补充脚本
- PostgreSQL myappdb: pgvector 扩展安装 + vaxport_rag schema 权限授权

---

## v1.4.0 数据补充与 SKILL 扩展

### 新增 6 张质量管理表（analog_quality schema）

运行 `scripts/generate_quality_supplement.py` 创建以下表：

| 表名 | 记录数 | 用途 |
|------|--------|------|
| `oos_records` | 40 | OOS 超标记录（实验室55%/生产45%） |
| `oot_records` | 20 | OOT 超趋势记录（alert/action） |
| `change_control_records` | 30 | 变更控制记录 |
| `change_control_risk_assessment` | 20 | FMEA 风险评估 |
| `cleaning_validation_results` | 30 | 清洁验证结果 |
| `cleaning_validation_limits` | 12 | 清洁验证限度标准 |

**vlm_reader 权限**：新表自动继承 SELECT 权限，与现有 analog_quality 表一致。

### 新增 5 个领域 SKILL

| SKILL | 核心法规/方法 | 测试题 |
|-------|-------------|--------|
| `oos-oot-investigation` | FDA OOS指南(2006) + Phase I/II + WE/Nelson规则 | H41 |
| `cleaning-validation` | PDE/ADE/MACO + PDA TR29 + 最差产品选择 | H42 |
| `change-control-assessment` | ICH Q9 + FMEA(S×P×D) + RPN>100强制缓解 | H43 |
| `cold-chain-assessment` | MKT(Arrhenius) + WHO GDP + 稳定性对照 | H44 |
| `trend-spc-warning` | WE 8条规则 + Nelson补充 + 控制图(I-MR) | H45 |

**测试结果**（5题×3次，2026-06-09）：
- SKILL要素命中率：100%（5/5 题全部命中关键法规和方法论）
- 结构一致性：100%（3次运行输出相同分析框架）
- 平均耗时：H41=435s, H42=303s, H43=351s, H44=896s, H45=457s

### 数据生成脚本使用

```bash
# 补充质量管理数据（幂等，可重复运行）
PGPASSWORD=your_password python scripts/generate_quality_supplement.py --password your_password

# 验证数据
PGPASSWORD=your_password psql -h localhost -U postgres -d myappdb \
  -c "SELECT count(*) FROM analog_quality.oos_records"
```
