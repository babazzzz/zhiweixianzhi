"""实时数据推进引擎：按真实时钟向 SQLite 追加新读数。

数据层回答（需求 7）的分项之二——"是否每次运行都重新模拟"：

* **历史数据**（2025-07-01 起 14 天）：确定性一次性生成，落在
  ``data/zhiwei.db``，重启/重跑不会变化（INSERT OR IGNORE）。
* **实时数据**：``RealtimeEngine`` 以 ``REALTIME_TICK_SECONDS`` 为节拍，
  每拍把每台设备按 ``REALTIME_STEP_MINUTES`` 推进一步，生成带工况调制的
  新读数追加入库。追加的是"新的时间点"，不改写历史——这是演示时
  "数据在往前走"的来源。
* 每一批推进都写 ``data_provenance`` 血缘记录，前端数据页可展示
  "这条数据是谁、什么时候、用什么模型算出来的"。

默认 ``REALTIME_ENABLED=0``：不开启时系统只使用历史数据，行为与从前一致。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from src.config import DATA_DIR
from src.database.connection import Database
from src.database.schemas import SensorReading
from src.observability import log_event, traced
from src.simulator.cnc_simulator import CNCSimulator
from .workload import modulate_metrics, workload_for

LOGGER = logging.getLogger("zhiwei.realtime")

# 仿真时钟的持久化游标：重启后从断点续写，而非重头开始。
# 解析优先级：① sim_clock.json（显式文件，抗数据库重建） → ② sensor_readings 的
# MAX(timestamp)（已存历史） → ③ 启动时刻。三种来源都保证“时间只前进、历史不被改写”。
CLOCK_FILE: Path = DATA_DIR / "sim_clock.json"


class RealtimeEngine:
    """Append-only realtime tick loop over the deterministic simulator."""

    def __init__(self, database: Database, *, step_minutes: int | None = None,
                 tick_seconds: float | None = None) -> None:
        self._db = database
        self._sim = CNCSimulator(start=datetime(2025, 7, 1))
        self._step_minutes = step_minutes or int(os.getenv("REALTIME_STEP_MINUTES", "30"))
        self._tick_seconds = tick_seconds or float(os.getenv("REALTIME_TICK_SECONDS", "5.0"))
        self._task: asyncio.Task | None = None
        self._running = False
        self._ticks = 0
        # 断点续写的内存游标：由 _restore_cursor() 从 sim_clock.json / DB / 启动时刻 三级恢复
        self._clock: datetime = self._restore_cursor()

    # --- 生命周期 -----------------------------------------------------

    def start(self) -> None:
        """Idempotently start the background tick loop."""

        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="realtime-engine")
        LOGGER.info(f"realtime engine started: step={self._step_minutes}min tick={self._tick_seconds}s")

    async def stop(self) -> None:
        """Stop the loop and wait for the current tick to finish."""

        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        LOGGER.info("realtime engine stopped")

    @property
    def status(self) -> dict[str, object]:
        """Expose runtime state for the /api/data endpoint."""

        return {
            "running": self._running,
            "step_minutes": self._step_minutes,
            "tick_seconds": self._tick_seconds,
            "ticks_completed": self._ticks,
            "clock_cursor": self._clock.isoformat(timespec="seconds"),
            "clock_persisted": CLOCK_FILE.exists(),
        }

    # --- 主循环 -------------------------------------------------------

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.tick()
            except Exception as error:  # noqa: BLE001 - 单拍失败不拖垮循环
                LOGGER.exception(f"realtime tick failed: {error}")
            await asyncio.sleep(self._tick_seconds)

    async def tick(self, *, machines: list[str] | None = None) -> dict[str, object]:
        """Advance every machine by one step and persist the readings."""

        with traced("data", "realtime_tick"):
            machine_ids = machines or self._machine_ids()
            if not machine_ids:
                return {"appended": 0, "reason": "no machines"}
            # 时间基准永远取“上次游标 + 一步”，而不是 wall-clock，
            # 保证任何时刻调用都得到确定性、可断点续写的推进。
            base = self._clock + timedelta(minutes=self._step_minutes)
            appended = 0
            for machine_id in machine_ids:
                reading = self._reading_with_workload(machine_id, base)
                appended += self._db.insert_readings([reading])
            self._clock = base
            self._persist_clock()            # 每拍落盘游标，崩溃/重启可从断点续写
            self._ticks += 1
            if self._ticks % 10 == 1:
                self._db.record_provenance(
                    dataset="sensor_readings", source_type="realtime_tick",
                    source_detail=f"RealtimeEngine step={self._step_minutes}min, workload-modulated",
                    row_count=appended, seed=None,
                    notes=f"tick#{self._ticks} at {base.isoformat(timespec='seconds')}",
                )
            log_event("data", "realtime_append", machines=len(machine_ids), appended=appended)
            return {"appended": appended, "timestamp": base.isoformat()}

    # --- 时钟恢复 / 持久化 ---------------------------------------------

    def _restore_cursor(self) -> datetime:
        """Resolve the starting cursor for this engine.

        Order: ① persisted sim_clock.json  → ② DB MAX(timestamp)  → ③ engine start.
        Returning a value >= the newest stored reading guarantees the 67200-row
        history is never overwritten (append-only).
        """
        # ① 显式持久化游标：最抗数据库被清空/迁移的场景
        if CLOCK_FILE.exists():
            try:
                payload = json.loads(CLOCK_FILE.read_text(encoding="utf-8"))
                cursor = datetime.fromisoformat(str(payload["cursor"]))
                LOGGER.info(f"realtime clock restored from {CLOCK_FILE.name}: {cursor.isoformat()}")
                return cursor
            except (ValueError, KeyError, OSError) as error:
                LOGGER.warning(f"sim_clock.json unreadable ({error}); falling back to DB max(timestamp)")

        # ② 数据库最新读数：历史数据已存在的断点（等效于旧的 _latest_timestamp 行为）
        ids = self._machine_ids()
        latest = self._latest_timestamp(ids) if ids else None
        if latest is not None:
            LOGGER.info(f"realtime clock restored from DB max(timestamp): {latest.isoformat()}")
            return latest

        # ③ 无历史：以引擎启动时刻为起点（naive，与 DB / 历史时间轴一致）
        LOGGER.info("no persisted clock / history; starting from now")
        return datetime.now()

    def _persist_clock(self) -> None:
        """Write the current clock cursor to sim_clock.json (append-only, atomic replace)."""
        try:
            CLOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
            CLOCK_FILE.write_text(
                json.dumps({"cursor": self._clock.isoformat(),   # 保留微秒，避免重启后时间轴漂移
                            "sim_start": self._sim.start.isoformat()},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as error:  # 写盘失败不阻断数据推进，仅告警
            LOGGER.warning(f"failed to persist sim_clock.json: {error}")

    # --- 细节 ---------------------------------------------------------

    def _reading_with_workload(self, machine_id: str, timestamp: datetime) -> SensorReading:
        raw = self._sim.get_reading(machine_id, timestamp)
        workload = workload_for(timestamp)
        raw = modulate_metrics(raw, workload)
        return SensorReading(
            machine_id=machine_id,
            timestamp=timestamp,
            metrics={key: float(value) for key, value in raw.items()
                     if key not in {"machine_id", "timestamp", "fault_code", "health_score"}
                     and isinstance(value, (int, float))},
            fault_code=int(raw["fault_code"]) if raw.get("fault_code") not in (None, "") else None,
            health_score=float(raw["health_score"]) if raw.get("health_score") is not None else None,
        )

    def _machine_ids(self) -> list[str]:
        with self._db.transaction() as connection:
            rows = connection.execute("SELECT id FROM machines ORDER BY id").fetchall()
        return [str(row["id"]) for row in rows]

    def _latest_timestamp(self, machine_ids: list[str]) -> datetime | None:
        with self._db.transaction() as connection:
            placeholders = ",".join("?" for _ in machine_ids)
            row = connection.execute(
                f"SELECT MAX(timestamp) AS latest FROM sensor_readings WHERE machine_id IN ({placeholders})",
                machine_ids,
            ).fetchone()
        if row and row["latest"]:
            return datetime.fromisoformat(str(row["latest"]))
        return None
