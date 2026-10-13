"""文档写作前的事实核对：把两份文档要引用的所有数字一次性量出来。

只读脚本，不改任何数据。用于避免文档写入错误数字。
"""

from __future__ import annotations

import collections
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, ".")

from src.api.main import app
from src.config import PROJECT_ROOT, SENSOR_COLUMNS
from src.knowledge.fault_graph import FaultGraph
from src.simulator.fault_injector import _SCENARIOS
from src.skills import SkillRegistry


def main() -> None:
    db = PROJECT_ROOT / "data" / "zhiwei.db"
    print("=" * 66)
    print("数据库")
    print("=" * 66)
    print(f"文件大小: {db.stat().st_size / 1024 / 1024:.2f} MB")
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    tables = [r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    for t in tables:
        if t == "sqlite_sequence":
            continue
        n = con.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
        print(f"  {t:26s} {n:>7d}")
    rng = con.execute("SELECT MIN(timestamp) a, MAX(timestamp) b, COUNT(*) c FROM sensor_readings").fetchone()
    print(f"  sensor_readings 时间范围: {rng['a']} → {rng['b']}  ({rng['c']} 条)")
    print(f"  健康机/故障机: {con.execute('SELECT COUNT(DISTINCT machine_id) c FROM sensor_readings').fetchone()['c']} 台有读数")

    print()
    print("=" * 66)
    print("仿真与故障注入")
    print("=" * 66)
    print(f"22 维传感器列数: {len(SENSOR_COLUMNS)}")
    for mid, sc in sorted(_SCENARIOS.items()):
        actual = con.execute("SELECT fault_code FROM sensor_readings WHERE machine_id=? LIMIT 1", (mid,)).fetchone()
        print(f"  {mid} → 注入 fault_code={sc.fault_code} onset={sc.onset_day} precursor={sc.precursor_days} "
              f"severity={sc.severity}  库内={actual['fault_code']}")
    print(f"注入故障设备数: {len(_SCENARIOS)} / 100")

    print()
    print("=" * 66)
    print("知识层")
    print("=" * 66)
    g = FaultGraph()
    print(f"因果图: {g.graph.number_of_nodes()} 节点 / {g.graph.number_of_edges()} 边 "
          f"({dict(collections.Counter(d['type'] for _, d in g.graph.nodes(data=True)))})")
    print(f"FMECA 规则: {con.execute('SELECT COUNT(*) c FROM fmeca_rules').fetchone()['c']} 条")
    print(f"案例库: {con.execute('SELECT COUNT(*) c FROM case_library').fetchone()['c']} 条")
    chunks_path = PROJECT_ROOT / "data" / "index" / "chunks.json"
    if chunks_path.exists():
        chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
        print(f"RAG chunk 总数: {len(chunks)}")
        print(f"  按 doc_type: {dict(sorted(collections.Counter(c.get('doc_type') for c in chunks).items()))}")
        avg = sum(len(c.get('text', '')) for c in chunks) / max(len(chunks), 1)
        print(f"  平均字符数: {avg:.1f}")

    print()
    print("=" * 66)
    print("技能 / 接口")
    print("=" * 66)
    SkillRegistry.discover()
    metas = SkillRegistry.all()
    print(f"已注册技能: {len(metas)}")
    for m in metas:
        print(f"  {m.id:22s} v{m.version:6s} {m.category:12s} deterministic={m.deterministic} side_effects={m.side_effects}")
    builtin = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    rest = [r for r in app.routes if getattr(r, "methods", None) and r.methods and r.path not in builtin]
    ws = [r for r in app.routes if r.__class__.__name__ == "APIWebSocketRoute"]
    print(f"业务 REST 路由: {len(rest)}（其中 SSE 流式 1 个：POST /api/diagnostics/stream）")
    print(f"WebSocket: {len(ws)} → {[r.path for r in ws]}")
    print(f"业务接口合计: {len(rest) + len(ws)}")

    print()
    print("=" * 66)
    print("代码规模")
    print("=" * 66)
    src = PROJECT_ROOT / "src"
    py = [p for p in src.rglob("*.py") if "__pycache__" not in p.parts]
    print(f"src/ 下 .py 文件: {len(py)}")
    print(f"src/ 顶层子目录: {sorted(p.name for p in src.iterdir() if p.is_dir() and p.name != '__pycache__')}")
    web_pages = sorted(p.name for p in (PROJECT_ROOT / "web" / "src" / "pages").glob("*.tsx"))
    print(f"web/src/pages: {web_pages}")
    tests = sorted(p.name for p in (PROJECT_ROOT / "tests").glob("test_*.py"))
    print(f"tests/: {tests}")


if __name__ == "__main__":
    main()
