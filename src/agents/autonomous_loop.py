"""Autonomous monitoring loop — the Agent's always-on heartbeat.

The loop scans every device at a fixed interval, decides whether diagnostic
analysis is required, drives the LangGraph pipeline when it is, and emits
structured Agent activity / notifications so a human auditor can see exactly
what the Agent did, when, and why. It is the difference between a "report
generator" and a real Agent that closes the loop without human prompting.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from src.config import SYSTEM_CONFIG
from src.database.connection import Database


# === Severity thresholds (centralized so they can be tuned in one place) ===

HEALTH_WARNING_THRESHOLD = 0.55   # below this: trigger diagnostic
HEALTH_CRITICAL_THRESHOLD = 0.45  # below this: also push to human review queue
FAULT_CODE_CRITICAL = 1           # fault_code 1 = bearing wear (most urgent)
RECENT_DIAG_COOLDOWN_MINUTES = 30  # don't re-diagnose a machine within 30 min
LOOP_INTERVAL_SECONDS = 30        # how often the loop wakes up


# === Activity log (kept in-process for SSE/WebSocket fan-out) ===

@dataclass
class ActivityEvent:
    """Lightweight event broadcast to WebSocket subscribers and persisted in DB."""

    id: int
    timestamp: str
    agent_name: str
    machine_id: str | None
    action: str
    severity: str
    reason: str
    details: dict[str, Any] = field(default_factory=dict)
    requires_human_review: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "agent_name": self.agent_name,
            "machine_id": self.machine_id,
            "action": self.action,
            "severity": self.severity,
            "reason": self.reason,
            "details": self.details,
            "requires_human_review": self.requires_human_review,
        }


class ActivityBus:
    """In-memory pub/sub for Agent activity. Subscribers (WebSocket) get every event."""

    def __init__(self, max_recent: int = 200) -> None:
        self._subscribers: list[asyncio.Queue[ActivityEvent]] = []
        self._recent: list[ActivityEvent] = []
        self._max_recent = max_recent

    def publish(self, event: ActivityEvent) -> None:
        self._recent.append(event)
        if len(self._recent) > self._max_recent:
            self._recent.pop(0)
        # Push to every subscriber queue (non-blocking).
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> asyncio.Queue[ActivityEvent]:
        queue: asyncio.Queue[ActivityEvent] = asyncio.Queue(maxsize=200)
        # Replay recent events to the new subscriber so the UI gets immediate history.
        for event in list(self._recent)[-20:]:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                break
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[ActivityEvent]) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def recent(self, limit: int = 50) -> list[ActivityEvent]:
        return list(self._recent[-limit:])


# === Diagnostic callback type ===

# The loop needs a way to ask the LangGraph graph to actually run a diagnostic.
# The FastAPI app injects this callable at startup so we don't import the graph here
# (avoids circular import + keeps the loop independently testable).
DiagnosticRunner = Callable[[str], Awaitable[dict[str, Any]]]


@dataclass
class AutonomousLoop:
    """Background coroutine that keeps the Agent self-driving."""

    database: Database
    bus: ActivityBus
    run_diagnostic: DiagnosticRunner
    interval_seconds: int = LOOP_INTERVAL_SECONDS
    warning_threshold: float = HEALTH_WARNING_THRESHOLD
    critical_threshold: float = HEALTH_CRITICAL_THRESHOLD
    cooldown_minutes: int = RECENT_DIAG_COOLDOWN_MINUTES
    _task: asyncio.Task[None] | None = None
    _stop_event: asyncio.Event | None = None
    last_scan_at: str | None = None
    last_summary: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ start/stop

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="autonomous-loop")
        self._log(
            agent_name="MonitorAgent",
            action="loop_started",
            reason=f"自主监控循环已启动，扫描间隔 {self.interval_seconds}s，警告阈值 {self.warning_threshold}，紧急阈值 {self.critical_threshold}",
            severity="info",
        )

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()
        self._task = None
        self._log(
            agent_name="MonitorAgent",
            action="loop_stopped",
            reason="自主监控循环已停止",
            severity="info",
        )

    # --------------------------------------------------------------- public helpers

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ------------------------------------------------------------------ main loop

    async def _run(self) -> None:
        assert self._stop_event is not None
        # First scan happens almost immediately so the UI sees activity fast.
        await asyncio.sleep(2.0)
        while not self._stop_event.is_set():
            try:
                await self._scan_once()
            except Exception as exc:  # pragma: no cover - defensive
                self._log(
                    agent_name="MonitorAgent",
                    action="scan_error",
                    reason=f"扫描循环异常: {type(exc).__name__}: {exc}",
                    severity="error",
                    details={"exception": repr(exc)},
                )
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue

    async def trigger_scan(self) -> dict[str, Any]:
        """Public entry-point so an admin button can force an immediate scan."""

        return await self._scan_once()

    async def _scan_once(self) -> dict[str, Any]:
        started_at = datetime.now().astimezone()
        machines = self.database.list_machines()
        scanned = 0
        healthy = 0
        warning = 0
        critical = 0
        offline = 0
        triggered: list[str] = []
        pending_human_review: list[str] = []

        for machine in machines:
            scanned += 1
            machine_id = machine.get("id")
            latest = machine.get("latest_reading") or {}
            health = latest.get("health_score") if isinstance(latest, dict) else None
            fault_code = latest.get("fault_code") if isinstance(latest, dict) else None

            # No reading yet — count as offline.
            if not latest or health is None:
                offline += 1
                continue

            health_value = float(health)
            fault_value = int(fault_code or 0)

            if health_value < self.critical_threshold or fault_value == FAULT_CODE_CRITICAL:
                critical += 1
            elif health_value < self.warning_threshold:
                warning += 1
            else:
                healthy += 1

            if health_value < self.warning_threshold and self._should_diagnose(machine_id):
                triggered.append(machine_id)
                await self._auto_diagnose(machine_id, health_value, fault_value)
                if health_value < self.critical_threshold:
                    pending_human_review.append(machine_id)

        summary = {
            "scanned": scanned,
            "healthy": healthy,
            "warning": warning,
            "critical": critical,
            "offline": offline,
            "auto_triggered": triggered,
            "pending_human_review": pending_human_review,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now().astimezone().isoformat(),
            "duration_ms": int((datetime.now().astimezone() - started_at).total_seconds() * 1000),
        }
        self.last_scan_at = summary["finished_at"]
        self.last_summary = summary

        self._log(
            agent_name="MonitorAgent",
            action="scan_completed",
            reason=f"扫描 {scanned} 台设备 — 健康 {healthy} / 警告 {warning} / 紧急 {critical} / 无数据 {offline}，自动触发 {len(triggered)} 次诊断",
            severity="critical" if critical > 0 else ("warning" if warning > 0 else "info"),
            details=summary,
        )
        return summary

    # ---------------------------------------------------------- decision helpers

    def _should_diagnose(self, machine_id: str | None) -> bool:
        if not machine_id:
            return False
        cooldown_cutoff = (datetime.now().astimezone() - timedelta(minutes=self.cooldown_minutes)).isoformat()
        recent = self.database.list_diagnostics(machine_id=machine_id, limit=1)
        if not recent:
            return True
        return recent[0]["created_at"] < cooldown_cutoff

    async def _auto_diagnose(self, machine_id: str, health: float, fault_code: int) -> None:
        self._log(
            agent_name="MonitorAgent",
            action="auto_trigger_diagnostic",
            machine_id=machine_id,
            reason=f"health_score={health:.3f} 低于阈值 {self.warning_threshold}，自动触发 7 个 Agent 诊断流程",
            severity="warning" if health >= self.critical_threshold else "critical",
            details={"health_score": health, "fault_code": fault_code},
            requires_human_review=health < self.critical_threshold,
        )
        try:
            result = await self.run_diagnostic(machine_id)
            severity = result.get("severity", "warning")
            fault_name = result.get("fault_mode") or result.get("fault_name") or "未识别故障"
            _rul_dict = result.get("rul") if isinstance(result.get("rul"), dict) else {}
            rul = _rul_dict.get("rul_p50") or _rul_dict.get("p50") or result.get("rul_p50") or result.get("rul_hours")
            diagnostic_id = result.get("diagnostic_id")

            # Always create a maintenance order for medium+ severity.
            if severity in ("warning", "critical", "emergency"):
                order = self._create_auto_order(machine_id, diagnostic_id, severity, fault_name)
                self._log(
                    agent_name="DecisionAgent",
                    action="auto_create_order",
                    machine_id=machine_id,
                    reason=f"诊断完成（{severity} / {fault_name}），Agent 已自动创建维护工单 {order['id']}",
                    severity=severity if severity != "warning" else "warning",
                    details={"order_id": order["id"], "maintenance_type": order["maintenance_type"], "estimated_cost": order["parts_cost"] + order["labor_cost"]},
                    requires_human_review=severity in ("critical", "emergency"),
                )

            # Critical/emergency: also create a notification and log to the human review queue.
            if severity in ("critical", "emergency"):
                title = f"{machine_id} {fault_name} 紧急"
                message = f"Agent 检测到 {machine_id} 存在 {severity} 级风险（{fault_name}），RUL 约 {rul} 小时。工单已自动创建，请审核。"
                notif_id = self.database.create_notification(
                    machine_id=machine_id,
                    severity=severity,
                    title=title,
                    message=message,
                    related_diagnostic_id=diagnostic_id,
                )
                self._log(
                    agent_name="DecisionAgent",
                    action="notify_human",
                    machine_id=machine_id,
                    reason=f"已生成待审通知 {notif_id}：{title}",
                    severity="critical",
                    details={"notification_id": notif_id, "rul_hours": rul},
                    requires_human_review=True,
                )

            self._log(
                agent_name="ReportAgent",
                action="report_generated",
                machine_id=machine_id,
                reason=f"诊断报告已生成 — 故障 {fault_name}，置信度 {result.get('confidence', 0):.2f}，RUL {rul} 小时",
                severity="info",
                details={"diagnostic_id": diagnostic_id, "confidence": result.get("confidence")},
            )
        except Exception as exc:  # pragma: no cover - defensive
            self._log(
                agent_name="MonitorAgent",
                action="auto_diagnostic_failed",
                machine_id=machine_id,
                reason=f"自动诊断失败: {type(exc).__name__}: {exc}",
                severity="error",
                details={"exception": repr(exc)},
                requires_human_review=True,
            )

    def _create_auto_order(self, machine_id: str, diagnostic_id: str | None, severity: str, fault_name: str) -> dict[str, Any]:
        order_id = f"WO-{datetime.now().astimezone().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        maintenance_type = "preventive" if severity == "warning" else "corrective"
        parts_cost = 1500.0 if severity == "warning" else 4500.0
        labor_cost = 800.0 if severity == "warning" else 2200.0
        notes = f"[Agent 自动派单] 故障: {fault_name} | 诊断ID: {diagnostic_id or 'N/A'} | 严重度: {severity}"
        self.database.create_maintenance_order({
            "id": order_id,
            "machine_id": machine_id,
            "diagnostic_id": diagnostic_id,
            "maintenance_type": maintenance_type,
            "start_time": datetime.now().astimezone().isoformat(),
            "end_time": None,
            "downtime_hours": None,
            "parts_cost": parts_cost,
            "labor_cost": labor_cost,
            "parts_json": json.dumps([fault_name], ensure_ascii=False),
            "technician": "Agent-待分配",
            "notes": notes,
            "status": "pending" if severity in ("critical", "emergency") else "auto_scheduled",
        })
        return {
            "id": order_id,
            "maintenance_type": maintenance_type,
            "parts_cost": parts_cost,
            "labor_cost": labor_cost,
        }

    # --------------------------------------------------------------- activity sink

    def _log(
        self,
        *,
        agent_name: str,
        action: str,
        reason: str,
        severity: str = "info",
        machine_id: str | None = None,
        details: dict[str, Any] | None = None,
        requires_human_review: bool = False,
    ) -> ActivityEvent:
        activity_id = self.database.log_agent_activity(
            agent_name=agent_name,
            action=action,
            reason=reason,
            machine_id=machine_id,
            severity=severity,
            details=details or {},
            requires_human_review=requires_human_review,
        )
        event = ActivityEvent(
            id=activity_id,
            timestamp=datetime.now().astimezone().isoformat(),
            agent_name=agent_name,
            machine_id=machine_id,
            action=action,
            severity=severity,
            reason=reason,
            details=details or {},
            requires_human_review=requires_human_review,
        )
        self.bus.publish(event)
        return event
