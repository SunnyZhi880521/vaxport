"""终端 UI — Claude Code 风格的 rich 彩色输出"""

from rich.console import Console
from rich.panel import Panel
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text
from rich.rule import Rule
from rich import box

console = Console()


def print_welcome(model: str, backend: str, pg_host: str, pg_db: str, skills_count: int):
    """启动欢迎信息 — 简洁风格"""
    console.print()
    console.print(
        Panel(
            f"[bold bright_green]vaxport[/]  "
            f"[dim]疫苗企业数据分析终端[/]\n\n"
            f"[dim]模型 [/]{model} [dim]@[/] {backend}\n"
            f"[dim]数据 [/]{pg_db} [dim]@[/] {pg_host}\n"
            f"[dim]技能 [/]{skills_count} 个 SKILL",
            border_style="bright_black",
            padding=(1, 2),
        )
    )
    console.print()


def print_turn_separator():
    """对话轮次分隔线"""
    console.print(Rule(style="bright_black"), no_wrap=True)


def print_thinking():
    """思考中提示 — 打印在对话区，后续输出自然覆盖"""
    console.print("[dim]⏳ 思考中...[/]")


def clear_thinking():
    """清除思考提示（改用正常换行后无需清除，保留空函数兼容调用）"""
    pass


def print_llm_answer(text: str):
    """LLM 回答 — markdown 渲染"""
    console.print()
    md = Markdown(text, inline_code_theme="monokai")
    console.print(md)
    console.print()


def print_tool_call(tool_name: str, arguments: dict):
    """工具调用 — 折叠样式"""
    short_args = _format_args(arguments)
    console.print(f"  [bright_black]⚙ [/][dim]{tool_name}[/] [bright_black]({short_args})[/]")


def print_tool_result(row_count: int, truncated: bool = False):
    """工具结果摘要"""
    note = " [yellow](已截断)[/]" if truncated else ""
    console.print(f"  [bright_black]  ↳ [/][dim]{row_count} 行结果{note}[/]")


def print_sql(sql: str):
    """SQL 语句 — 调试模式"""
    console.print(f"  [bright_black]  SQL: [/][blue]{sql}[/]")


def print_error(text: str):
    """错误 — 红色醒目"""
    console.print(f"\n  [red]✗ {text}[/]")


def print_warning(text: str):
    """警告"""
    console.print(f"\n  [yellow]! {text}[/]")


def print_info(text: str):
    """信息 — 暗色"""
    console.print(f"  [dim]{text}[/]")


def print_status(status: dict):
    """/status 命令输出"""
    table = Table(
        title=None,
        box=box.SIMPLE,
        border_style="bright_black",
        show_header=False,
        padding=(0, 2),
    )
    table.add_column("项", style="dim")
    table.add_column("值", style="white")

    table.add_row("模型", f"{status.get('model', 'N/A')} [dim]({status.get('backend', 'N/A')})[/]")
    table.add_row("Token", f"{status.get('token_pct', 0)}% ({status.get('tokens_used', 0)}/{status.get('context_window', 0)})")
    table.add_row("PG 连接", status.get("pg_status", "未连接"))
    table.add_row("会话轮次", str(status.get("turns", 0)))
    table.add_row("已加载 SKILL", str(status.get("skills_count", 0)))
    table.add_row("调试模式", "ON" if status.get("debug", False) else "OFF")

    console.print()
    console.print(Panel(table, title="状态", border_style="bright_black", padding=(1, 2)))
    console.print()


def print_schema_overview(summary: dict):
    """数据库 schema 概览面板 — 按 数据库→schema 归类展示表/视图"""
    if not summary:
        return

    # 构建归类文字
    lines = []
    # 格式: {db_name: {schema: {"tables":[], "views":[], "matviews":[]}}}
    for db_name, schemas in sorted(summary.items()):
        for schema, info in sorted(schemas.items()):
            sections = []

            if info.get("tables"):
                tables_str = ", ".join(sorted(info["tables"]))
                sections.append(f"[white]表[/] [dim]({len(info['tables'])})[/]: {tables_str}")

            if info.get("views"):
                views_str = ", ".join(sorted(info["views"]))
                sections.append(f"[cyan]视图[/] [dim]({len(info['views'])})[/]: {views_str}")

            if info.get("matviews"):
                mv_str = ", ".join(sorted(info["matviews"]))
                sections.append(f"[bright_cyan]物化视图[/] [dim]({len(info['matviews'])})[/]: {mv_str}")

            if sections:
                block = f"[bold bright_green]{db_name}/{schema}[/]\n" + "\n".join(f"  {s}" for s in sections)
                lines.append(block)

    if not lines:
        console.print("[dim]未发现用户表/视图[/]")
        return

    body = "\n\n".join(lines)
    console.print()
    console.print(
        Panel(body, title="数据库表概览", border_style="bright_black", padding=(1, 2))
    )
    console.print()

def print_skills_list(skills):
    """/skills 命令输出"""
    if not skills:
        console.print("[dim]没有加载 SKILL[/]")
        return

    table = Table(
        box=box.SIMPLE,
        border_style="bright_black",
        show_header=True,
    )
    table.add_column("名称", style="cyan", no_wrap=True)
    table.add_column("描述", style="white")
    table.add_column("", style="dim", width=10)

    for s in skills:
        table.add_row(
            s.name or s.dir_name,
            s.short_desc,
            s.availability_badge,
        )

    console.print()
    console.print(Panel(table, title=f"已加载 {len(skills)} 个 SKILL", border_style="bright_black", padding=(1, 2)))
    console.print()


def print_tools_list(tools: list[dict]):
    """/tools 命令输出"""
    console.print()
    console.print(f"[dim]已注册 {len(tools)} 个查询工具:[/]")
    for t in tools:
        console.print(f"  [cyan]{t['name']}[/] [dim]— {t['description'][:80]}[/]")


def print_sessions_list(sessions: list[dict]):
    """会话列表"""
    if not sessions:
        console.print("[dim]没有保存的会话[/]")
        return

    table = Table(
        box=box.SIMPLE,
        border_style="bright_black",
    )
    table.add_column("时间", style="dim")
    table.add_column("查询摘要", style="white")
    table.add_column("消息", style="dim", justify="right")

    for s in sessions[:20]:
        table.add_row(
            s["start_time"][:19],
            s["first_query"][:80],
            str(s["message_count"]),
        )

    console.print()
    console.print(Panel(table, title="已保存会话", border_style="bright_black", padding=(1, 2)))
    console.print()


def print_debug_info(agent_result: dict):
    """调试信息面板"""
    console.print()
    console.print(
        Panel(
            f"[dim]轮次[/] {agent_result.get('turns', 0)}  "
            f"[dim]Token[/] {agent_result.get('tokens_used', 0)}/{agent_result.get('context_window', 0)}"
            f" ({agent_result.get('token_pct', 0)}%)  "
            f"[dim]模型[/] {agent_result.get('model', 'N/A')}\n"
            + "\n".join(
                f"[dim]SQL #{i}:[/] [blue]{sql}[/]"
                for i, sql in enumerate(agent_result.get("sql_queries", []), 1)
            ),
            title="debug",
            border_style="yellow",
            padding=(0, 1),
        )
    )


def hud_text(model: str, backend: str, db_info: str, token_pct: int, turns: int, debug: bool) -> str:
    """生成 HUD 状态栏文本"""
    parts = [
        f" {model}({backend}) ",
        f" · {db_info} ",
        f" · {token_pct}% ctx ",
        f" · 轮{turns}",
    ]
    if debug:
        parts.append(" · DEBUG")
    return "".join(parts)


def _format_args(args: dict) -> str:
    """格式化工具参数为简短字符串"""
    if not args:
        return ""
    items = []
    for k, v in args.items():
        if v is None or v == "":
            continue
        s = str(v)
        if len(s) > 20:
            s = s[:18] + ".."
        items.append(f"{k}={s}")
    return ", ".join(items[:3])