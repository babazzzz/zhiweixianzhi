"""Structured logging with trace propagation for the whole 智维先知 backend.

日志设计（三份落盘文件，均在 ``data/logs/``）：

===================  ==========================================================
``app.log``          主日志。TSV 前缀（时间/级别/组件/trace_id）+ JSON 正文，按 5MB 轮转
``llm_audit.jsonl``  每次大模型调用：provider / model / tokens / 耗时 / 入参出参摘要
``tool_calls.jsonl`` 每次 Skill / Tool 调用：调用方 / skill_id / 入参 / 出参 / 耗时
===================  ==========================================================

三份文件通过 ``trace_id`` 串联：一次 HTTP 请求、一次自动诊断、一次聊天问答都会
在入口处生成（或透传）同一个 trace_id，于是「谁在什么时候调用了什么」可以在
日志查看页按 trace_id 一查到底。

与 ``agent_activity`` 表的分工：表存**业务事件**（人读：Agent 做了什么决策），
日志存**技术调用链**（机读：调了哪个模型、传了什么、耗时多少），两者靠 trace_id 互相关联。
"""

from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from src.config import LOG_DIR

LOG_DIR.mkdir(parents=True, exist_ok=True)

APP_LOG = LOG_DIR / "app.log"
LLM_AUDIT_LOG = LOG_DIR / "llm_audit.jsonl"
TOOL_CALL_LOG = LOG_DIR / "tool_calls.jsonl"

MAX_BYTES = 5_000_000
BACKUP_COUNT = 5

#: Trace id for the current execution context (one HTTP request / one diagnostic run).
trace_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")


def new_trace_id() -> str:
    """Return a short, collision-resistant trace identifier."""

    return uuid.uuid4().hex[:12]


class TraceFilter(logging.Filter):
    """Attach the active trace_id to every record so the formatter can print it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = trace_id_ctx.get()  # type: ignore[attr-defined]
        return True


class JSONLineWriter:
    """Append-only JSONL sink guarded by a lock — safe for concurrent coroutines."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def write(self, payload: dict[str, Any]) -> None:
        """Append one record, stamping timestamp and trace_id if absent."""

        record = {"ts": datetime.now().astimezone().isoformat(), "trace_id": trace_id_ctx.get(), **payload}
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(f"{line}\n")

    def read_tail(self, limit: int = 200) -> list[dict[str, Any]]:
        """Return the most recent records (newest first) for the log console."""

        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as handle:
            lines = handle.readlines()[-limit:]
        records: list[dict[str, Any]] = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records


_llm_audit_writer = JSONLineWriter(LLM_AUDIT_LOG)
_tool_call_writer = JSONLineWriter(TOOL_CALL_LOG)


def get_logger(component: str) -> logging.Logger:
    """Return a namespaced logger writing to the rotating ``app.log``.

    同一个组件重复调用只会返回同一个 logger（handler 幂等），避免重复落盘。
    """

    logger = logging.getLogger(f"zhiwei.{component}")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False

    handler = RotatingFileHandler(APP_LOG, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s\t%(levelname)s\t%(name)s\t%(trace_id)s\t%(message)s"))
    handler.addFilter(TraceFilter())
    logger.addHandler(handler)
    return logger


def log_event(component: str, action: str, level: int = logging.INFO, **fields: Any) -> None:
    """Write one structured event: a stable ``action`` name plus arbitrary JSON fields."""

    get_logger(component).log(level, json.dumps({"action": action, **fields}, ensure_ascii=False, default=str))


@contextmanager
def traced(component: str, action: str, **fields: Any) -> Iterator[None]:
    """Context manager that binds a trace_id and logs one call with its latency.

    Nested usage is supported: the inner block reuses the outer trace_id, so a whole
    request chain collapses into one id.

    Example::

        with traced("chat", "classify_intent", thread_id=thread_id):
            intent = await llm.chat(...)
    """

    token = None
    if trace_id_ctx.get() == "-":
        token = trace_id_ctx.set(new_trace_id())
    started = time.perf_counter()
    error: BaseException | None = None
    try:
        yield
    except BaseException as exc:  # noqa: BLE001 - re-raised below after logging
        error = exc
        raise
    finally:
        payload: dict[str, Any] = {
            **fields,
            "status": "error" if error else "ok",
            "ms": round((time.perf_counter() - started) * 1000, 1),
        }
        if error is not None:
            payload["error"] = f"{type(error).__name__}: {error}"
        log_event(component, action, logging.ERROR if error else logging.INFO, **payload)
        if token is not None:
            trace_id_ctx.reset(token)


def llm_audit(
    *,
    agent: str,
    provider: str,
    model: str,
    latency_ms: int,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    ok: bool = True,
    prompt_preview: str = "",
    output_preview: str = "",
    error: str = "",
) -> None:
    """Record one LLM invocation for cost/latency auditing and debugging."""

    _llm_audit_writer.write({
        "agent": agent, "provider": provider, "model": model,
        "latency_ms": latency_ms, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens, "ok": ok,
        "prompt_preview": prompt_preview[:400], "output_preview": output_preview[:400], "error": error,
    })


def tool_call(
    *,
    caller: str,
    tool: str,
    args: dict[str, Any],
    latency_ms: float,
    ok: bool = True,
    deterministic: bool = True,
    output_preview: str = "",
    error: str = "",
) -> None:
    """Record one Skill / Tool invocation — the audit trail behind every Agent action."""

    _tool_call_writer.write({
        "caller": caller, "tool": tool, "args": args, "latency_ms": round(latency_ms, 1),
        "ok": ok, "deterministic": deterministic, "output_preview": output_preview[:800], "error": error,
    })


def read_app_logs(
    *,
    limit: int = 200,
    level: str | None = None,
    component: str | None = None,
    trace_id: str | None = None,
    since: str | None = None,
) -> list[dict[str, Any]]:
    """Parse ``app.log`` into structured dicts, newest first, with optional filters.

    行格式：``时间\\t级别\\t组件\\ttrace_id\\tJSON正文``
    """

    if not APP_LOG.exists():
        return []
    with APP_LOG.open("r", encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()

    records: list[dict[str, Any]] = []
    for line in reversed(lines):
        parts = line.rstrip("\n").split("\t", 4)
        if len(parts) != 5:
            continue
        timestamp, level_name, comp, tid, body = parts
        if level and level_name.upper() != level.upper():
            continue
        if component and component not in comp:
            continue
        if trace_id and trace_id != tid:
            continue
        if since and timestamp < since:
            continue
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"raw": body}
        records.append({
            "ts": timestamp, "level": level_name, "component": comp.removeprefix("zhiwei."),
            "trace_id": tid, **payload,
        })
        if len(records) >= limit:
            break
    return records


def read_jsonl(name: str, limit: int = 200, trace_id: str | None = None) -> list[dict[str, Any]]:
    """Read ``llm_audit`` or ``tool_calls`` records, newest first."""

    writer = {"llm_audit": _llm_audit_writer, "tool_calls": _tool_call_writer}.get(name)
    if writer is None:
        return []
    records = writer.read_tail(limit=limit * 4)
    if trace_id:
        records = [item for item in records if item.get("trace_id") == trace_id]
    return records[:limit]


def log_stats() -> dict[str, Any]:
    """Return log file sizes and record counts — shown on the log console header."""

    def _count(path: Path) -> int:
        if not path.exists():
            return 0
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return sum(1 for _ in handle)

    return {
        "log_dir": str(LOG_DIR),
        "files": [
            {"name": path.name, "size_bytes": path.stat().st_size if path.exists() else 0, "lines": _count(path)}
            for path in (APP_LOG, LLM_AUDIT_LOG, TOOL_CALL_LOG)
        ],
    }
