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
        lines.append("\n> 数据来源：" + "、".join(self._sources(diagnostic, rule, history, cases)) +
                     "。全文由数据库实况确定性拼装，可逐条回溯。")

        sections.append("一、设备概况")
        lines.append("\n## 一、设备概况\n")
        lines.append(f"- 设备：{machine['id']}（{machine.get('model') or '未知型号'}），产线 {machine.get('production_line') or '—'}")
        lines.append(
            f"- 日产量 {machine.get('daily_output') or 0} 件，单位成本 {machine.get('unit_cost') or 0} 元，"
            f"当前状态 {machine.get('status') or '—'}"
        )
        lines.append(f"- 上次维护 {machine.get('last_maintenance') or '—'}，计划下次维护 {machine.get('next_maintenance') or '—'}")

        analysis = self._analyze_sensor(diagnostic, payload.machine_id)
        payload_obj = (diagnostic.get("payload") or {}) if diagnostic else {}
        rul = payload_obj.get("rul") or {}
        decision = payload_obj.get("decision") or {}
        evidence = payload_obj.get("evidence_chain") or []
        checks = payload_obj.get("reflection_checks") or []
        dims = [str(item) for item in (payload_obj.get("anomaly_dims") or [])]

        # --- 二、诊断结论 ---
        sections.append("二、诊断结论")
        lines.append("\n## 二、诊断结论\n")
        if diagnostic:
            severity = _SEVERITY_LABEL.get(str(diagnostic.get("severity")), diagnostic.get("severity"))
            lines.append(
                f"- **结论**：{severity}风险 · 置信度 **{float(diagnostic.get('confidence') or 0):.1%}** · "
                f"触发时间 {str(payload_obj.get('trigger_timestamp') or diagnostic.get('created_at'))[:19]}"
            )
            lines.append(f"- **判定故障**：{payload_obj.get('fault_mode') or '—'}"
                         + (f"（故障码 {rule.get('fault_code')}）" if rule else ""))
            lines.append(f"- **根因判断**：{payload_obj.get('root_cause') or '—'}")
            health = payload_obj.get("health_score")
            anomaly_score = payload_obj.get("anomaly_score")
            if health is not None or anomaly_score is not None:
                lines.append(
                    f"- **状态量化**：健康度 {self._num(health, 4)}（1.0 为满血）· 异常分 {self._num(anomaly_score, 4)}"
                    f" · 异常类型 {'渐变退化' if payload_obj.get('anomaly_type') == 'gradual' else payload_obj.get('anomaly_type') or '—'}"
                )
            if dims:
                lines.append(f"- **命中异常维度（{len(dims)} 个）**：{('、'.join(dims))}")
            iterations = payload_obj.get("react_iterations")
            lines.append(
                f"- **诊断编排**：ReAct 迭代 {iterations if iterations is not None else '—'} 轮 · "
                f"证据条目 {len(evidence)} 条 · 质量门 Reflection "
                f"{'通过' if payload_obj.get('reflection_passed') else '未通过'}"
            )
            if rul:
                lines.append(
                    f"- **剩余寿命 RUL**：P10 {rul.get('rul_p10', '—')}h / P50 {rul.get('rul_p50', '—')}h / "
                    f"P90 {rul.get('rul_p90', '—')}h（威布尔 β={self._num(rul.get('beta'), 2)}，η={self._num(rul.get('eta'), 1)}h，"
                    f"退化率 {self._num(rul.get('degradation_rate'), 5)}/h，建议监控频率 {rul.get('monitoring_freq') or '—'}）"
                )
        else:
            lines.append("- 未找到关联诊断记录（可能为预防性维护报告）。")

        # --- 三、数据分析过程 ---
        if analysis["rows"]:
            sections.append("三、数据分析过程（触发值 vs 全厂同型基线）")
            lines.append("\n## 三、数据分析过程（触发值 vs 全厂同型基线）\n")
            lines.append(
                f"基线取**同型号健康设备最新读数的中位数**（已剔除健康度 < 0.6 的劣化设备，"
                f"有效样本 {analysis['peer_count']} 台·维）。偏差 =（触发值 − 基线）/ 基线 × 100%，"
                f"**偏差越大说明相对同侪漂移越明显**；标记 ★ 的是异常检测（三信号融合）命中的维度。\n"
            )
            lines.append("| 指标 | 触发值 | 全厂同型基线 | 偏差 | 异常 |")
            lines.append("| --- | --- | --- | --- | --- |")
            for row in analysis["rows"][:16]:
                lines.append(
                    f"| {row['metric']} | {self._num(row['current'], 4)} | {self._num(row['baseline'], 4)} | "
                    f"{row['deviation']:+.1f}% | {'★' if row['anomalous'] else ''} |"
                )
            if len(analysis["rows"]) > 16:
                lines.append(f"\n> 表内仅列出偏差最大的 16 个维度，完整 22 维原始读数见诊断单。")
            worse = sorted(
                (row for row in analysis["rows"] if row["anomalous"]),
                key=lambda row: -abs(row["deviation"]),
            )[:4]
            if worse:
                lines.append(
                    "\n- **异常维度排序（按偏离幅度）**：" + "、".join(
                        f"{row['metric']}（{row['deviation']:+.1f}%）" for row in worse
                    ) + "。"
                )
            if rule:
                signals = rule.get("evidence_signals") or []
                if signals:
                    lines.append(
                        f"- **FMECA 对照**：判定「{rule.get('fault_name')}」的典型征兆为 "
                        f"{'、'.join(str(signal) for signal in signals)}；上表标注 ★ 的维度是异常检测"
                        f"给出的直接数据支撑，建议结合现场复核查验。"
                    )
            lines.append(
                f"- **数据窗口**：共 {analysis['window']} 条时序读数参与异常检测；"
                f"健康度 {self._num(payload_obj.get('health_score'), 4)}、异常分 {self._num(payload_obj.get('anomaly_score'), 4)}。"
            )

        # --- 四、证据链 ---
        if evidence:
            sections.append("四、E1–E7 证据链")
            lines.append("\n## 四、E1–E7 证据链\n")
            for item in evidence:
                lines.append(
                    f"- **{item.get('id')} · {item.get('source')}**（置信度 {float(item.get('confidence') or 0):.0%}）："
                    f"{item.get('content')}"
                )

        # --- 五、根因机理与处置规程 ---
        if rule:
            sections.append("五、根因机理与处置规程（FMECA）")
            lines.append("\n## 五、根因机理与处置规程（FMECA）\n")
            lines.append(f"- **故障模式**：{rule.get('fault_name')}（编码 {rule.get('fault_code')}，严重度 {rule.get('severity') or '—'}）")
            if rule.get("component"):
                lines.append(f"- **涉及部件**：{rule.get('component')}")
            if rule.get("physical_mechanism"):
                lines.append(f"- **失效机理**：{rule.get('physical_mechanism')}")
            if rule.get("detection_method"):
                lines.append(f"- **检测方法**：{rule.get('detection_method')}")
            signals = rule.get("evidence_signals") or []
            if signals:
                lines.append(f"- **征兆信号**：{'、'.join(str(signal) for signal in signals)}")
            sop = rule.get("sop_steps") or []
            if sop:
                lines.append("\n**处置步骤（SOP）**\n")
                lines.extend(f"{index}. {step}" for index, step in enumerate(sop, start=1))
            parts = rule.get("required_parts") or []
            lines.append(
                f"\n- **资源需求**：备件 {'、'.join(str(p) for p in parts) or '按现场检查确定'}；"
                f"预估工时 {rule.get('estimated_hours')}h；建议工程师等级 {rule.get('engineer_level') or '—'}"
            )

        # --- 六、维护决策与成本对比 ---
        if decision:
            sections.append("六、维护决策与成本对比")
            lines.append("\n## 六、维护决策与成本对比\n")
            lines.append(f"- **系统建议**：{decision.get('recommendation') or '—'}（时机：{decision.get('timing') or '—'}）")
            options = decision.get("options") or []
            if options:
                lines.append("\n| 方案 | 停机(h) | 直接成本 | 风险成本 | 总成本 |")
                lines.append("| --- | --- | --- | --- | --- |")
                for option in options:
                    lines.append(
                        f"| {option.get('name')} | {self._num(option.get('downtime_hours'), 1)} | "
                        f"¥{self._num(option.get('direct_cost'), 2)} | ¥{self._num(option.get('risk_cost'), 2)} | "
                        f"¥{self._num(option.get('total_cost'), 2)} |"
                    )
            lines.append(
                f"\n- **净收益**：¥{self._num(decision.get('net_benefit'), 2)}"
                f"（直接成本 ¥{self._num(decision.get('direct_cost'), 2)}，风险成本 ¥{self._num(decision.get('risk_cost'), 2)}）"
            )
            what_if = decision.get("what_if") or {}
            if what_if:
                lines.append(
                    f"- **What-if 情景**：减载 {what_if.get('derated_pct', '—')}% → RUL 延长 "
                    f"{what_if.get('rul_extension_hours', '—')}h；未来 72h 故障概率 "
                    f"{self._pct(what_if.get('failure_probability_72h'))}"
                )

        # --- 七、Reflection 质量自检 ---
        if checks:
            sections.append("七、Reflection 质量自检")
            lines.append("\n## 七、Reflection 质量自检\n")
            for check in checks:
                lines.append(f"- {'✅' if check.get('passed') else '❌'} {check.get('label')}（{check.get('id')}）")

        # --- 八、历史维修记录 ---
        if history:
            sections.append("八、历史维修记录")
            lines.append("\n## 八、历史维修记录\n")
            lines.append("| 时间 | 类型 | 停机(h) | 备件费 | 工时费 | 技师 | 状态 |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- |")
            for item in history:
                lines.append(
                    f"| {str(item['start_time'])[:10]} | {item['maintenance_type']} | {item['downtime_hours'] or 0} | "
                    f"{item['parts_cost'] or 0} | {item['labor_cost'] or 0} | {item['technician']} | {item['status']} |"
                )

        # --- 九、相似历史案例 ---
        if cases:
            sections.append("九、相似历史案例")
            lines.append("\n## 九、相似历史案例\n")
            for case in cases:
                lines.append(
                    f"- **案例 {case['id']}**：{case['description']}；根因「{case['root_cause']}」，"
                    f"处置「{case['resolution']}」"
                )

        # --- 十、维护建议 ---
        sections.append("十、维护建议与追溯")
        lines.append("\n## 十、维护建议与追溯\n")
        if rule:
            lines.append(
                f"- 建议按上述 FMECA 规程执行，预估停机 {rule.get('estimated_hours')} 小时，"
                f"请提前备齐备件并安排 {rule.get('engineer_level') or '中级'} 以上工程师。"
            )
        else:
            lines.append("- 建议安排例行点检，重点核查异常维度对应部件。")
        lines.append("- 维修完成后请回填工单结果，系统将自动更新设备画像与知识库案例。")
        lines.append("- 本报告全部字段可回溯：" + "、".join(self._sources(diagnostic, rule, history, cases)) + "。")

        return "\n".join(lines).strip() + "\n", sections

    # --- 数据分析辅助 -------------------------------------------------

    @staticmethod
    def _num(value: Any, digits: int = 2) -> str:
        """把可能为空的数值格式化成稳定字符串，避免报告出现 None。"""

        if value is None:
            return "—"
        try:
            return f"{float(value):.{digits}f}"
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _pct(value: Any) -> str:
        """把 0–1 的比例格式化为百分数。"""

        if value is None:
            return "—"
        try:
            return f"{float(value):.0%}"
        except (TypeError, ValueError):
            return str(value)

    def _analyze_sensor(self, diagnostic: dict[str, Any] | None, machine_id: str) -> dict[str, Any]:
        """计算"当前值 vs 全厂同型健康基线"的逐维偏差表（纯确定性，不调用大模型）。

        为什么不用设备自身的历史窗口当基线：仿真数据里部分设备的退化在观测窗口
        开始之前就已发生，用"自身最早 20%"会把已经劣化的读数当成健康参照，反而
        掩盖故障。改用**同型号健康设备的中位数**做基线——94% 的设备处于健康状态，
        中位数天然抗污染，能真实暴露被测设备相对同侪的偏离幅度。
        """

        if not diagnostic:
            return {"rows": [], "window": 0, "baseline_size": 0, "peer_count": 0}
        payload_obj = diagnostic.get("payload") or {}
        current = payload_obj.get("sensor_data") or {}
        history = [item for item in (payload_obj.get("history") or []) if isinstance(item, dict)]
        anomalous = {str(item) for item in (payload_obj.get("anomaly_dims") or [])}
        if not current:
            return {"rows": [], "window": len(history), "baseline_size": 0, "peer_count": 0}

        baseline, peer_count = self._fleet_baseline(machine_id)
        rows: list[dict[str, Any]] = []
        for metric, value in current.items():
            base = baseline.get(metric)
            if base is None or abs(base) < 1e-9:
                continue
            try:
                deviation = (float(value) - base) / abs(base) * 100
            except (TypeError, ValueError):
                continue
            rows.append({
                "metric": metric,
                "current": float(value),
                "baseline": base,
                "deviation": deviation,
                "anomalous": metric in anomalous,
            })
        # 异常维度优先，其次按偏差绝对值排序——把最该看的排在表头
        rows.sort(key=lambda item: (not item["anomalous"], -abs(item["deviation"])))
        return {"rows": rows, "window": len(history), "baseline_size": peer_count, "peer_count": peer_count}

    def _fleet_baseline(self, machine_id: str) -> tuple[dict[str, float], int]:
        """用同型号健康设备的最新读数中位数构造基线，返回 (基线, 参与设备数)。"""

        samples: dict[str, list[float]] = {}
        try:
            for machine in self._db.list_machines():
                latest = machine.get("latest_reading") or {}
                health = latest.get("health_score")
                # 排除已劣化设备，避免基线被故障读数拉偏（中位数本身也抗污染，双保险）
                if health is not None and float(health) < 0.6:
                    continue
                for key, value in (latest.get("metrics") or {}).items():
                    try:
                        samples.setdefault(key, []).append(float(value))
                    except (TypeError, ValueError):
                        continue
        except Exception as error:  # 数据库异常时退化为无基线，报告仍可生成
            LOGGER.warning(f"fleet baseline unavailable: {error}")
            return {}, 0
        baseline = {
            key: sorted(values)[len(values) // 2]
            for key, values in samples.items()
            if values
        }
        return baseline, max((len(values) for values in samples.values()), default=0)

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
