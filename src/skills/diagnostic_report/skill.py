"""Deterministic Markdown rendering of one diagnostic report.

设计取舍
--------
报告是**纯字符串组装**，不调用大模型。这样做的好处：

* 同样一次诊断，任何时候重新渲染都得到同一份报告（可复现、可审计）；
* 不会因为模型抽风而在正式报告里出现编造的读数或维修记录；
* 生成耗时稳定在毫秒级，不会拖慢诊断主流程。

需要"语言润色"时，应由上层显式调用 LLM 并把润色结果标注出来，而不是混在这里。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from ..base import BaseSkill, SkillExample, SkillMeta
from .schema import DiagnosticReportInput, DiagnosticReportOutput, ReportDecision, ReportEvidence

#: 严重度 → 中文标签，报告里统一使用中文，避免 normal/critical 混排
SEVERITY_LABELS: dict[str, str] = {
    "normal": "正常", "warning": "警告", "critical": "紧急", "emergency": "特急", "offline": "离线",
}


def generate_report(
    state: Mapping[str, object],
    evidence: Sequence[Mapping[str, object]],
    decision: Mapping[str, object],
) -> dict[str, Any]:
    """Create a Markdown report from state without inventing missing measurements.

    保留旧签名以兼容 ``report_agent``；新调用方请用 ``DiagnosticReportSkill.run()``。
    """

    payload = DiagnosticReportInput(
        machine_id=str(state.get("machine_id", "unknown")),
        fault_mode=str(state.get("fault_mode", "未检测到明确故障")),
        root_cause=str(state.get("root_cause", "未形成可证实根因")),
        severity=str(state.get("severity", "normal")),
        confidence=float(state.get("confidence", 0.0) or 0.0),
        rul={str(key): value for key, value in dict(state.get("rul", {}) or {}).items()},
        evidence=[ReportEvidence(**{k: v for k, v in dict(item).items() if k in ReportEvidence.model_fields}) for item in evidence],
        decision=ReportDecision(**{k: v for k, v in dict(decision).items() if k in ReportDecision.model_fields}) if decision else None,
    )
    return DiagnosticReportSkill().run(payload).model_dump()


class DiagnosticReportSkill(BaseSkill):
    """Render one diagnostic run into a consistent, auditable Markdown report."""

    meta = SkillMeta(
        id="diagnostic_report",
        name="诊断报告渲染",
        version="2.0.0",
        description=(
            "把一次诊断的完整状态（故障模式、根因、RUL 区间、E1–E7 证据链、维护决策）"
            "渲染为固定结构的 Markdown 报告。纯字符串组装、不调用大模型，因此可复现、可审计、毫秒级。"
        ),
        category="report",
        deterministic=True,
        requires_llm=False,
        tags=("report", "markdown", "diagnosis"),
        examples=(
            SkillExample(
                description="渲染一份轴承磨损的紧急诊断报告",
                payload={
                    "machine_id": "CNC_007",
                    "fault_mode": "轴承磨损",
                    "root_cause": "滚道疲劳伴随润滑不足",
                    "severity": "critical",
                    "confidence": 0.86,
                    "rul": {"rul_p10": 96, "rul_p50": 72, "rul_p90": 50},
                },
            ),
        ),
    )
    input_schema = DiagnosticReportInput
    output_schema = DiagnosticReportOutput

    def run(self, payload: DiagnosticReportInput) -> DiagnosticReportOutput:
        """Render the report and return it with metadata."""

        rul = payload.rul
        severity_label = SEVERITY_LABELS.get(payload.severity, payload.severity)
        lines = [
            f"# 智维先知诊断报告 — {payload.machine_id}",
            f"生成时间：{datetime.now().astimezone().isoformat()}",
            "",
            "## 诊断摘要",
            f"- 故障模式：{payload.fault_mode}",
            f"- 根因：{payload.root_cause}",
            f"- 严重度：{severity_label}（{payload.severity}）",
            f"- 置信度：{payload.confidence:.1%}",
            "",
            "## RUL 区间",
            f"- P10：{rul.get('rul_p10', '—')} h（乐观）",
            f"- P50：{rul.get('rul_p50', '—')} h",
            f"- P90：{rul.get('rul_p90', '—')} h（保守）",
            "",
            "## E1–E7 证据链",
        ]
        if payload.evidence:
            lines.extend(
                f"- **{item.id}** [{item.source}] {item.content}（置信度 {item.confidence:.0%}）"
                for item in payload.evidence
            )
        else:
            lines.append("- 本次未形成证据链（读数未触发异常阈值）")

        lines.extend(["", "## 维护决策"])
        if payload.decision:
            decision = payload.decision
            lines.extend([
                f"- 推荐：{decision.recommendation}",
                f"- 窗口：{decision.timing}",
                f"- 预计停机：{decision.downtime_hours} h",
                f"- 直接成本：¥{decision.direct_cost}",
                f"- 净收益：¥{decision.net_benefit}",
            ])
            if decision.parts:
                lines.append(f"- 备件：{'、'.join(decision.parts)}")
        else:
            lines.append("- 当前读数未触发异常，未生成维护决策。")

        lines.extend(["", "> 本报告由确定性算法与结构化知识生成；LLM 仅用于可选的语言润色，不参与数值生成。"])
        markdown = "\n".join(lines)
        return DiagnosticReportOutput(
            title=f"{payload.machine_id} 诊断报告",
            markdown=markdown,
            generated_at=datetime.now().astimezone().isoformat(),
            char_count=len(markdown),
        )
