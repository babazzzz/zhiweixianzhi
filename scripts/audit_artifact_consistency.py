"""一致性审计：对比 6 台故障机的"真值 vs 最新诊断 vs 待审通知 vs 最新报告"。

只读脚本，不改数据。

口径说明（与 2026-09-11 的决定一致）：
* **待审通知（pending）**是"待办事项"，必须与真值一致 → 参与判定。
* **已审批通知（approved/dismissed）**是历史审计留存，按用户决定原样保留 → 单独列出，不参与判定。
* 工单备注、agent_activity 属历史流水，同样不参与判定。
"""
from __future__ import annotations

import json
import sqlite3
import sys

sys.path.insert(0, ".")

from src.config import PROJECT_ROOT
from src.simulator.fault_injector import _SCENARIOS

FAULT_NAMES = {
    0: "未识别故障", 1: "轴承磨损", 2: "电机过载", 3: "温度过高",
    4: "电压异常", 5: "电流异常", 6: "转子故障", 7: "电源模块故障", 8: "散热系统故障",
}


def _fault_from_title(title: str) -> str:
    """标题形如 'CNC_078 转子故障 紧急' → '转子故障'。"""

    parts = title.split()
    return parts[1] if len(parts) >= 2 else title


def _fault_from_summary(summary: str | None) -> str:
    """摘要形如 'CNC_036 判定 紧急 风险（散热系统故障），建议…' → '散热系统故障'。"""

    if not summary or "（" not in summary or "）" not in summary:
        return "<无>"
    return summary[summary.index("（") + 1: summary.index("）")]


def main() -> None:
    con = sqlite3.connect(str(PROJECT_ROOT / "data" / "zhiwei.db"))
    con.row_factory = sqlite3.Row

    print(f"{'设备':9s} {'真值':13s} | {'最新诊断':13s} {'待审通知':13s} {'最新报告':13s} | 结论")
    print("-" * 90)
    stale: list[str] = []
    for mid, sc in sorted(_SCENARIOS.items()):
        truth = FAULT_NAMES.get(sc.fault_code, "?")

        d = con.execute(
            "SELECT payload_json FROM diagnostics WHERE machine_id=? ORDER BY created_at DESC LIMIT 1", (mid,)
        ).fetchone()
        dname = str(json.loads(d["payload_json"]).get("fault_mode") or "未识别故障") if d else "<无>"

        n = con.execute(
            "SELECT title FROM notifications WHERE machine_id=? AND status='pending' ORDER BY created_at DESC LIMIT 1",
            (mid,),
        ).fetchone()
        nname = _fault_from_title(n["title"]) if n else "<无>"

        r = con.execute(
            "SELECT summary FROM maintenance_reports WHERE machine_id=? ORDER BY generated_at DESC LIMIT 1", (mid,)
        ).fetchone()
        rname = _fault_from_summary(r["summary"]) if r else "<无>"

        ok = dname == truth and nname in (truth, "<无>") and rname in (truth, "<无>")
        if not ok:
            stale.append(mid)
        print(f"{mid:9s} {truth:13s} | {dname:13s} {nname:13s} {rname:13s} | {'一致' if ok else '**不一致**'}")

    print()
    if stale:
        print("仍不一致的设备:", ", ".join(stale))
    else:
        print("待审队列 / 诊断 / 报告全部与真值一致。")

    print("\n=== 已审批通知（历史留存，按决定未修改）===")
    rows = con.execute(
        "SELECT machine_id,title,status,reviewer,review_notes FROM notifications "
        "WHERE status<>'pending' ORDER BY created_at"
    ).fetchall()
    for row in rows:
        print(f"  {row['machine_id']}  [{row['status']}]  {row['title']}  reviewer={row['reviewer']}  notes={row['review_notes']}")


if __name__ == "__main__":
    main()
