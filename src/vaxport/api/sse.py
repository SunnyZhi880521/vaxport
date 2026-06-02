"""SSE 流式端点 — Agent 执行 + plan_confirm 双请求协调"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from vaxport.agent import ProgressCallbacks, Agent
from vaxport.api.server import (
    _state, _pending_plans, _pending_results, _pending_feedback,
    _active_executions, _execution_feedback,
)
from vaxport.orchestrator import AGENT_LABELS

sse_router = APIRouter()


# ── Pydantic 模型 ──────────────────────────────────

class StreamRequest(BaseModel):
    query: str
    plan_mode: bool = False


class ConfirmRequest(BaseModel):
    request_id: str
    confirmed: bool
    feedback: str = ""


class CancelRequest(BaseModel):
    request_id: str


# ── 计划决策解析 ───────────────────────────────────

def _parse_plan_decisions(plan_text: str) -> list[dict]:
    """从 plan 文本中解析决策项，返回结构化列表"""
    decisions = []
    if "无需用户决策" in plan_text:
        return decisions

    # 匹配 "**决策项 N**: ..." 段落
    decision_blocks = re.split(r'\*\*决策项\s*\d+\s*[:：]', plan_text)
    if len(decision_blocks) < 2:
        return decisions

    for block in decision_blocks[1:]:
        title_match = re.match(r'([^\n*]+)', block)
        title = title_match.group(1).strip().rstrip('*').strip() if title_match else ""

        options = []
        for rank, prefix in [("1", "🥇"), ("2", "🥈"), ("3", "🥉")]:
            # 匹配 "🥇 [方案A — 推荐]: ..." 或 "🥇 方案A: ..."
            opt_pattern = rf'{re.escape(prefix)}\s*(?:\[)?([^\]:\n]+)(?:\])?\s*[:：]\s*([^\n]*)'
            opt_match = re.search(opt_pattern, block)
            if opt_match:
                options.append({
                    "rank": rank,
                    "label": opt_match.group(1).strip(),
                    "desc": opt_match.group(2).strip(),
                })

        if options:
            decisions.append({"title": title, "options": options})

    return decisions


# ── SSE Callbacks（将 ProgressCallbacks → SSE 事件）─────

class SSECallbacks(ProgressCallbacks):
    """将 Agent 进度回调转换为 SSE 事件，通过异步队列发送"""

    def __init__(self, queue: "asyncio.Queue"):
        super().__init__()
        self._queue = queue
        self._tool_calls = []
        self._answer_parts = []
        self._plan_parts = []

    def _put(self, event: dict):
        """同步发送事件到异步队列"""
        try:
            self._queue.put_nowait(event)
        except Exception:
            pass

    def get_pending_feedback(self) -> str | None:
        msgs = _execution_feedback.pop(self._request_id, None)
        if msgs:
            combined = "; ".join(msgs)
            self._put({
                "event": "status",
                "data": {"message": f"💬 收到追问: {combined}"},
            })
            return combined
        return None

    def on_thinking(self, description: str = ""):
        self._put({"event": "status", "data": {"message": description or "思考中..."}})

    def on_tool_call(self, tool_name: str, arguments: dict):
        self._tool_calls.append({"name": tool_name, "args": arguments})
        self._put({
            "event": "tool_call",
            "data": {"name": tool_name, "args": arguments},
        })
        self._put({
            "event": "status",
            "data": {"message": f"⚙ 执行: {tool_name}"},
        })

    def on_tool_result(self, row_count: int, truncated: bool = False):
        self._put({
            "event": "tool_result",
            "data": {"row_count": row_count, "truncated": truncated},
        })
        if truncated:
            self._put({
                "event": "status",
                "data": {"message": f"   ↳ {row_count} 行结果 (已截断)"},
            })
        else:
            self._put({
                "event": "status",
                "data": {"message": f"   ↳ {row_count} 行结果"},
            })

    def on_sql(self, sql: str):
        self._put({"event": "sql", "data": {"sql": sql}})

    def on_text_chunk(self, text: str):
        self._answer_parts.append(text)
        self._put({"event": "text_chunk", "data": {"text": text}})

    def on_plan_chunk(self, text: str):
        self._plan_parts.append(text)
        self._put({"event": "plan_chunk", "data": {"text": text}})

    def on_plan(self, plan_text: str) -> bool:
        """PRE-HOOK: 暂停等待用户确认

        在 SSE 线程中调用，通过 threading.Event 阻塞，等待 /api/chat/confirm。
        """
        global _pending_plans, _pending_results, _pending_feedback

        request_id = self._request_id  # 由外部注入

        # 解析决策项
        decisions = _parse_plan_decisions(plan_text)
        has_decisions = bool(decisions)

        self._put({
            "event": "plan_ready",
            "data": {
                "request_id": request_id,
                "plan_text": plan_text,
                "has_decisions": has_decisions,
                "decisions": decisions,
            },
        })

        # 创建 Event 阻塞等待确认
        event = threading.Event()
        _pending_plans[request_id] = event
        _pending_results[request_id] = {"confirmed": False, "feedback": ""}

        # 等待确认（最长 5 分钟）
        confirmed = event.wait(timeout=300)

        # 清理
        _pending_plans.pop(request_id, None)
        result = _pending_results.pop(request_id, {"confirmed": False, "feedback": ""})

        if not confirmed or not result.get("confirmed"):
            self._put({
                "event": "status",
                "data": {"message": "⏸️ 计划已取消"},
            })
            return False

        # 应用反馈
        feedback = result.get("feedback", "")
        if feedback:
            self.plan_feedback = feedback

        self._put({
            "event": "status",
            "data": {"message": "✅ 计划已确认，开始执行..."},
        })
        return True


# ── SSE 事件生成器 ─────────────────────────────────

async def _event_generator(request_id: str, query: str, plan_mode: bool,
                           cancel_event: threading.Event) -> AsyncGenerator[dict, None]:
    """在后台线程运行 Agent，通过 asyncio.Queue 桥接 SSE 流"""
    import asyncio

    queue: asyncio.Queue = asyncio.Queue(maxsize=500)

    # 先发送 meta 事件
    yield {
        "event": "meta",
        "data": json.dumps({
            "request_id": request_id,
            "query": query,
            "plan_mode": plan_mode,
        }, ensure_ascii=False),
    }

    def _run_agent():
        """在独立线程中运行 Agent"""
        try:
            if _state["app"] is None or _state["app"].orchestrator is None:
                queue.put_nowait({
                    "event": "error",
                    "data": {"message": "后端未初始化"},
                })
                queue.put_nowait({"event": "done", "data": {}})
                return

            # 创建 callbacks
            callbacks = SSECallbacks(queue)
            callbacks._request_id = request_id

            # 意图分类
            route = _state["app"].orchestrator.classify(query)
            agent_type = route["intent"]
            _, agent_label, _ = AGENT_LABELS.get(agent_type, ("", "通用", ""))

            queue.put_nowait({
                "event": "status",
                "data": {
                    "message": f"🔍 分类: {agent_label} Agent",
                    "agent_type": agent_type,
                    "agent_label": agent_label,
                    "reason": route.get("reason", ""),
                },
            })

            # 构建历史
            history = None
            if _state["app"].session and _state["app"].session.messages:
                history = []
                if _state["app"].session.summary:
                    history.append({
                        "role": "system",
                        "content": f"📋 会话摘要:\n{_state["app"].session.summary}",
                    })
                history.extend(_state["app"].session.messages[-20:])

            # 执行
            result = _state["app"].orchestrator.run(
                query,
                callbacks=callbacks,
                plan_mode=plan_mode,
                history=history,
                cancel_event=cancel_event,
            )

            # 构造 answer 事件
            answer = result.get("answer", "")
            queue.put_nowait({
                "event": "answer",
                "data": {
                    "answer": answer,
                    "agent_type": result.get("agent_type", agent_type),
                    "agent_chain": result.get("agent_chain", [agent_type]),
                    "turns": result.get("turns", 0),
                    "tokens_used": result.get("tokens_used", 0),
                    "token_pct": result.get("token_pct", 0),
                    "sql_queries": result.get("sql_queries", []),
                    "model": result.get("model", ""),
                    "backend": result.get("backend", ""),
                },
            })

            # 更新会话
            if _state["app"].session is not None:
                try:
                    _state["app"].session.add_message("user", query)
                    _state["app"].session.add_message("assistant", answer)
                    _state["app"].session.save()
                except Exception:
                    pass

        except Exception as e:
            queue.put_nowait({
                "event": "error",
                "data": {"message": str(e)},
            })
        finally:
            queue.put_nowait({"event": "done", "data": {}})

    # 启动 Agent 线程
    thread = threading.Thread(target=_run_agent, daemon=True)
    thread.start()

    start_time = time.time()
    last_heartbeat = 0.0

    # 从队列消费事件 → SSE
    while True:
        try:
            event = await asyncio.wait_for(queue.get(), timeout=0.5)
        except asyncio.TimeoutError:
            if thread.is_alive():
                now = time.time()
                if now - last_heartbeat >= 5:
                    yield {
                        "event": "heartbeat",
                        "data": json.dumps({
                            "elapsed": round(now - start_time, 1),
                        }),
                    }
                    last_heartbeat = now
                continue
            if queue.empty():
                break
            continue

        yield {
            "event": event["event"],
            "data": json.dumps(event["data"], ensure_ascii=False, default=str),
        }

        if event["event"] in ("done", "error"):
            break

    thread.join(timeout=5)


# ── SSE 端点 ───────────────────────────────────────

@sse_router.post("/api/chat/stream")
async def chat_stream(req: StreamRequest):
    """SSE 流式 Agent 执行端点"""
    global _state, _active_executions

    if _state["app"] is None:
        raise HTTPException(status_code=503, detail="后端未初始化")

    request_id = str(uuid.uuid4())
    cancel_event = threading.Event()
    _active_executions[request_id] = cancel_event

    async def _stream_wrapper():
        try:
            async for event in _event_generator(request_id, req.query, req.plan_mode, cancel_event):
                yield event
        finally:
            _active_executions.pop(request_id, None)
            _execution_feedback.pop(request_id, None)

    return EventSourceResponse(_stream_wrapper())


# ── plan_confirm 端点 ──────────────────────────────

@sse_router.post("/api/chat/confirm")
async def confirm_plan(req: ConfirmRequest):
    """确认/取消执行计划

    Go TUI 在收到 plan_ready 事件后，用户做出决定时调用此端点。
    """
    global _pending_plans, _pending_results, _pending_feedback

    event = _pending_plans.get(req.request_id)
    if event is None:
        raise HTTPException(
            status_code=404,
            detail=f"未找到等待确认的计划: {req.request_id}（可能已超时或不存在）",
        )

    _pending_results[req.request_id] = {
        "confirmed": req.confirmed,
        "feedback": req.feedback,
    }
    event.set()

    return {"status": "ok"}


@sse_router.post("/api/chat/cancel")
async def cancel_execution(req: CancelRequest):
    """取消正在进行的执行"""
    global _pending_plans, _pending_results, _active_executions

    # 优先取消 plan_confirm 等待
    plan_event = _pending_plans.get(req.request_id)
    if plan_event:
        _pending_results[req.request_id] = {"confirmed": False, "feedback": ""}
        plan_event.set()
        return {"status": "ok", "cancelled": "plan_confirm"}

    # 取消活跃执行
    cancel_event = _active_executions.get(req.request_id)
    if cancel_event:
        cancel_event.set()
        return {"status": "ok", "cancelled": "execution"}

    raise HTTPException(
        status_code=404,
        detail=f"未找到活跃执行: {req.request_id}",
    )


@sse_router.post("/api/chat/feedback")
async def append_feedback(req: ConfirmRequest):
    """追加上下文文本到等待中的 plan 或活跃执行（用户在 plan 等待期间或执行中输入文字）"""
    global _pending_feedback, _execution_feedback

    # 如果是 plan 等待期间
    if req.request_id in _pending_plans:
        _pending_feedback[req.request_id] = req.feedback
        return {"status": "ok", "mode": "plan"}

    # 如果是活跃执行期间
    if req.request_id in _active_executions:
        if req.request_id not in _execution_feedback:
            _execution_feedback[req.request_id] = []
        _execution_feedback[req.request_id].append(req.feedback)
        return {"status": "ok", "mode": "execution"}

    return {"status": "ok", "mode": "unknown"}