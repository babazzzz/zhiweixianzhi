"""Small transactional repository over SQLite with a portable schema boundary."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Iterable, Mapping, Sequence

from src.config import DATABASE_URL, PROJECT_ROOT
from src.database.schemas import MachineMetadata, SensorReading


class Database:
    """Own SQLite lifecycle and expose parameterized domain queries."""

    def __init__(self, database_url: str = DATABASE_URL) -> None:
        self._path = self._resolve_path(database_url)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # 全部建表语句均为 IF NOT EXISTS，启动时自动补齐新增表（幂等）
        self.initialize_schema()

    @staticmethod
    def _resolve_path(database_url: str) -> Path:
        """Resolve SQLite URLs while rejecting unsupported database drivers."""

        prefix = "sqlite:///"
        if not database_url.startswith(prefix):
            raise ValueError("This local distribution supports sqlite:/// URLs only")
        value = database_url.removeprefix(prefix)
        path = Path(value)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Yield a connection that commits on success and rolls back on failure."""

        connection = sqlite3.connect(self._path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize_schema(self) -> None:
        """Create all required tables and indexes idempotently."""

        statements = (
            """CREATE TABLE IF NOT EXISTS machines (
                id TEXT PRIMARY KEY, model TEXT NOT NULL, manufacture_date TEXT NOT NULL,
                last_maintenance TEXT, next_maintenance TEXT, last_repair TEXT,
                daily_output INTEGER NOT NULL, unit_cost REAL NOT NULL, production_line TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""",
            """CREATE TABLE IF NOT EXISTS sensor_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT, machine_id TEXT NOT NULL REFERENCES machines(id),
                timestamp TEXT NOT NULL, metrics_json TEXT NOT NULL, fault_code INTEGER NOT NULL DEFAULT 0,
                health_score REAL, UNIQUE(machine_id, timestamp)
            )""",
            "CREATE INDEX IF NOT EXISTS idx_readings_machine_time ON sensor_readings(machine_id, timestamp DESC)",
            """CREATE TABLE IF NOT EXISTS fmeca_rules (
                fault_code INTEGER PRIMARY KEY, fault_name TEXT NOT NULL, system_name TEXT NOT NULL,
                component TEXT NOT NULL, evidence_signals_json TEXT NOT NULL, physical_mechanism TEXT NOT NULL,
                detection_method TEXT NOT NULL, sop_steps_json TEXT NOT NULL, required_parts_json TEXT NOT NULL,
                estimated_hours REAL NOT NULL, engineer_level TEXT NOT NULL, severity TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS case_library (
                id TEXT PRIMARY KEY, machine_id TEXT NOT NULL, fault_code INTEGER NOT NULL,
                description TEXT NOT NULL, param_changes_json TEXT NOT NULL, root_cause TEXT NOT NULL,
                resolution TEXT NOT NULL, rul_hours INTEGER, risk_path TEXT NOT NULL, created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS diagnostics (
                id TEXT PRIMARY KEY, machine_id TEXT NOT NULL REFERENCES machines(id), created_at TEXT NOT NULL,
                status TEXT NOT NULL, severity TEXT NOT NULL, confidence REAL NOT NULL, payload_json TEXT NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_diagnostics_machine_time ON diagnostics(machine_id, created_at DESC)",
            """CREATE TABLE IF NOT EXISTS maintenance_records (
                id TEXT PRIMARY KEY, machine_id TEXT NOT NULL REFERENCES machines(id), diagnostic_id TEXT,
                maintenance_type TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT, downtime_hours REAL,
                parts_cost REAL NOT NULL, labor_cost REAL NOT NULL, parts_json TEXT NOT NULL,
                technician TEXT NOT NULL, notes TEXT NOT NULL, status TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS agent_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                machine_id TEXT,
                action TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'info',
                reason TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                requires_human_review INTEGER NOT NULL DEFAULT 0
            )""",
            "CREATE INDEX IF NOT EXISTS idx_agent_activity_time ON agent_activity(timestamp DESC)",
            "CREATE INDEX IF NOT EXISTS idx_agent_activity_machine ON agent_activity(machine_id, timestamp DESC)",
            """CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                machine_id TEXT,
                severity TEXT NOT NULL,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                reviewed_at TEXT,
                reviewer TEXT,
                review_notes TEXT,
                related_activity_id INTEGER,
                related_diagnostic_id TEXT,
                related_order_id TEXT
            )""",
            "CREATE INDEX IF NOT EXISTS idx_notifications_status_time ON notifications(status, created_at DESC)",
            # === 对话记忆（短期）：一次会话内的多轮问答 ===
            """CREATE TABLE IF NOT EXISTS conversation_threads (
                id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '新会话',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0, summary TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS conversation_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL REFERENCES conversation_threads(id),
                role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,
                intent TEXT, tools_json TEXT NOT NULL DEFAULT '[]', citations_json TEXT NOT NULL DEFAULT '[]'
            )""",
            "CREATE INDEX IF NOT EXISTS idx_conv_messages_thread ON conversation_messages(thread_id, id)",
            # === 长期记忆：跨会话沉淀的事实 / 偏好 / 经验 ===
            """CREATE TABLE IF NOT EXISTS long_term_memory (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, subject TEXT NOT NULL,
                content TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'agent',
                confidence REAL NOT NULL DEFAULT 0.8, pinned INTEGER NOT NULL DEFAULT 0,
                access_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_accessed_at TEXT,
                meta_json TEXT NOT NULL DEFAULT '{}'
            )""",
            "CREATE INDEX IF NOT EXISTS idx_ltm_kind_subject ON long_term_memory(kind, subject)",
            "CREATE INDEX IF NOT EXISTS idx_ltm_updated ON long_term_memory(updated_at DESC)",
            # === 维护报告：审批通过后由 maintenance_report skill 生成 ===
            """CREATE TABLE IF NOT EXISTS maintenance_reports (
                id TEXT PRIMARY KEY, machine_id TEXT NOT NULL, diagnostic_id TEXT,
                notification_id TEXT, order_id TEXT, title TEXT NOT NULL,
                summary TEXT NOT NULL, markdown TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'generated',
                generated_at TEXT NOT NULL, generated_by TEXT NOT NULL DEFAULT 'maintenance_report',
                approved_by TEXT, meta_json TEXT NOT NULL DEFAULT '{}'
            )""",
            "CREATE INDEX IF NOT EXISTS idx_maint_reports_machine ON maintenance_reports(machine_id, generated_at DESC)",
            # === 数据血缘：记录每一批数据的来源，回答"这些数据是怎么来的" ===
            """CREATE TABLE IF NOT EXISTS data_provenance (
                id TEXT PRIMARY KEY, dataset TEXT NOT NULL, source_type TEXT NOT NULL,
                source_detail TEXT NOT NULL, row_count INTEGER NOT NULL DEFAULT 0,
                seed INTEGER, generated_at TEXT NOT NULL, notes TEXT NOT NULL DEFAULT ''
            )""",
        )
        with self.transaction() as connection:
            for statement in statements:
                connection.execute(statement)

    def count(self, table: str) -> int:
        """Return a row count for a trusted internal table name."""

        allowed = {"machines", "sensor_readings", "fmeca_rules", "case_library", "diagnostics", "maintenance_records", "agent_activity", "notifications"}
        if table not in allowed:
            raise ValueError("Unsupported table")
        with self.transaction() as connection:
            row = connection.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()
        return int(row["total"] if row else 0)

    def upsert_machine(self, machine: MachineMetadata) -> None:
        """Insert or update a machine while retaining its stable identifier."""

        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO machines (id,model,manufacture_date,last_maintenance,next_maintenance,last_repair,daily_output,unit_cost,production_line,status)
                VALUES (:id,:model,:manufacture_date,:last_maintenance,:next_maintenance,:last_repair,:daily_output,:unit_cost,:production_line,:status)
                ON CONFLICT(id) DO UPDATE SET model=excluded.model, manufacture_date=excluded.manufacture_date,
                last_maintenance=excluded.last_maintenance, next_maintenance=excluded.next_maintenance,
                last_repair=excluded.last_repair, daily_output=excluded.daily_output, unit_cost=excluded.unit_cost,
                production_line=excluded.production_line, status=excluded.status""",
                machine.__dict__ if hasattr(machine, "__dict__") else {field: getattr(machine, field) for field in machine.__dataclass_fields__},
            )

    def insert_readings(self, readings: Iterable[SensorReading]) -> int:
        """Insert timestamped readings in a single transaction, ignoring duplicates."""

        rows = [(item.machine_id, item.timestamp.isoformat(), json.dumps(item.metrics, ensure_ascii=False), item.fault_code, item.health_score) for item in readings]
        if not rows:
            return 0
        with self.transaction() as connection:
            connection.executemany("INSERT OR IGNORE INTO sensor_readings (machine_id,timestamp,metrics_json,fault_code,health_score) VALUES (?,?,?,?,?)", rows)
            return int(connection.execute("SELECT changes() AS changes").fetchone()["changes"])

    def list_machines(self, status: str | None = None) -> list[dict[str, object]]:
        """List machines with their latest reading and last diagnostic summary."""

        where = "WHERE m.status = ?" if status else ""
        params: tuple[str, ...] = (status,) if status else ()
        query = f"""SELECT m.*, r.timestamp AS latest_timestamp, r.metrics_json, r.health_score, r.fault_code,
            d.id AS diagnostic_id, d.severity AS diagnostic_severity, d.confidence AS diagnostic_confidence
            FROM machines m
            LEFT JOIN sensor_readings r ON r.id = (SELECT id FROM sensor_readings WHERE machine_id=m.id ORDER BY timestamp DESC LIMIT 1)
            LEFT JOIN diagnostics d ON d.id = (SELECT id FROM diagnostics WHERE machine_id=m.id ORDER BY created_at DESC LIMIT 1)
            {where} ORDER BY m.id"""
        with self.transaction() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._machine_row(row) for row in rows]

    def get_machine(self, machine_id: str) -> dict[str, object] | None:
        """Fetch one machine and its current data summary."""

        rows = self.list_machines()
        return next((row for row in rows if row["id"] == machine_id), None)

    def get_readings(self, machine_id: str, limit: int = 240, before: str | None = None) -> list[dict[str, object]]:
        """Return an ascending timestamp window for charts and diagnostic skills."""

        bounded = min(max(limit, 1), 5000)
        predicate = "machine_id = ?" if before is None else "machine_id = ? AND timestamp < ?"
        params: Sequence[object] = (machine_id, bounded) if before is None else (machine_id, before, bounded)
        with self.transaction() as connection:
            rows = connection.execute(f"SELECT timestamp,metrics_json,fault_code,health_score FROM sensor_readings WHERE {predicate} ORDER BY timestamp DESC LIMIT ?", params).fetchall()
        return [{"timestamp": row["timestamp"], "metrics": json.loads(row["metrics_json"]), "fault_code": int(row["fault_code"]), "health_score": row["health_score"]} for row in reversed(rows)]

    def latest_reading(self, machine_id: str) -> dict[str, object] | None:
        """Return the most recent known sample for a machine."""

        rows = self.get_readings(machine_id, limit=1)
        return rows[-1] if rows else None

    def save_diagnostic(self, diagnostic_id: str, machine_id: str, status: str, severity: str, confidence: float, payload: Mapping[str, object]) -> None:
        """Persist a complete diagnostic snapshot and make it reusable as episodic memory."""

        with self.transaction() as connection:
            connection.execute("INSERT INTO diagnostics (id,machine_id,created_at,status,severity,confidence,payload_json) VALUES (?,?,?,?,?,?,?)", (diagnostic_id, machine_id, datetime.now().astimezone().isoformat(), status, severity, confidence, json.dumps(payload, ensure_ascii=False, default=str)))

    def list_diagnostics(self, machine_id: str | None = None, limit: int = 50) -> list[dict[str, object]]:
        """Return diagnostic history ordered by newest first."""

        bounded = min(max(limit, 1), 200)
        where = "WHERE machine_id = ?" if machine_id else ""
        params: Sequence[object] = (machine_id, bounded) if machine_id else (bounded,)
        with self.transaction() as connection:
            rows = connection.execute(f"SELECT * FROM diagnostics {where} ORDER BY created_at DESC LIMIT ?", params).fetchall()
        return [{"id": row["id"], "machine_id": row["machine_id"], "created_at": row["created_at"], "status": row["status"], "severity": row["severity"], "confidence": row["confidence"], "payload": json.loads(row["payload_json"])} for row in rows]

    def get_diagnostic(self, diagnostic_id: str) -> dict[str, object] | None:
        """Return one persisted diagnostic by its public UUID."""

        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM diagnostics WHERE id = ?", (diagnostic_id,)).fetchone()
        if row is None:
            return None
        return {"id": row["id"], "machine_id": row["machine_id"], "created_at": row["created_at"], "status": row["status"], "severity": row["severity"], "confidence": row["confidence"], "payload": json.loads(row["payload_json"])}

    def upsert_fmeca(self, rule: Mapping[str, object]) -> None:
        """Upsert one structured FMECA rule."""

        values = {key: json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value for key, value in rule.items()}
        with self.transaction() as connection:
            connection.execute("""INSERT INTO fmeca_rules VALUES (:fault_code,:fault_name,:system_name,:component,:evidence_signals_json,:physical_mechanism,:detection_method,:sop_steps_json,:required_parts_json,:estimated_hours,:engineer_level,:severity)
            ON CONFLICT(fault_code) DO UPDATE SET fault_name=excluded.fault_name,system_name=excluded.system_name,component=excluded.component,evidence_signals_json=excluded.evidence_signals_json,physical_mechanism=excluded.physical_mechanism,detection_method=excluded.detection_method,sop_steps_json=excluded.sop_steps_json,required_parts_json=excluded.required_parts_json,estimated_hours=excluded.estimated_hours,engineer_level=excluded.engineer_level,severity=excluded.severity""", values)

    def get_fmeca(self, fault_code: int) -> dict[str, object] | None:
        """Return one FMECA rule converted from JSON columns."""

        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM fmeca_rules WHERE fault_code = ?", (fault_code,)).fetchone()
        return self._fmeca_row(row) if row else None

    def list_fmeca(self) -> list[dict[str, object]]:
        """Return all structured failure modes ordered by severity code."""

        with self.transaction() as connection:
            rows = connection.execute("SELECT * FROM fmeca_rules ORDER BY fault_code").fetchall()
        return [self._fmeca_row(row) for row in rows]

    def add_case(self, case: Mapping[str, object]) -> None:
        """Insert a curated historical case if its ID is new."""

        payload = dict(case)
        payload["param_changes_json"] = json.dumps(payload.pop("param_changes"), ensure_ascii=False)
        with self.transaction() as connection:
            connection.execute("""INSERT OR IGNORE INTO case_library (id,machine_id,fault_code,description,param_changes_json,root_cause,resolution,rul_hours,risk_path,created_at)
            VALUES (:id,:machine_id,:fault_code,:description,:param_changes_json,:root_cause,:resolution,:rul_hours,:risk_path,:created_at)""", payload)

    def search_cases(self, query: str, fault_code: int | None = None, limit: int = 3) -> list[dict[str, object]]:
        """Perform deterministic lexical case retrieval without invented embeddings."""

        tokens = [token for token in query.lower().replace("，", " ").replace("、", " ").split() if token]
        sql = "SELECT * FROM case_library" + (" WHERE fault_code = ?" if fault_code is not None else "")
        with self.transaction() as connection:
            rows = connection.execute(sql, (fault_code,) if fault_code is not None else ()).fetchall()
        ranked: list[tuple[int, sqlite3.Row]] = []
        for row in rows:
            haystack = " ".join((row["description"], row["root_cause"], row["resolution"])).lower()
            ranked.append((sum(token in haystack for token in tokens), row))
        ranked.sort(key=lambda item: (item[0], item[1]["created_at"]), reverse=True)
        return [self._case_row(row) for _, row in ranked[:limit]]

    def create_maintenance_order(self, payload: Mapping[str, object]) -> str:
        """Persist an auditable maintenance order and return its ID."""

        order_id = str(payload["id"])
        with self.transaction() as connection:
            connection.execute("""INSERT INTO maintenance_records (id,machine_id,diagnostic_id,maintenance_type,start_time,end_time,downtime_hours,parts_cost,labor_cost,parts_json,technician,notes,status)
            VALUES (:id,:machine_id,:diagnostic_id,:maintenance_type,:start_time,:end_time,:downtime_hours,:parts_cost,:labor_cost,:parts_json,:technician,:notes,:status)""", dict(payload))
        return order_id

    def list_maintenance_orders(self, limit: int = 100) -> list[dict[str, object]]:
        """Return work orders that the operations console can track."""

        with self.transaction() as connection:
            rows = connection.execute("SELECT * FROM maintenance_records ORDER BY start_time DESC LIMIT ?", (min(max(limit, 1), 200),)).fetchall()
        return [{key: (json.loads(row[key]) if key == "parts_json" else row[key]) for key in row.keys()} for row in rows]

    # === Agent Activity (autonomous loop audit trail) ===

    def log_agent_activity(self, agent_name: str, action: str, reason: str, machine_id: str | None = None, severity: str = "info", details: Mapping[str, object] | None = None, requires_human_review: bool = False) -> int:
        """Record an autonomous action so the human can review Agent behavior in real time.

        Returns the inserted activity id (used to link notifications).
        """

        details_payload = json.dumps(details or {}, ensure_ascii=False, default=str)
        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO agent_activity (timestamp,agent_name,machine_id,action,severity,reason,details_json,requires_human_review) VALUES (?,?,?,?,?,?,?,?)",
                (datetime.now().astimezone().isoformat(), agent_name, machine_id, action, severity, reason, details_payload, 1 if requires_human_review else 0),
            )
            return int(cursor.lastrowid)

    def list_agent_activity(self, limit: int = 200, machine_id: str | None = None) -> list[dict[str, object]]:
        """Return Agent activity log ordered by newest first."""

        bounded = min(max(limit, 1), 1000)
        where = "WHERE machine_id = ?" if machine_id else ""
        params: Sequence[object] = (machine_id, bounded) if machine_id else (bounded,)
        with self.transaction() as connection:
            rows = connection.execute(f"SELECT * FROM agent_activity {where} ORDER BY timestamp DESC LIMIT ?", params).fetchall()
        return [{"id": row["id"], "timestamp": row["timestamp"], "agent_name": row["agent_name"], "machine_id": row["machine_id"], "action": row["action"], "severity": row["severity"], "reason": row["reason"], "details": json.loads(row["details_json"] or "{}"), "requires_human_review": bool(row["requires_human_review"])} for row in rows]

    # === Notifications (critical alerts awaiting human review) ===

    def create_notification(self, *, machine_id: str | None, severity: str, title: str, message: str, related_activity_id: int | None = None, related_diagnostic_id: str | None = None, related_order_id: str | None = None) -> str:
        """Create a pending notification that requires human review (or auto-acks for low severity)."""

        notification_id = f"NOTIF-{uuid.uuid4().hex[:10].upper()}"
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO notifications (id,created_at,machine_id,severity,title,message,status,related_activity_id,related_diagnostic_id,related_order_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (notification_id, datetime.now().astimezone().isoformat(), machine_id, severity, title, message, "pending", related_activity_id, related_diagnostic_id, related_order_id),
            )
        return notification_id

    def list_notifications(self, status: str | None = "pending", limit: int = 50) -> list[dict[str, object]]:
        """List notifications; default returns only those awaiting human review."""

        bounded = min(max(limit, 1), 200)
        where = "WHERE status = ?" if status else ""
        params: Sequence[object] = (status, bounded) if status else (bounded,)
        with self.transaction() as connection:
            rows = connection.execute(f"SELECT * FROM notifications {where} ORDER BY created_at DESC LIMIT ?", params).fetchall()
        return [dict(row) for row in rows]

    def review_notification(self, notification_id: str, reviewer: str, action: str, notes: str = "") -> dict[str, object] | None:
        """Approve or dismiss a pending notification; returns the updated row."""

        new_status = "approved" if action == "approve" else "dismissed"
        with self.transaction() as connection:
            connection.execute(
                "UPDATE notifications SET status=?, reviewed_at=?, reviewer=?, review_notes=? WHERE id=? AND status='pending'",
                (new_status, datetime.now().astimezone().isoformat(), reviewer, notes, notification_id),
            )
            row = connection.execute("SELECT * FROM notifications WHERE id=?", (notification_id,)).fetchone()
        return dict(row) if row else None

    # === Conversation memory (chat threads & messages) ===

    def create_thread(self, title: str = "新会话") -> str:
        """Create a chat thread and return its id."""

        thread_id = f"T-{uuid.uuid4().hex[:10].upper()}"
        now = datetime.now().astimezone().isoformat()
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO conversation_threads (id,title,created_at,updated_at) VALUES (?,?,?,?)",
                (thread_id, title, now, now),
            )
        return thread_id

    def list_threads(self, limit: int = 50) -> list[dict[str, object]]:
        """List chat threads ordered by recent activity."""

        bounded = min(max(limit, 1), 200)
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM conversation_threads ORDER BY updated_at DESC LIMIT ?", (bounded,)
            ).fetchall()
        return [dict(row) for row in rows]

    def add_message(self, thread_id: str, role: str, content: str, *, intent: str | None = None,
                    tools: list[dict[str, object]] | None = None, citations: list[dict[str, object]] | None = None) -> int:
        """Append one message to a thread and bump the thread counters."""

        now = datetime.now().astimezone().isoformat()
        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO conversation_messages (thread_id,role,content,created_at,intent,tools_json,citations_json) VALUES (?,?,?,?,?,?,?)",
                (thread_id, role, content, now, intent, json.dumps(tools or [], ensure_ascii=False), json.dumps(citations or [], ensure_ascii=False)),
            )
            connection.execute(
                "UPDATE conversation_threads SET updated_at=?, message_count=message_count+1 WHERE id=?",
                (now, thread_id),
            )
            row = connection.execute("SELECT message_count FROM conversation_threads WHERE id=?", (thread_id,)).fetchone()
            count = int(row["message_count"]) if row else 0
            if count % 12 == 0:
                window = connection.execute(
                    "SELECT role, content FROM conversation_messages WHERE thread_id=? ORDER BY id DESC LIMIT 12", (thread_id,)
                ).fetchall()
                summary = "；".join(f"{m['role']}:{m['content'][:40]}" for m in reversed(window))[:400]
                connection.execute(
                    "UPDATE conversation_threads SET summary=?, title=? WHERE id=? AND title='新会话'",
                    (summary, summary[:16] or "新会话", thread_id),
                )
        return int(cursor.lastrowid or 0)

    def list_messages(self, thread_id: str, limit: int = 100) -> list[dict[str, object]]:
        """Return a thread's messages in chronological order."""

        bounded = min(max(limit, 1), 500)
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM conversation_messages WHERE thread_id=? ORDER BY id LIMIT ?", (thread_id, bounded)
            ).fetchall()
        return [{"id": row["id"], "role": row["role"], "content": row["content"], "created_at": row["created_at"], "intent": row["intent"], "tools": json.loads(row["tools_json"]), "citations": json.loads(row["citations_json"])} for row in rows]

    def get_thread(self, thread_id: str) -> dict[str, object] | None:
        """Fetch one thread header including rolling summary."""

        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM conversation_threads WHERE id=?", (thread_id,)).fetchone()
        return dict(row) if row else None

    # === Long-term memory (facts / preferences / lessons) ===

    def remember(self, *, kind: str, subject: str, content: str, source: str = "agent",
                 confidence: float = 0.8, pinned: bool = False, meta: dict[str, object] | None = None) -> str:
        """Insert or merge one long-term memory item (deduped by kind+subject+content hash)."""

        memory_id = f"M-{uuid.uuid4().hex[:10].upper()}"
        now = datetime.now().astimezone().isoformat()
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT id, confidence, access_count FROM long_term_memory WHERE kind=? AND subject=? AND content=?",
                (kind, subject, content),
            ).fetchone()
            if existing:
                connection.execute(
                    "UPDATE long_term_memory SET confidence=MAX(confidence,?), updated_at=?, access_count=access_count+1 WHERE id=?",
                    (confidence, now, existing["id"]),
                )
                return str(existing["id"])
            connection.execute(
                "INSERT INTO long_term_memory (id,kind,subject,content,source,confidence,pinned,created_at,updated_at,meta_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (memory_id, kind, subject, content, source, confidence, int(pinned), now, now, json.dumps(meta or {}, ensure_ascii=False)),
            )
        return memory_id

    def recall(self, subject: str | None = None, kind: str | None = None, query: str | None = None,
               limit: int = 20) -> list[dict[str, object]]:
        """Recall long-term memories, pinned first, then recency; optional LIKE query."""

        bounded = min(max(limit, 1), 100)
        clauses: list[str] = []
        params: list[object] = []
        if subject:
            clauses.append("subject = ?")
            params.append(subject)
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if query:
            clauses.append("(content LIKE ? OR subject LIKE ?)")
            params.extend([f"%{query}%", f"%{query}%"])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(bounded)
        with self.transaction() as connection:
            rows = connection.execute(
                f"SELECT * FROM long_term_memory {where} ORDER BY pinned DESC, updated_at DESC LIMIT ?", params
            ).fetchall()
        return [dict(row) for row in rows]

    def touch_memory(self, memory_id: str) -> None:
        """Update access stats when a memory item is actually injected into a prompt."""

        now = datetime.now().astimezone().isoformat()
        with self.transaction() as connection:
            connection.execute(
                "UPDATE long_term_memory SET access_count=access_count+1, last_accessed_at=? WHERE id=?",
                (now, memory_id),
            )

    def forget_memory(self, memory_id: str) -> bool:
        """Delete one long-term memory item."""

        with self.transaction() as connection:
            cursor = connection.execute("DELETE FROM long_term_memory WHERE id=?", (memory_id,))
        return cursor.rowcount > 0

    # === Maintenance reports (generated after admin approval) ===

    def save_report(self, *, machine_id: str, title: str, summary: str, markdown: str,
                    diagnostic_id: str | None = None, notification_id: str | None = None,
                    order_id: str | None = None, approved_by: str | None = None,
                    meta: dict[str, object] | None = None) -> str:
        """Persist a generated maintenance report and return its id."""

        report_id = f"RPT-{uuid.uuid4().hex[:10].upper()}"
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO maintenance_reports (id,machine_id,diagnostic_id,notification_id,order_id,title,summary,markdown,generated_at,approved_by,meta_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (report_id, machine_id, diagnostic_id, notification_id, order_id, title, summary, markdown, datetime.now().astimezone().isoformat(), approved_by, json.dumps(meta or {}, ensure_ascii=False)),
            )
        return report_id

    def count_readings_for_machine(self, machine_id: str) -> int:
        """返回某台设备的读数条数（用于重算前后核对影响面）。"""

        with self.transaction() as connection:
            row = connection.execute("SELECT COUNT(*) AS total FROM sensor_readings WHERE machine_id=?", (machine_id,)).fetchone()
        return int(row["total"] if row else 0)

    def delete_readings_for_machine(self, machine_id: str) -> int:
        """删除某台设备的全部历史读数，返回删除条数。

        仅供"定向重算"使用（例如仿真器补建征兆后只重跑故障设备）。WHERE 参数化，
        避免误删整表；其余设备与 diagnostics/reports/messages 等表完全不受影响。
        注意：绝不要用 regenerate.py 代替——它会 unlink 整个数据库文件。
        """

        with self.transaction() as connection:
            cursor = connection.execute("DELETE FROM sensor_readings WHERE machine_id=?", (machine_id,))
        return int(cursor.rowcount or 0)

    def delete_reports_for_machine(self, machine_id: str) -> int:
        """删除某台设备的全部维护报告，返回删除条数（用于诊断刷新后重建报告）。"""

        with self.transaction() as connection:
            cursor = connection.execute("DELETE FROM maintenance_reports WHERE machine_id=?", (machine_id,))
        return int(cursor.rowcount or 0)

    def list_reports(self, machine_id: str | None = None, limit: int = 50) -> list[dict[str, object]]:
        """List maintenance reports, newest first, optionally filtered by machine."""

        bounded = min(max(limit, 1), 200)
        where = "WHERE machine_id = ?" if machine_id else ""
        params: Sequence[object] = (machine_id, bounded) if machine_id else (bounded,)
        with self.transaction() as connection:
            rows = connection.execute(
                f"""SELECT id,machine_id,diagnostic_id,notification_id,order_id,title,summary,status,generated_at,generated_by,approved_by
                    FROM maintenance_reports {where} ORDER BY generated_at DESC LIMIT ?""", params,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_report(self, report_id: str) -> dict[str, object] | None:
        """Fetch one full maintenance report including markdown body."""

        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM maintenance_reports WHERE id=?", (report_id,)).fetchone()
        return dict(row) if row else None

    # === Data provenance (answers "where does the data come from") ===

    def record_provenance(self, *, dataset: str, source_type: str, source_detail: str,
                          row_count: int = 0, seed: int | None = None, notes: str = "") -> str:
        """Record how a dataset was produced so the UI can explain its lineage."""

        provenance_id = f"SRC-{uuid.uuid4().hex[:8].upper()}"
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO data_provenance (id,dataset,source_type,source_detail,row_count,seed,generated_at,notes) VALUES (?,?,?,?,?,?,?,?)",
                (provenance_id, dataset, source_type, source_detail, row_count, seed, datetime.now().astimezone().isoformat(), notes),
            )
        return provenance_id

    def list_provenance(self, dataset: str | None = None, limit: int = 50) -> list[dict[str, object]]:
        """List data lineage records, newest first."""

        bounded = min(max(limit, 1), 200)
        where = "WHERE dataset = ?" if dataset else ""
        params: Sequence[object] = (dataset, bounded) if dataset else (bounded,)
        with self.transaction() as connection:
            rows = connection.execute(
                f"SELECT * FROM data_provenance {where} ORDER BY generated_at DESC LIMIT ?", params
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _machine_row(row: sqlite3.Row) -> dict[str, object]:
        """Map a joined machine row into JSON-compatible data."""

        metrics = json.loads(row["metrics_json"]) if row["metrics_json"] else {}
        return {"id": row["id"], "model": row["model"], "manufacture_date": row["manufacture_date"], "last_maintenance": row["last_maintenance"], "next_maintenance": row["next_maintenance"], "last_repair": row["last_repair"], "daily_output": row["daily_output"], "unit_cost": row["unit_cost"], "production_line": row["production_line"], "status": row["status"], "latest_reading": {"timestamp": row["latest_timestamp"], "metrics": metrics, "fault_code": int(row["fault_code"] or 0), "health_score": row["health_score"]} if row["latest_timestamp"] else None, "latest_diagnostic": {"id": row["diagnostic_id"], "severity": row["diagnostic_severity"], "confidence": row["diagnostic_confidence"]} if row["diagnostic_id"] else None}

    @staticmethod
    def _fmeca_row(row: sqlite3.Row) -> dict[str, object]:
        """Decode structured JSON fields in an FMECA row."""

        return {"fault_code": row["fault_code"], "fault_name": row["fault_name"], "system": row["system_name"], "component": row["component"], "evidence_signals": json.loads(row["evidence_signals_json"]), "physical_mechanism": row["physical_mechanism"], "detection_method": row["detection_method"], "sop_steps": json.loads(row["sop_steps_json"]), "required_parts": json.loads(row["required_parts_json"]), "estimated_hours": row["estimated_hours"], "engineer_level": row["engineer_level"], "severity": row["severity"]}

    @staticmethod
    def _case_row(row: sqlite3.Row) -> dict[str, object]:
        """Decode a historical case row for agent and API consumers."""

        return {"id": row["id"], "machine_id": row["machine_id"], "fault_code": row["fault_code"], "description": row["description"], "param_changes": json.loads(row["param_changes_json"]), "root_cause": row["root_cause"], "resolution": row["resolution"], "rul_hours": row["rul_hours"], "risk_path": row["risk_path"], "created_at": row["created_at"]}
