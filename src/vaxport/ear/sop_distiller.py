"""SOP Distiller — 持续积累成功轨迹，阈值触发蒸馏"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SOP:
    """标准操作流程"""
    id: str
    task_type: str
    trigger_pattern: str  # 触发关键词模式（用|分隔）
    steps: list[dict]  # [{"action": "db_query", "template": "SELECT ..."}]
    success_count: int = 0
    confidence: float = 0.0
    last_used: float = 0.0
    created_at: float = field(default_factory=time.time)


@dataclass
class TrajectoryBuffer:
    """轨迹缓冲区（用于蒸馏前暂存）"""
    task_id: str
    task_type: str
    tool_calls: list[dict]  # [{"tool": "...", "args": {...}, "success": true}]
    success: bool
    timestamp: float = field(default_factory=time.time)


class SOPDistiller:
    """SOP蒸馏器 — 持续积累+阈值触发"""

    def __init__(self, db_path: Optional[str] = None, semantic_memory=None):
        if db_path is None:
            db_path = str(Path.home() / ".vaxport" / "ear_sop.db")
        self.db_path = db_path
        self._semantic_memory = semantic_memory  # SemanticMemory 实例（可选）

        # 可配置参数
        self.trigger_threshold = 50  # 累积多少条触发蒸馏
        self.min_similarity = 0.7  # 聚类内相似度阈值
        self.min_cluster_size = 5  # 最少几条相似任务才蒸馏

        self._buffer: list[TrajectoryBuffer] = []
        self._ensure_db()

    def _ensure_db(self):
        """确保数据库表存在"""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sops (
                id TEXT PRIMARY KEY,
                task_type TEXT,
                trigger_pattern TEXT,
                steps TEXT,
                success_count INTEGER,
                confidence REAL,
                last_used REAL,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS trajectory_buffer (
                task_id TEXT PRIMARY KEY,
                task_type TEXT,
                tool_calls TEXT,
                success INTEGER,
                timestamp REAL
            );
        """)
        conn.commit()
        conn.close()

    def on_task_complete(self, task_id: str, task_type: str, tool_calls: list[dict], success: bool):
        """每次成功任务后立即记录到缓冲区"""
        if not success:
            return

        # 记录到缓冲区
        self._buffer.append(TrajectoryBuffer(
            task_id=task_id,
            task_type=task_type,
            tool_calls=tool_calls,
            success=success,
        ))

        # 同时持久化到数据库
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT OR REPLACE INTO trajectory_buffer
            (task_id, task_type, tool_calls, success, timestamp)
            VALUES (?, ?, ?, ?, ?)""",
            (
                task_id,
                task_type,
                json.dumps(tool_calls, ensure_ascii=False),
                1 if success else 0,
                time.time(),
            ),
        )
        conn.commit()

        # 检查是否达到触发阈值
        cursor = conn.execute("SELECT COUNT(*) FROM trajectory_buffer")
        count = cursor.fetchone()[0]
        conn.close()

        if count >= self.trigger_threshold:
            self.distill()

    def distill(self):
        """触发蒸馏"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("SELECT task_id, task_type, tool_calls FROM trajectory_buffer")
        rows = cursor.fetchall()

        if not rows:
            conn.close()
            return

        # 按task_type聚类
        clusters: dict[str, list[tuple]] = {}
        for row in rows:
            task_type = row[1]
            if task_type not in clusters:
                clusters[task_type] = []
            clusters[task_type].append(row)

        # 对每个聚类尝试蒸馏
        for task_type, trajectories in clusters.items():
            if len(trajectories) < self.min_cluster_size:
                continue

            # 计算聚类内相似度
            similarity = self._compute_cluster_similarity(trajectories)
            if similarity < self.min_similarity:
                continue

            # 提取SOP
            sop = self._extract_sop(task_type, trajectories)
            if sop:
                self._save_sop(sop)
                logger.info(f"SOP蒸馏成功: {sop.id}, task_type={task_type}, steps={len(sop.steps)}")

        # 清空缓冲区
        conn.execute("DELETE FROM trajectory_buffer")
        conn.commit()
        conn.close()

        # 清空内存缓冲区
        self._buffer.clear()

    def _compute_cluster_similarity(self, trajectories: list[tuple]) -> float:
        """计算聚类内的工具调用模式相似度"""
        if len(trajectories) < 2:
            return 0.0

        # 提取每个轨迹的工具序列
        patterns = []
        for row in trajectories:
            tool_calls = json.loads(row[2])
            pattern = tuple(tc.get("tool", "") for tc in tool_calls)
            patterns.append(pattern)

        # 计算两两相似度，取平均
        total_sim = 0.0
        count = 0
        for i in range(len(patterns)):
            for j in range(i + 1, len(patterns)):
                sim = self._sequence_similarity(patterns[i], patterns[j])
                total_sim += sim
                count += 1

        return total_sim / count if count > 0 else 0.0

    def _sequence_similarity(self, seq1: tuple, seq2: tuple) -> float:
        """计算两个工具序列的相似度（简单的Jaccard相似度）"""
        set1 = set(seq1)
        set2 = set(seq2)
        intersection = set1 & set2
        union = set1 | set2
        return len(intersection) / len(union) if union else 0.0

    def _extract_sop(self, task_type: str, trajectories: list[tuple]) -> Optional[SOP]:
        """从轨迹聚类中提取SOP"""
        # 找最常见的工具调用序列
        patterns = []
        for row in trajectories:
            tool_calls = json.loads(row[2])
            # 提取工具名称序列（忽略参数细节）
            pattern = [tc.get("tool", "") for tc in tool_calls if tc.get("tool")]
            patterns.append(tuple(pattern))

        # 找出现最多的模式
        from collections import Counter
        pattern_counts = Counter(patterns)
        if not pattern_counts:
            return None

        most_common_pattern, count = pattern_counts.most_common(1)[0]
        if count < self.min_cluster_size:
            return None

        # 生成SOP
        sop_id = f"sop_{task_type}_{int(time.time())}"
        steps = [{"action": tool} for tool in most_common_pattern]

        # 生成触发关键词
        keywords = self._extract_keywords(task_type)

        return SOP(
            id=sop_id,
            task_type=task_type,
            trigger_pattern="|".join(keywords),
            steps=steps,
            success_count=count,
            confidence=count / len(trajectories),
        )

    def _extract_keywords(self, task_type: str) -> list[str]:
        """从任务类型提取触发关键词"""
        keyword_map = {
            "统计分析": ["趋势", "分析", "统计", "对比", "cpk", "spc"],
            "报告生成": ["报告", "总结", "汇报"],
            "异常检测": ["异常", "偏差", "oos", "capa", "预警"],
            "文档检索": ["sop", "法规", "文档", "检索", "查找"],
            "通用查询": ["查询", "数据", "查看"],
        }
        return keyword_map.get(task_type, [task_type])

    def _save_sop(self, sop: SOP):
        """保存SOP到数据库"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT OR REPLACE INTO sops
            (id, task_type, trigger_pattern, steps, success_count, confidence, last_used, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sop.id,
                sop.task_type,
                sop.trigger_pattern,
                json.dumps(sop.steps, ensure_ascii=False),
                sop.success_count,
                sop.confidence,
                sop.last_used,
                sop.created_at,
            ),
        )
        conn.commit()
        conn.close()

    def retrieve_sop(self, task_description: str, task_type: str) -> Optional[SOP]:
        """检索匹配的SOP（orchestrator调用）。
        语义匹配优先路径：similarity > 0.75 时直接返回语义匹配结果。
        关词匹配兜底：现有逻辑不变。"""
        # 语义匹配优先
        if self._semantic_memory:
            similar_cases = self._semantic_memory.search_similar_cases(
                task_description, top_k=1, task_type=task_type,
            )
            if similar_cases:
                best = similar_cases[0]
                sim = best.get("similarity", 0.0)
                if sim >= 0.75:
                    logger.info(f"SOPDistiller: 语义匹配命中 (sim={sim:.2f})")
                    # 将语义匹配案例转换为 SOP 格式
                    tables = best.get("tables_used", "")
                    if isinstance(tables, str):
                        try:
                            tables = json.loads(tables)
                        except Exception:
                            tables = [tables] if tables else []
                    steps = [{"action": "semantic_reference", "tables": tables}]
                    return SOP(
                        id=f"semantic_{best.get('id', '')}",
                        task_type=task_type,
                        trigger_pattern=best.get("query_summary", ""),
                        steps=steps,
                        success_count=1,
                        confidence=sim,
                        last_used=time.time(),
                    )

        # 关词匹配兜底
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            """SELECT id, task_type, trigger_pattern, steps, success_count, confidence, last_used
            FROM sops
            WHERE task_type = ? AND confidence >= 0.7
            ORDER BY confidence DESC, success_count DESC
            LIMIT 1""",
            (task_type,),
        )
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        # 检查触发关键词是否匹配
        trigger_pattern = row[2]
        keywords = trigger_pattern.split("|")
        task_lower = task_description.lower()
        if not any(kw in task_lower for kw in keywords):
            return None

        sop = SOP(
            id=row[0],
            task_type=row[1],
            trigger_pattern=row[2],
            steps=json.loads(row[3]),
            success_count=row[4],
            confidence=row[5],
            last_used=row[6],
        )

        # 更新last_used
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE sops SET last_used = ? WHERE id = ?", (time.time(), sop.id))
        conn.commit()
        conn.close()

        return sop

    def get_status(self) -> dict:
        """获取蒸馏状态"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("SELECT COUNT(*) FROM trajectory_buffer")
        buffer_count = cursor.fetchone()[0]

        cursor = conn.execute("SELECT COUNT(*) FROM sops")
        sop_count = cursor.fetchone()[0]

        cursor = conn.execute("SELECT AVG(confidence) FROM sops")
        avg_confidence = cursor.fetchone()[0] or 0.0

        conn.close()

        return {
            "buffer_count": buffer_count,
            "trigger_threshold": self.trigger_threshold,
            "next_trigger_in": max(0, self.trigger_threshold - buffer_count),
            "sop_count": sop_count,
            "avg_confidence": round(avg_confidence, 2),
        }
