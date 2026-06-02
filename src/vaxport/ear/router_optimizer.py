"""Router Optimizer — 从历史路由决策中学习最优Agent映射"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RoutingSuggestion:
    """路由建议"""
    agent: str
    confidence: float
    reason: str


class RouterOptimizer:
    """路由优化器 — 基于历史数据建议最优Agent"""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # 复用FeedbackLoop的数据库
            db_path = str(Path.home() / ".vaxport" / "ear_feedback.db")
        self.db_path = db_path

        # 最少需要多少条数据才启用建议
        self.min_data_threshold = 100

    def suggest_agent(self, task_description: str, task_type: str) -> Optional[RoutingSuggestion]:
        """基于历史数据建议最优Agent"""
        conn = sqlite3.connect(self.db_path)

        # 检查数据量是否足够
        cursor = conn.execute("SELECT COUNT(*) FROM routing_decisions")
        total_count = cursor.fetchone()[0]

        if total_count < self.min_data_threshold:
            conn.close()
            return None

        # 按task_type分组，找成功率最高的Agent
        cursor = conn.execute(
            """SELECT agent_assigned, COUNT(*) as count, AVG(success) as success_rate
            FROM routing_decisions rd
            JOIN trajectories t ON rd.task_id = t.task_id
            WHERE t.task_type = ?
            GROUP BY agent_assigned
            HAVING count >= 5
            ORDER BY success_rate DESC, count DESC
            LIMIT 1""",
            (task_type,),
        )
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        agent, count, success_rate = row

        # 只有成功率>70%才建议
        if success_rate < 0.7:
            return None

        return RoutingSuggestion(
            agent=agent,
            confidence=success_rate,
            reason=f"基于{count}次同类任务历史，{agent} Agent成功率{success_rate:.0%}",
        )

    def get_routing_stats(self) -> dict:
        """获取各Agent的路由统计"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            """SELECT t.task_type, rd.agent_assigned, COUNT(*) as count, AVG(rd.success) as success_rate
            FROM routing_decisions rd
            JOIN trajectories t ON rd.task_id = t.task_id
            GROUP BY t.task_type, rd.agent_assigned
            ORDER BY t.task_type, success_rate DESC"""
        )
        rows = cursor.fetchall()
        conn.close()

        stats = {}
        for task_type, agent, count, success_rate in rows:
            if task_type not in stats:
                stats[task_type] = []
            stats[task_type].append({
                "agent": agent,
                "count": count,
                "success_rate": round(success_rate, 2),
            })

        return stats
