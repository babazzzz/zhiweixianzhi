"""就地修正**待审通知**里写错的故障名（只改故障名，不动审批状态/审核人/备注/时间）。

背景
----
诊断规则修复前，兜底规则把 6 台故障机统统判成"轴承磨损"，自主循环据此发出的待审通知
标题/正文都写错了故障名。诊断、报告、设备列表已修正，但通知中心默认展示的待审队列仍是旧结论，
与设备页自相矛盾。**待审通知是"待办事项"而非历史快照**，写错会误导处置，故就地改正。

约束
----
* 只改 ``status='pending'`` 的行；已审批（approved/dismissed）的记录原样保留（审计留存）。
* 只替换标题/正文中的故障名，其余字段（created_at / severity / related_* / status）一律不动。
* 先备份整库；支持 ``--dry-run``。

用法::
    .venv/Scripts/python.exe scripts/fix_stale_notifications.py --dry-run
    .venv/Scripts/python.exe scripts/fix_stale_notifications.py
"""

from __future__ import annotations

import argparse
import datetime
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import PROJECT_ROOT  # noqa: E402
from src.simulator.fault_injector import _SCENARIOS  # noqa: E402

FAULT_NAMES = {
    0: "未识别故障", 1: "轴承磨损", 2: "电机过载", 3: "温度过高",
    4: "电压异常", 5: "电流异常", 6: "转子故障", 7: "电源模块故障", 8: "散热系统故障",
}
_TITLE_RE = re.compile(r"^(?P<mid>\S+)\s+(?P<fault>\S+)\s+(?P<tail>\S+)$")
_MSG_RE = re.compile(r"（(?P<fault>[^（）]+)）")


def backup(db_path: Path) -> Path:
    """SQLite 在线备份，保留 WAL 一致性。"""

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    target = db_path.with_suffix(db_path.suffix + f".bak_{stamp}")
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="修正待审通知里写错的故障名")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要做的改动，不写库")
    args = parser.parse_args()

    db_path = PROJECT_ROOT / "data" / "zhiwei.db"
    truth = {mid: FAULT_NAMES.get(sc.fault_code, "未识别故障") for mid, sc in _SCENARIOS.items()}

    if not args.dry_run:
        print(f"备份 -> {backup(db_path).name}")

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id,machine_id,title,message FROM notifications WHERE status='pending' ORDER BY created_at"
    ).fetchall()

    changed = 0
    skipped = 0
    unmatched = 0
    for row in rows:
        mid = row["machine_id"]
        correct = truth.get(mid)
        if correct is None:
            skipped += 1
            continue

        m = _TITLE_RE.match(row["title"])
        if not m:
            unmatched += 1
            print(f"  ! 标题格式未识别，跳过：{row['id']} {row['title']!r}")
            continue
        old_fault = m.group("fault")
        if old_fault == correct:
            skipped += 1
            continue

        new_title = f"{m.group('mid')} {correct} {m.group('tail')}"
        new_message = _MSG_RE.sub(lambda mm: f"（{correct}）", row["message"], count=1)
        changed += 1
        print(f"  {row['id']}  {mid}  「{old_fault}」→「{correct}」")

        if not args.dry_run:
            with con:  # 事务：只更新故障名相关文本，其余字段不动
                con.execute(
                    "UPDATE notifications SET title=?, message=? WHERE id=? AND status='pending'",
                    (new_title, new_message, row["id"]),
                )

    if not args.dry_run:
        con.close()

    print()
    print(f"待审通知共 {len(rows)} 条：修正 {changed} 条、已是正确结论 {skipped} 条、格式异常 {unmatched} 条")
    if args.dry_run:
        print("（--dry-run：未写库）")


if __name__ == "__main__":
    main()
