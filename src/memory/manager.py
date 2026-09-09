"""MemoryManager：记忆模块唯一门面。

用法（聊天 Agent 内）::

    memory = MemoryManager()
    # 1) 会话记录
    thread_id = memory.open_thread("设备查询")
    memory.append_message(thread_id, "user", "B2012 主轴温度为什么高？")
    history = memory.recent_history(thread_id, turns=6)
    # 2) 注入长期记忆
    context = memory.build_context(subject="B2012")
    # 3) 沉淀新事实（去重，重复写入只提升置信度）
    memory.remember(kind="machine", subject="B2012",
                    content="主轴轴承 2025-07 已更换，更换后振动显著下降")
"""

from __future__ import annotations

import logging
from typing import Any

from src.database.connection import Database

LOGGER = logging.getLogger("zhiwei.memory")

#: 长期记忆允许的类型，写入前校验，防止随手乱塞导致记忆混乱
MEMORY_KINDS = ("machine", "fact", "preference", "lesson")


class MemoryManager:
    """Facade over session storage and long-term memory tables."""

    def __init__(self, database: Database | None = None) -> None:
        self._db = database or Database()

    # --- 1) 会话记忆 -------------------------------------------------

    def open_thread(self, title: str = "新会话") -> str:
        """Create (or reuse by title) a chat thread and return its id."""

        for thread in self._db.list_threads(limit=50):
            if thread["title"] == title:
                return str(thread["id"])
        return self._db.create_thread(title)

    def append_message(self, thread_id: str, role: str, content: str, *, intent: str | None = None,
                       tools: list[dict[str, Any]] | None = None,
                       citations: list[dict[str, Any]] | None = None) -> int:
        """Record one turn; the DB layer auto-rolls a summary every 12 messages."""

        return self._db.add_message(thread_id, role, content, intent=intent, tools=tools, citations=citations)

    def recent_history(self, thread_id: str, turns: int = 8) -> list[dict[str, Any]]:
        """Return the last ``turns`` messages for prompt assembly (oldest first)."""

        messages = self._db.list_messages(thread_id, limit=200)
        return messages[-turns:]

    def thread_summary(self, thread_id: str) -> str:
        """Rolling summary of the thread (may be empty early on)."""

        thread = self._db.get_thread(thread_id)
        return str((thread or {}).get("summary") or "")

    # --- 2) 长期记忆 -------------------------------------------------

    def remember(self, *, kind: str, subject: str, content: str, source: str = "agent",
                 confidence: float = 0.8, pinned: bool = False,
                 meta: dict[str, Any] | None = None) -> str:
        """Persist one long-term fact with kind validation and dedup."""

        if kind not in MEMORY_KINDS:
            raise ValueError(f"未知记忆类型 {kind!r}，允许：{MEMORY_KINDS}")
        content = content.strip()
        if not content:
            raise ValueError("记忆内容不能为空")
        return self._db.remember(kind=kind, subject=subject.strip() or "global",
                                 content=content, source=source, confidence=confidence,
                                 pinned=pinned, meta=meta)

    def recall(self, *, subject: str | None = None, kind: str | None = None,
               query: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        """Fetch memories for injection, pinned first then recency."""

        return self._db.recall(subject=subject, kind=kind, query=query, limit=limit)

    def forget(self, memory_id: str) -> bool:
        """Delete one memory item (user-driven correction path)."""

        return self._db.forget_memory(memory_id)

    # --- 3) 上下文组装（供聊天/诊断 prompt 使用） ---------------------

    def build_context(self, *, subject: str | None = None, query: str | None = None,
                      max_items: int = 6) -> str:
        """Assemble a compact memory block to prepend to a prompt.

        策略：置顶记忆优先，其次按 subject 匹配与 query 关键词，最多
        ``max_items`` 条，每条截断 120 字，避免挤占上下文窗口。
        """

        items = self.recall(subject=subject, limit=max_items) if subject else []
        if query:
            seen = {item["id"] for item in items}
            for item in self.recall(query=query, limit=max_items):
                if item["id"] not in seen:
                    items.append(item)
        items = items[:max_items]
        if not items:
            return ""
        lines = []
        for item in items:
            self._db.touch_memory(str(item["id"]))
            kind_label = {"machine": "设备事实", "fact": "事实", "preference": "偏好", "lesson": "经验"}.get(item["kind"], item["kind"])
            prefix = "★" if item["pinned"] else "·"
            lines.append(f"{prefix} [{kind_label}|{item['subject']}] {str(item['content'])[:120]}")
        return "\n".join(lines)

    # --- 4) 设备画像自动沉淀（情节 → 画像） ---------------------------

    def refresh_machine_profile(self, machine_id: str) -> list[str]:
        """Derive durable machine facts from diagnostics/maintenance history.

        把"发生过的事"压缩成"这台设备的稳定事实"，是情节记忆向画像的
        沉淀路径，避免两条链路各记各的。
        """

        with self._db.transaction() as connection:
            diag = connection.execute(
                """SELECT severity, COUNT(*) AS hits FROM diagnostics
                   WHERE machine_id=? GROUP BY severity ORDER BY hits DESC""",
                (machine_id,),
            ).fetchall()
            maint = connection.execute(
                """SELECT COUNT(*) AS repairs, MAX(start_time) AS last_repair
                   FROM maintenance_records WHERE machine_id=?""",
                (machine_id,),
            ).fetchone()

        remembered: list[str] = []
        if diag:
            top = "、".join(f"{row['severity']}×{row['hits']}" for row in diag)
            remembered.append(self.remember(
                kind="machine", subject=machine_id,
                content=f"历史诊断分布：{top}（截至最近统计）",
                source="profile_sync", confidence=0.7,
            ))
        if maint and maint["repairs"]:
            remembered.append(self.remember(
                kind="machine", subject=machine_id,
                content=f"累计维修 {maint['repairs']} 次，最近一次 {str(maint['last_repair'])[:10]}",
                source="profile_sync", confidence=0.9,
            ))
        return remembered
