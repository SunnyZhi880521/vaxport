"""vaxport FastAPI 后端 — REST + SSE 接口

将现有 Orchestrator/Agent 暴露为 HTTP 接口，供 Go Bubble Tea TUI 消费。
现有 Python 业务代码 (agent/orchestrator/tools/config/...) 零修改。
"""

from .server import app, _state

__all__ = ["app", "_state"]