#!/usr/bin/env python
"""SKILL 基线测试框架 — 题目解析、进度管理、结果收集"""

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

DEMO_FILE = Path(__file__).parent.parent / "docs" / "demo-questions.md"

# Phase 0 测试集：15 题覆盖主要分析模式
PHASE0_QUESTIONS = {
    "process_capability": ["H2", "H15"],
    "trend_spc": ["H11", "H29"],
    "deviation_capa": ["H5", "H21"],
    "stability": ["H8", "H36"],
    "traceability": ["H1", "H27"],
    "comprehensive_report": ["H3", "H17"],
    "cross_domain": ["H9", "H39", "H40"],
}

# 新 SKILL 测试集：5 题覆盖 5 个新增 SKILL
NEW_SKILL_QUESTIONS = {
    "oos_oot_investigation": ["H41"],
    "cleaning_validation": ["H42"],
    "change_control": ["H43"],
    "cold_chain": ["H44"],
    "spc_trend": ["H45"],
}

ALL_PHASE0_IDS = []
for ids in PHASE0_QUESTIONS.values():
    ALL_PHASE0_IDS.extend(ids)

ALL_NEW_SKILL_IDS = []
for ids in NEW_SKILL_QUESTIONS.values():
    ALL_NEW_SKILL_IDS.extend(ids)


def load_all_questions() -> dict[str, str]:
    """从 demo-questions.md 解析所有题目"""
    if not DEMO_FILE.exists():
        raise FileNotFoundError(f"{DEMO_FILE} not found")

    content = DEMO_FILE.read_text(encoding="utf-8")
    questions = {}

    pattern = r'### (S\d+|M\d+|H\d+)\.\s*(.*?)\n\n> (.+?)(?=\n\n---)'
    for m in re.finditer(pattern, content, re.DOTALL):
        qid = m.group(1)
        title = m.group(2).strip()
        text = m.group(3).replace('\n> ', '').strip()
        text = text.replace('> ', '')
        questions[qid] = {
            "id": qid,
            "title": title,
            "text": text,
        }

    return questions


def load_phase0_questions() -> dict[str, dict]:
    """加载 Phase 0 测试集（15 题）"""
    all_q = load_all_questions()
    result = {}
    for qid in ALL_PHASE0_IDS:
        if qid in all_q:
            q = all_q[qid]
            q["category"] = _get_category(qid)
            result[qid] = q
    return result


def load_new_skill_questions() -> dict[str, dict]:
    """加载新 SKILL 测试集（5 题）"""
    all_q = load_all_questions()
    result = {}
    for qid in ALL_NEW_SKILL_IDS:
        if qid in all_q:
            q = all_q[qid]
            q["category"] = _get_new_skill_category(qid)
            result[qid] = q
    return result


def _get_category(qid: str) -> str:
    for cat, ids in PHASE0_QUESTIONS.items():
        if qid in ids:
            return cat
    return "unknown"


def _get_new_skill_category(qid: str) -> str:
    for cat, ids in NEW_SKILL_QUESTIONS.items():
        if qid in ids:
            return cat
    return "unknown"


class ProgressManager:
    """进度管理器 — 支持 pause/continue"""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.progress_file = output_dir / "progress.json"
        self.progress = self._load()

    def _load(self) -> dict:
        if self.progress_file.exists():
            with open(self.progress_file, encoding="utf-8") as f:
                return json.load(f)
        return {
            "started_at": datetime.now().isoformat(),
            "completed": {},
            "failed": {},
            "current": None,
        }

    def save(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with open(self.progress_file, "w", encoding="utf-8") as f:
            json.dump(self.progress, f, ensure_ascii=False, indent=2)

    def is_done(self, qid: str, run: int) -> bool:
        key = f"{qid}_run{run}"
        return key in self.progress["completed"]

    def is_question_done(self, qid: str, total_runs: int = 3) -> bool:
        return all(self.is_done(qid, r) for r in range(1, total_runs + 1))

    def mark_complete(self, qid: str, run: int, elapsed: int,
                      output_file: str, token_count: int = 0):
        key = f"{qid}_run{run}"
        self.progress["completed"][key] = {
            "qid": qid,
            "run": run,
            "elapsed_seconds": elapsed,
            "output_file": output_file,
            "tokens": token_count,
            "completed_at": datetime.now().isoformat(),
        }
        self.progress["current"] = None
        self.save()

    def mark_failed(self, qid: str, run: int, error: str):
        key = f"{qid}_run{run}"
        self.progress["failed"][key] = {
            "qid": qid,
            "run": run,
            "error": error,
            "failed_at": datetime.now().isoformat(),
        }
        self.progress["current"] = None
        self.save()

    def set_current(self, qid: str, run: int):
        self.progress["current"] = {"qid": qid, "run": run}
        self.save()

    def get_summary(self, total_questions: int, runs_per_question: int) -> dict:
        total = total_questions * runs_per_question
        done = len(self.progress["completed"])
        failed = len(self.progress["failed"])
        return {
            "total": total,
            "completed": done,
            "failed": failed,
            "remaining": total - done - failed,
            "pct": round(done / total * 100, 1) if total > 0 else 0,
        }


class TestResult:
    """单次测试结果"""

    def __init__(self, qid: str, run: int, question_text: str):
        self.qid = qid
        self.run = run
        self.question_text = question_text
        self.answer: str = ""
        self.tool_calls: list[dict] = []
        self.sql_queries: list[str] = []
        self.elapsed_seconds: int = 0
        self.tokens_used: int = 0
        self.agent_type: str = ""
        self.agent_chain: list[str] = []
        self.turns: int = 0
        self.error: Optional[str] = None
        self.skill_validation: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "qid": self.qid,
            "run": self.run,
            "question_text": self.question_text,
            "answer": self.answer,
            "tool_calls": self.tool_calls,
            "sql_queries": self.sql_queries,
            "elapsed_seconds": self.elapsed_seconds,
            "tokens_used": self.tokens_used,
            "agent_type": self.agent_type,
            "agent_chain": self.agent_chain,
            "turns": self.turns,
            "error": self.error,
            "skill_validation": self.skill_validation,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TestResult":
        r = cls(d["qid"], d["run"], d["question_text"])
        r.answer = d.get("answer", "")
        r.tool_calls = d.get("tool_calls", [])
        r.sql_queries = d.get("sql_queries", [])
        r.elapsed_seconds = d.get("elapsed_seconds", 0)
        r.tokens_used = d.get("tokens_used", 0)
        r.agent_type = d.get("agent_type", "")
        r.agent_chain = d.get("agent_chain", [])
        r.turns = d.get("turns", 0)
        r.error = d.get("error")
        r.skill_validation = d.get("skill_validation")
        return r

    def save(self, output_dir: Path):
        qid_dir = output_dir / self.qid
        qid_dir.mkdir(parents=True, exist_ok=True)
        filepath = qid_dir / f"run_{self.run}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)


def load_test_result(output_dir: Path, qid: str, run: int) -> Optional[TestResult]:
    filepath = output_dir / qid / f"run_{run}.json"
    if not filepath.exists():
        return None
    with open(filepath, encoding="utf-8") as f:
        return TestResult.from_dict(json.load(f))


def load_all_results(output_dir: Path, question_ids: list[str],
                     runs: int = 3) -> dict[str, list[TestResult]]:
    results = {}
    for qid in question_ids:
        results[qid] = []
        for run in range(1, runs + 1):
            r = load_test_result(output_dir, qid, run)
            if r:
                results[qid].append(r)
    return results
