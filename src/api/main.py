"""Enterprise FastAPI surface for monitoring, diagnostics and equipment graph views."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncGenerator

# 确保项目根目录在 Python 路径中（直接运行 python src/api/main.py 时需要）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.agents.autonomous_loop import ActivityBus, ActivityEvent, AutonomousLoop
from src.database.connection import Database
from src.database.init_db import bootstrap_database
from src.graph.main_graph import build_graph
from src.knowledge.fault_graph import FaultGraph
from src.severity import NEUTRAL_HEALTH as _NEUTRAL_HEALTH, severity_from_reading as _severity_from_reading
from src.observability import (
    get_logger,
    log_stats,
    new_trace_id,
    read_app_logs,
    read_jsonl,
    trace_id_ctx,
)


class DiagnosticRequest(BaseModel):
    """Diagnostic trigger payload."""

    machine_id: str = Field(min_length=3, max_length=32, pattern=r"^CNC_\d{3}$")
    thread_id: str | None = None


class KnowledgeSearchRequest(BaseModel):
    """Knowledge search payload."""

    query: str = Field(min_length=1, max_length=500)
    fault_code: int | None = Field(default=None, ge=0, le=9)
    top_k: int = Field(default=5, ge=1, le=20)


class MaintenanceOrderRequest(BaseModel):
    """Human-approved maintenance order payload."""

    machine_id: str = Field(min_length=3, max_length=32, pattern=r"^CNC_\d{3}$")
    diagnostic_id: str | None = None
    maintenance_type: str = Field(default="corrective", max_length=32)
    technician: str = Field(default="待分配", max_length=64)
    notes: str = Field(default="", max_length=2000)


# --- 严重度评分：统一口径 -------------------------------------------------
# 口径已抽到 `src.severity`（诊断智能体也要用，不能从 API 层反向 import）。
#   * health_score 缺失 → 按中性基线 0.8 处理（字段缺失 ≠ 设备离线）；
#   * "离线"只由读数新鲜度决定——最新读数落后全局最新时间超过阈值才算离线。
_OFFLINE_LAG_MINUTES = int(os.getenv("OFFLINE_LAG_MINUTES", "120"))


def _parse_ts(value: object) -> datetime | None:
    """宽松解析 ISO 时间戳，失败返回 None。"""

    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _offline_predicate(machines: list[dict[str, object]]):
    """返回判定"离线"的闭包：读数落后全局最新时间超过阈值即视为离线。"""

    stamps = [_parse_ts((machine.get("latest_reading") or {}).get("timestamp")) for machine in machines]
    valid = [stamp for stamp in stamps if stamp is not None]
    newest = max(valid) if valid else None

    def is_offline(machine: dict[str, object]) -> bool:
        latest = machine.get("latest_reading")
        if not isinstance(latest, dict):
            return True
        stamp = _parse_ts(latest.get("timestamp"))
        if stamp is None or newest is None:
            return True
        return (newest - stamp).total_seconds() > _OFFLINE_LAG_MINUTES * 60

    return is_offline


class ReportPushRequest(BaseModel):
    """维护报告推送参数：支持企业微信 / 飞书 / 钉钉群机器人 Webhook。"""

    channel: str = Field(default="", max_length=16, description="feishu | wecom | dingtalk，留空读 .env")
    webhook_url: str | None = Field(default=None, max_length=500, description="留空则用 .env 的 REPORT_WEBHOOK_URL")


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Initialize local persistence and start the autonomous monitoring loop."""

    application.state.database = bootstrap_database()
    application.state.fault_graph = FaultGraph()
    application.state.graph = build_graph(application.state.database, application.state.fault_graph)
    application.state.activity_bus = ActivityBus()

    # The graph.ainvoke call is async, but the loop lives in async context, so wrap it.
    async def _runner(machine_id: str) -> dict[str, object]:
        return await _run_diagnostic_for_loop(machine_id)

    application.state.autonomous_loop = AutonomousLoop(
        database=application.state.database,
        bus=application.state.activity_bus,
        run_diagnostic=_runner,
    )
    # 自主循环开关：默认关闭，避免不必要的 token 消耗；设为 1/true/yes 启动
    loop_enabled = os.getenv("AUTONOMOUS_LOOP_ENABLED", "0").lower() in {"1", "true", "yes", "on"}
    application.state.loop_enabled = loop_enabled
    if loop_enabled:
        await application.state.autonomous_loop.start()
    else:
        application.state.activity_bus.publish(ActivityEvent(
            id=0, timestamp=datetime.now().astimezone().isoformat(),
            agent_name="System", machine_id=None, action="loop_disabled",
            reason="自主监控循环未启动（AUTONOMOUS_LOOP_ENABLED=0）。如需启用，设置环境变量或调用 /api/agent/loop/toggle 接口。",
            severity="info",
        ))

    # 实时数据推进（需求 7）：默认关闭，开启后按真实时钟向 SQLite 追加工况调制读数
    application.state.realtime = None
    if os.getenv("REALTIME_ENABLED", "0").lower() in {"1", "true", "yes", "on"}:
        from src.data import RealtimeEngine
        engine = RealtimeEngine(application.state.database)
        engine.start()
        application.state.realtime = engine

    # 聊天 Agent（需求 2）：懒加载，首次提问时才装配 RAG 索引
    application.state.chat_agent = None

    # Skill 注册表：进程启动时统一发现注册（幂等），审批联动 / 技能中心都依赖它
    from src.skills import SkillRegistry
    SkillRegistry.discover()

    try:
        yield
    finally:
        if application.state.autonomous_loop.is_running():
            await application.state.autonomous_loop.stop()
        if application.state.realtime is not None:
            await application.state.realtime.stop()


async def _run_diagnostic_for_loop(machine_id: str) -> dict[str, object]:
    """Diagnostic entry-point used by the autonomous loop (and reusable by HTTP too)."""

    database = get_database()
    machine = database.get_machine(machine_id)
    if machine is None:
        raise HTTPException(status_code=404, detail="Machine not found")
    thread_id = f"loop-{machine_id}"
    state = await app.state.graph.ainvoke({"machine_id": machine_id, "retry_count": 0, "max_retries": 3, "events": [], "messages": []}, {"configurable": {"thread_id": thread_id}})
    diagnostic_id = str(uuid.uuid4())
    payload = dict(state)
    payload.pop("messages", None)
    database.save_diagnostic(diagnostic_id, machine_id, "completed", str(state.get("severity", "normal")), float(state.get("confidence", 0.0)), payload)
    _rul_dict = state.get("rul") if isinstance(state.get("rul"), dict) else {}
    _rul_p50 = _rul_dict.get("rul_p50") or _rul_dict.get("p50") or state.get("rul_p50")
    return {**payload, "diagnostic_id": diagnostic_id, "machine_id": machine_id, "severity": state.get("severity"), "fault_name": state.get("fault_mode") or state.get("fault_name") or "未识别故障", "confidence": state.get("confidence"), "rul_p50": _rul_p50, "rul_hours": _rul_p50}


app = FastAPI(title="智维先知企业级 Agent API", version="2.0.0", lifespan=lifespan)
origins = [item.strip() for item in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",") if item.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    """Bind one trace_id per HTTP request so every downstream log line is linkable.

    请求头带 ``X-Trace-Id`` 时透传（用于把前端/外部系统的链路串起来），否则新生成一个。
    响应头回写 trace_id，前端报错时可以直接把这个 id 贴给排查的人。
    """

    trace_id = request.headers.get("X-Trace-Id") or new_trace_id()
    token = trace_id_ctx.set(trace_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        get_logger("api").exception(
            json.dumps({"action": "request_failed", "path": request.url.path, "method": request.method}, ensure_ascii=False)
        )
        raise
    finally:
        trace_id_ctx.reset(token)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    response.headers["X-Trace-Id"] = trace_id
    response.headers["X-Response-Time"] = f"{elapsed_ms}ms"
    get_logger("api").info(
        json.dumps(
            {"action": "request", "method": request.method, "path": request.url.path,
             "status": response.status_code, "ms": elapsed_ms},
            ensure_ascii=False,
        )
    )
    return response


def get_database() -> Database:
    """Return the initialized repository."""

    return app.state.database


def get_fault_graph() -> FaultGraph:
    """Return the in-memory causal graph."""

    return app.state.fault_graph


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    """Return service metadata."""

    return {"service": "智维先知企业级 Agent", "version": app.version, "status": "running", "docs": "/docs"}


@app.get("/health", tags=["system"])
async def health() -> dict[str, object]:
    """Return dependency and data readiness information."""

    database = get_database()
    from src.llm.client import UnifiedLLMClient
    return {"status": "ok", "database": {"machines": database.count("machines"), "readings": database.count("sensor_readings"), "diagnostics": database.count("diagnostics")}, "llm": await UnifiedLLMClient(timeout_seconds=4.0).health_check()}


@app.get("/api/overview", tags=["monitoring"])
async def overview() -> dict[str, object]:
    """Return fleet KPIs calculated from the stored telemetry."""

    database = get_database()
    machines = database.list_machines()
    is_offline = _offline_predicate(machines)
    scored: list[dict[str, object]] = []
    for machine in machines:
        latest = machine.get("latest_reading")
        if not isinstance(latest, dict) or is_offline(machine):
            scored.append({**machine, "live_health_score": None, "live_anomaly_score": None, "live_severity": "offline", "live_fault_code": 0})
            continue
        # 主信号：仿真器写入的活动故障码；次信号：health_score 退化
        score, severity = _severity_from_reading(latest.get("health_score"), int(latest.get("fault_code") or 0))
        scored.append({**machine, "live_health_score": score, "live_anomaly_score": round(1.0 - score, 4), "live_severity": severity, "live_fault_code": int(latest.get("fault_code") or 0)})
    severities = {"normal": 0, "warning": 0, "critical": 0, "offline": 0}
    for machine in scored:
        severity = str(machine["live_severity"])
        severities[severity] = severities.get(severity, 0) + 1
    health_scores = [float(machine["live_health_score"]) for machine in scored if machine["live_health_score"] is not None]
    return {"fleet": {"total": len(scored), "normal": severities["normal"], "warning": severities["warning"], "critical": severities["critical"], "offline": severities["offline"], "average_health": round(sum(health_scores) / len(health_scores), 4) if health_scores else 0.0}, "machines": scored, "diagnostics": database.list_diagnostics(limit=20), "orders": database.list_maintenance_orders(limit=20)}


@app.get("/api/machines", tags=["machines"])
async def list_machines(status: str | None = Query(default=None)) -> dict[str, object]:
    """List machines, optionally filtered by persisted status."""

    machines = get_database().list_machines(status)
    is_offline = _offline_predicate(machines)
    enriched: list[dict[str, object]] = []
    for machine in machines:
        latest = machine.get("latest_reading")
        if not isinstance(latest, dict) or is_offline(machine):
            enriched.append({**machine, "live_health_score": None, "live_severity": "offline", "live_fault_code": 0})
            continue
        score, severity = _severity_from_reading(latest.get("health_score"), int(latest.get("fault_code") or 0))
        enriched.append({**machine, "live_health_score": score, "live_severity": severity, "live_fault_code": int(latest.get("fault_code") or 0)})
    return {"items": enriched, "count": len(enriched)}


@app.get("/api/machines/{machine_id}", tags=["machines"])
async def machine_detail(machine_id: str) -> dict[str, object]:
    """Return a machine profile, latest reading and recent diagnostics."""

    database = get_database()
    machine = database.get_machine(machine_id)
    if machine is None:
        raise HTTPException(status_code=404, detail="Machine not found")
    history = database.get_readings(machine_id, 500)
    latest = history[-1] if history else None
    if latest:
        score, severity = _severity_from_reading(latest.get("health_score"), int(latest.get("fault_code") or 0))
        health_info = {"health_score": score, "anomaly_score": round(1.0 - score, 4), "severity": severity, "fault_code": int(latest.get("fault_code") or 0)}
    else:
        health_info = None
    return {"machine": machine, "health": health_info, "history": history, "diagnostics": database.list_diagnostics(machine_id, 20), "orders": [order for order in database.list_maintenance_orders(200) if order["machine_id"] == machine_id]}


@app.get("/api/machines/{machine_id}/realtime", tags=["machines"])
async def machine_realtime(machine_id: str) -> dict[str, object]:
    """Return latest telemetry and a live anomaly result."""

    database = get_database()
    history = database.get_readings(machine_id, 500)
    if not history:
        raise HTTPException(status_code=404, detail="No telemetry for machine")
    latest = history[-1]
    score, severity = _severity_from_reading(latest.get("health_score"), int(latest.get("fault_code") or 0))
    return {"machine_id": machine_id, "reading": latest, "health": {"health_score": score, "anomaly_score": round(1.0 - score, 4), "severity": severity, "fault_code": int(latest.get("fault_code") or 0)}}


@app.get("/api/machines/{machine_id}/history", tags=["machines"])
async def machine_history(machine_id: str, limit: int = Query(default=240, ge=1, le=5000)) -> dict[str, object]:
    """Return chart-ready time series data."""

    return {"machine_id": machine_id, "items": get_database().get_readings(machine_id, limit)}


async def _run_diagnostic(request: DiagnosticRequest) -> dict[str, object]:
    """Execute the graph and persist its state as episodic diagnostic memory."""

    database = get_database()
    machine = database.get_machine(request.machine_id)
    if machine is None:
        raise HTTPException(status_code=404, detail="Machine not found")
    thread_id = request.thread_id or str(uuid.uuid4())
    state = await app.state.graph.ainvoke({"machine_id": request.machine_id, "retry_count": 0, "max_retries": 3, "events": [], "messages": []}, {"configurable": {"thread_id": thread_id}})
    diagnostic_id = str(uuid.uuid4())
    payload = dict(state)
    payload.pop("messages", None)
    database.save_diagnostic(diagnostic_id, request.machine_id, "completed", str(state.get("severity", "normal")), float(state.get("confidence", 0.0)), payload)
    _rul_dict = state.get("rul") if isinstance(state.get("rul"), dict) else {}
    _rul_p50 = _rul_dict.get("rul_p50") or _rul_dict.get("p50")
    return {"id": diagnostic_id, "thread_id": thread_id, "diagnostic_id": diagnostic_id, "machine_id": request.machine_id, "severity": state.get("severity"), "fault_name": state.get("fault_mode") or state.get("fault_name") or "未识别故障", "confidence": state.get("confidence"), "rul_p50": _rul_p50, "rul_hours": _rul_p50, **payload}


@app.post("/api/diagnostics/trigger", tags=["diagnostics"])
async def trigger_diagnostic(request: DiagnosticRequest) -> dict[str, object]:
    """Trigger the event-driven Monitor→Diagnostic→Prediction→Decision workflow."""

    return await _run_diagnostic(request)


@app.get("/api/diagnostics", tags=["diagnostics"])
async def list_diagnostics(machine_id: str | None = Query(default=None), limit: int = Query(default=50, ge=1, le=200)) -> dict[str, object]:
    """Return persisted diagnostic history."""

    items = get_database().list_diagnostics(machine_id, limit)
    return {"items": items, "count": len(items)}


@app.get("/api/diagnostics/{diagnostic_id}", tags=["diagnostics"])
async def diagnostic_detail(diagnostic_id: str) -> dict[str, object]:
    """Return one complete report and evidence chain."""

    item = get_database().get_diagnostic(diagnostic_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Diagnostic not found")
    return item


@app.get("/api/knowledge/fmeca", tags=["knowledge"])
async def list_fmeca() -> dict[str, object]:
    """Browse structured FMECA rules."""

    items = get_database().list_fmeca()
    return {"items": items, "count": len(items)}


@app.get("/api/knowledge/fmeca/{fault_code}", tags=["knowledge"])
async def fmeca_detail(fault_code: int) -> dict[str, object]:
    """Return one FMECA rule and SOP."""

    item = get_database().get_fmeca(fault_code)
    if item is None:
        raise HTTPException(status_code=404, detail="FMECA rule not found")
    return item


@app.post("/api/knowledge/search", tags=["knowledge"])
async def knowledge_search(request: KnowledgeSearchRequest) -> dict[str, object]:
    """Hybrid knowledge search: BM25 + bge-m3 dense fused with RRF, plus FMECA/case results."""

    database = get_database()
    rules = [rule for rule in database.list_fmeca() if request.query.lower() in str(rule).lower()]
    cases = database.search_cases(request.query, request.fault_code, request.top_k)
    # 混合检索（需求 6）：自适应切分块上的稠密+稀疏召回
    hybrid: list[dict[str, object]] = []
    try:
        agent = await get_chat_agent()
        hits = await agent.index_retriever().search(
            request.query, top_k=request.top_k,
            fault_code=request.fault_code if request.fault_code else None,
        )
        hybrid = [
            {"id": hit.chunk.id, "score": round(hit.score, 4), "doc_type": hit.chunk.doc_type,
             "source": hit.chunk.source, "section": hit.chunk.section_path,
             "text": hit.chunk.text[:600], "bm25_rank": hit.bm25_rank, "dense_rank": hit.dense_rank}
            for hit in hits
        ]
    except Exception as error:  # noqa: BLE001 - 混合检索失败时退回词法结果
        from src.observability import get_logger
        get_logger("api").warning(json.dumps({"action": "hybrid_search_failed", "error": str(error)}, ensure_ascii=False))
    return {
        "query": request.query, "fmeca": rules[: request.top_k], "cases": cases,
        "chunks": hybrid,
        "sources": ["fmeca_rules", "case_library", "knowledge_chunks(manual/spec/cost/fmeca)"],
    }


@app.get("/api/topology", tags=["knowledge"])
async def topology() -> dict[str, object]:
    """Return a device→production-line→fault causal graph for the graph UI."""

    machines = get_database().list_machines()
    nodes: list[dict[str, object]] = [{"id": str(machine["id"]), "label": str(machine["id"]), "type": "machine", "status": machine["status"], "line": machine["production_line"]} for machine in machines]
    lines = sorted({str(machine["production_line"]) for machine in machines})
    nodes.extend({"id": line, "label": line, "type": "line"} for line in lines)
    edges = [{"source": str(machine["id"]), "target": str(machine["production_line"]), "type": "production"} for machine in machines]
    causal = get_fault_graph().topology()
    nodes.extend({"id": f"fault:{node['id']}", "label": node.get("label", node["id"]), "type": f"fault_{node.get('type', 'node')}"} for node in causal["nodes"])
    edges.extend({"source": f"fault:{edge['source']}", "target": f"fault:{edge['target']}", "type": "causal", "probability": edge.get("probability", 0.0)} for edge in causal["edges"])
    return {"nodes": nodes, "edges": edges}


@app.get("/api/operations/orders", tags=["operations"])
async def list_orders() -> dict[str, object]:
    """Return maintenance work orders."""

    items = get_database().list_maintenance_orders()
    return {"items": items, "count": len(items)}


@app.post("/api/operations/orders", tags=["operations"])
async def create_order(request: MaintenanceOrderRequest) -> dict[str, object]:
    """Create a human-visible work order from a reviewed recommendation."""

    if get_database().get_machine(request.machine_id) is None:
        raise HTTPException(status_code=404, detail="Machine not found")
    order_id = f"WO-{datetime_slug()}-{request.machine_id}"
    payload = {"id": order_id, "machine_id": request.machine_id, "diagnostic_id": request.diagnostic_id, "maintenance_type": request.maintenance_type, "start_time": datetime.now().astimezone().isoformat(), "end_time": None, "downtime_hours": 0.0, "parts_cost": 0.0, "labor_cost": 0.0, "parts_json": json.dumps([], ensure_ascii=False), "technician": request.technician, "notes": request.notes, "status": "open"}
    get_database().create_maintenance_order(payload)
    return {"id": order_id, "status": "open"}


def datetime_slug() -> str:
    """Return a sortable short timestamp for human-facing order IDs."""

    from datetime import datetime
    return datetime.now().strftime("%Y%m%d%H%M%S")


class RealtimeHub:
    """Manage WebSocket clients without sharing mutable diagnostic state."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def connect(self, socket: WebSocket) -> None:
        """Accept and register a client."""

        await socket.accept()
        self.clients.add(socket)

    def disconnect(self, socket: WebSocket) -> None:
        """Remove a disconnected client."""

        self.clients.discard(socket)

    async def broadcast(self, payload: dict[str, object]) -> None:
        """Broadcast a JSON snapshot and drop broken sockets."""

        disconnected: list[WebSocket] = []
        for socket in self.clients:
            try:
                await socket.send_json(payload)
            except Exception:
                disconnected.append(socket)
        for socket in disconnected:
            self.disconnect(socket)


hub = RealtimeHub()


@app.websocket("/ws/realtime")
async def realtime(socket: WebSocket) -> None:
    """Push fleet snapshots and accept ping messages."""

    await hub.connect(socket)
    try:
        while True:
            try:
                message = await asyncio.wait_for(socket.receive_json(), timeout=5.0)
            except asyncio.TimeoutError:
                message = {"action": "snapshot"}
            if message.get("action") == "ping":
                await socket.send_json({"type": "pong"})
            else:
                machines = get_database().list_machines()
                await socket.send_json({"type": "fleet_snapshot", "machines": machines, "diagnostics": get_database().list_diagnostics(limit=10)})
    except WebSocketDisconnect:
        pass
    finally:
        hub.disconnect(socket)


async def diagnostic_stream(request: DiagnosticRequest) -> AsyncGenerator[str, None]:
    """Yield graph event values as Server-Sent Events."""

    thread_id = request.thread_id or str(uuid.uuid4())
    try:
        async for value in app.state.graph.astream({"machine_id": request.machine_id, "retry_count": 0, "max_retries": 3, "events": [], "messages": []}, {"configurable": {"thread_id": thread_id}}, stream_mode="values"):
            serializable = {key: value[key] for key in ("machine_id", "events", "severity", "fault_mode", "rul", "decision", "reflection_checks") if key in value}
            yield f"data: {json.dumps(serializable, ensure_ascii=False, default=str)}\n\n"
        yield "data: {\"status\":\"complete\"}\n\n"
    except Exception as error:
        yield f"data: {json.dumps({'status': 'error', 'message': str(error)}, ensure_ascii=False)}\n\n"


@app.post("/api/diagnostics/stream", tags=["diagnostics"])
async def stream_diagnostic(request: DiagnosticRequest) -> StreamingResponse:
    """Stream intermediate specialist events for the reasoning console."""

    return StreamingResponse(diagnostic_stream(request), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})


# ============================================================================
#  Agent 自主行为 API — 让用户看到 Agent 在做什么
# ============================================================================

class NotificationReviewRequest(BaseModel):
    """Human review payload for a pending notification."""

    action: str = Field(pattern=r"^(approve|dismiss)$")
    reviewer: str = Field(default="操作员", min_length=1, max_length=64)
    notes: str = Field(default="", max_length=2000)


@app.get("/api/agent/activity", tags=["agent"])
async def list_agent_activity(limit: int = Query(default=100, ge=1, le=500), machine_id: str | None = Query(default=None)) -> dict[str, object]:
    """Return the Agent activity log so the human can audit what the Agent did."""

    database = get_database()
    items = database.list_agent_activity(limit=limit, machine_id=machine_id)
    return {
        "items": items,
        "count": len(items),
        "loop_running": app.state.autonomous_loop.is_running(),
        "last_scan_at": app.state.autonomous_loop.last_scan_at,
        "last_summary": app.state.autonomous_loop.last_summary,
    }


@app.get("/api/agent/activity/realtime", tags=["agent"])
async def list_recent_activity(limit: int = Query(default=30, ge=1, le=200)) -> dict[str, object]:
    """Return the in-memory recent activity (fast path, no DB read)."""

    bus: ActivityBus = app.state.activity_bus
    return {"items": [event.to_dict() for event in bus.recent(limit)], "count": min(limit, len(bus.recent(limit)))}


@app.post("/api/agent/scan", tags=["agent"])
async def trigger_scan() -> dict[str, object]:
    """Force the autonomous loop to scan immediately."""

    return await app.state.autonomous_loop.trigger_scan()


@app.post("/api/agent/loop/toggle", tags=["agent"])
async def toggle_loop(enable: bool | None = Query(default=None)) -> dict[str, object]:
    """开启或关闭自主监控循环。不传参数则切换当前状态。"""

    loop = app.state.autonomous_loop
    currently_running = loop.is_running()
    should_enable = (not currently_running) if enable is None else enable

    if should_enable and not currently_running:
        await loop.start()
        app.state.loop_enabled = True
    elif not should_enable and currently_running:
        await loop.stop()
        app.state.loop_enabled = False

    return {"loop_running": loop.is_running(), "loop_enabled": app.state.loop_enabled, "message": "自主循环已开启" if loop.is_running() else "自主循环已关闭"}


@app.get("/api/agent/loop/status", tags=["agent"])
async def loop_status() -> dict[str, object]:
    """查询自主循环当前状态。"""

    return {"loop_running": app.state.autonomous_loop.is_running(), "loop_enabled": getattr(app.state, "loop_enabled", False), "last_scan_at": app.state.autonomous_loop.last_scan_at, "last_summary": app.state.autonomous_loop.last_summary}


@app.websocket("/ws/agent")
async def agent_activity_stream(socket: WebSocket) -> None:
    """Push every Agent activity event in real time (used by the Agent monitor page)."""

    await socket.accept()
    bus: ActivityBus = app.state.activity_bus
    queue = bus.subscribe()
    try:
        # Send an initial snapshot so the UI has context immediately.
        await socket.send_json({
            "type": "snapshot",
            "loop_running": app.state.autonomous_loop.is_running(),
            "recent": [event.to_dict() for event in bus.recent(20)],
            "last_scan_at": app.state.autonomous_loop.last_scan_at,
            "last_summary": app.state.autonomous_loop.last_summary,
        })
        while True:
            event = await queue.get()
            await socket.send_json({"type": "activity", "event": event.to_dict()})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        bus.unsubscribe(queue)


@app.get("/api/notifications", tags=["agent"])
async def list_notifications(status: str | None = Query(default="pending"), limit: int = Query(default=50, ge=1, le=200)) -> dict[str, object]:
    """List Agent-issued notifications (default: pending human review)."""

    return {"items": get_database().list_notifications(status=status, limit=limit), "count": len(get_database().list_notifications(status=status, limit=limit))}


@app.post("/api/notifications/{notification_id}/review", tags=["agent"])
async def review_notification(notification_id: str, request: NotificationReviewRequest) -> dict[str, object]:
    """Approve or dismiss a pending Agent notification (the human-in-the-loop audit point)."""

    database = get_database()
    # Mirror the human decision back into the activity log so the Agent can learn from it.
    row = database.review_notification(notification_id, request.reviewer, request.action, request.notes)
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found or already reviewed")
    database.log_agent_activity(
        agent_name="HumanReviewer",
        action=f"notification_{request.action}",
        machine_id=row.get("machine_id"),
        reason=f"{request.reviewer} {'批准' if request.action == 'approve' else '驳回'} 通知 {notification_id}：{request.notes or '(无备注)'}",
        severity="info",
        details={"notification_id": notification_id, "reviewer": request.reviewer, "notes": request.notes, "original_severity": row.get("severity")},
    )

    # 审批联动：高风险/紧急告警获批后自动生成维护报告（需求 8 的触发链）
    report: dict[str, object] | None = None
    if request.action == "approve" and row.get("machine_id") and row.get("severity") in {"high", "critical"}:
        try:
            from src.skills import SkillRegistry
            report = SkillRegistry.run("maintenance_report", {
                "machine_id": str(row["machine_id"]),
                "diagnostic_id": row.get("related_diagnostic_id"),
                "notification_id": notification_id,
                "approved_by": request.reviewer,
            }, caller="api.approval_chain")
        except Exception as error:  # noqa: BLE001 - 报告生成失败不阻断审批结果返回
            report = {"error": f"{type(error).__name__}: {error}"}
        database.log_agent_activity(
            agent_name="System",
            action="maintenance_report_generated",
            machine_id=row.get("machine_id"),
            reason=f"通知 {notification_id} 获批，自动生成维护报告"
            + (f"（{report.get('report_id')}）" if report and report.get("report_id") else "（失败，可手动重试）"),
            severity="info",
            details={"notification_id": notification_id, "report": report},
        )
    return {**row, "report": report}


# ============================================================================
#  可观测性 API — 让"调用了什么 / 耗时多少 / 出了什么错"可查
# ============================================================================

@app.get("/api/logs", tags=["observability"])
async def read_logs(
    limit: int = Query(default=200, ge=1, le=2000),
    level: str | None = Query(default=None, description="INFO / WARNING / ERROR"),
    component: str | None = Query(default=None, description="组件名子串，如 llm / chat / api"),
    trace_id: str | None = Query(default=None),
    since: str | None = Query(default=None, description="起始时间字符串，与 app.log 的时间戳格式一致"),
) -> dict[str, object]:
    """Read structured application logs (newest first) for the log console."""

    items = read_app_logs(limit=limit, level=level, component=component, trace_id=trace_id, since=since)
    return {"items": items, "count": len(items), "stats": log_stats()}


@app.get("/api/logs/llm", tags=["observability"])
async def read_llm_audit(
    limit: int = Query(default=200, ge=1, le=2000),
    trace_id: str | None = Query(default=None),
) -> dict[str, object]:
    """Read LLM invocation audits: provider, model, tokens, latency."""

    items = read_jsonl("llm_audit", limit=limit, trace_id=trace_id)
    return {"items": items, "count": len(items)}


@app.get("/api/logs/tools", tags=["observability"])
async def read_tool_calls(
    limit: int = Query(default=200, ge=1, le=2000),
    trace_id: str | None = Query(default=None),
) -> dict[str, object]:
    """Read Skill / Tool invocation audits: caller, args, latency, determinism."""

    items = read_jsonl("tool_calls", limit=limit, trace_id=trace_id)
    return {"items": items, "count": len(items)}


@app.get("/api/logs/trace/{trace_id}", tags=["observability"])
async def read_trace(trace_id: str) -> dict[str, object]:
    """Return every log record sharing one trace_id — the full call chain of one request."""

    return {
        "trace_id": trace_id,
        "app": read_app_logs(limit=500, trace_id=trace_id),
        "llm": read_jsonl("llm_audit", limit=200, trace_id=trace_id),
        "tools": read_jsonl("tool_calls", limit=200, trace_id=trace_id),
    }


@app.get("/api/logs/stats", tags=["observability"])
async def read_log_stats() -> dict[str, object]:
    """Return log file sizes and line counts."""

    return log_stats()


# ============================================================================
#  Skills 注册表 API — 前端"技能中心"页与 Agent 工具复用
# ============================================================================

class SkillRunRequest(BaseModel):
    """Generic skill execution payload."""

    payload: dict[str, object] = Field(default_factory=dict)


@app.get("/api/skills", tags=["skills"])
async def list_skills() -> dict[str, object]:
    """List every registered skill with metadata, schemas and examples."""

    from src.skills import SkillRegistry
    SkillRegistry.discover()
    metas = SkillRegistry.all()
    return {"items": [meta.as_dict() for meta in metas], "count": len(metas)}


@app.get("/api/skills/{skill_id}", tags=["skills"])
async def skill_detail(skill_id: str) -> dict[str, object]:
    """Return one skill's contract: input/output JSON schema, examples, health."""

    from src.skills import SkillRegistry
    SkillRegistry.discover()
    skill = SkillRegistry.get(skill_id)
    return {
        "meta": skill.meta.as_dict(),
        "input_schema": skill.input_schema.model_json_schema(),
        "output_schema": skill.output_schema.model_json_schema(),
        "examples": [example.as_dict() for example in skill.meta.examples],
    }


@app.post("/api/skills/{skill_id}/run", tags=["skills"])
async def run_skill(skill_id: str, request: SkillRunRequest) -> dict[str, object]:
    """Execute a skill through the registry (audited to tool_calls.jsonl)."""

    from src.skills import SkillRegistry
    SkillRegistry.discover()
    try:
        return SkillRegistry.run(skill_id, request.payload, caller="api.manual")
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}") from error
    except Exception as error:  # noqa: BLE001 - 参数/执行错误统一 400，前端可读
        raise HTTPException(status_code=400, detail=f"{type(error).__name__}: {error}") from error


# ============================================================================
#  聊天查询 API — 第三条 LangGraph 图（classify → dispatch → compose）
# ============================================================================

class ChatRequest(BaseModel):
    """One chat turn."""

    question: str = Field(min_length=1, max_length=2000)
    thread_id: str | None = None


async def get_chat_agent():
    """Lazy singleton so the RAG index loads on first use, not server boot."""

    if app.state.chat_agent is None:
        from src.agents.chat_agent import ChatAgent
        app.state.chat_agent = ChatAgent(get_database())
    return app.state.chat_agent


@app.post("/api/chat", tags=["chat"])
async def chat(request: ChatRequest) -> dict[str, object]:
    """Answer a query question with deterministic tools + LLM composition."""

    agent = await get_chat_agent()
    return await agent.ask(request.question, request.thread_id)


@app.get("/api/chat/threads", tags=["chat"])
async def chat_threads(limit: int = Query(default=30, ge=1, le=100)) -> dict[str, object]:
    """List chat threads for the sidebar."""

    items = get_database().list_threads(limit)
    return {"items": items, "count": len(items)}


@app.get("/api/chat/threads/{thread_id}", tags=["chat"])
async def chat_thread_messages(thread_id: str) -> dict[str, object]:
    """Return one thread's full transcript."""

    thread = get_database().get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")
    return {**thread, "messages": get_database().list_messages(thread_id)}


# ============================================================================
#  记忆 API — 会话/长期记忆的查看与治理
# ============================================================================

class RememberRequest(BaseModel):
    """Write one long-term memory item."""

    kind: str = Field(pattern="^(machine|fact|preference|lesson)$")
    subject: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=2000)
    pinned: bool = False
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


@app.get("/api/memory", tags=["memory"])
async def list_memory(
    subject: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    query: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, object]:
    """Recall long-term memories (pinned first, then recency)."""

    from src.memory import MemoryManager
    manager = MemoryManager(get_database())
    items = manager.recall(subject=subject, kind=kind, query=query, limit=limit)
    return {"items": items, "count": len(items)}


@app.post("/api/memory", tags=["memory"])
async def write_memory(request: RememberRequest) -> dict[str, object]:
    """Persist one long-term memory item (deduped by content)."""

    from src.memory import MemoryManager
    manager = MemoryManager(get_database())
    memory_id = manager.remember(
        kind=request.kind, subject=request.subject, content=request.content,
        source="user", confidence=request.confidence, pinned=request.pinned,
    )
    return {"id": memory_id}


@app.delete("/api/memory/{memory_id}", tags=["memory"])
async def delete_memory(memory_id: str) -> dict[str, object]:
    """Forget one memory item (user correction path)."""

    from src.memory import MemoryManager
    manager = MemoryManager(get_database())
    if not manager.forget(memory_id):
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"deleted": memory_id}


@app.post("/api/memory/profile/{machine_id}/refresh", tags=["memory"])
async def refresh_machine_profile(machine_id: str) -> dict[str, object]:
    """Re-derive a machine's profile facts from diagnostics/maintenance history."""

    from src.memory import MemoryManager
    manager = MemoryManager(get_database())
    return {"machine_id": machine_id, "remembered": manager.refresh_machine_profile(machine_id)}


# ============================================================================
#  维护报告 API — 审批联动产物
# ============================================================================

@app.get("/api/reports", tags=["reports"])
async def list_reports(machine_id: str | None = Query(default=None), limit: int = Query(default=50, ge=1, le=200)) -> dict[str, object]:
    """List generated maintenance reports (newest first)."""

    items = get_database().list_reports(machine_id, limit)
    return {"items": items, "count": len(items)}


@app.get("/api/reports/{report_id}", tags=["reports"])
async def report_detail(report_id: str) -> dict[str, object]:
    """Return one full maintenance report including its markdown body."""

    item = get_database().get_report(report_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return item


def _webhook_payload(channel: str, report: dict[str, object]) -> dict[str, object]:
    """把报告渲染成各 IM 平台群机器人认识的报文格式。

    * 飞书（feishu）：text 消息，兼容"自定义机器人"最简配置；
    * 企业微信（wecom）：markdown 消息；
    * 钉钉（dingtalk）：markdown 消息（带标题）。
    """

    title = str(report.get("title") or "维护报告")
    summary = str(report.get("summary") or "")
    markdown = str(report.get("markdown") or "")
    body = markdown[:1800] + ("\n…（正文过长已截断，完整报告见系统）" if len(markdown) > 1800 else "")
    text = f"【{title}】审批通过，维护报告已生成\n\n{summary}\n\n{body}"
    if channel == "feishu":
        return {"msg_type": "text", "content": {"text": text}}
    if channel == "wecom":
        return {"msgtype": "markdown", "markdown": {"content": text}}
    if channel == "dingtalk":
        return {"msgtype": "markdown", "markdown": {"title": title, "text": text}}
    return {"text": text}


@app.post("/api/reports/{report_id}/push", tags=["reports"])
async def push_report(report_id: str, request: ReportPushRequest) -> dict[str, object]:
    """把维护报告推送到企业微信 / 飞书 / 钉钉群机器人。

    Webhook 地址优先取请求体，其次读 .env 的 ``REPORT_WEBHOOK_URL``；
    渠道取请求体，其次 ``REPORT_WEBHOOK_TYPE``，默认飞书。未配置时返回 400 并给出配置指引。
    """

    database = get_database()
    report = database.get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")

    url = (request.webhook_url or os.getenv("REPORT_WEBHOOK_URL") or "").strip()
    channel = (request.channel or os.getenv("REPORT_WEBHOOK_TYPE") or "feishu").strip().lower()
    if not url:
        raise HTTPException(
            status_code=400,
            detail="未配置推送地址：可在页面填入 Webhook URL，或在 .env 设置 REPORT_WEBHOOK_URL / REPORT_WEBHOOK_TYPE",
        )

    payload = _webhook_payload(channel, report)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
    except Exception as error:  # 网络异常要让前端看到明确原因，而不是 500 堆栈
        raise HTTPException(status_code=502, detail=f"推送失败：{error}") from error

    pushed = response.status_code < 300
    get_logger("api").info(json.dumps(
        {"action": "report_push", "report_id": report_id, "channel": channel,
         "status_code": response.status_code, "pushed": pushed}, ensure_ascii=False))
    return {
        "report_id": report_id,
        "channel": channel,
        "pushed": pushed,
        "status_code": response.status_code,
        "detail": "" if pushed else response.text[:300],
    }


# ============================================================================
#  数据层 API — 数据来源/血缘/实时引擎状态（回答"数据怎么来的"）
# ============================================================================

@app.get("/api/data/provenance", tags=["data"])
async def data_provenance(dataset: str | None = Query(default=None), limit: int = Query(default=50, ge=1, le=200)) -> dict[str, object]:
    """Explain where each dataset came from (lineage records)."""

    items = get_database().list_provenance(dataset, limit)
    return {"items": items, "count": len(items)}


@app.get("/api/data/realtime/status", tags=["data"])
async def realtime_status() -> dict[str, object]:
    """Expose the realtime engine state (running, step, ticks)."""

    engine = getattr(app.state, "realtime", None)
    status = engine.status if engine is not None else {"running": False, "reason": "REALTIME_ENABLED=0"}
    return status


@app.post("/api/data/realtime/tick", tags=["data"])
async def realtime_tick_manual() -> dict[str, object]:
    """Manually advance the simulator by one step (demo/debug path)."""

    from src.data import RealtimeEngine
    engine = RealtimeEngine(get_database(), tick_seconds=10**6)
    result = await engine.tick()
    return result
