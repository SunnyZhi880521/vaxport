"""会话管理 — 对话历史持久化 + 审计日志"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional


SESSION_DIR = Path.home() / ".vaxport" / "sessions"
AUTO_SAVE_FILE = SESSION_DIR / "_auto_save.json"
AUDIT_LOG = Path.home() / ".vaxport" / "audit.log"


class Session:
    """会话管理"""

    def __init__(self, session_id: Optional[str] = None):
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        self.session_id = session_id or datetime.now().strftime("%Y-%m-%d-%H%M%S")
        self.messages: list[dict] = []
        self.start_time = datetime.now().isoformat()
        self.summary: str = ""  # 旧消息压缩摘要
        self._summary_msg_count: int = 0  # 上次更新摘要时的消息数

    def add_message(self, role: str, content: str):
        self.messages.append({
            "role": role,
            "content": content,
            "time": datetime.now().isoformat(),
        })

    def save(self, name: Optional[str] = None):
        """保存会话到 JSON 文件（带日期文件名，用于手动导出和 exit）"""
        filename = f"{name or self.session_id}.json"
        filepath = SESSION_DIR / filename
        self._write(filepath)
        return str(filepath)

    def auto_save(self):
        """自动保存到固定文件 _auto_save.json，每次覆盖写入，不积累历史文件"""
        self._write(AUTO_SAVE_FILE)

    def _write(self, filepath: Path):
        SESSION_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "session_id": self.session_id,
            "start_time": self.start_time,
            "saved_at": datetime.now().isoformat(),
            "messages": self.messages,
            "summary": self.summary,
            "_summary_msg_count": self._summary_msg_count,
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # 自动清理超过 1 天的旧会话文件
        _cleanup_old_sessions()

    @staticmethod
    def load(session_ref: str) -> Optional["Session"]:
        """从 JSON 文件恢复会话"""
        filepath = SESSION_DIR / f"{session_ref}.json"
        if not filepath.exists():
            # 尝试直接匹配完整文件名
            candidates = list(SESSION_DIR.glob(f"{session_ref}*"))
            if candidates:
                filepath = candidates[0]
            else:
                return None

        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)

        session = Session(session_id=data.get("session_id", session_ref))
        session.messages = data.get("messages", [])
        session.start_time = data.get("start_time", "")
        session.summary = data.get("summary", "")
        session._summary_msg_count = data.get("_summary_msg_count", 0)
        return session

    @staticmethod
    def list_sessions() -> list[dict]:
        """列出所有已保存的会话"""
        sessions = []
        if not SESSION_DIR.exists():
            return sessions

        for f in sorted(SESSION_DIR.glob("*.json"), key=os.path.getmtime, reverse=True):
            try:
                with open(f, encoding="utf-8") as fp:
                    data = json.load(fp)
                first_query = ""
                for msg in data.get("messages", []):
                    if msg.get("role") == "user":
                        first_query = msg.get("content", "")[:100]
                        break
                sessions.append({
                    "file": f.stem,
                    "start_time": data.get("start_time", ""),
                    "first_query": first_query,
                    "message_count": len(data.get("messages", [])),
                })
            except Exception:
                sessions.append({
                    "file": f.stem,
                    "start_time": "",
                    "first_query": "(无法读取)",
                    "message_count": 0,
                })
        return sessions

    def get_history_summary(self) -> str:
        """获取对话历史摘要"""
        summary = []
        for msg in self.messages:
            role = msg["role"]
            content = msg["content"][:80]
            summary.append(f"[{role}] {content}...")
        return "\n".join(summary)

    def needs_summary_update(self) -> bool:
        """判断是否需要更新摘要（新增消息超过 10 条）"""
        return len(self.messages) > 20 and len(self.messages) - self._summary_msg_count >= 10

    def update_summary(self, summary_text: str):
        """更新会话摘要"""
        self.summary = summary_text
        self._summary_msg_count = len(self.messages)


def write_audit_log(entry: dict):
    """写入审计日志（追加式 JSON 行）"""
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["timestamp"] = datetime.now().isoformat()
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _cleanup_old_sessions():
    """删除超过 1 天的旧会话文件（保留 _auto_save.json）"""
    import time
    cutoff = time.time() - 86400  # 24 小时
    for f in SESSION_DIR.glob("*.json"):
        if f.name == "_auto_save.json":
            continue
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def build_audit_entry(
    user: str,
    model: str,
    query: str,
    sql_list: list,
    row_count: int,
    duration_ms: int,
    answer: str,
) -> dict:
    """构建审计日志条目"""
    return {
        "user": user,
        "model": model,
        "query": query,
        "sql": sql_list,
        "rows": row_count,
        "duration_ms": duration_ms,
        "answer": answer[:500],  # 截断长回答
    }