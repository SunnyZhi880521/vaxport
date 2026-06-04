"""REST API 路由 — 查询/会话/操作类端点"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from vaxport.api.server import _state, _db_name_map, _get_active_db_name
from vaxport.config import Config, load_config

router = APIRouter()


# ── Pydantic 请求/响应模型 ──────────────────────────

class ModelSwitchRequest(BaseModel):
    backend: str  # "aliyun" | "ollama"
    model: str


class AgentModelRequest(BaseModel):
    agent_name: str  # "task_assigner", "general", "analyze_reporter", ...
    model: str | None = None  # None = 继承全局


class TemperatureRequest(BaseModel):
    agent_name: str  # "task_assigner", "general", "analyze_reporter", ...
    temperature: float  # 0.0 ~ 2.0


class SessionResumeRequest(BaseModel):
    session_ref: str  # 会话文件名/ID


class FeedbackRequest(BaseModel):
    message: str


class EARFeedbackRequest(BaseModel):
    task_id: str
    satisfaction: bool  # True=满意, False=不满意
    notes: str = ""


class ConfigUpdateRequest(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    ollama_url: str | None = None
    ollama_model: str | None = None
    backend: str | None = None        # "aliyun" | "ollama"
    model: str | None = None          # 全局模型名
    agent_model: dict | None = None   # {"agent_name": "xxx", "model": "yyy" | null}
    auto_plan: bool | None = None
    plan_confirm: bool | None = None
    auto_qc: bool | None = None
    db_host: str | None = None
    db_port: int | None = None
    db_database: str | None = None
    db_user: str | None = None
    db_password: str | None = None


class DbTestRequest(BaseModel):
    host: str
    port: int = 5432
    database: str
    user: str
    password: str = ""


# ── 查询类端点 ─────────────────────────────────────

@router.get("/api/status")
async def get_status():
    """获取后端运行状态"""
    global _state
    app = _state["app"]
    if app is None:
        return {"status": "not_started", "message": "后端尚未初始化"}

    llm = app.llm
    backend_status = llm.get_status() if llm else {}

    return {
        "status": "running",
        "model": llm.active_model if llm else "",
        "backend": llm.active_backend if llm else "",
        "backends": backend_status,
        "pg_status": _pg_status(),
        "pg_active_db": _get_active_db_name(),
        "pg_databases": sorted(_db_name_map.keys()) if _db_name_map else [],
        "version": _get_version(),
        "skills_count": app.skills.count if app.skills else 0,
        "tools_count": len(app.tools.list_tools()) if app.tools else 0,
        "debug_mode": app.debug_mode,
        "plan_mode": getattr(app, '_plan_mode', False),
    }


@router.get("/api/tools")
async def get_tools():
    """列出所有已注册工具"""
    if _state["app"] is None or _state["app"].tools is None:
        return {"tools": [], "count": 0}
    tools = _state["app"].tools.list_tools()
    return {"tools": tools, "count": len(tools)}


@router.get("/api/schemas")
async def get_schemas():
    """获取数据库 schema 树（表/视图/物化视图 + 行数估算）"""
    if _state["app"] is None:
        return {"databases": [], "error": "后端未初始化"}

    result = {"databases": [], "active_db": _get_active_db_name()}

    if _state["app"].mdb and _state["app"].mdb.is_connected:
        for name in _state["app"].mdb.names:
            db = _state["app"].mdb.get(name)
            result["databases"].append(_build_schema_tree(name, db))
    elif _state["app"].db and _state["app"].db.is_connected:
        result["databases"].append(
            _build_schema_tree(_state["app"].config.pg_database, _state["app"].db)
        )

    return result


@router.get("/api/skills")
async def get_skills():
    """列出已加载 SKILL"""
    if _state["app"] is None or _state["app"].skills is None:
        return {"skills": [], "count": 0}
    skills = _state["app"].skills.list_skills()
    return {"skills": skills, "count": len(skills)}


@router.get("/api/models")
async def get_models():
    """列出可用模型（按后端分组）"""
    if _state["app"] is None or _state["app"].llm is None:
        return {"models": [], "active_model": "", "active_backend": ""}

    llm = _state["app"].llm
    backend_status = llm.get_status()
    models = []

    for name, info in backend_status.items():
        # 去掉 " ← 当前" 标记
        clean_name = name.replace(" ← 当前", "")
        label = "阿里百炼" if clean_name == "aliyun" else "本地 Ollama"
        try:
            model_list = llm.list_models(clean_name)
        except Exception:
            model_list = [info.get("model", "")]
        for m in model_list:
            if m:
                models.append({
                    "backend": clean_name,
                    "backend_label": label,
                    "model_id": m,
                })

    # Agent 模型偏好
    agent_models = {}
    if _state["app"].config:
        for agent_name in ["task_assigner", "general", "analyze_reporter", "quality_supervision", "document_search"]:
            m = _state["app"].config.get_agent_model(agent_name)
            agent_models[agent_name] = m  # None = 继承全局

    return {
        "models": models,
        "active_model": llm.active_model,
        "active_backend": llm.active_backend,
        "agent_models": agent_models,
    }


@router.post("/api/models/switch")
async def switch_model(req: ModelSwitchRequest):
    """切换全局 LLM 后端/模型"""
    if _state["app"] is None or _state["app"].llm is None:
        raise HTTPException(status_code=503, detail="后端未初始化")

    backend_map = {"aliyun": "aliyun", "ollama": "ollama"}
    target = backend_map.get(req.backend)
    if not target:
        raise HTTPException(status_code=400, detail=f"未知后端: {req.backend}")

    success = _state["app"].llm.set_model(target, req.model)
    if not success:
        raise HTTPException(status_code=400, detail=f"切换到 {target} 失败")

    # 持久化
    _state["app"].config.set("agent", "primary_backend", target)
    if target == "aliyun":
        _state["app"].config.set("api", "aliyun_model", req.model)
    else:
        _state["app"].config.set("local", "ollama_model", req.model)
    _state["app"].config.save()

    return {
        "status": "ok",
        "backend": _state["app"].llm.active_backend,
        "model": _state["app"].llm.active_model,
    }


@router.post("/api/models/agent")
async def set_agent_model(req: AgentModelRequest):
    """设置 Agent 模型偏好"""
    if _state["app"] is None:
        raise HTTPException(status_code=503, detail="后端未初始化")

    valid_agents = {"task_assigner", "general", "analyze_reporter", "quality_supervision", "document_search"}
    if req.agent_name not in valid_agents:
        raise HTTPException(status_code=400, detail=f"未知 Agent: {req.agent_name}")

    # 持久化
    _state["app"].config.set_agent_model(req.agent_name, req.model)

    # 更新运行中的 Orchestrator
    if _state["app"].orchestrator:
        _state["app"].orchestrator.update_agent_model(req.agent_name, req.model)

    return {
        "status": "ok",
        "agent_name": req.agent_name,
        "model": req.model,
    }


@router.post("/api/temperature")
async def set_temperature(req: TemperatureRequest):
    """设置指定 Agent 的 LLM temperature（0.0~2.0）"""
    if _state["app"] is None:
        raise HTTPException(status_code=503, detail="后端未初始化")

    valid_agents = {"task_assigner", "general", "analyze_reporter", "quality_supervision", "document_search"}
    if req.agent_name not in valid_agents:
        raise HTTPException(status_code=400, detail=f"未知 Agent: {req.agent_name}")

    temp = max(0.0, min(2.0, req.temperature))
    _state["app"].config.set_agent_temperature(req.agent_name, temp)

    # 更新运行中的 Orchestrator
    if _state["app"].orchestrator:
        _state["app"].orchestrator.update_agent_temperature(req.agent_name, temp)

    return {"status": "ok", "agent_name": req.agent_name, "temperature": temp}


@router.post("/api/classify")
async def classify_query(req: dict):
    """意图分类（供 TUI 提前获取路由信息）"""
    if _state["app"] is None or _state["app"].orchestrator is None:
        raise HTTPException(status_code=503, detail="后端未初始化")

    query = req.get("query", "")
    if not query:
        raise HTTPException(status_code=400, detail="query 不能为空")

    result = _state["app"].orchestrator.classify(query)
    return result


@router.get("/api/config")
async def get_config():
    """获取配置（敏感字段脱敏）"""
    if _state["app"] is None:
        raise HTTPException(status_code=503, detail="后端未初始化")

    cfg = _state["app"].config
    api_key = cfg.aliyun_api_key
    masked_key = ""
    if api_key and len(api_key) >= 8:
        masked_key = api_key[:4] + "****" + api_key[-4:]
    elif api_key:
        masked_key = api_key[:4] + "****"

    return {
        "api": {
            "aliyun_model": cfg.aliyun_model,
            "aliyun_base_url": cfg.aliyun_base_url,
            "aliyun_key": masked_key,
        },
        "local": {
            "ollama_url": cfg.ollama_url,
            "ollama_model": cfg.ollama_model,
        },
        "pg": {
            "host": cfg.pg_host,
            "port": cfg.pg_port,
            "database": cfg.pg_database,
            "user": cfg.pg_user,
            "password": cfg.pg_password,
            "databases": cfg.db_configs,
        },
        "agent": {
            "max_tool_rounds": cfg.max_tool_rounds,
            "primary_backend": cfg.primary_backend,
            "auto_plan": cfg.auto_plan,
            "plan_confirm": cfg.plan_confirm,
            "auto_review": cfg.auto_review,
            "agent_temperatures": cfg.agent_temperatures,
            "agent_models": cfg.agent_models,
        },
    }


@router.post("/api/config/update")
async def update_config(req: ConfigUpdateRequest):
    """持久化更新配置项（部分更新，仅传需要修改的字段）"""
    if _state["app"] is None:
        raise HTTPException(status_code=503, detail="后端未初始化")

    cfg = _state["app"].config
    changed = False

    if req.api_key is not None:
        cfg.set("api", "aliyun_key", req.api_key)
        changed = True
    if req.base_url is not None:
        cfg.set("api", "aliyun_base_url", req.base_url)
        changed = True
    if req.ollama_url is not None:
        cfg.set("local", "ollama_url", req.ollama_url)
        changed = True
    if req.ollama_model is not None:
        cfg.set("local", "ollama_model", req.ollama_model)
        changed = True
    if req.backend is not None:
        cfg.set("agent", "primary_backend", req.backend)
        changed = True
    if req.model is not None:
        backend = req.backend or cfg.primary_backend
        if backend == "aliyun":
            cfg.set("api", "aliyun_model", req.model)
        else:
            cfg.set("local", "ollama_model", req.model)
        changed = True
    if req.agent_model is not None:
        agent_name = req.agent_model.get("agent_name", "")
        model_id = req.agent_model.get("model")  # None = 恢复继承全局
        if agent_name:
            cfg.set_agent_model(agent_name, model_id)
            changed = True
    if req.auto_plan is not None:
        cfg.set("agent", "auto_plan", req.auto_plan)
        changed = True
    if req.plan_confirm is not None:
        cfg.set("agent", "plan_confirm", req.plan_confirm)
        changed = True
    if req.auto_qc is not None:
        cfg.set("agent", "auto_review", req.auto_qc)
        changed = True
    if req.db_host is not None:
        cfg.set("pg", "host", req.db_host)
        changed = True
    if req.db_port is not None:
        cfg.set("pg", "port", req.db_port)
        changed = True
    if req.db_database is not None:
        cfg.set("pg", "database", req.db_database)
        changed = True
    if req.db_user is not None:
        cfg.set("pg", "user", req.db_user)
        changed = True
    if req.db_password is not None:
        cfg.set("pg", "password", req.db_password)
        changed = True

    if changed:
        cfg.save()

    return {"status": "ok"}


@router.post("/api/db/test")
async def test_db_connection(req: DbTestRequest):
    """测试数据库连接"""
    import psycopg2
    try:
        conn = psycopg2.connect(
            host=req.host,
            port=req.port,
            dbname=req.database,
            user=req.user,
            password=req.password,
            connect_timeout=10,
        )
        conn.close()
        return {"status": "ok", "message": "连接成功"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"连接失败: {str(e)}")


# ── 会话类端点 ─────────────────────────────────────

@router.get("/api/session/status")
async def get_session_status():
    """当前会话状态"""
    if _state["app"] is None or _state["app"].session is None:
        return {"message_count": 0, "summary": ""}
    s = _state["app"].session
    return {
        "message_count": len(s.messages) if s.messages else 0,
        "summary": s.summary or "",
        "has_summary": bool(s.summary),
    }


@router.get("/api/session/history")
async def get_session_history():
    """会话历史摘要"""
    if _state["app"] is None or _state["app"].session is None:
        return {"message_count": 0, "summary": "", "messages": []}
    s = _state["app"].session
    summary = s.get_history_summary() if s.messages else ""
    return {
        "message_count": len(s.messages) if s.messages else 0,
        "summary": summary,
        "messages": s.messages[-50:] if s.messages else [],
    }


@router.post("/api/session/resume")
async def resume_session(req: SessionResumeRequest):
    """恢复已保存会话"""
    from vaxport.session import Session

    if _state["app"] is None:
        raise HTTPException(status_code=503, detail="后端未初始化")

    session = Session.load(req.session_ref)
    if session is None:
        raise HTTPException(status_code=404, detail=f"会话不存在: {req.session_ref}")

    _state["app"].session = session
    return {
        "status": "ok",
        "session_ref": req.session_ref,
        "message_count": len(session.messages) if session.messages else 0,
    }


@router.post("/api/session/save")
async def save_session():
    """保存当前会话"""
    if _state["app"] is None or _state["app"].session is None:
        raise HTTPException(status_code=503, detail="后端未初始化")

    path = _state["app"].session.save()
    return {"status": "ok", "path": str(path) if path else ""}


@router.post("/api/session/clear")
async def clear_session():
    """清空当前会话"""
    from vaxport.session import Session

    if _state["app"] is None:
        raise HTTPException(status_code=503, detail="后端未初始化")

    _state["app"].session = Session()
    return {"status": "ok", "message": "会话已清空"}


@router.get("/api/session/list")
async def list_sessions():
    """列出所有已保存的会话"""
    from vaxport.session import Session
    sessions = Session.list_sessions()
    return {"sessions": sessions, "count": len(sessions)}


class DeleteSessionRequest(BaseModel):
    file: str


@router.delete("/api/session/delete")
async def delete_session(req: DeleteSessionRequest):
    """删除指定会话文件"""
    from vaxport.session import SESSION_DIR

    filepath = SESSION_DIR / f"{req.file}.json"
    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"会话不存在: {req.file}")

    filepath.unlink()
    return {"status": "ok"}


class SwitchDBRequest(BaseModel):
    database: str


@router.post("/api/session/switch-db")
async def switch_database(req: SwitchDBRequest):
    """切换当前数据库"""
    if _state["app"] is None:
        raise HTTPException(status_code=503, detail="后端未初始化")

    app = _state["app"]
    if not app.mdb or not app.mdb.is_connected:
        raise HTTPException(status_code=400, detail="仅多数据库模式支持切换")

    if req.database not in app.mdb.names:
        raise HTTPException(status_code=404, detail=f"数据库不存在: {req.database}")

    ok = app.mdb.switch_to(req.database)
    if not ok:
        raise HTTPException(status_code=500, detail="数据库切换失败")

    return {"status": "ok", "active_db": req.database}


# ── 操作类端点 ─────────────────────────────────────

@router.post("/api/debug/toggle")
async def toggle_debug():
    """切换调试模式"""
    if _state["app"] is None:
        raise HTTPException(status_code=503, detail="后端未初始化")
    _state["app"].debug_mode = not _state["app"].debug_mode
    return {"status": "ok", "debug_mode": _state["app"].debug_mode}


@router.post("/api/schema/refresh")
async def refresh_schema():
    """重新扫描数据库 schema"""
    if _state["app"] is None:
        raise HTTPException(status_code=503, detail="后端未初始化")
    if not _state["app"].db or not _state["app"].db.is_connected:
        raise HTTPException(status_code=503, detail="数据库未连接")

    try:
        if _state["app"].mdb and _state["app"].mdb.is_connected:
            for name in _state["app"].mdb.names:
                db = _state["app"].mdb.get(name)
                _state["app"].tools.discover_and_register(db=db, db_name=name)
        else:
            _state["app"].tools.discover_and_register()

        # 同步更新 orchestrator 中的 db_context
        db_overview = _state["app"]._build_db_overview()
        if db_overview and _state["app"].orchestrator:
            _state["app"].orchestrator.set_db_context(db_overview)

        return {
            "status": "ok",
            "tools_count": len(_state["app"].tools.list_tools()),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Schema 刷新失败: {e}")


@router.post("/api/feedback")
async def submit_feedback(req: FeedbackRequest):
    """提交用户反馈（异步提取记忆）"""
    if _state["app"] is None:
        raise HTTPException(status_code=503, detail="后端未初始化")

    # 异步提取反馈记忆
    try:
        from vaxport.memory import FeedbackMemory
        memory = FeedbackMemory()
        memory.extract_and_store(req.message, _state["app"].llm)
    except Exception:
        pass  # 反馈提取失败不影响主流程

    return {"status": "ok"}


@router.post("/api/ear/feedback")
async def submit_ear_feedback(req: EARFeedbackRequest):
    """提交EAR显式反馈（满意/不满意）"""
    try:
        from vaxport.ear import FeedbackLoop
        feedback_loop = FeedbackLoop()
        feedback_loop.capture_explicit_feedback(req.task_id, req.satisfaction, req.notes)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/ear/stats")
async def get_ear_stats():
    """获取EAR统计信息（轨迹、反馈、路由）"""
    try:
        from vaxport.ear import FeedbackLoop
        feedback_loop = FeedbackLoop()
        return {
            "trajectory": feedback_loop.get_trajectory_stats(),
            "feedback": feedback_loop.get_feedback_stats(),
            "routing": feedback_loop.get_routing_stats(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/ear/sop/status")
async def get_sop_status():
    """获取SOP蒸馏状态"""
    try:
        from vaxport.ear import SOPDistiller
        distiller = SOPDistiller()
        return distiller.get_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/ear/routing/stats")
async def get_routing_stats():
    """获取路由优化统计"""
    try:
        from vaxport.ear import RouterOptimizer
        optimizer = RouterOptimizer()
        return optimizer.get_routing_stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/shutdown")
async def shutdown():
    """关闭后端"""
    if _app and _state["app"].session:
        try:
            _state["app"].session.save()
        except Exception:
            pass
    os._exit(0)


@router.get("/api/health")
async def health_check():
    """健康检查（Go TUI 启动探活）"""
    if _state["app"] is None:
        return {"status": "starting"}
    return {
        "status": "ok",
        "model": _state["app"].llm.active_model if _state["app"].llm else "",
        "backend": _state["app"].llm.active_backend if _state["app"].llm else "",
    }


# ── 辅助函数 ───────────────────────────────────────

def _pg_status() -> str:
    """数据库连接状态"""
    if _state["app"] is None:
        return "未初始化"
    if _state["app"].mdb and _state["app"].mdb.is_connected:
        db_list = ", ".join(
            f"{n}{'*' if n == _state["app"].mdb.active_name else ''}"
            for n in _state["app"].mdb.names
        )
        return f"{db_list}@{_state["app"].config.pg_host} (*=当前)"
    if _state["app"].db and _state["app"].db.is_connected:
        return f"{_state["app"].config.pg_host}/{_state["app"].config.pg_database} (已连接)"
    return "未连接"


def _get_version() -> str:
    try:
        from vaxport import __version__
        return __version__
    except ImportError:
        return "2.0.0"


def _build_schema_tree(db_name: str, db) -> dict:
    """构建单个数据库的 schema 树"""
    schemas = {}
    estimates = db.get_table_row_estimates() if db.is_connected else {}

    for full_name, info in estimates.items():
        schema, table = full_name.split(".", 1) if "." in full_name else ("public", full_name)
        if schema not in schemas:
            schemas[schema] = {"name": schema, "tables": [], "views": [], "matviews": []}

        n = info["rows_estimate"]
        tag = "s" if n <= 100 else "m" if n <= 1000 else "l"
        entry = {
            "name": table,
            "columns": info["columns"],
            "rows_estimate": n,
            "size_tag": tag,
            "type": info.get("type", "table"),
        }

        obj_type = info.get("type", "table")
        if obj_type == "view":
            schemas[schema]["views"].append(entry)
        elif obj_type == "materialized_view":
            schemas[schema]["matviews"].append(entry)
        else:
            schemas[schema]["tables"].append(entry)

    # 排序
    for s in schemas.values():
        s["tables"].sort(key=lambda x: x["name"])
        s["views"].sort(key=lambda x: x["name"])
        s["matviews"].sort(key=lambda x: x["name"])

    return {
        "name": db_name,
        "schemas": sorted(schemas.values(), key=lambda x: x["name"]),
    }


# ── 本地文件代理（供 GUI 渲染 matplotlib 图表）─────────────

from fastapi.responses import FileResponse


@router.get("/api/files/{path:path}")
async def serve_local_file(path: str):
    """提供 ~/.vaxport/ 下的本地文件访问（图表等）"""
    base_dir = Path.home() / ".vaxport"
    file_path = (base_dir / path).resolve()

    # 安全检查：只允许访问 .vaxport 目录下的文件
    if not str(file_path).startswith(str(base_dir.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    return FileResponse(file_path)


# ── 导出端点 ─────────────────────────────────────────

class ExportRequest(BaseModel):
    content: str
    name: str | None = None


@router.post("/api/export/markdown")
async def export_markdown(req: ExportRequest):
    """导出 Markdown 文件，并复制引用的图表到 images/ 子目录"""
    import re
    import shutil

    cfg = load_config()
    export_dir = cfg.export_dir
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = req.name or timestamp
    export_subdir = export_dir / name
    export_subdir.mkdir(parents=True, exist_ok=True)

    content = req.content
    images_dir = export_subdir / "images"
    copied = 0

    for m in re.finditer(r'!\[([^\]]*)\]\(([^)]+)\)', content):
        src_path = m.group(2)
        src = None
        # 完整路径: /Users/.../.vaxport/charts/xxx.png
        if "/.vaxport/charts/" in src_path or "vaxport/charts" in src_path:
            src = Path(src_path).expanduser()
        # 相对路径（纯文件名）: chart_xxx.png
        elif not src_path.startswith("http") and not src_path.startswith("/"):
            src = Path.home() / ".vaxport" / "charts" / src_path
        if src is None or not src.exists():
            continue
        images_dir.mkdir(exist_ok=True)
        dst = images_dir / src.name
        if not dst.exists():
            shutil.copy2(src, dst)
            copied += 1
        rel_path = f"images/{src.name}"
        content = content.replace(src_path, rel_path)

    filepath = export_subdir / f"{name}.md"
    filepath.write_text(content, encoding="utf-8")
    return {"export_path": str(filepath), "images_copied": copied}