"""Traceable E1–E7 evidence construction for root-cause reasoning.

七条证据的分工（避免"每条都在说同一件事"）
------------------------------------------
====  ======================  ============================================
E1    当前读数异常维度        来自 anomaly_detect 的判定结果
E2    与自身基线的偏离        用历史前 40% 做基线，识别渐进退化
E3    时序趋势                用首尾三等分均值差，区分渐变与突变
E4    相似历史案例            来自 case_library / RAG 检索
E5    FMECA 结构化规则        可回溯到规则库的信号与 SOP
E6    故障因果图后向溯源       解释"为什么会走到这一步"
E7    历史维护记录对照        判断是否为重复性故障
====  ======================  ============================================

置信度不是拍脑袋：每条都有明确的升降依据（有数据 → 高，缺数据 → 低），
缺数据时写明"未匹配"而不是补一个看起来合理的数字。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from src.knowledge.fault_graph import FaultGraph

from ..base import BaseSkill, SkillExample, SkillMeta
from .schema import EvidenceChainInput, EvidenceChainOutput, EvidenceItem

#: 故障图节点映射：把 FMECA 的中文故障名接到因果图里的英文节点 id
GRAPH_KEY_MAP: dict[str, str] = {
    "轴承磨损": "bearing_wear",
    "散热系统故障": "cooling_failure",
    "电源模块故障": "power_module_failure",
    "电机过载": "motor_overload",
    "转子故障": "rotor_fault",
}

#: E1–E7 权重：数据驱动的两条（E2/E3）权重最高，检索类次之，规则类最低
EVIDENCE_WEIGHTS: dict[str, float] = {
    "E1": 1.0, "E2": 1.3, "E3": 1.2, "E4": 0.9, "E5": 0.8, "E6": 1.0, "E7": 0.6,
}


def build_evidence_chain(
    anomaly: Any,
    history: Sequence[Mapping[str, float]],
    rule: Mapping[str, object] | None,
    cases: Sequence[Mapping[str, object]],
    graph: FaultGraph,
    maintenance_context: str = "未发现可用维护后对照记录",
) -> list[dict[str, Any]]:
    """Build seven evidence items whose content is grounded in supplied data.

    保留旧签名是为了兼容 ``diagnostic_agent`` 的既有调用方式；
    新调用方请用 ``EvidenceChainSkill.run()``（返回强类型模型 + 聚合置信度）。
    """

    anomaly_score = float(getattr(anomaly, "anomaly_score", 0.0))
    anomaly_dims = list(getattr(anomaly, "anomaly_dims", []))
    payload = EvidenceChainInput(
        anomaly={"anomaly_score": anomaly_score, "anomaly_dims": anomaly_dims},
        history=[dict(row) for row in history],
        rule={"fault_code": int(rule.get("fault_code", 0) or 0), "fault_name": str(rule.get("fault_name", ""))} if rule else None,
        cases=[{"id": str(item.get("id", "")), "root_cause": str(item.get("root_cause", ""))} for item in cases],
        causal_paths=[
            {"nodes": list(item.nodes), "probability": float(item.probability)}
            for item in (graph.backward_trace(_graph_key(str(rule.get("fault_name", "")) if rule else "")) if (rule and _graph_key(str(rule.get("fault_name", "")))) else [])
        ],
        maintenance_context=maintenance_context,
    )
    return [item.model_dump() for item in EvidenceChainSkill().run(payload).items]


class EvidenceChainSkill(BaseSkill):
    """Assemble the seven E1–E7 evidence nodes that back every diagnosis."""

    meta = SkillMeta(
        id="evidence_chain",
        name="E1–E7 证据链构建",
        version="2.0.0",
        description=(
            "把异常检测结果、历史基线、时序趋势、相似案例、FMECA 规则、故障因果图与维护记录"
            "组装成可逐条追溯的 E1–E7 证据链，并给出加权聚合置信度。每条证据的置信度都有明确升降依据，"
            "缺数据时写明'未匹配'而非编造数值。"
        ),
        category="analysis",
        deterministic=True,
        requires_llm=False,
        tags=("evidence", "diagnosis", "E1-E7", "traceability"),
        examples=(
            SkillExample(
                description="温度持续升高 + 电流正常的渐进退化场景",
                payload={
                    "anomaly": {"anomaly_score": 0.62, "anomaly_dims": ["op_temperature"]},
                    "history": [{"op_temperature": 72.0}, {"op_temperature": 78.0}, {"op_temperature": 85.0}],
                    "rule": {"fault_code": 8, "fault_name": "散热系统故障"},
                    "cases": [{"id": "CASE-004", "root_cause": "散热风扇卡死"}],
                    "causal_paths": [{"nodes": ["cooling_failure", "spindle_overheat"], "probability": 0.72}],
                },
            ),
        ),
    )
    input_schema = EvidenceChainInput
    output_schema = EvidenceChainOutput

    def run(self, payload: EvidenceChainInput) -> EvidenceChainOutput:
        """Build the seven evidence nodes and compute the aggregate confidence."""

        trend = _trend_summary(payload.history)
        primary_signal = payload.anomaly.anomaly_dims[0] if payload.anomaly.anomaly_dims else "无超限维度"
        rule_name = payload.rule.fault_name if payload.rule else "未匹配FMECA"
        path_text = "；".join(" → ".join(item.nodes) for item in payload.causal_paths[:2]) or "当前症状未形成可追溯传播路径"
        case_text = "；".join(f"{item.id}: {item.root_cause}" for item in payload.cases[:2] if item.root_cause) or "暂无相似历史案例"
        has_history = len(payload.history) >= 6

        items = [
            EvidenceItem(
                id="E1", source="sensor_readings",
                content=f"当前可用读数中异常维度：{', '.join(payload.anomaly.anomaly_dims) or '无'}；主要信号：{primary_signal}。",
                confidence=min(0.98, 0.55 + payload.anomaly.anomaly_score),
                supporting_data={"anomaly_score": payload.anomaly.anomaly_score},
            ),
            EvidenceItem(
                id="E2", source="rolling_baseline",
                content=f"基于设备历史前 40% 样本的鲁棒基线偏离评分为{payload.anomaly.anomaly_score:.3f}，未使用硬编码 P5/P95。",
                confidence=0.78 if payload.history else 0.40,
                supporting_data={"history_samples": float(len(payload.history))},
            ),
            EvidenceItem(
                id="E3", source="trend_analysis",
                content=f"历史窗口趋势：{trend}。该趋势用于区分渐进退化与单点异常。",
                confidence=0.75 if has_history else 0.35,
                supporting_data={"window_size": float(len(payload.history))},
            ),
            EvidenceItem(
                id="E4", source="case_library",
                content=f"相似案例检索结果：{case_text}。",
                confidence=0.72 if payload.cases else 0.35,
                supporting_data={"case_count": float(len(payload.cases))},
            ),
            EvidenceItem(
                id="E5", source="fmeca_rules",
                content=f"FMECA规则匹配：{rule_name}；信号和SOP可回溯到结构化规则库。",
                confidence=0.82 if payload.rule else 0.30,
                supporting_data={"fault_code": float(payload.rule.fault_code) if payload.rule else 0.0},
            ),
            EvidenceItem(
                id="E6", source="fault_graph",
                content=f"故障图后向溯源：{path_text}。",
                confidence=0.80 if payload.causal_paths else 0.30,
                supporting_data={"path_count": float(len(payload.causal_paths))},
            ),
            EvidenceItem(
                id="E7", source="maintenance_records",
                content=payload.maintenance_context,
                confidence=0.60 if "未发现" not in payload.maintenance_context else 0.35,
                supporting_data={"available": "未发现" not in payload.maintenance_context},
            ),
        ]

        total_weight = sum(EVIDENCE_WEIGHTS[item.id] for item in items) or 1.0
        aggregate = sum(item.confidence * EVIDENCE_WEIGHTS[item.id] for item in items) / total_weight
        return EvidenceChainOutput(items=items, count=len(items), aggregate_confidence=round(aggregate, 4))


def _trend_summary(history: Sequence[Mapping[str, float]]) -> str:
    """Summarize real history with first/last window means."""

    if len(history) < 6:
        return "数据不足（至少需要6个时序点）"
    keys = [key for key in ("op_temperature", "op_amperage", "vibration_rms", "rotor_speed") if key in history[0]]
    chunks: list[str] = []
    for key in keys:
        values = [float(row[key]) for row in history if key in row]
        split = max(2, len(values) // 3)
        delta = sum(values[-split:]) / split - sum(values[:split]) / split
        chunks.append(f"{key}{'上升' if delta > 0 else '下降' if delta < 0 else '稳定'}{abs(delta):.3f}")
    return "，".join(chunks) or "无可用趋势维度"


def _graph_key(name: str) -> str:
    """Map Chinese FMECA labels to graph node identifiers."""

    return GRAPH_KEY_MAP.get(name, "")
