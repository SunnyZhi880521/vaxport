"""Feedback Loop — 用户反馈采集 + 轨迹日志"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TrajectoryRecord:
    """单次任务的完整轨迹记录"""
    task_id: str
    task_type: str  # 统计分析/报告生成/异常检测/文档检索
    agent_assigned: str
    tool_calls: list[dict]  # [{"tool": "...", "args": {...}, "success": true/false}]
    success: bool
    duration_seconds: float
    token_usage: int
    timestamp: float = field(default_factory=time.time)


@dataclass
class FeedbackRecord:
    """用户反馈记录"""
    task_id: str
    feedback_type: str  # explicit / implicit_retry
    satisfaction: Optional[bool]  # True=满意, False=不满意, None=隐式
    timestamp: float = field(default_factory=time.time)
    notes: str = ""


@dataclass
class RoutingDecision:
    """路由决策记录"""
    task_id: str
    task_description: str
    agent_assigned: str
    success: bool
    timestamp: float = field(default_factory=time.time)


class FeedbackLoop:
    """反馈采集 + 轨迹日志存储"""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = str(Path.home() / ".vaxport" / "ear_feedback.db")
        self.db_path = db_path
        self._ensure_db()

    def _ensure_db(self):
        """确保数据库表存在"""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trajectories (
                task_id TEXT PRIMARY KEY,
                task_type TEXT,
                agent_assigned TEXT,
                tool_calls TEXT,
                success INTEGER,
                duration_seconds REAL,
                token_usage INTEGER,
                timestamp REAL
            );

            CREATE TABLE IF NOT EXISTS feedbacks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT,
                feedback_type TEXT,
                satisfaction INTEGER,
                timestamp REAL,
                notes TEXT,
                FOREIGN KEY (task_id) REFERENCES trajectories(task_id)
            );

            CREATE TABLE IF NOT EXISTS routing_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT,
                task_description TEXT,
                agent_assigned TEXT,
                success INTEGER,
                timestamp REAL,
                FOREIGN KEY (task_id) REFERENCES trajectories(task_id)
            );
        """)
        conn.commit()
        conn.close()

    def log_trajectory(self, record: TrajectoryRecord):
        """记录任务轨迹"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT OR REPLACE INTO trajectories
            (task_id, task_type, agent_assigned, tool_calls, success,
             duration_seconds, token_usage, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.task_id,
                record.task_type,
                record.agent_assigned,
                json.dumps(record.tool_calls, ensure_ascii=False),
                1 if record.success else 0,
                record.duration_seconds,
                record.token_usage,
                record.timestamp,
            ),
        )
        conn.commit()
        conn.close()

    def capture_explicit_feedback(self, task_id: str, satisfaction: bool, notes: str = ""):
        """采集显式反馈（用户点满意/不满意）"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO feedbacks (task_id, feedback_type, satisfaction, timestamp, notes)
            VALUES (?, 'explicit', ?, ?, ?)""",
            (task_id, 1 if satisfaction else 0, time.time(), notes),
        )
        conn.commit()
        conn.close()

    def capture_implicit_feedback(self, task_id: str, task_type: str, user_id: str = "default"):
        """采集隐式反馈（用户在短时间内对同类任务重新请求）"""
        conn = sqlite3.connect(self.db_path)
        # 查找最近10分钟内同类型的任务
        cursor = conn.execute(
            """SELECT task_id FROM trajectories
            WHERE task_type = ? AND timestamp > ? AND task_id != ?
            ORDER BY timestamp DESC LIMIT 1""",
            (task_type, time.time() - 600, task_id),
        )
        recent = cursor.fetchone()
        if recent:
            # 有近期同类任务，可能是隐式重试
            conn.execute(
                """INSERT INTO feedbacks (task_id, feedback_type, satisfaction, timestamp, notes)
                VALUES (?, 'implicit_retry', NULL, ?, ?)""",
                (recent[0], time.time(), f"用户重新请求了同类任务，可能不满意"),
            )
            conn.commit()
        conn.close()

    def log_routing_decision(self, record: RoutingDecision):
        """记录路由决策"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO routing_decisions
            (task_id, task_description, agent_assigned, success, timestamp)
            VALUES (?, ?, ?, ?, ?)""",
            (
                record.task_id,
                record.task_description,
                record.agent_assigned,
                1 if record.success else 0,
                record.timestamp,
            ),
        )
        conn.commit()
        conn.close()

    def get_trajectory_stats(self, limit: int = 100) -> dict:
        """获取轨迹统计信息"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            """SELECT COUNT(*), AVG(success), AVG(duration_seconds), AVG(token_usage)
            FROM trajectories ORDER BY timestamp DESC LIMIT ?""",
            (limit,),
        )
        row = cursor.fetchone()
        conn.close()
        return {
            "total": row[0] or 0,
            "success_rate": row[1] or 0,
            "avg_duration": row[2] or 0,
            "avg_tokens": row[3] or 0,
        }

    def get_feedback_stats(self, limit: int = 100) -> dict:
        """获取反馈统计信息"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            """SELECT
                COUNT(*) as total,
                SUM(CASE WHEN feedback_type = 'explicit' THEN 1 ELSE 0 END) as explicit_count,
                SUM(CASE WHEN satisfaction = 1 THEN 1 ELSE 0 END) as satisfied,
                SUM(CASE WHEN satisfaction = 0 THEN 1 ELSE 0 END) as unsatisfied
            FROM feedbacks
            WHERE timestamp > ?""",
            (time.time() - 86400 * 7,),  # 最近7天
        )
        row = cursor.fetchone()
        conn.close()
        return {
            "total": row[0] or 0,
            "explicit": row[1] or 0,
            "satisfied": row[2] or 0,
            "unsatisfied": row[3] or 0,
        }

    def get_routing_stats(self) -> dict:
        """获取路由决策统计"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            """SELECT agent_assigned, COUNT(*), AVG(success)
            FROM routing_decisions
            GROUP BY agent_assigned
            ORDER BY COUNT(*) DESC"""
        )
        rows = cursor.fetchall()
        conn.close()
        return {
            row[0]: {"count": row[1], "success_rate": row[2]}
            for row in rows
        }
