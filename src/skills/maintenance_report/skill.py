"""MaintenanceReportSkill：审批通过后自动生成结构化维护报告。

设计要点
--------
* **确定性**：报告全部由数据库实况拼装（设备档案 / 最近诊断 / FMECA 规则 /
  维修历史 / 相似案例），不调用大模型，同样输入必得同样输出，便于审计。
* **可追溯**：输出里带 ``data_sources``，正文每节注明数据出处；诊断、通知、
  工单 ID 全部写入报告头。
* **独立触发链**：管理员在通知页点"批准"后，API 层调用本 skill 并把生成的
  ``report_id`` 回写到通知与诊断记录，前端报告页据此展示。
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from src.database.connection import Database
from src.observability import traced

from ..base import BaseSkill, SkillExample, SkillMeta
from .schema import MaintenanceReportInput, MaintenanceReportOutput

LOGGER = logging.getLogger("zhiwei.skills.maintenance_report")

_SEVERITY_LABEL = {"high": "高", "medium": "中", "low": "低", "info": "提示", "critical": "紧急"}


class MaintenanceReportSkill(BaseSkill):
    """Assemble an auditable maintenance report from live database state."""

    meta = SkillMeta(
        id="maintenance_report",
        name="维护报告生成",
        version="1.0.0",
        description=(
            "设备被判定高风险且管理员批准后自动调用：汇总设备档案、最近诊断结论、"
            "FMECA 处置规程、历史维修记录与相似案例，生成可审计的 Markdown 维护报告并落库。"
            "纯确定性拼装，不调用大模型。"
        ),
        category="operations",
        deterministic=True,
        requires_llm=False,
        tags=("report", "maintenance", "approval", "E8"),
        examples=(
            SkillExample(
                description="B2012 高风险诊断获批后生成维护报告",
                payload={"machine_id": "B2012", "approved_by": "管理员"},
            ),
        ),
    )
    input_schema = MaintenanceReportInput
    output_schema = MaintenanceReportOutput

    def __init__(self, database: Database | None = None) -> None:
        self._db = database or Database()

    # BaseSkill.run(payload: dict) -> dict 的入口适配
    def run(self, payload: MaintenanceReportInput | dict[str, Any]) -> dict[str, Any]:  # type: ignore[override]
        if isinstance(payload, dict):
            payload = MaintenanceReportInput(**payload)
        return self.execute(payload).model_dump()

    def execute(self, payload: MaintenanceReportInput) -> MaintenanceReportOutput:
        """Gather evidence and render the report."""

        with traced("skill", "maintenance_report", machine_id=payload.machine_id):
            machine = self._db.get_machine(payload.machine_id)
            if machine is None:
                raise ValueError(f"设备不存在：{payload.machine_id}")

            diagnostic = self._resolve_diagnostic(payload)
            fault_code = self._fault_code_of(diagnostic)
            rule = self._db.get_fmeca(fault_code) if fault_code is not None else None
            cases = self._db.search_cases("维修 处置 根因", fault_code=fault_code, limit=2) if payload.include_history else []
            history = self._maintenance_history(payload.machine_id) if payload.include_history else []

            markdown, sections = self._render(payload, machine, diagnostic, rule, history, cases)
            summary = self._summarize(machine, diagnostic, rule)

            report_id = self._db.save_report(
                machine_id=payload.machine_id,
                title=f"{payload.machine_id} 维护报告",
                summary=summary,
                markdown=markdown,
                diagnostic_id=str(diagnostic["id"]) if diagnostic else payload.diagnostic_id,
                notification_id=payload.notification_id,
                order_id=payload.order_id,
                approved_by=payload.approved_by,
                meta={"sections": sections, "fault_code": fault_code},
            )
            LOGGER.info(f"maintenance report generated: {report_id} machine={payload.machine_id}")
            return MaintenanceReportOutput(
                report_id=report_id,
                machine_id=payload.machine_id,
                title=f"{payload.machine_id} 维护报告",
                summary=summary,
                markdown=markdown,
                sections=sections,
                data_sources=self._sources(diagnostic, rule, history, cases),
            )

    # --- 数据归集 -----------------------------------------------------

    def _resolve_diagnostic(self, payload: MaintenanceReportInput) -> dict[str, Any] | None:
        if payload.diagnostic_id:
            found = self._db.get_diagnostic(payload.diagnostic_id)
            if found is None:
                raise ValueError(f"诊断记录不存在：{payload.diagnostic_id}")
            return found
        recent = self._db.list_diagnostics(machine_id=payload.machine_id, limit=1)
        return recent[0] if recent else None

    @staticmethod
    def _fault_code_of(diagnostic: dict[str, Any] | None) -> int | None:
        if not diagnostic:
            return None
        payload_obj = diagnostic.get("payload") or {}
        if isinstance(payload_obj, dict):
            rule = payload_obj.get("rule") or {}
            code = rule.get("fault_code", payload_obj.get("fault_code"))
            if code is not None:
                try:
                    return int(code)
                except (TypeError, ValueError):
                    return None
        return None

    def _maintenance_history(self, machine_id: str) -> list[dict[str, Any]]:
        with self._db.transaction() as connection:
            rows = connection.execute(
                """SELECT id, maintenance_type, start_time, end_time, downtime_hours,
                          parts_cost, labor_cost, technician, notes, status
                   FROM maintenance_records WHERE machine_id=? ORDER BY start_time DESC LIMIT 5""",
                (machine_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # --- 渲染 ---------------------------------------------------------

    def _render(
        self,
        payload: MaintenanceReportInput,
        machine: dict[str, Any],
        diagnostic: dict[str, Any] | None,
        rule: dict[str, Any] | None,
        history: list[dict[str, Any]],
        cases: list[dict[str, Any]],
    ) -> tuple[str, list[str]]:
        lines: list[str] = []
        sections: list[str] = []

        def section(title: str) -> None:
            sections.append(title)
            lines.append(f"\n## {title}\n")

        lines.append(f"# {payload.machine_id} 维护报告")
        header_bits = [f"生成时间：{self._now()}"]
        if diagnostic:
            header_bits.append(f"诊断单：{diagnostic['id']}")
        if payload.notification_id:
            header_bits.append(f"审批通知：{payload.notification_id}")
        if payload.order_id:
            header_bits.append(f"工单：{payload.order_id}")
        if payload.approved_by:
            header_bits.append(f"批准人：{payload.approved_by}")
        lines.append("\n".join(f"- {bit}" for bit in header_bits))

        section("一、设备概况")
        lines.append(
            f"- 设备：{machine['id']}（{machine.get('model') or '未知型号'}），产线 {machine.get('production_line') or '—'}"
        )
        lines.append(
            f"- 日产量 {machine.get('daily_output') or 0} 件，单位成本 {machine.get('unit_cost') or 0} 元，"
            f"当前状态 {machine.get('status') or '—'}"
        )
        last_maint = machine.get("last_maintenance") or "—"
        next_maint = machine.get("next_maintenance") or "—"
        lines.append(f"- 上次维护 {last_maint}，计划下次维护 {next_maint}")

        section("二、诊断结论")
        if diagnostic:
            severity = _SEVERITY_LABEL.get(str(diagnostic.get("severity")), diagnostic.get("severity"))
            lines.append(
                f"- 结论：**{severity}** 风险（置信度 {float(diagnostic.get('confidence') or 0):.0%}），"
                f"诊断时间 {str(diagnostic.get('created_at'))[:19]}"
            )
            payload_obj = diagnostic.get("payload") or {}
            rule_info = payload_obj.get("rule") or {}
            if rule_info.get("fault_name"):
                lines.append(f"- 判定故障：{rule_info.get('fault_name')}")
            anomalies = payload_obj.get("anomaly") or {}
            dims = anomalies.get("anomaly_dims") or []
            if dims:
                lines.append(f"- 异常维度：{('、'.join(str(d) for d in dims))}")
        else:
            lines.append("- 未找到关联诊断记录（可能为预防性维护报告）")

        if rule:
            section("三、根因机理与处置规程（FMECA）")
            lines.append(f"- 故障模式：{rule.get('fault_name')}（编码 {rule.get('fault_code')}）")
            if rule.get("component"):
                lines.append(f"- 涉及部件：{rule.get('component')}")
            if rule.get("physical_mechanism"):
                lines.append(f"- 失效机理：{rule.get('physical_mechanism')}")
            sop = rule.get("sop_steps") or []
            if sop:
                lines.append("- 处置步骤：")
                lines.extend(f"  {index}. {step}" for index, step in enumerate(sop, start=1))
            parts = rule.get("required_parts") or []
            if parts:
                lines.append(f"- 所需备件：{'、'.join(str(p) for p in parts)}（预估工时 {rule.get('estimated_hours')}h，"
                             f"建议工程师等级 {rule.get('engineer_level') or '—'}）")

        if history:
            section("四、历史维修记录")
            lines.append("| 时间 | 类型 | 停机(h) | 备件费 | 工时费 | 技师 | 状态 |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- |")
            for item in history:
                lines.append(
                    f"| {str(item['start_time'])[:10]} | {item['maintenance_type']} | {item['downtime_hours'] or 0} | "
                    f"{item['parts_cost'] or 0} | {item['labor_cost'] or 0} | {item['technician']} | {item['status']} |"
                )

        if cases:
            section("五、相似历史案例")
            for case in cases:
                lines.append(
                    f"- 案例 {case['id']}：{case['description']}；根因「{case['root_cause']}」，"
                    f"处置「{case['resolution']}」"
                )

        section("六、维护建议")
        if rule:
            lines.append(
                f"- 建议按上述 FMECA 规程执行，预估停机 {rule.get('estimated_hours')} 小时，"
                f"请提前备齐备件并安排 {rule.get('engineer_level') or '中级'} 以上工程师。"
            )
        else:
            lines.append("- 建议安排例行点检，重点核查异常维度对应部件。")
        lines.append("- 维修完成后请回填工单结果，系统将自动更新设备画像与知识库案例。")

        return "\n".join(lines).strip() + "\n", sections

    def _summarize(self, machine: dict[str, Any], diagnostic: dict[str, Any] | None,
                   rule: dict[str, Any] | None) -> str:
        if diagnostic and rule:
            severity = _SEVERITY_LABEL.get(str(diagnostic.get("severity")), str(diagnostic.get("severity")))
            return (
                f"{machine['id']} 判定 {severity} 风险（{rule.get('fault_name')}），"
                f"建议按 FMECA 规程执行维护，预估工时 {rule.get('estimated_hours')} 小时。"
            )
        if diagnostic:
            return f"{machine['id']} 最近诊断为「{diagnostic.get('severity')}」风险，建议安排检查。"
        return f"{machine['id']} 暂无诊断记录，本报告为预防性维护参考。"

    @staticmethod
    def _sources(diagnostic: dict[str, Any] | None, rule: dict[str, Any] | None,
                 history: list[dict[str, Any]], cases: list[dict[str, Any]]) -> list[str]:
        sources = ["machines（设备档案）"]
        if diagnostic:
            sources.append(f"diagnostics/{diagnostic['id']}")
        if rule:
            sources.append(f"fmeca_rules/F-{rule.get('fault_code')}")
        if history:
            sources.append(f"maintenance_records×{len(history)}")
        if cases:
            sources.append(f"case_library×{len(cases)}")
        return sources

    @staticmethod
    def _now() -> str:
        from datetime import datetime
        return datetime.now().astimezone().isoformat(timespec="seconds")
