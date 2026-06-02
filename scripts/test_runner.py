#!/usr/bin/env python
"""vaxport 批量测试执行器 — 从 demo-questions.md 解析题目，自动运行，支持暂停/继续

自动卡死检测：每 60s 检查 vaxport 进程 CPU 使用率。
连续 5 次 CPU=0.0% 且已运行 >20 分钟 → 判定为卡死，自动 kill 并标记为 stuck。
"""

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

LOG_DIR = Path.home() / ".vaxport" / "test_logs"
PROGRESS_FILE = LOG_DIR / "progress.json"
DEMO_FILE = Path(__file__).parent.parent / "docs" / "demo-questions.md"

STUCK_CPU_CHECKS = 5       # 连续 N 次 CPU=0.0% 判定卡死
STUCK_MIN_ELAPSED = 1200   # 至少运行 20 分钟
MONITOR_INTERVAL = 60      # 每 60s 检查一次


def load_questions():
    """从 demo-questions.md 解析题目"""
    if not DEMO_FILE.exists():
        print(f"ERROR: {DEMO_FILE} not found")
        sys.exit(1)

    content = DEMO_FILE.read_text(encoding="utf-8")
    questions = {}

    pattern = r'### (S\d+|M\d+|H\d+)\. .*?\n\n> (.+?)\n\n---'
    for m in re.finditer(pattern, content, re.DOTALL):
        qid = m.group(1)
        text = m.group(2).replace('\n> ', '').strip()
        text = text.replace('> ', '')
        questions[qid] = text

    return questions


def load_progress():
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, encoding='utf-8') as f:
            return json.load(f)
    return None


def save_progress(progress):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def get_process_cpu(pid: int) -> float:
    """获取进程 CPU 使用率，失败返回 -1"""
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "%cpu="],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (subprocess.TimeoutExpired, ValueError):
        pass
    return -1.0


def run_question(qid, question):
    """运行单题，不设超时。由外部 monitor 线程负责卡死检测和 kill。"""
    log_stdout = LOG_DIR / f"{qid}_stdout.log"
    log_stderr = LOG_DIR / f"{qid}_stderr.log"

    start = time.time()
    try:
        proc = subprocess.Popen(
            ["vaxport", question],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        # 将进程引用和启动时间存到全局变量，供 monitor 使用
        _current_process["pid"] = proc.pid
        _current_process["start"] = start
        _current_process["qid"] = qid

        stdout, stderr = proc.communicate()
        elapsed = int(time.time() - start)

        log_stdout.write_text(stdout, encoding='utf-8')
        log_stderr.write_text(stderr, encoding='utf-8')
        with open(log_stderr, 'a', encoding='utf-8') as f:
            f.write(f"\nRC={proc.returncode} ELAPSED={elapsed}s\n")

        _current_process["pid"] = None
        return proc.returncode, elapsed
    except Exception as e:
        elapsed = int(time.time() - start)
        with open(log_stderr, 'a', encoding='utf-8') as f:
            f.write(f"ERROR: {e}\n")
        _current_process["pid"] = None
        return -2, elapsed


# 全局状态，供 monitor 线程和 run_question 共享
_current_process = {"pid": None, "start": 0.0, "qid": ""}
_monitor_stop = threading.Event()


def monitor_thread():
    """后台监控线程：检测卡死进程并自动 kill"""
    zero_cpu_count = 0

    while not _monitor_stop.is_set():
        time.sleep(MONITOR_INTERVAL)

        pid = _current_process.get("pid")
        if pid is None:
            zero_cpu_count = 0
            continue

        cpu = get_process_cpu(pid)
        elapsed = int(time.time() - _current_process["start"])
        qid = _current_process.get("qid", "?")

        if cpu < 0:
            # 进程已退出
            zero_cpu_count = 0
            continue

        if cpu == 0.0:
            zero_cpu_count += 1
            print(f"  [{qid}] CPU=0.0% ({zero_cpu_count}/{STUCK_CPU_CHECKS}) elapsed={elapsed}s", flush=True)
        else:
            if zero_cpu_count > 0:
                print(f"  [{qid}] CPU recovered → {cpu}%", flush=True)
            zero_cpu_count = 0

        if zero_cpu_count >= STUCK_CPU_CHECKS and elapsed >= STUCK_MIN_ELAPSED:
            print(f"  [{qid}] STUCK DETECTED: {zero_cpu_count}×CPU=0.0%, {elapsed}s elapsed → killing", flush=True)
            try:
                os.kill(pid, signal.SIGTERM)
                time.sleep(3)
                # 确保进程已死
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            except ProcessLookupError:
                pass
            # 标记为 stuck
            progress = load_progress()
            if progress:
                progress["questions"][qid] = "stuck"
                save_progress(progress)
            _current_process["pid"] = None
            zero_cpu_count = 0


def print_status(progress):
    qs = progress["questions"]
    total = len(qs)
    done = sum(1 for v in qs.values() if v != "pending")
    passed = sum(1 for v in qs.values() if v == "pass")
    failed = sum(1 for v in qs.values() if v == "fail")
    stuck_c = sum(1 for v in qs.values() if v == "stuck")
    errors = sum(1 for v in qs.values() if v == "error")
    print(f"\n{'='*50}")
    parts = [f"进度: {done}/{total}", f"通过: {passed}", f"失败: {failed}"]
    if stuck_c:
        parts.append(f"卡死: {stuck_c}")
    if errors:
        parts.append(f"错误: {errors}")
    print(f"{' | '.join(parts)}")
    print(f"{'='*50}")


def _sort_key(qid):
    prefix = qid[0]
    num = int(qid[1:])
    order = {"S": 0, "M": 1, "H": 2}
    return (order[prefix], num)


def cmd_status():
    progress = load_progress()
    if not progress:
        print("无测试进度记录")
        return
    print_status(progress)


def cmd_pause():
    progress = load_progress()
    if not progress:
        print("无测试进度记录")
        return
    if progress["status"] == "paused":
        print("测试已暂停")
        return
    progress["status"] = "paused"
    save_progress(progress)
    print("paused")
    print_status(progress)


def cmd_continue():
    progress = load_progress()
    if not progress:
        print("无测试进度记录，请先 start")
        return
    if progress["status"] == "running":
        print("测试正在运行中")
        return
    progress["status"] = "running"
    save_progress(progress)
    print("continuing")
    _run_all(progress)


def cmd_start():
    progress = load_progress()
    if progress and progress["status"] == "running":
        print("测试已在运行中。如需重新开始，请先 pause 再 start。")
        return

    questions = load_questions()
    if not questions:
        print("未找到题目")
        return

    if progress:
        existing_qs = progress["questions"]
        new_qs = {}
        for qid in sorted(questions, key=_sort_key):
            new_qs[qid] = existing_qs.get(qid, "pending")
        progress["questions"] = new_qs
    else:
        progress = {
            "test_started": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "questions": {qid: "pending" for qid in sorted(questions, key=_sort_key)},
        }

    progress["status"] = "running"
    save_progress(progress)
    print(f"started: {len(questions)} questions")
    _run_all(progress)


def cmd_reset():
    """reset <qid> [<qid2> ...] — 将指定题目标记为 pending（用于重试 stuck/timeout）"""
    if len(sys.argv) < 3:
        print("Usage: python test_runner.py reset <qid1> [qid2 ...]")
        return
    progress = load_progress()
    if not progress:
        print("无测试进度记录")
        return
    for qid in sys.argv[2:]:
        if qid in progress["questions"]:
            old = progress["questions"][qid]
            progress["questions"][qid] = "pending"
            print(f"  {qid}: {old} → pending")
        else:
            print(f"  {qid}: 不存在")
    progress["status"] = "paused"
    save_progress(progress)


def _run_all(progress):
    questions = load_questions()
    qs = progress["questions"]

    # 启动监控线程
    monitor = threading.Thread(target=monitor_thread, daemon=True)
    monitor.start()

    try:
        for qid in sorted(qs, key=_sort_key):
            current = load_progress()
            if current["status"] == "paused":
                print(f"paused before {qid}")
                print_status(current)
                return
            if current["questions"].get(qid) != "pending":
                continue

            question = questions.get(qid)
            if not question:
                print(f"  SKIP {qid}: question not found")
                continue

            print(f"[{qid}] {question[:100]}...", flush=True)
            rc, elapsed = run_question(qid, question)

            # 检查是否被 monitor 标记为 stuck
            current = load_progress()
            if current["questions"].get(qid) == "stuck":
                print(f"  STUCK ({elapsed}s)", flush=True)
                continue

            if rc == 0:
                status = "pass"
                print(f"  PASS ({elapsed}s)", flush=True)
            elif rc == -2:
                status = "error"
                print(f"  ERROR ({elapsed}s)", flush=True)
            else:
                status = "fail"
                print(f"  FAIL (rc={rc}, {elapsed}s)", flush=True)

            progress["questions"][qid] = status
            save_progress(progress)

        progress["status"] = "completed"
        progress["test_completed"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_progress(progress)
        print_status(progress)
        print("ALL DONE")
    finally:
        _monitor_stop.set()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_runner.py <start|pause|continue|status|reset>")
        sys.exit(1)

    cmd = sys.argv[1]
    handlers = {
        "start": cmd_start, "pause": cmd_pause,
        "continue": cmd_continue, "status": cmd_status,
        "reset": cmd_reset,
    }
    if cmd not in handlers:
        print(f"未知命令: {cmd}")
        print("Usage: python test_runner.py <start|pause|continue|status|reset>")
        sys.exit(1)
    handlers[cmd]()