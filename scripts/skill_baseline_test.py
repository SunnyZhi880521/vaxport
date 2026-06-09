#!/usr/bin/env python
"""SKILL 基线测试执行器 — 直接调用 vaxport orchestrator 运行测试集

使用方式：
  python scripts/skill_baseline_test.py                  # 运行全部 15 题 × 3 次
  python scripts/skill_baseline_test.py --questions 3    # 只运行前 3 题
  python scripts/skill_baseline_test.py --runs 1         # 每题只运行 1 次
  python scripts/skill_baseline_test.py --resume         # 从中断处继续
  python scripts/skill_baseline_test.py --ids H2 H5 H8   # 只运行指定题目
"""

import argparse
import json
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_framework import (
    ProgressManager, TestResult, load_phase0_questions, load_new_skill_questions,
    ALL_PHASE0_IDS, ALL_NEW_SKILL_IDS,
)

BASELINE_DIR = Path(__file__).parent.parent / "tests" / "baseline"
SKILL_DIR = Path(__file__).parent.parent / "tests" / "with_skill"
QUESTION_INTERVAL = 10


class CaptureCallbacks:
    """捕获 Agent 执行过程中的工具调用和 SQL"""

    def __init__(self):
        self.tool_calls = []
        self.sql_queries = []
        self.text_parts = []

    def on_tool_call(self, tool_name, arguments):
        self.tool_calls.append({"name": tool_name, "args": arguments})

    def on_sql(self, sql):
        self.sql_queries.append(sql)

    def on_text_chunk(self, text):
        self.text_parts.append(text)

    # 兼容 ProgressCallbacks 接口
    def on_thinking(self, description=""): pass
    def on_tool_result(self, row_count, truncated=False): pass
    def on_plan_chunk(self, text): pass
    def on_chart(self, file_path): pass
    def on_plan(self, plan_text): return True  # 自动确认计划


def setup_app():
    """初始化 vaxport App（同 CLI 的 setup 流程）"""
    from vaxport.config import load_config
    from vaxport.cli import App

    config = load_config()
    app = App(config)
    app.setup(quiet=True)
    return app


def run_single_query(app, query: str, timeout: int = 600, skill_enabled: bool = False) -> dict:
    """直接调用 orchestrator 执行查询"""
    start_time = time.time()
    callbacks = CaptureCallbacks()

    result = {
        "answer": "",
        "tool_calls": [],
        "sql_queries": [],
        "agent_type": "",
        "agent_chain": [],
        "turns": 0,
        "tokens_used": 0,
        "elapsed": 0,
        "error": None,
    }

    try:
        # 使用 vaxport 的 ProgressCallbacks
        from vaxport.agent import ProgressCallbacks

        class Bridge(ProgressCallbacks):
            def __init__(self, capture):
                super().__init__()
                self._c = capture
            def on_thinking(self, desc=""):
                print(f"    ⏳ {desc}"[:80], end="\r")
            def on_tool_call(self, name, args):
                self._c.on_tool_call(name, args)
                print(f"    ⚙ {name}")
            def on_tool_result(self, rows, trunc=False):
                pass
            def on_sql(self, sql):
                self._c.on_sql(sql)
            def on_text_chunk(self, text):
                self._c.on_text_chunk(text)
            def on_plan_chunk(self, text):
                pass
            def on_chart(self, path):
                pass
            def on_plan(self, plan_text):
                return True  # 自动确认

        bridge = Bridge(callbacks)

        orch_result = app.orchestrator.run(
            query,
            callbacks=bridge,
            plan_mode=False,
            skill_enabled=skill_enabled,
        )

        result["answer"] = orch_result.get("answer", "")
        result["agent_type"] = orch_result.get("agent_type", "")
        result["agent_chain"] = orch_result.get("agent_chain", [])
        result["turns"] = orch_result.get("turns", 0)
        result["tokens_used"] = orch_result.get("tokens_used", 0)
        result["tool_calls"] = callbacks.tool_calls
        result["sql_queries"] = callbacks.sql_queries
        result["skill_validation"] = orch_result.get("skill_validation", None)

    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        import traceback
        traceback.print_exc()

    result["elapsed"] = round(time.time() - start_time, 1)
    return result


def main():
    parser = argparse.ArgumentParser(description="SKILL 基线测试")
    parser.add_argument("--questions", type=int, default=0,
                        help="运行前 N 题（0=全部）")
    parser.add_argument("--runs", type=int, default=3,
                        help="每题运行次数（默认 3）")
    parser.add_argument("--resume", action="store_true",
                        help="从中断处继续")
    parser.add_argument("--interval", type=int, default=QUESTION_INTERVAL,
                        help="题间间隔秒数")
    parser.add_argument("--ids", nargs="+", default=None,
                        help="只运行指定题目 ID（如 H2 H5 H8）")
    parser.add_argument("--skill", action="store_true",
                        help="启用 SKILL 注入（默认关闭=基线模式）")
    parser.add_argument("--new-skill", action="store_true",
                        help="运行新 SKILL 测试集（H41-H45）而非 Phase 0")
    args = parser.parse_args()

    # 加载题目
    if args.new_skill:
        questions = load_new_skill_questions()
    else:
        questions = load_phase0_questions()
    if args.ids:
        qids = [qid for qid in args.ids if qid in questions]
    else:
        qids = list(questions.keys())
        if args.questions > 0:
            qids = qids[:args.questions]

    if not qids:
        print("ERROR: 未找到匹配的题目")
        sys.exit(1)

    output_dir = SKILL_DIR if args.skill else BASELINE_DIR
    mode_label = "with SKILL" if args.skill else "baseline (no SKILL)"

    print(f"{'=' * 60}")
    print(f"SKILL 测试 — {mode_label}")
    print(f"题目: {len(qids)} 题 × {args.runs} 次 = {len(qids) * args.runs} 次测试")
    print(f"输出: {output_dir}")
    print(f"{'=' * 60}")

    # 初始化进度
    progress = ProgressManager(output_dir)
    summary = progress.get_summary(len(qids), args.runs)
    print(f"进度: {summary['completed']}/{summary['total']} ({summary['pct']}%)")

    if summary["remaining"] == 0 and not args.ids:
        print("所有测试已完成！运行 --ids 指定题目或重新执行。")
        return

    # 初始化 vaxport
    print("\n初始化 vaxport...")
    app = setup_app()
    print(f"  模型: {app.llm.active_model} @ {app.llm.active_backend}")
    print(f"  数据库: {app.config.pg_database}")
    print(f"  工具: {len(app.tools.list_tools())} 个")

    # 注册信号
    def signal_handler(sig, frame):
        print("\n保存进度...")
        progress.save()
        sys.exit(0)
    signal.signal(signal.SIGINT, signal_handler)

    # 执行测试
    total_tests = len(qids) * args.runs
    completed = summary["completed"]

    for qi, qid in enumerate(qids):
        q = questions[qid]
        print(f"\n{'─' * 55}")
        print(f"[{qi+1}/{len(qids)}] {qid}: {q['title'][:50]}")
        print(f"  类别: {q['category']}")

        for run in range(1, args.runs + 1):
            if progress.is_done(qid, run):
                print(f"  Run {run}/{args.runs}: 已完成（跳过）")
                continue

            progress.set_current(qid, run)
            print(f"  Run {run}/{args.runs}: 执行中...")

            # 清空会话
            if hasattr(app, 'session') and app.session:
                from vaxport.session import Session
                app.session = Session()

            result = run_single_query(app, q["text"], skill_enabled=args.skill)

            if result["error"]:
                print(f"  ❌ {result['error']}")
                progress.mark_failed(qid, run, result["error"])
            else:
                # 保存结果
                test_result = TestResult(qid, run, q["text"])
                test_result.answer = result["answer"]
                test_result.tool_calls = result["tool_calls"]
                test_result.sql_queries = result["sql_queries"]
                test_result.agent_type = result["agent_type"]
                test_result.agent_chain = result["agent_chain"]
                test_result.turns = result["turns"]
                test_result.tokens_used = result["tokens_used"]
                test_result.elapsed_seconds = int(result["elapsed"])
                test_result.skill_validation = result.get("skill_validation")
                test_result.save(output_dir)

                progress.mark_complete(
                    qid, run,
                    elapsed=int(result["elapsed"]),
                    output_file=str(output_dir / qid / f"run_{run}.json"),
                    token_count=result["tokens_used"],
                )

                completed += 1
                pct = completed / total_tests * 100
                answer_preview = result["answer"][:100].replace("\n", " ")
                print(
                    f"  ✅ {result['elapsed']:.0f}s | "
                    f"{result['turns']}轮 | "
                    f"{len(result['tool_calls'])}工具 | "
                    f"{pct:.0f}%"
                )
                print(f"     输出预览: {answer_preview}...")

            if run < args.runs or qid != qids[-1]:
                time.sleep(args.interval)

    # 最终摘要
    summary = progress.get_summary(len(qids), args.runs)
    print(f"\n{'=' * 60}")
    print(f"基线测试完成")
    print(f"完成: {summary['completed']}/{summary['total']} ({summary['pct']}%)")
    print(f"失败: {summary['failed']}")
    print(f"\n生成报告:")
    print(f"  python scripts/generate_baseline_report.py")


if __name__ == "__main__":
    main()
