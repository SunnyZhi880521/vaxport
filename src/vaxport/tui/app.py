"""
vaxport textual TUI — Toad 风格
Dracula 主题 + Markdown widget 原生渲染 + Footer 快捷键
"""

import asyncio
import json
import os
import re
import sys
import threading
from pathlib import Path

# ── Kitty IME 补丁（必须在 driver 初始化之前执行）──────────
# Ghostty/Kitty 终端用 Kitty 键盘协议发送 CJK IME 组合文本，
# text 字段含多个 Unicode 码点（冒号分隔如 "20320:22909" = "你好")。
# Textual 原始 regex 不匹配冒号，int() 也无法解析，导致 CJK 乱码。
# 此补丁在模块导入时 patch XTermParser，确保 driver 创建时已生效。
from textual._xterm_parser import XTermParser
from textual.keys import _character_to_key
from textual import events as _tevents

_RE_KITTY_IME = re.compile(r"\x1b\[(\d*);(\d*);([\d:]+)([u~ABCDEFHPQRS])")
_ORIG_SEQ_TO_KEYS = XTermParser._sequence_to_key_events


def _patched_seq_to_keys(self_parser, sequence: str, alt: bool = False):
    ime_match = _RE_KITTY_IME.fullmatch(sequence)
    if ime_match is not None:
        _, _, text_str, _ = ime_match.groups()
        # 只有含 ":" 的多码点 IME 组合文本才走补丁分支，
        # 普通的 3 字段 CSI-u 序列（如 Backspace \x1b[127;5;1u）不应被拦截
        if ":" in text_str:
            try:
                chars = [chr(int(cp)) for cp in text_str.split(":") if cp.strip()]
            except (ValueError, OverflowError):
                chars = []
            if chars:
                for ch in chars:
                    yield _tevents.Key(_character_to_key(ch), ch)
                return
    yield from _ORIG_SEQ_TO_KEYS(self_parser, sequence, alt)


XTermParser._sequence_to_key_events = _patched_seq_to_keys
# ── 补丁结束 ────────────────────────────────────────────────

# ── Kitty 协议降级补丁（全局）──────────────────────────────────
# Textual 默认启用 Kitty keyboard protocol flags=25 (disambiguate + report_all_keys + report_associated_text)。
# report_all_keys (flag 8) 在 Textual 8.2.7 中存在已知缺陷：
#   - Backspace 被双重处理导致一次删 2 个字符
#   - iTerm2 中 IME 中文输入不发送任何字节
# 降级到 flags=1 (仅 disambiguate)，IME 文字作为 UTF-8 直传，Backspace 正常。
# 失去了 shift+enter 等高级组合键区分能力，但换来正确的输入体验。

from textual.drivers.linux_driver import LinuxDriver
_ORIG_START_APP_MODE = LinuxDriver.start_application_mode


def _downgrade_kitty_start_app_mode(self):
    _ORIG_START_APP_MODE(self)
    # 原始方法已写入 \x1b[>25u (flags=1+8+16)，弹出并替换为 flags=1
    self.write("\x1b[<u")   # pop 当前键盘模式
    self.write("\x1b[>1u")  # push 只 disambiguate (flag 1)
    self.flush()


LinuxDriver.start_application_mode = _downgrade_kitty_start_app_mode
# ── Kitty 降级补丁结束 ──────────────────────────────────────────

from vaxport.orchestrator import AGENT_LABELS
from vaxport.memory import FeedbackMemory

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import Provider, Hit, Hits, DiscoveryHit
from textual.containers import Horizontal, Vertical, VerticalScroll, Container
from textual.events import Paste
from textual.screen import ModalScreen
from textual.widgets import Static, RichLog, OptionList, TextArea, Markdown, Tree, Input
from textual.widgets._option_list import Option
from textual import work
from textual.message import Message

import re as _re
import platform as _platform


def _fix_windows_paths(text: str) -> str:
    """兜底修复：LLM 可能把路径改写为与当前 OS 不匹配的格式。

    仅在非 Windows 系统上修复被 LLM 错误转换为 Windows 格式的路径。
    Windows 用户不需要此修复——chart 路径本来就是 Windows 格式。
    """
    if not text or _platform.system() == "Windows":
        return text
    return _re.sub(
        r'C:\\Users\\([^.\\]+)\\\.vaxport\\',
        r'/Users/\1/.vaxport/',
        text,
    )


class ChatInput(TextArea):
    """多行输入框：Enter 提交，Ctrl+N 换行，↑↓ 浏览历史，/ 触发命令补全"""

    BINDINGS = [
        Binding("ctrl+n", "insert_newline", "换行", show=False),
        Binding("ctrl+y", "trigger_copy", "复制全部", show=True, tooltip="复制"),
    ]

    SLASH_COMMANDS = [
        ("/exit", "退出程序"),
        ("/quit", "退出程序"),
        ("/help", "显示帮助"),
        ("/model", "切换模型"),
        ("/status", "显示状态"),
        ("/skills", "已加载 SKILL"),
        ("/tools", "可用查询工具"),
        ("/clear", "清空对话"),
        ("/history", "对话历史"),
        ("/debug", "切换调试"),
        ("/save", "保存会话"),
        ("/copy", "复制回答"),
        ("/export", "导出 Markdown"),
        ("/refresh-schema", "刷新 schema"),
    ]

    class Submitted(Message):
        """Enter 提交事件"""
        def __init__(self, text: str):
            super().__init__()
            self.text = text

    class CommandHint(Message):
        """输入 / 时发送命令提示"""
        def __init__(self, text: str):
            super().__init__()
            self.text = text

    class CopyRequested(Message):
        """Ctrl+Y 请求复制全部对话内容"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._history: list[str] = []
        self._history_pos: int = -1
        self._draft_text: str = ""
        self._hint_active: bool = False

    async def _on_key(self, event):
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            text = self.text.strip()
            if text:
                self._history.append(text)
                self._history_pos = -1
                self._draft_text = ""
                self._hint_active = False
            self.post_message(self.Submitted(text))
            self.clear()
            return

        if event.key == "up":
            if self.cursor_location[0] == 0 and self._history:
                if self._history_pos == -1:
                    self._draft_text = self.text
                    self._history_pos = len(self._history) - 1
                elif self._history_pos > 0:
                    self._history_pos -= 1
                else:
                    await super()._on_key(event)
                    return
                event.prevent_default()
                event.stop()
                self.text = self._history[self._history_pos]
                return

        if event.key == "down":
            last_line = self.document.line_count - 1
            if self.cursor_location[0] == last_line and self._history_pos >= 0:
                if self._history_pos < len(self._history) - 1:
                    self._history_pos += 1
                    event.prevent_default()
                    event.stop()
                    self.text = self._history[self._history_pos]
                else:
                    self._history_pos = -1
                    event.prevent_default()
                    event.stop()
                    self.text = self._draft_text
                return

        await super()._on_key(event)
        self._check_command_hint()

    async def _on_paste(self, event: Paste) -> None:
        """处理粘贴事件

        macOS 的 NSTextView 在 Textual Paste 事件到达前已插入剪贴板内容，
        再调用 super()._on_paste 会导致重复插入。跳过 Textual 的默认处理，
        仅更新命令提示。
        """
        if sys.platform != "darwin":
            await super()._on_paste(event)
        self._check_command_hint()

    def _check_command_hint(self) -> None:
        """检测是否正在输入 / 命令，发送提示"""
        text = self.text.strip()
        if text.startswith("/"):
            self._hint_active = True
            self.post_message(self.CommandHint(text))
            self.update_suggestion()
        elif self._hint_active:
            # 退出命令模式，通知 app 恢复状态
            self._hint_active = False
            self.post_message(self.CommandHint(""))
            self.suggestion = ""

    def update_suggestion(self) -> None:
        """当输入 / 时，自动补全命令名称"""
        text = self.text.strip()
        if text.startswith("/"):
            partial = text.lstrip("/").lower()
            cmd_names = [c[0].lstrip("/") for c in self.SLASH_COMMANDS]
            matches = [c for c in cmd_names if c.startswith(partial)]
            # 去重保持顺序
            seen = set()
            unique = []
            for m in matches:
                if m not in seen:
                    seen.add(m)
                    unique.append(m)
            if unique and partial:
                self.suggestion = unique[0][len(partial):]
            else:
                self.suggestion = ""
        else:
            self.suggestion = ""

    def action_insert_newline(self):
        self.insert("\n")

    def action_trigger_copy(self):
        self.post_message(self.CopyRequested())

    # ── Backspace 防抖（Textual 8.2.7 Kitty 协议双事件兜底）──
    _last_backspace_time: float = 0.0

    def action_delete_left(self) -> None:
        """删除光标左侧字符（带 50ms 防抖，防止协议层双事件）"""
        import time
        now = time.monotonic()
        if now - self._last_backspace_time < 0.05:
            return  # 50ms 内重复事件，丢弃
        self._last_backspace_time = now
        super().action_delete_left()


BACKEND_LABELS = {
    "aliyun": "阿里百炼",
    "ollama": "本地 Ollama",
}

BARE_COMMANDS = {
    "exit", "quit", "help",
    "clear", "status", "tables", "skills", "tools",
    "model", "debug", "history", "copy",
}


# ── 模型选择器弹窗 ──────────────────────────────────────────

AGENT_MODEL_ROWS = [
    ("global",         "全局默认",     ""),
    ("task_assigner",  "TaskAssigner", "📋"),
    ("general",        "通用 Agent",   "🤖"),
    ("analyze_reporter", "分析报告",    "📊"),
    ("quality_supervision", "质量监督", "⚖️"),
    ("document_search", "文档检索",     "🔍"),
]


class AgentModelPickerScreen(ModalScreen[str | None]):
    """Ctrl+P Agent 模型选择器 — 两级：Agent 列表 → 模型列表"""

    BINDINGS = [
        Binding("escape", "cancel", "返回/取消", show=False),
    ]

    def __init__(self, cfg, llm_client, orchestrator):
        super().__init__()
        self._cfg = cfg
        self._llm = llm_client
        self._orchestrator = orchestrator
        self._models_cache: list[tuple[str, str, str]] = []  # [(backend, model_id, label)]
        self._selected_agent: str | None = None  # 当前正在选模型的 agent

    def compose(self) -> ComposeResult:
        with Container(id="picker-container"):
            yield Static("Agent 模型配置  [↑↓]移动  [Enter]修改模型  [Esc]关闭",
                         id="picker-title")
            yield OptionList(id="picker-list")

    def on_mount(self) -> None:
        self._show_agent_list()

    def _show_agent_list(self) -> None:
        """显示 Agent 列表，标注当前模型"""
        self._selected_agent = None
        ol = self.query_one("#picker-list", OptionList)
        ol.clear_options()
        self.query_one("#picker-title", Static).update(
            "Agent 模型配置  [↑↓]移动  [Enter]修改模型  [Esc]关闭"
        )

        global_model = f"{self._llm.active_model} ({BACKEND_LABELS.get(self._llm.active_backend, self._llm.active_backend)})" if self._llm else "N/A"

        for agent_key, display_name, icon in AGENT_MODEL_ROWS:
            if agent_key == "global":
                current = global_model
            else:
                model_id = self._cfg.get_agent_model(agent_key)
                current = model_id if model_id else f"[dim](继承全局: {global_model})[/]"
            prefix = f"{icon} " if icon else ""
            ol.add_option(Option(
                f"{prefix}{display_name}: {current}",
                id=f"agent:{agent_key}"
            ))

    def _show_model_list(self, agent_key: str) -> None:
        """显示模型列表供选择"""
        self._selected_agent = agent_key
        ol = self.query_one("#picker-list", OptionList)
        ol.clear_options()

        agent_info = next((r for r in AGENT_MODEL_ROWS if r[0] == agent_key), None)
        agent_display = agent_info[1] if agent_info else agent_key
        self.query_one("#picker-title", Static).update(
            f"选择 {agent_display} 的模型  [↑↓]移动  [Enter]确认  [Esc]返回"
        )

        current_model = self._cfg.get_agent_model(agent_key)

        # "(继承全局)" 选项
        is_inherit = current_model is None
        inherit_prefix = "[#50FA7B]> [/]" if is_inherit else "   "
        ol.add_option(Option(
            f"{inherit_prefix}[italic](继承全局)[/]",
            id=f"model:{agent_key}:__inherit__"
        ))

        # 模型列表
        last_backend = ""
        for backend, model_id, _label in self._models_cache:
            if backend != last_backend:
                ol.add_option(Option(
                    f"[bold #BD93F9]-- {BACKEND_LABELS.get(backend, backend)} --[/]",
                    disabled=True
                ))
                last_backend = backend
            is_current = (current_model == model_id)
            prefix = "[#50FA7B]▸ [/]" if is_current else "   "
            ol.add_option(Option(
                f"{prefix}{model_id}",
                id=f"model:{agent_key}:{model_id}"
            ))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        opt_id = event.option_id or ""

        if opt_id.startswith("agent:"):
            # 选中 Agent → 进入模型列表
            agent_key = opt_id.split(":", 1)[1]
            self._show_model_list(agent_key)

        elif opt_id.startswith("model:"):
            # 选中模型 → 持久化并返回 Agent 列表
            _, agent_key, model_id = opt_id.split(":", 2)
            if model_id == "__inherit__":
                model_id = None
            # 持久化
            if agent_key == "global":
                # 全局模型变更：找到 model_id 对应的 backend 并切换
                if model_id and self._llm:
                    backend = self._find_backend_for_model(model_id)
                    if backend:
                        self._llm.set_model(backend, model_id)
            else:
                self._cfg.set_agent_model(agent_key, model_id)
                if self._orchestrator:
                    self._orchestrator.update_agent_model(agent_key, model_id)
            # 返回 Agent 列表
            self._show_agent_list()

    def _find_backend_for_model(self, model_id: str) -> str | None:
        """从模型缓存中查找 model_id 对应的后端名"""
        for backend, mid, _ in self._models_cache:
            if mid == model_id:
                return backend
        return None

    def action_cancel(self) -> None:
        if self._selected_agent is not None:
            # 在模型列表中 → 返回 Agent 列表
            self._show_agent_list()
        else:
            # 在 Agent 列表中 → 关闭
            self.dismiss(None)

    def update_models_cache(self, items: list[tuple[str, str, str]]) -> None:
        """外部更新模型缓存"""
        self._models_cache = items


class DatabasePickerScreen(ModalScreen[str | None]):
    """Ctrl+D 数据库选择器"""

    BINDINGS = [
        Binding("escape", "cancel", "取消", show=False),
    ]

    def __init__(self, db_names: list[str], current: str):
        super().__init__()
        self._db_names = db_names
        self._current = current

    def compose(self) -> ComposeResult:
        with Container(id="picker-container"):
            yield Static("选择数据库  [↑↓]移动  [Enter]确认  [Esc]取消", id="picker-title")
            yield OptionList(id="picker-list")

    def on_mount(self) -> None:
        ol = self.query_one("#picker-list", OptionList)
        for name in self._db_names:
            is_current = (name == self._current)
            prefix = "[#50FA7B]▸ [/]" if is_current else "   "
            ol.add_option(Option(
                f"{prefix}[bold #BD93F9]{name}[/]",
                id=f"db:{name}"
            ))
        if ol.option_count:
            ol.highlighted = 0

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id and event.option_id.startswith("db:"):
            _, name = event.option_id.split(":", 1)
            self.dismiss(name)

    def action_cancel(self) -> None:
        self.dismiss(None)


# ── 决策解析 ──────────────────────────────────────────────

def _parse_plan_decisions(text: str) -> list[dict]:
    """从计划文本中解析待决策事项，返回 [{title, options: [{rank, label, desc}]}]"""
    decisions = []
    pattern = r'\*\*决策项\s+(\d+):\s*(.+?)\*\*'
    matches = list(re.finditer(pattern, text))

    # rank emoji → 固定映射，避免 content[:2] 吞噬 label 字符
    _RANK_EMOJI = {'🥇': '🥇', '🥈': '🥈', '🥉': '🥉'}

    for i, match in enumerate(matches):
        title = f"决策项 {match.group(1)}: {match.group(2).strip()}"
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section = text[start:end]

        options = []
        for line in section.split('\n'):
            line = line.strip()
            if not (line.startswith('- 🥇') or line.startswith('- 🥈') or line.startswith('- 🥉')):
                continue
            content = line[2:].strip()
            # 精确提取 rank emoji（1 个 Unicode 字符），避免吞噬 label
            rank = None
            rest = ""
            for emoji in ('🥇', '🥈', '🥉'):
                if content.startswith(emoji):
                    rank = emoji
                    rest = content[len(emoji):].strip()
                    break
            if rank is None:
                continue  # 未识别 rank，跳过

            # 分离 label 和 description
            if ': ' in rest:
                label_part, desc = rest.split(': ', 1)
            elif '：' in rest:
                label_part, desc = rest.split('：', 1)
            else:
                label_part, desc = rest, ""
            label = label_part.strip().strip('*').strip()
            # 过滤空/无意义标签（LLM 漏写方案名只留冒号/括号/星号）
            if not label or label in ('[]', '**', ''):
                continue
            options.append({"rank": rank, "label": label, "desc": desc.strip()})

        if options:
            decisions.append({"title": title, "options": options})

    return decisions


# ── 决策选择器弹窗 ────────────────────────────────────────

class CustomInputModal(ModalScreen[str | None]):
    """方案D: 用户自定义输入"""

    BINDINGS = [
        Binding("escape", "cancel", "取消", show=False),
    ]

    def __init__(self, decision_title: str):
        super().__init__()
        self._decision_title = decision_title

    def compose(self) -> ComposeResult:
        with Container(id="custom-input-container"):
            yield Static("", id="custom-input-title")
            yield Input(
                placeholder="输入你的自定义方案...",
                id="custom-input-field",
            )

    def on_mount(self) -> None:
        self.query_one("#custom-input-title", Static).update(
            f"📝 {self._decision_title} — 自定义方案"
        )
        self.query_one("#custom-input-field", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if text:
            self.dismiss(text)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class DecisionPickerScreen(ModalScreen[list | None]):
    """逐个展示决策项，用户通过上下键选择方案 (A/B/C/D)"""

    BINDINGS = [
        Binding("escape", "cancel", "取消", show=False),
    ]

    def __init__(self, decisions: list[dict]):
        super().__init__()
        self._decisions = decisions
        self._current_idx = 0
        self._results: list[str] = []

    def compose(self) -> ComposeResult:
        with Container(id="decision-container"):
            yield Static("", id="decision-title")
            yield Static("", id="decision-question")
            yield OptionList(id="decision-list")

    def on_mount(self) -> None:
        self._show_current()

    def _show_current(self) -> None:
        total = len(self._decisions)
        current = self._current_idx + 1
        decision = self._decisions[self._current_idx]

        self.query_one("#decision-title", Static).update(
            f"决策项 {current}/{total}  [↑↓]选择  [Enter]确认  [Esc]取消"
        )
        self.query_one("#decision-question", Static).update(
            f"[bold #F8F8F2]{decision['title']}[/]\n\n"
            f"[#6272A4]请选择方案:[/]"
        )

        ol = self.query_one("#decision-list", OptionList)
        ol.clear_options()
        for i, opt in enumerate(decision["options"]):
            ol.add_option(Option(
                f"{opt['rank']} {opt['label']}: {opt['desc']}",
                id=f"opt:{i}"
            ))
        ol.add_option(Option("📝 方案D: 自定义输入...", id="opt:custom"))
        ol.highlighted = 0
        ol.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if not event.option_id:
            return
        if event.option_id == "opt:custom":
            decision = self._decisions[self._current_idx]
            self.app.push_screen(
                CustomInputModal(decision["title"]),
                self._on_custom_done
            )
        else:
            _, idx = event.option_id.split(":", 1)
            # 防御：校验选中项 label 非空，防止 Enter 选中空方案后跳到下一题
            try:
                idx_int = int(idx)
                opt = self._decisions[self._current_idx]["options"][idx_int]
                if not opt.get("label", "").strip():
                    return  # 空标签选项，忽略此次选择
            except (ValueError, IndexError):
                return
            self._results.append(idx)
            self._advance()

    def _on_custom_done(self, text: str | None) -> None:
        if text is not None:
            self._results.append(f"D:{text}")
            self._advance()

    def _advance(self) -> None:
        self._current_idx += 1
        if self._current_idx >= len(self._decisions):
            self.dismiss(self._results)
        else:
            self._show_current()

    def action_cancel(self) -> None:
        self.dismiss(None)


class ModelCommandProvider(Provider):
    """注入 Ctrl+P 命令面板的模型选择选项"""

    async def discover(self) -> Hits:
        yield DiscoveryHit(
            "Select model",
            self.app.action_show_model_picker,
            help="切换 LLM 大模型 / 后端",
        )

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        command = "Select model"
        score = matcher.match(command)
        if score > 0:
            yield Hit(
                score,
                matcher.highlight(command),
                self.app.action_show_model_picker,
                help="切换 LLM 大模型 / 后端",
            )


class ToolCallLog(RichLog):
    """可折叠的工具调用日志 — 默认折叠，Ctrl+O 展开/收起。
    使用 RichLog 作为基类，write() 天然支持动态追加。
    """

    def __init__(self):
        super().__init__(highlight=True, markup=True, classes="tool-call-line")
        self._entries: list[tuple[str, str]] = []  # [(type, text), ...]
        self._expanded: bool = False

    @property
    def has_entries(self) -> bool:
        return len(self._entries) > 0

    def add_call(self, text: str) -> None:
        self._entries.append(("call", self._escape_markup(text)))
        self._redraw()

    def add_result(self, text: str) -> None:
        self._entries.append(("result", self._escape_markup(text)))
        self._redraw()

    def add_thinking(self, text: str) -> None:
        self._entries.append(("thinking", f"[#50FA7B]💭 {text}[/]"))
        self._redraw()

    @staticmethod
    def _escape_markup(text: str) -> str:
        return text.replace("[", "\\[")

    def toggle(self) -> bool:
        self._expanded = not self._expanded
        self._redraw()
        return self._expanded

    def _redraw(self) -> None:
        """清空并重写全部内容"""
        self.clear()
        if not self._entries:
            return
        call_count = sum(1 for t, _ in self._entries if t == "call")

        if self._expanded:
            for entry_type, text in self._entries:
                if entry_type == "result":
                    self.write(f"  {text}")
                else:
                    self.write(text)
            self.write("[#6272A4]-- \\[Ctrl+O 收起详情][/]")
        else:
            _, latest_text = self._entries[-1]
            if call_count == 0:
                self.write(f"{latest_text} | \\[Ctrl+O 展开详情]")
            elif call_count == 1 and len(self._entries) <= 2:
                self.write(f"{latest_text}")
            else:
                self.write(
                    f"⚙ 共 {call_count} 次查询 | 最近: {latest_text} | "
                    f"\\[Ctrl+O 展开详情]"
                )


# ── 主应用 ──────────────────────────────────────────────────

class VaxportApp(App):
    """vaxport 终端数据分析主界面 — Toad 风格"""

    CSS_PATH = "style.tcss"
    TITLE = "vaxport"
    ENABLE_COMMAND_PALETTE = False
    COMMANDS = App.COMMANDS | {ModelCommandProvider}
    BINDINGS = [
        Binding("ctrl+p", "show_model_picker", "模型", tooltip="切换"),
        Binding("ctrl+d", "show_db_picker", "数据库", tooltip="切换"),
        Binding("ctrl+t", "toggle_plan_mode", "规划/执行", tooltip="切换"),
        Binding("ctrl+s", "toggle_sidebar_tab", "侧边栏", tooltip="切换"),
        Binding("ctrl+e", "expand_all_tree", "展开", tooltip="目录树"),
        Binding("ctrl+w", "collapse_all_tree", "折叠", tooltip="目录树"),
        Binding("ctrl+o", "toggle_tool_log", "工具日志", tooltip="面板"),
        Binding("ctrl+shift+c", "copy_last_answer", "复制回答", tooltip="编辑"),
        Binding("ctrl+q", "quit", "退出", tooltip="系统"),
    ]

    def __init__(self, config=None, llm=None, db=None, tools=None,
                 orchestrator=None, skills=None, session=None, debug_mode=False,
                 mdb=None):
        super().__init__()
        self.cfg = config
        self.llm = llm
        self.db = db
        self.mdb = mdb  # MultiDatabase (多库支持)
        self.tools = tools
        self.orchestrator = orchestrator
        self.skills = skills
        self.session = session
        self.debug_mode = debug_mode
        self._plan_mode: bool = False
        self._sidebar_tab: str = "tables"
        self._last_result: dict = {}
        self._last_answer: str = ""
        self._first_query = True
        self._welcome_widget: Markdown | None = None
        self._tool_log: ToolCallLog | None = None
        self._tool_summary: Static | None = None   # 概要行，始终可见
        self._tool_details: list[Static] = []       # 详情行，可折叠
        self._tool_entries: list[tuple[str, str]] = []  # [(type, text), ...]
        self._tool_expanded: bool = False            # 是否展开详情
        self._busy = False
        self._model_cache: list | None = None
        self._plan_confirm_event: threading.Event | None = None
        self._plan_confirm_result: bool = False
        self._plan_confirm_plan: str = ""
        self._plan_has_decisions: bool = False  # 计划中是否有待决策项
        self._plan_had_picker: bool = False  # 是否使用了 DecisionPicker（区别于纯文本确认）
        self._plan_feedback: str = ""  # 用户对决策项的反馈
        self._plan_confirm_widget = None      # "[Enter] 确认执行" 提示 widget（确认后移除）
        self._execution_cancel: threading.Event = threading.Event()  # Esc 取消执行
        self._pending_decisions: list[dict] = []  # 决策选择器进行中的决策列表
        self._pending_query: str = ""  # 计划取消后待处理的新查询
        self._tui_feedback: list[str] = []  # 执行中的交互追问消息
        self._feedback_memory = FeedbackMemory()  # 跨会话反馈记忆

    @property
    def _is_db_connected(self) -> bool:
        if self.mdb:
            return self.mdb.is_connected
        return bool(self.db and self.db.is_connected)

    @property
    def _db_display(self) -> str:
        if self.mdb and self.mdb.active_name:
            return f"{self.mdb.active_name}@{self.cfg.pg_host}"
        if self.db and self.db.is_connected:
            return f"{self.cfg.pg_database}@{self.cfg.pg_host}"
        return "未连接"

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="main-area"):
                yield Static("", id="header-bar")
                yield VerticalScroll(id="conversation")
                yield Static("", id="agent-status")
                with Container(id="prompt-container"):
                    yield Static("", id="prompt-info")
                    yield ChatInput(id="user_input")
            with Vertical(id="sidebar"):
                yield Static(" 数据库表", id="sidebar-header")
                yield Tree("", id="sidebar-content")
                yield Static("", id="shortcuts-panel")

    def on_mount(self) -> None:
        self._update_header()
        self._update_prompt_info()
        self._update_shortcuts()
        self.query_one("#user_input", ChatInput).focus()
        self._show_welcome()
        self._populate_sidebar()
        # Kitty IME patch 已在模块导入时执行，无需 on_mount 再做
        # 注入跨会话反馈记忆
        memory_text = self._feedback_memory.build_system_prompt_section()
        if memory_text and self.orchestrator:
            self.orchestrator.set_memory_context(memory_text)

    def _build_shortcuts_text(self) -> str:
        """构建快捷键文本，按 group 分行紧凑排列"""
        groups = [
            ("切换", [
                ("ctrl+p", "模型"),
                ("ctrl+d", "数据库"),
                ("ctrl+t", "规划"),
                ("ctrl+s", "侧边栏"),
            ]),
            ("树", [
                ("ctrl+e", "展开"),
                ("ctrl+w", "折叠"),
            ]),
            ("其他", [
                ("ctrl+o", "日志"),
                ("ctrl+q", "退出"),
            ]),
        ]
        lines = ["[bold #6272A4]快捷键[/]"]
        for group_name, keys in groups:
            parts = []
            for key, desc in keys:
                parts.append(f"[#F8F8F2]{key}[/] [#6272A4]{desc}[/]")
            line = "  ".join(parts)
            lines.append(line)
        return "\n".join(lines)

    def _update_shortcuts(self) -> None:
        try:
            panel = self.query_one("#shortcuts-panel", Static)
            panel.update(self._build_shortcuts_text())
        except Exception:
            pass

    def on_key(self, event) -> None:
        """Esc 取消计划确认 / 取消执行"""
        if event.key == "escape":
            if self._plan_confirm_event is not None:
                self._plan_confirm_result = False
                self._plan_confirm_event.set()
                self._add_info("[#F1FA8C]计划已取消[/]")
                event.prevent_default()
                event.stop()
            elif self._busy:
                self._execution_cancel.set()
                self._add_info("[#F1FA8C]正在取消执行...[/]")
                event.prevent_default()
                event.stop()

    def on_resize(self, event=None) -> None:
        """终端尺寸变化时强制刷新布局"""
        try:
            size = event.size if event else self.size
            self.screen._screen_resized(size)
        except Exception:
            pass

    # ── 对话区 widget 管理 ───────────────────────────────────

    def _add_user_message(self, text: str) -> None:
        """挂载用户消息"""
        try:
            conv = self.query_one("#conversation", VerticalScroll)
            # 在每次对话之间加一个空行，增加视觉分隔
            conv.mount(Static("", classes="divider"))
            conv.mount(Static(f"> {text}", classes="user-message"))
            conv.scroll_end(animate=False)
        except Exception:
            pass

    def _add_tool_call(self, text: str) -> None:
        """挂载工具调用行 (已弃用，新逻辑见 _add_to_tool_log)"""
        try:
            conv = self.query_one("#conversation", VerticalScroll)
            conv.mount(Static(text, classes="tool-call-line"))
            conv.scroll_end(animate=False)
        except Exception:
            pass

    # ── 工具调用日志（概要行 + 独立详情行，通过 display 控制折叠）──

    @staticmethod
    def _escape_markup(text: str) -> str:
        """转义 Rich markup 特殊字符，防止 MarkupError"""
        return text.replace("[", "\\[")

    def _start_tool_log(self) -> None:
        """开始新一轮工具调用日志"""
        self._tool_entries = []
        self._tool_details = []
        self._tool_expanded = False
        self._tool_summary = None

    def _add_to_tool_log(self, call_text: str = "", result_text: str = "",
                         thinking_text: str = "") -> None:
        """追加工具调用条目"""
        try:
            conv = self.query_one("#conversation", VerticalScroll)
        except Exception:
            return

        # 添加详情行
        if call_text:
            safe = self._escape_markup(call_text)
            w = Static(f"[#6272A4]⚙ {safe}[/]", classes="tool-detail-line")
            w.display = self._tool_expanded
            conv.mount(w)
            self._tool_details.append(w)
            self._tool_entries.append(("call", safe))
        if result_text:
            safe = self._escape_markup(result_text)
            w = Static(f"[#6272A4]  ↳ {safe}[/]", classes="tool-detail-line")
            w.display = self._tool_expanded
            conv.mount(w)
            self._tool_details.append(w)
            self._tool_entries.append(("result", safe))
        if thinking_text:
            safe = self._escape_markup(thinking_text)
            w = Static(f"[#50FA7B]💭 {safe}[/]", classes="tool-detail-line")
            w.display = self._tool_expanded
            conv.mount(w)
            self._tool_details.append(w)
            self._tool_entries.append(("thinking", safe))

        # 更新概要行
        self._update_tool_summary()

    def _update_tool_summary(self) -> None:
        """重建概要行"""
        try:
            conv = self.query_one("#conversation", VerticalScroll)
        except Exception:
            return

        # 移除旧概要行
        if self._tool_summary is not None:
            try:
                self._tool_summary.remove()
            except Exception:
                pass

        if not self._tool_entries:
            self._tool_summary = None
            return

        call_count = sum(1 for t, _ in self._tool_entries if t == "call")
        latest_type, latest_text = self._tool_entries[-1] if self._tool_entries else ("", "")
        if latest_type == "thinking":
            latest_display = f"[#50FA7B]💭 {latest_text}[/]"
        elif latest_type == "result":
            latest_display = f"[#6272A4]↳ {latest_text}[/]"
        else:
            latest_display = f"[#6272A4]⚙ {latest_text}[/]"

        ctrl_hint = "收起详情" if self._tool_expanded else "展开详情"
        if call_count == 0:
            text = f"{latest_display} | \\[Ctrl+O {ctrl_hint}]"
        elif call_count == 1 and len(self._tool_entries) <= 2:
            text = f"{latest_display}"
        else:
            text = (
                f"⚙ 共 {call_count} 次查询 | 最近: {latest_display} | "
                f"\\[Ctrl+O {ctrl_hint}]"
            )
        self._tool_summary = Static(text, classes="tool-summary-line")
        conv.mount(self._tool_summary)
        conv.scroll_end(animate=False)

    def action_toggle_tool_log(self) -> None:
        """Ctrl+O: 展开/折叠工具调用详情"""
        if not self._tool_entries:
            return
        self._tool_expanded = not self._tool_expanded
        for w in self._tool_details:
            w.display = self._tool_expanded
        self._update_tool_summary()

    def _add_agent_response(self, markdown_text: str) -> None:
        """挂载 agent 回复 (Markdown widget)"""
        try:
            conv = self.query_one("#conversation", VerticalScroll)
            conv.mount(Markdown(markdown_text))
            conv.scroll_end(animate=False)
        except Exception:
            pass

    def _show_thinking_text(self, text: str) -> None:
        """挂载思考过程文本（浅色 + 无序编号）"""
        try:
            conv = self.query_one("#conversation", VerticalScroll)
            conv.mount(Markdown(text, classes="thinking-content"))
            conv.scroll_end(animate=False)
        except Exception:
            pass

    def _set_agent_status(self, text: str) -> None:
        """更新 Agent 状态栏（固定于对话区下方）"""
        try:
            self.query_one("#agent-status", Static).update(text)
        except Exception:
            pass

    def _add_info(self, text: str) -> Static:
        """挂载信息/状态行，返回 widget 引用以便后续移除"""
        try:
            conv = self.query_one("#conversation", VerticalScroll)
            w = Static(text, classes="loading-line")
            conv.mount(w)
            conv.scroll_end(animate=False)
            return w
        except Exception:
            return None

    def _show_plan_for_confirm(self, plan_text: str) -> None:
        """显示执行计划并提示用户确认"""
        # 检测是否有待用户决策的关键事项
        has_decisions = (
            "待用户决策" in plan_text
            and "无需用户决策" not in plan_text
        )
        self._plan_has_decisions = has_decisions

        self._add_info("-" * 40)

        if has_decisions:
            # 提取决策章节，在显示时高亮提示
            prompt = (
                "**[Enter] 确认（使用推荐方案）| 输入选择后按 Enter（如 '1A, 2B'）| [Esc] 取消**\n\n"
                "> ⚠️ 计划中有**待决策事项**（见下方七），请在输入框中回复你的选择，或直接按 Enter 使用 🥇 推荐方案。"
            )
        else:
            prompt = "**[Enter] 确认执行 | [Esc] 取消**"

        self._add_agent_response(f"""## 📋 执行计划

{plan_text}

---
{prompt}""")

    def _start_decision_picker(self, plan_text: str, decisions: list[dict]) -> None:
        """显示计划后启动逐项决策选择器"""
        self._show_plan_for_confirm(plan_text)
        self._pending_decisions = decisions
        self.push_screen(
            DecisionPickerScreen(decisions),
            self._on_decisions_done
        )

    def _on_decisions_done(self, results: list | None) -> None:
        """决策选择器完成回调"""
        if results is None:
            self._plan_confirm_result = False
            self._plan_feedback = ""
            self._add_info("[#F1FA8C]决策已取消[/]")
            if self._plan_confirm_event:
                self._plan_confirm_event.set()
        else:
            # 存储决策反馈，但不立即执行——让用户有时间查看计划详情
            self._plan_confirm_result = True
            self._plan_feedback = self._format_decision_feedback(
                self._pending_decisions, results
            )
            self._add_info(
                "[#50FA7B]✓ 已记录决策选择。请查看上方计划，按 [Enter] 确认执行，或输入补充意见[/]"
            )
        self._pending_decisions = []

    def _format_decision_feedback(
        self, decisions: list[dict], results: list[str]
    ) -> str:
        """将决策结果格式化为用户反馈文本"""
        lines = []
        for i, (decision, result) in enumerate(zip(decisions, results)):
            if result.startswith("D:"):
                custom = result[2:]
                lines.append(
                    f"- **{decision['title']}**: 自定义方案 — {custom}"
                )
            else:
                idx = int(result)
                opt = decision["options"][idx]
                lines.append(
                    f"- **{decision['title']}**: {opt['rank']} {opt['label']}"
                )
        return "\n".join(lines)

    def _clear_conversation(self) -> None:
        """清空对话区"""
        try:
            conv = self.query_one("#conversation", VerticalScroll)
            conv.remove_children()
        except Exception:
            pass

    # ── 头部 / 状态 ──────────────────────────────────────────

    def _update_header(self) -> None:
        from vaxport import __version__
        self.query_one("#header-bar", Static).update(
            f"[bold #BD93F9]疫苗企业数据分析终端[/] [dim #6272A4]v{__version__}[/]"
        )

    def _build_agent_tag(self, agent_type: str) -> str:
        """构建 Agent 标识标签（Markdown 格式）"""
        icon, label, color = AGENT_LABELS.get(agent_type, ("[G]", "通用", "#BD93F9"))
        return f"> {icon} **{label} Agent** | 负责领域见侧边栏\n"

    def _update_prompt_info(self) -> None:
        model = self.llm.active_model if self.llm else "N/A"
        backend = BACKEND_LABELS.get(
            self.llm.active_backend, self.llm.active_backend or "N/A"
        ) if self.llm else "N/A"
        db = self._footer_db()
        mode = "规划" if self._plan_mode else "执行"
        pct = (self._last_result or {}).get("token_pct", 0)
        turns = (self._last_result or {}).get("turns", 0)
        agent_type = (self._last_result or {}).get("agent_type", "")
        _, agent_label, _ = AGENT_LABELS.get(agent_type, ("", "", ""))
        agent_str = f" | {agent_label}" if agent_label else ""
        bar = self._context_bar(pct)
        text = f"{mode}{agent_str}  |  {model} @ {backend}  |  {db}  |  Context {bar} {pct}%  |  轮次 {turns}"
        if self.debug_mode:
            text += "  |  DEBUG"
        self.query_one("#prompt-info", Static).update(text)

    @staticmethod
    def _context_bar(pct: int, width: int = 10) -> str:
        """生成可视化进度条，如 ████████░░ 80%"""
        filled = max(0, min(width, round(pct / 100 * width)))
        empty = width - filled
        bar = "█" * filled + "░" * empty
        return f"[#50FA7B]{bar}[/]" if pct < 80 else f"[#F1FA8C]{bar}[/]"

    def _footer_db(self) -> str:
        if self._is_db_connected and self.cfg:
            return self._db_display
        return "未连接"

    # ── 侧边栏 ───────────────────────────────────────────────

    def _populate_sidebar(self) -> None:
        try:
            tree = self.query_one("#sidebar-content", Tree)
        except Exception:
            return
        tree.clear()

        if self._sidebar_tab == "skills":
            self._populate_sidebar_skills(tree)
        else:
            self._populate_sidebar_tables(tree)

    def _populate_sidebar_tables(self, tree: Tree) -> None:
        connected = (self.mdb and self.mdb.is_connected) or \
                    (self.db and self.db.is_connected)
        if not (connected and self.tools):
            tree.root.add("[#6272A4]未连接[/]")
            return
        summary = self.tools.get_schema_summary()
        if not summary:
            tree.root.add("[#6272A4]无用户表[/]")
            return

        active_db = self.mdb.active_name if self.mdb else ""

        tree.root.set_label("[bold #6272A4]PostgreSQL[/]")
        tree.root.expand()

        for db_name in sorted(summary.keys()):
            schemas = summary[db_name]
            # 数据库节点
            is_active = (db_name == active_db)
            prefix = "[#50FA7B]> [/]" if is_active else "> "
            db_label = f"{prefix}[bold #F8F8F2]{db_name}[/]"
            db_node = tree.root.add(db_label, expand=True)

            for schema in sorted(schemas.keys()):
                info = schemas[schema]
                tbl_count = len(info.get("tables", []))
                schema_label = f"[bold #BD93F9]{schema}[/]  [{tbl_count} 表]"
                schema_node = db_node.add(schema_label, expand=False)
                for t in sorted(info.get("tables", [])):
                    schema_node.add_leaf(f"[#A4A4B4]{t}[/]")
                for v in sorted(info.get("views", [])):
                    schema_node.add_leaf(f"[#6272A4]◈ {v}[/]")
                for mv in sorted(info.get("matviews", [])):
                    schema_node.add_leaf(f"[#6272A4]◆ {mv}[/]")

    def _populate_sidebar_skills(self, tree: Tree) -> None:
        if not self.skills:
            tree.root.add("[#6272A4]无 SKILL[/]")
            return
        skills = sorted(self.skills.list_skills(),
                        key=lambda s: (s.name or s.dir_name).lower())
        if not skills:
            tree.root.add("[#6272A4]无 SKILL[/]")
            return
        root_node = tree.root.add(f"[#6272A4]共 {len(skills)} 个[/]", expand=True)
        for s in skills:
            name = s.name or s.dir_name
            root_node.add_leaf(f"[bold #BD93F9]{name}[/]")

    def action_expand_all_tree(self) -> None:
        try:
            tree = self.query_one("#sidebar-content", Tree)
            tree.root.expand_all()
        except Exception:
            pass

    def action_collapse_all_tree(self) -> None:
        try:
            tree = self.query_one("#sidebar-content", Tree)
            tree.root.collapse_all()
        except Exception:
            pass

    # ── 欢迎 ─────────────────────────────────────────────────

    def _show_welcome(self) -> None:
        welcome = self._build_welcome_text()
        self._add_agent_response(welcome)
        # 记录欢迎 widget 引用，供模型切换时更新
        try:
            conv = self.query_one("#conversation", VerticalScroll)
            children = list(conv.children)
            if children:
                self._welcome_widget = children[0]
        except Exception:
            pass

    def _build_welcome_text(self) -> str:
        from vaxport import __version__
        model = self.llm.active_model if self.llm else "N/A"
        backend = BACKEND_LABELS.get(
            self.llm.active_backend, self.llm.active_backend or "N/A"
        ) if self.llm else "N/A"
        skills_count = self.skills.count if self.skills else 0

        db_line = ""
        if self._is_db_connected and self.cfg:
            db_line = f"- **数据库**: {self._db_display}\n"

        return f"""# 疫苗企业数据分析终端 v{__version__}

- **模型**: {model} @ {backend}
{db_line}- **技能**: {skills_count} 个 SKILL
- **Agent**: 4 个专家 (分析报告 | 质量监督 | 文档检索 | 通用)

输入问题开始分析。"""

    def _update_welcome(self) -> None:
        """模型切换后更新欢迎面板中的模型信息"""
        if self._welcome_widget is not None:
            try:
                self._welcome_widget.update(self._build_welcome_text())
            except Exception:
                pass

    # ── 规划模式 / 侧边栏 / 复制 ──────────────────────────────

    def action_toggle_plan_mode(self) -> None:
        self._plan_mode = not self._plan_mode
        self._update_prompt_info()

    def action_toggle_sidebar_tab(self) -> None:
        self._sidebar_tab = "skills" if self._sidebar_tab == "tables" else "tables"
        label = "SKILL 列表" if self._sidebar_tab == "skills" else "数据库表"
        self.query_one("#sidebar-header", Static).update(f" {label}")
        self._populate_sidebar()

    def action_copy_last_answer(self) -> None:
        if not self._last_answer:
            self._add_info("没有可复制的内容")
            return
        self._copy_to_clipboard(self._last_answer)
        self._add_info(f"已复制到剪贴板 ({len(self._last_answer)} 字)")

    def on_chat_input_copy_requested(self, event: ChatInput.CopyRequested) -> None:
        """Ctrl+Y: 复制全部对话内容到剪贴板"""
        text = self._get_conversation_text()
        if not text:
            self._add_info("没有可复制的内容")
            return
        self._copy_to_clipboard(text)
        self._add_info(f"已复制全部对话到剪贴板 ({len(text)} 字)")

    def _get_conversation_text(self) -> str:
        """提取对话区全部文本"""
        try:
            conv = self.query_one("#conversation", VerticalScroll)
            lines: list[str] = []
            for child in conv.children:
                try:
                    if hasattr(child, "renderable") and child.renderable is not None:
                        text = str(child.renderable)
                        if text.strip():
                            lines.append(text.strip())
                except Exception:
                    pass
            return "\n\n".join(lines)
        except Exception:
            return ""

    @staticmethod
    def _copy_to_clipboard(text: str) -> None:
        import subprocess
        import os
        if os.name == "posix":
            for cmd in (["xclip", "-selection", "clipboard"], ["xsel", "-ib"]):
                try:
                    subprocess.run(cmd, input=text.encode(), check=False, timeout=5)
                    return
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    continue
        try:
            subprocess.run(["pbcopy"], input=text.encode(), check=False, timeout=5)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # ── Ctrl+P 模型选择器 ────────────────────────────────────

    @property
    def _model_cache_path(self) -> Path:
        return Path(self.cfg.session_dir).parent / "model_cache.json"

    def _load_model_cache(self) -> list | None:
        """从磁盘加载缓存的模型列表"""
        try:
            if self._model_cache_path.exists():
                data = json.loads(self._model_cache_path.read_text())
                if isinstance(data, list) and len(data) > 0:
                    return [(item[0], item[1], item[2]) for item in data]
        except Exception:
            pass
        return None

    def _save_model_cache(self, items: list) -> None:
        """保存模型列表到磁盘"""
        try:
            self._model_cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._model_cache_path.write_text(json.dumps(items, ensure_ascii=False))
        except Exception:
            pass

    def _startup_picker(self) -> None:
        """启动时读缓存秒出全量清单，后台拉取 API 更新缓存"""
        cached = self._load_model_cache()
        if cached:
            self._model_cache = cached
            self._show_picker(cached)
            # 后台异步刷新，有变化再更新弹窗
            self._enrich_cache()
        else:
            # 首次使用，无缓存 → 走完整拉取
            self._fetch_models()

    @work(thread=True, exclusive=True)
    def _enrich_cache(self) -> None:
        """后台拉取完整模型列表，有变化则更新缓存并刷新弹窗"""
        items = self._do_fetch_models()
        if items and items != self._model_cache:
            self._model_cache = items
            self._save_model_cache(items)
            self.call_from_thread(self._refresh_picker)

    def _refresh_picker(self) -> None:
        """如果模型选择器仍打开，更新模型缓存"""
        try:
            if isinstance(self.screen, AgentModelPickerScreen) and self._model_cache:
                self.screen.update_models_cache(self._model_cache)
        except Exception:
            pass

    def action_show_model_picker(self) -> None:
        if self._model_cache:
            self._show_picker(self._model_cache)
        else:
            self._fetch_models()

    @work(thread=True, exclusive=True)
    def _fetch_models(self) -> None:
        items = self._do_fetch_models()
        if not items:
            self.call_from_thread(self._add_info, "[#FF5555]未配置任何模型后端[/]")
            return
        self._model_cache = items
        self._save_model_cache(items)
        self.call_from_thread(self._show_picker, items)

    def _do_fetch_models(self) -> list:
        seen: set[tuple[str, str]] = set()
        items: list[tuple[str, str, str]] = []

        if self.llm:
            for name in self.llm.available_backends:
                state = self.llm._states.get(name)
                if state and state.model:
                    seen.add((name, state.model))

        if self.llm:
            for name in self.llm.available_backends:
                try:
                    api_models = self.llm.list_models(name)
                    for m in api_models:
                        if (name, m) not in seen:
                            seen.add((name, m))
                except Exception:
                    pass

        for backend, model_id in sorted(seen, key=lambda x: (x[0], x[1])):
            items.append((backend, model_id, f"  {model_id}"))

        return items

    def _show_picker(self, items: list[tuple[str, str, str]]) -> None:
        screen = AgentModelPickerScreen(self.cfg, self.llm, self.orchestrator)
        if items:
            screen.update_models_cache(items)

        async def handle_result(_selection: str | None) -> None:
            # AgentModelPickerScreen 自行处理持久化和切换，
            # 关闭后刷新 header 以反映可能的全局模型变更
            self._update_header()
            self._update_prompt_info()
            self._update_welcome()

        self.push_screen(screen, handle_result)

    def action_show_db_picker(self) -> None:
        """Ctrl+D: 弹出数据库选择器"""
        mdb = self.mdb
        if not mdb or not mdb.names:
            self._add_info("[#FF5555]仅配置了单数据库，无需切换[/]")
            return
        if len(mdb.names) <= 1:
            self._add_info(f"[#6272A4]仅一个数据库: {mdb.active_name}[/]")
            return

        screen = DatabasePickerScreen(mdb.names, mdb.active_name)

        async def handle_result(selection: str | None) -> None:
            if selection and selection != mdb.active_name:
                if mdb.switch_to(selection):
                    self._add_info(f"已切换数据库: [#50FA7B]{selection}[/]")
                    self._update_header()
                    self._populate_sidebar()
                else:
                    self._add_info(f"[#FF5555]切换失败: {selection} 不可用[/]")

        self.push_screen(screen, handle_result)

    # ── 输入处理 ────────────────────────────────────────────

    def on_chat_input_command_hint(self, event: ChatInput.CommandHint) -> None:
        """输入 / 时显示命令提示"""
        text = event.text.lower()
        if not text:
            self._update_prompt_info()
            return
        hints = [
            f"[bold #BD93F9]{c[0]}[/] {c[1]}"
            for c in ChatInput.SLASH_COMMANDS
            if c[0].startswith(text)
        ]
        if hints:
            self.query_one("#prompt-info", Static).update("  ".join(hints))
        else:
            self._update_prompt_info()

    async def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        user_input = event.text.strip()

        # ── 计划确认模式 ──
        if self._plan_confirm_event is not None:
            if user_input.lower() in ("ok", "确认", "yes", "y", ""):
                self._plan_confirm_result = True
                if user_input.strip():
                    # 用户输入了额外内容
                    if self._plan_feedback:
                        self._plan_feedback += "; " + user_input.strip()
                    else:
                        self._plan_feedback = user_input.strip()
                # 否则保留 _plan_feedback（可能来自决策选择器）
                self._plan_confirm_event.set()
                self._set_agent_status("计划已确认，开始执行...")
            elif self._plan_has_decisions and self._plan_had_picker:
                # DecisionPicker 已用过 → 用户输入作为补充反馈
                self._plan_confirm_result = True
                if self._plan_feedback:
                    self._plan_feedback += "; " + user_input.strip()
                else:
                    self._plan_feedback = user_input.strip()
                self._plan_confirm_event.set()
                self._set_agent_status("已采纳决策，开始执行...")
            else:
                # 无 DecisionPicker → 自然语言输入取消计划，转为新查询
                self._plan_confirm_result = False
                self._plan_confirm_event.set()
                self._pending_query = user_input
                self._add_info("[#F1FA8C]计划已取消，正在处理新输入...[/]")
            return

        if not user_input:
            return

        bare = user_input.lower()
        if bare in BARE_COMMANDS:
            self._handle_bare(bare)
            return

        if user_input.startswith("/"):
            self._handle_slash(user_input)
            return

        if self._busy:
            # 交互追问：不阻塞，将输入作为 feedback 注入正在执行的 Agent
            self._add_user_message(user_input)
            self._tui_feedback.append(user_input)
            self._add_info("[#F1FA8C]💬 追问已发送，Agent 将在下一步思考时回应...[/]")
            return

        if not self._first_query:
            self._add_info("-" * 40)
        self._first_query = False

        self._add_user_message(user_input)

        self._busy = True
        self._start_tool_log()
        self._agent_worker(query=user_input)

    # ── Agent Worker ─────────────────────────────────────────

    @work(thread=True, exclusive=True)
    def _agent_worker(self, query: str) -> None:
        import time
        from pathlib import Path
        from vaxport.agent import ProgressCallbacks
        from vaxport.session import build_audit_entry, write_audit_log

        app_ref = self

        class TUICallbacks(ProgressCallbacks):
            def __init__(self):
                super().__init__()
                self._answer_parts: list[str] = []
                self._chunk_count = 0
                self._answer_start_idx = 0  # 最终答案在 _answer_parts 中的起始索引
                self._plan_widget = None        # Markdown（规划流式阶段）
                self._plan_parts: list[str] = []
                # MarkdownStream 字段（由 App._setup_answer_stream 初始化）
                self._answer_widget = None      # Markdown — 答案 widget
                self._chunk_queue = None        # asyncio.Queue — 答案流队列
                self._stream = None             # MarkdownStream — 答案流
                # Plan 流式节流
                self._last_flush_time = 0.0
                self._last_flush_plan_count = 0

            def set_plan_widget(self, widget) -> None:
                """绑定规划阶段流式输出 widget（Markdown）"""
                self._plan_widget = widget

            def get_pending_feedback(self) -> str | None:
                """返回执行中用户的追问消息"""
                if app_ref._tui_feedback:
                    combined = "; ".join(app_ref._tui_feedback)
                    app_ref._tui_feedback = []
                    return combined
                return None

            def get_streamed_content(self) -> str:
                """返回最终答案部分（排除中间思考文本）"""
                return "".join(self._answer_parts[self._answer_start_idx:])

            def mark_answer_start(self) -> None:
                """标记当前位置为最终答案起点，之前的文本属于思考过程"""
                self._answer_start_idx = len(self._answer_parts)

            def clear_thinking(self) -> None:
                """已取消 thinking widget，此方法为空操作（保留以兼容基类调用）。"""
                pass

            def finalize_stream(self) -> str:
                """停止 MarkdownStreams，返回累积的完整答案文本。"""
                if self._chunk_queue:
                    self._chunk_queue.put_nowait(None)  # 哨兵
                    self._chunk_queue = None
                self._stream = None
                return "".join(self._answer_parts)

            def _flush_plan(self) -> None:
                """渐进更新 plan Markdown（全量刷新 + 节流）"""
                new_n = len(self._plan_parts) - self._last_flush_plan_count
                if new_n <= 0 or not self._plan_widget:
                    return
                import time
                now = time.time()
                if new_n < 5 and (now - self._last_flush_time) < 0.05:
                    return
                full_text = "".join(self._plan_parts)
                # 将 "### 一、" 类章节标题转为无序列表项，降级展示
                display_text = re.sub(
                    r'^### ([一二三四五六七八九十]+、)',
                    r'- **\1**',
                    full_text,
                    flags=re.MULTILINE,
                )
                widget = self._plan_widget

                def _do_flush_plan():
                    widget.update(display_text)
                    try:
                        conv = app_ref.query_one("#conversation", VerticalScroll)
                        conv.scroll_end(animate=False)
                    except Exception:
                        pass

                app_ref.call_from_thread(_do_flush_plan)
                self._last_flush_plan_count = len(self._plan_parts)
                self._last_flush_time = now

            def on_tool_call(self, tool_name, arguments):
                # 仅更新状态栏，不在对话区展示
                app_ref.call_from_thread(
                    app_ref._set_agent_status, f"⚙ 执行: {tool_name}"
                )

            def on_tool_result(self, row_count, truncated=False):
                note = " (已截断)" if truncated else ""
                app_ref.call_from_thread(
                    app_ref._set_agent_status, f"   ↳ {row_count} 行结果{note}"
                )

            def on_thinking(self, description=""):
                # 仅更新状态栏，不在对话区展示思考内容
                if "SQL" in description or "查询" in description:
                    app_ref.call_from_thread(
                        app_ref._set_agent_status, "⚙ 正在执行数据查询..."
                    )
                elif "分析" in description:
                    app_ref.call_from_thread(
                        app_ref._set_agent_status, "📊 正在分析数据，生成报告..."
                    )
                elif description:
                    app_ref.call_from_thread(
                        app_ref._set_agent_status, description
                    )

            def on_thinking_text(self, text: str):
                """ReAct 中间回合的思考文本 — 以无序编号 + 浅色样式展示"""
                # 格式化为无序列表，每段思考内容一个 -
                lines = text.strip().split("\n")
                bullet_lines = []
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    bullet_lines.append(f"- {line}")
                formatted = "\n".join(bullet_lines)
                app_ref.call_from_thread(
                    app_ref._show_thinking_text, formatted
                )

            def on_thinking_chunk(self, text: str):
                """ReAct 思考文本流式块 → 直接进入 answer widget（已取消独立 thinking widget）。"""
                self.on_text_chunk(text)

            def on_text_chunk(self, text: str):
                """ReAct 分析阶段流式文本块 — 入队供 MarkdownStream 消费"""
                self._answer_parts.append(text)
                self._chunk_count += 1
                if self._chunk_queue:
                    self._chunk_queue.put_nowait(text)

            def on_plan_chunk(self, text: str):
                """规划阶段流式文本块"""
                self._plan_parts.append(text)
                self._flush_plan()

            def on_plan(self, plan_text: str) -> bool:
                """PRE-HOOK: 暂停等待用户确认计划"""
                decisions = _parse_plan_decisions(plan_text)
                has_decisions_text = (
                    "待用户决策" in plan_text
                    and "无需用户决策" not in plan_text
                )
                event = threading.Event()
                app_ref._plan_confirm_event = event
                app_ref._plan_confirm_result = False
                app_ref._plan_confirm_plan = plan_text
                app_ref._plan_feedback = ""
                app_ref._plan_has_decisions = has_decisions_text
                app_ref._plan_had_picker = bool(decisions)

                if decisions:
                    app_ref.call_from_thread(
                        app_ref._start_decision_picker, plan_text, decisions
                    )
                elif self._plan_widget and self._plan_parts:
                    # 计划已流式显示 → 仅需确认提示，不重复挂载
                    def _add_confirm_prompt():
                        app_ref._plan_confirm_widget = app_ref._add_info(
                            "-" * 40 + "\n**[Enter] 确认执行 | [Esc] 取消**"
                        )
                    app_ref.call_from_thread(_add_confirm_prompt)
                else:
                    app_ref.call_from_thread(
                        app_ref._show_plan_for_confirm, plan_text
                    )

                # 等待用户确认（最长 5 分钟）
                event.wait(timeout=300)
                # 移除确认提示 widget
                if app_ref._plan_confirm_widget:
                    app_ref.call_from_thread(app_ref._plan_confirm_widget.remove)
                    app_ref._plan_confirm_widget = None
                app_ref._plan_confirm_event = None
                # 将用户反馈传回 callbacks
                self.plan_feedback = app_ref._plan_feedback
                return app_ref._plan_confirm_result

        callbacks = TUICallbacks()
        start_time = time.time()

        # ── 创建流式输出 widget ──
        widget_ready = threading.Event()

        def _create_stream_widgets():
            conv = self.query_one("#conversation", VerticalScroll)
            plan_w = Markdown("", classes="thinking-content")
            conv.mount(plan_w)
            callbacks.set_plan_widget(plan_w)
            # 答案 stream widget（所有 LLM 流式输出统一走 answer widget）
            answer_w = Markdown("")
            conv.mount(answer_w)
            self._setup_answer_stream(callbacks, answer_w)
            widget_ready.set()

        self.call_from_thread(_create_stream_widgets)
        widget_ready.wait(timeout=5)

        # 构造对话历史（供 classify 和 run 共用）
        history = None
        if self.session and self.session.messages:
            history = []
            if self.session.summary:
                history.append({"role": "system", "content": f"📋 会话摘要:\n{self.session.summary}"})
            history.extend(self.session.messages[-20:])

        # ── 方案1: 思考提示中显示 Agent ──
        route = self.orchestrator.classify(query, history=history)
        agent_type = route["intent"]
        _, agent_label, _ = AGENT_LABELS.get(agent_type, ("", "通用", ""))

        self.call_from_thread(
            self._set_agent_status,
            f"🔍 {agent_label} Agent 思考中..."
        )
        # 清除取消旗标
        self._execution_cancel.clear()
        result = self.orchestrator.run(query, callbacks=callbacks,
                                plan_mode=self._plan_mode, history=history,
                                cancel_event=self._execution_cancel)
        self._last_result = result
        # 停止 MarkdownStream，刷新最后的内容
        callbacks.finalize_stream()
        elapsed_ms = int((time.time() - start_time) * 1000)

        try:
            entry = build_audit_entry(
                user=Path.home().name,
                model=f"{self.llm.active_model}({self.llm.active_backend})",
                query=query,
                sql_list=result.get("sql_queries", []),
                row_count=0,
                duration_ms=elapsed_ms,
                answer=result["answer"],
            )
            write_audit_log(entry)
        except Exception:
            pass

        if self.session:
            self.session.add_message("user", query)
            self.session.add_message("assistant", result["answer"])
            # 增量更新会话摘要（超过 20 条消息时，每 10 条新增触发一次）
            if self.session.needs_summary_update():
                self._update_session_summary()
            # 每次查询后自动保存到固定文件，防止进程异常退出丢数据
            try:
                self.session.auto_save()
            except Exception:
                pass

            # 自动提取用户纠正反馈（异步，不阻塞）
            if self._feedback_memory.needs_extraction(query):
                self._extract_feedback(query)

        self.call_from_thread(self._show_agent_result, result, callbacks)

    def _extract_feedback(self, query: str) -> None:
        """异步提取用户纠正反馈并存储"""
        if not self.llm:
            return
        try:
            rule = self._feedback_memory.extract_and_store(query, self.llm)
            if rule:
                self._add_info(f"[#BD93F9]📝 已记住: {rule}[/]")
                # 同步到 orchestrator 的所有 Agent
                memory_text = self._feedback_memory.build_system_prompt_section()
                if self.orchestrator:
                    self.orchestrator.set_memory_context(memory_text)
        except Exception:
            pass

    def _update_session_summary(self) -> None:
        """用 LLM 增量更新会话摘要，保持旧上下文不丢失"""
        if not self.session:
            return
        old_summary = self.session.summary
        # 取距上次摘要以来的新消息（最多 20 条）
        start_idx = self.session._summary_msg_count
        new_msgs = self.session.messages[start_idx:]
        msgs_text = "\n".join(
            f"[{m['role']}]: {m.get('content', '')[:300]}"
            for m in new_msgs[-20:]
        )
        prompt = (
            "你是会话摘要助手。请将以下新旧信息合并为一段简洁的会话摘要（300 字以内），"
            "保留关键信息：用户的所有问题、数据查询结果的核心发现、重要结论。"
            "只输出摘要文本，不要加标题。"
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"旧摘要：{old_summary or '(无)'}\n\n新对话：\n{msgs_text}\n\n请生成合并后的摘要："},
        ]
        try:
            resp = self.llm.chat_completion(messages=messages, tools=None, stream=False)
            new_summary = (resp.choices[0].message.content or "").strip()
            if new_summary:
                self.session.update_summary(new_summary)
        except Exception:
            pass  # 摘要更新失败不影响主流程

    # ── MarkdownStream 流式输出 ──────────────────────────────

    def _setup_answer_stream(self, callbacks, answer_widget):
        """初始化 MarkdownStream 并启动异步消费 worker（从 worker 线程通过 call_from_thread 调用）。"""
        from textual.widgets import Markdown
        queue: asyncio.Queue = asyncio.Queue()
        stream = Markdown.get_stream(answer_widget)
        callbacks._chunk_queue = queue
        callbacks._stream = stream
        callbacks._answer_widget = answer_widget
        self._run_answer_stream(queue, stream)

    @work
    async def _run_answer_stream(self, queue: asyncio.Queue, stream):
        """消费 chunk 队列，逐 chunk 写入 MarkdownStream，实现 O(1) 增量追加。"""
        conv = self.query_one("#conversation", VerticalScroll)
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:  # 哨兵：停止流
                    break
                await stream.write(chunk)
                conv.scroll_end(animate=False)
        except asyncio.CancelledError:
            pass
        finally:
            await stream.stop()

    # ── Agent 结果展示 ──────────────────────────────────────

    def _show_agent_result(self, result: dict, callbacks=None) -> None:
        answer = result.get("answer", "")
        # 兜底修复：LLM 可能把 Unix 绝对路径改写为 Windows 格式 (C:\Users\...)
        answer = _fix_windows_paths(answer)
        self._last_answer = answer

        # 优先使用流式累积的完整内容，回退到 result["answer"]
        streamed = ""
        if callbacks and hasattr(callbacks, 'get_streamed_content'):
            streamed = callbacks.get_streamed_content()
        display_text = streamed if streamed else answer

        # ── 方案3: 回答区注入 Agent 标签 ──
        agent_chain = result.get("agent_chain", [])
        if agent_chain and display_text.strip():
            tags = []
            seen = set()
            for atype in agent_chain:
                if atype not in seen:
                    seen.add(atype)
                    icon, label, _ = AGENT_LABELS.get(atype, ("🤖", "通用", ""))
                    tags.append(f"{icon} **{label} Agent**")
            if tags:
                tag_line = " | ".join(tags)
                display_text = f"> {tag_line}\n{display_text.strip()}"
            else:
                display_text = display_text.strip()

        # 流式已完成 → 应用路径修复，更新最终展示内容
        if callbacks and callbacks._answer_widget and display_text:
            try:
                callbacks._answer_widget.update(display_text)
            except Exception:
                pass
        elif display_text:
            self._add_agent_response(display_text)

        # 清理 SKIP_PLAN 残留 + 正常计划执行完成后隐藏（避免与答案重叠展示）
        if callbacks and callbacks._plan_widget:
            plan_content = "".join(callbacks._plan_parts)
            if plan_content.strip() == "SKIP_PLAN":
                try:
                    callbacks._plan_widget.remove()
                except Exception:
                    pass
            else:
                # 执行完成，清空 plan widget 内容避免与答案重复展示
                try:
                    callbacks._plan_widget.update("")
                except Exception:
                    pass

        if self.debug_mode:
            self._add_info(
                f"轮次 {result.get('turns', 0)}  "
                f"Token {result.get('tokens_used', 0)}/{result.get('context_window', 0)}"
                f" ({result.get('token_pct', 0)}%)  "
                f"模型 {result.get('model', 'N/A')}"
            )
            for i, sql in enumerate(result.get("sql_queries", []), 1):
                self._add_info(f"SQL #{i}: {sql}")

        token_pct = result.get("token_pct", 0)
        if token_pct > 80:
            self._add_info(
                f"[#F1FA8C]! Token 用量 {token_pct}%，接近上下文窗口上限[/]"
            )

        self._update_prompt_info()
        self._busy = False
        self._set_agent_status("")  # 清除状态栏

        # 处理计划取消后暂存的新查询
        if self._pending_query:
            pending = self._pending_query
            self._pending_query = ""
            self._add_user_message(pending)
            self._busy = True
            self._start_tool_log()
            self._agent_worker(query=pending)

    # ── 裸词命令 ─────────────────────────────────────────────

    def _handle_bare(self, bare: str) -> None:
        if bare in ("exit", "quit"):
            if self.session:
                self.session.save()
            self._add_info("会话已保存。再见。")
            self.exit()
        elif bare == "help":
            self._show_help()
        elif bare == "clear":
            from vaxport.session import Session
            self.session = Session()
            self._last_result = {}
            self._first_query = True
            self._clear_conversation()
            self._show_welcome()
            self._update_prompt_info()
        elif bare == "status":
            self._show_status()
        elif bare == "tables":
            self._show_tables()
        elif bare == "skills":
            self._show_skills()
        elif bare == "tools":
            self._show_tools()
        elif bare == "model":
            self._show_model()
        elif bare == "debug":
            self.debug_mode = not self.debug_mode
            self._add_info(f"调试模式 {'ON' if self.debug_mode else 'OFF'}")
            self._update_prompt_info()
        elif bare == "history":
            self._show_history()
        elif bare == "copy":
            self.action_copy_last_answer()

    # ── / 命令 ───────────────────────────────────────────────

    def _handle_slash(self, cmd_line: str) -> None:
        parts = cmd_line.strip().split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if cmd in ("/exit", "/quit"):
            self._handle_bare("exit")
        elif cmd == "/help":
            self._show_help()
        elif cmd == "/model":
            self._cmd_model(args)
        elif cmd == "/status":
            self._show_status()
        elif cmd == "/skills":
            self._show_skills()
        elif cmd == "/tools":
            self._show_tools()
        elif cmd == "/clear":
            self._handle_bare("clear")
        elif cmd == "/history":
            self._show_history()
        elif cmd == "/debug":
            self._handle_bare("debug")
        elif cmd == "/save":
            if self.session:
                path = self.session.save(args if args else None)
                self._add_info(f"会话已保存: {path}")
        elif cmd == "/copy":
            self.action_copy_last_answer()
        elif cmd == "/export":
            self._cmd_export(args)
        elif cmd == "/refresh-schema":
            self._cmd_refresh_schema()
        else:
            self._add_info(f"[#FF5555]未知命令: {cmd}[/]")

        self._update_prompt_info()

    # ── 各命令实现 ───────────────────────────────────────────

    def _show_help(self) -> None:
        self._add_agent_response("""## 可用命令

| 命令 | 说明 |
|------|------|
| `exit`, `quit` | 退出程序 |
| `help` | 显示此帮助 |
| `clear` | 清空对话上下文 |
| `copy` | 复制最后一次回答 (Ctrl+Y) |
| `status` | 显示当前状态 |
| `tables` | 数据库表概览 |
| `skills` | 已加载 SKILL 列表 |
| `tools` | 可用查询工具 |
| `model` | 显示当前模型 |
| `debug` | 切换调试模式 |
| `history` | 对话历史摘要 |
| `/save [name]` | 保存当前会话 |
| `/export [name]` | 导出最后一次回答为 Markdown |
| `/refresh-schema` | 重新扫描数据库 schema |

**快捷键**: Ctrl+P 模型  |  Ctrl+T 规划/执行  |  Ctrl+S 侧边栏  |  Ctrl+Y 复制""")

    def _show_status(self) -> None:
        if not self.llm:
            self._add_info("LLM 未初始化")
            return
        backend_status = self.llm.get_status()
        last = self._last_result or {}
        db_status = (
            f"{self.cfg.pg_host}/{self.cfg.pg_database}"
            f" ({'已连接' if self._is_db_connected else '未连接'})"
        )
        backend_lines = "\n".join(
            f"- {n}: {i['model']} {'✓' if i['active'] else '✗'}"
            for n, i in backend_status.items()
        )

        self._add_agent_response(f"""## 状态

- **模型**: {self.llm.active_model} ({self.llm.active_backend})
- **Token**: {last.get('token_pct', 0)}% ({last.get('tokens_used', 0)}/{last.get('context_window', 0)})
- **PG 连接**: {db_status}
- **会话轮次**: {last.get('turns', 0)}
- **已加载 SKILL**: {self.skills.count if self.skills else 0}
- **调试模式**: {'ON' if self.debug_mode else 'OFF'}

### 后端

{backend_lines}""")

    def _show_tables(self) -> None:
        if not (self._is_db_connected and self.tools):
            self._add_info("数据库未连接，无表信息。")
            return
        summary = self.tools.get_schema_summary()
        if not summary:
            self._add_info("未发现用户表/视图")
            return

        lines = ["## 数据库表概览", ""]
        for schema in sorted(summary.keys()):
            info = summary[schema]
            lines.append(f"### {schema}")
            if info.get("tables"):
                lines.append(f"- 表 ({len(info['tables'])}): {', '.join(sorted(info['tables']))}")
            if info.get("views"):
                lines.append(f"- 视图 ({len(info['views'])}): {', '.join(sorted(info['views']))}")
            if info.get("matviews"):
                lines.append(f"- 物化视图 ({len(info['matviews'])}): {', '.join(sorted(info['matviews']))}")
            lines.append("")

        self._add_agent_response("\n".join(lines))

    def _show_skills(self) -> None:
        if not self.skills:
            self._add_info("没有加载 SKILL")
            return
        skills = sorted(self.skills.list_skills(),
                        key=lambda s: (s.name or s.dir_name).lower())
        lines = [f"## 已加载 {len(skills)} 个 SKILL", ""]
        for s in skills:
            name = s.name or s.dir_name
            lines.append(f"- **{name}** {s.availability_badge} — {s.short_desc}")

        self._add_agent_response("\n".join(lines))

    def _show_tools(self) -> None:
        if not self.tools:
            self._add_info("没有注册工具")
            return
        tools = self.tools.list_tools()
        lines = [f"## 已注册 {len(tools)} 个查询工具", ""]
        for t in tools:
            lines.append(f"- **{t['name']}** — {t['description'][:80]}")

        self._add_agent_response("\n".join(lines))

    def _show_model(self) -> None:
        if not self.llm:
            return
        self._add_agent_response(f"""## 当前模型

- **后端**: {self.llm.active_backend}
- **模型**: {self.llm.active_model}
- **可用后端**: {', '.join(self.llm.available_backends)}""")

    def _show_history(self) -> None:
        if not self.session:
            self._add_info("无会话")
            return
        summary = self.session.get_history_summary()
        if summary:
            self._add_agent_response(
                f"## 对话历史 ({len(self.session.messages)} 条消息)\n\n{summary}"
            )
        else:
            self._add_info("当前会话无历史")

    def _cmd_model(self, args: str) -> None:
        if not self.llm:
            return
        if not args:
            self._show_model()
            return
        backend_map = {"aliyun": "aliyun", "local": "ollama", "ollama": "ollama"}
        target = backend_map.get(args.lower())
        if not target:
            self._add_info(f"[#FF5555]未知后端: {args}。可用: aliyun, local[/]")
            return
        if self.llm.switch_backend(target):
            self._add_info(f"已切换到 {target}，模型: {self.llm.active_model}")
            self._update_prompt_info()
        else:
            self._add_info(f"[#FF5555]后端 {target} 不可用[/]")

    def _cmd_export(self, args: str) -> None:
        """导出最后一次回答为 Markdown 文件，图片一并打包"""
        if not self._last_answer:
            self._add_info("[#FF5555]没有可导出的内容[/]")
            return
        import re
        import shutil
        from datetime import datetime
        from pathlib import Path

        export_dir = self.cfg.export_dir
        name = args.strip() if args.strip() else f"vaxport_export_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}"
        export_subdir = export_dir / name
        export_subdir.mkdir(parents=True, exist_ok=True)

        content = self._last_answer
        images_dir = export_subdir / "images"
        copied = 0

        # 查找所有 Markdown 图片引用: ![title](path)
        for m in re.finditer(r'!\[([^\]]*)\]\(([^)]+)\)', content):
            src_path = m.group(2)
            # 只处理 ~/.vaxport/charts/ 下的本地图片
            if "/.vaxport/charts/" not in src_path and "vaxport/charts" not in src_path:
                continue
            src = Path(src_path).expanduser()
            if not src.exists():
                continue
            images_dir.mkdir(exist_ok=True)
            dst = images_dir / src.name
            if not dst.exists():
                shutil.copy2(src, dst)
                copied += 1
            # 替换为相对路径
            rel_path = f"images/{src.name}"
            content = content.replace(src_path, rel_path)

        filepath = export_subdir / f"{name}.md"
        filepath.write_text(content, encoding="utf-8")
        self._add_info(f"已导出: {filepath}" + (f" (含 {copied} 张图片)" if copied else ""))

    def _cmd_refresh_schema(self) -> None:
        if not self._is_db_connected:
            self._add_info("[#FF5555]数据库未连接，无法刷新 schema[/]")
            return
        try:
            if self.mdb and self.mdb.is_connected:
                for name in self.mdb.names:
                    db = self.mdb.get(name)
                    self.tools.discover_and_register(db=db, db_name=name)
            else:
                self.tools.discover_and_register()
            self._add_info(f"Schema 已刷新，共 {len(self.tools.list_tools())} 个查询工具")
            self._populate_sidebar()
            # 同步更新 system prompt 中的数据库概况
            db_overview = self._build_db_overview()
            if db_overview and self.orchestrator:
                self.orchestrator.set_db_context(db_overview)
                self._add_info("数据库概况已同步更新")
        except Exception as e:
            self._add_info(f"[#FF5555]Schema 刷新失败: {e}[/]")

    def _build_db_overview(self) -> str:
        """构建数据库表概况，注入 system prompt。LLM 查询前知道每表大概行数。"""
        dbs = []
        if self.mdb and self.mdb.is_connected:
            for name in self.mdb.names:
                dbs.append((name, self.mdb.get(name)))
        elif self.db and self.db.is_connected:
            dbs.append((self.cfg.pg_database, self.db))

        if not dbs:
            return ""

        lines = [
            "## 数据库表概况",
            "查询前检查行数：≤100 行可直接查全表，>500 行建议加 WHERE 过滤。",
            "[s]=≤100 [m]=100~1000 [l]=≥1000",
            "",
        ]

        for db_name, db in dbs:
            lines.append(f"### {db_name}")
            estimates = db.get_table_row_estimates()
            by_schema: dict[str, list] = {}
            for full_name, info in estimates.items():
                schema, table = full_name.split(".", 1)
                by_schema.setdefault(schema, []).append((table, info))

            for schema in sorted(by_schema.keys()):
                lines.append(f"  {schema}:")
                for table, info in sorted(by_schema[schema], key=lambda x: -x[1]["rows_estimate"]):
                    n = info["rows_estimate"]
                    tag = "s" if n <= 100 else "m" if n <= 1000 else "l"
                    col_names = [c["name"] if isinstance(c, dict) else c for c in info["columns"]]
                    cols = ", ".join(col_names[:8])
                    if len(col_names) > 8:
                        cols += ", ..."
                    flag = " ←大表，加过滤!" if n >= 1000 else ""
                    lines.append(f"    [{tag}~{n}] {table}: {cols}{flag}")
            lines.append("")

        return "\n".join(lines)