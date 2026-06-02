"""FastAPI 应用主入口 — 生命周期管理 + 全局状态"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from vaxport.config import load_config
from vaxport.cli import App

# ── 全局状态（模块级，使用可变容器确保跨模块引用正确）─────
# 注意：必须使用 dict/list 等可变容器，因为 lifespan 会重新赋值 _app。
# 如果直接 `_app = None`，其他模块 `from server import _app` 会得到 None 的快照。
_state = {
    "app": None,
}
_pending_plans: dict[str, threading.Event] = {}     # request_id → Event（plan_confirm 阻塞用）
_pending_results: dict[str, dict] = {}               # request_id → {"confirmed": bool, "feedback": str}
_active_executions: dict[str, threading.Event] = {}  # request_id → Event（执行取消信号）
_pending_feedback: dict[str, str] = {}               # request_id → 用户追加上下文文本
_execution_feedback: dict[str, list] = {}            # request_id → [用户在执行中发送的消息]
_db_name_map: dict[str, "Database"] = {}             # name → Database 实例（多库切换）

# 兼容性别名 — 其他模块通过 _app 访问（指向 _state["app"] 的值）
def _get_app():
    return _state["app"]


def _build_app() -> App:
    """构建并初始化 App 实例"""
    config = load_config()
    app = App(config)
    app.setup(quiet=True)  # API 模式下不打印欢迎信息

    # 填充多库名称映射（供数据库切换）
    global _db_name_map
    if app.mdb and app.mdb.is_connected:
        for name in app.mdb.names:
            _db_name_map[name] = app.mdb.get(name)
    elif app.db and app.db.is_connected:
        _db_name_map[config.pg_database] = app.db

    return app


def _get_active_db_name() -> str:
    """当前激活的数据库名称"""
    app = _state["app"]
    if app and app.mdb and app.mdb.is_connected:
        return app.mdb.active_name
    if app and app.db and app.db.is_connected:
        return app.config.pg_database
    return ""


@asynccontextmanager
async def lifespan(application: FastAPI):
    """FastAPI 生命周期: startup → yield → shutdown"""
    _state["app"] = _build_app()
    yield
    # shutdown: 保存会话，清理资源
    app = _state["app"]
    if app and app.session:
        try:
            app.session.save()
        except Exception:
            pass


app = FastAPI(
    title="vaxport API",
    description="疫苗企业数据分析终端 — Go Bubble Tea TUI 后端",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8931", "http://127.0.0.1:8931", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 延迟导入路由（避免循环引用）──────────────────────
from vaxport.api.routes import router  # noqa: E402
from vaxport.api.sse import sse_router  # noqa: E402

app.include_router(router)
app.include_router(sse_router)