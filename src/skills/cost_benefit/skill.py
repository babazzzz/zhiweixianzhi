"""Maintenance what-if and cost-benefit calculations from machine economics.

成本口径说明（避免"数字好看但口径不透明"）
------------------------------------------
* 直接成本 ``direct`` = 备件费 + 工时费。备件费按 FMECA 规则里的备件数量折算，
  工时费固定 360 元/小时；没有匹配规则时退化为 2 小时通用检修。
* 停机损失 ``downtime_loss`` = 日产量 × 单位成本 ÷ 24 × 维修工时。
* 故障损失 ``failure_loss`` = 两天产值 + 直接成本 × 1.8（含次生损伤与交期违约）。
* 风险概率 = min(0.95, 72 / RUL_P50) —— 即"剩余寿命撑不过 72 小时"的粗略概率。

三个候选策略共用同一套口径，因此横向可比；推荐项取**总成本最低**者。
"""

from __future__ import annotations

from typing import Any, Mapping

from ..base import BaseSkill, SkillExample, SkillMeta
from .schema import CostBenefitInput, CostBenefitOutput, DecisionOption, FmecaEffort, MachineEconomics

#: 工时单价（元/小时），集中定义便于后续按地区/工种细化
LABOR_RATE_PER_HOUR = 360.0
#: 单件备件折算价（元）
PART_UNIT_COST = 1500.0


def analyze_cost_benefit(
    machine: Mapping[str, object],
    rul: Mapping[str, object],
    rule: Mapping[str, object] | None,
) -> dict[str, Any]:
    """Compare immediate repair, scheduled repair and derated operation.

    保留 dict 版本的函数签名是为了兼容 ``decision_agent`` 的既有调用方式；
    新的调用方请直接使用 :meth:`CostBenefitSkill.run`（返回强类型 pydantic 模型）。
    """

    effort = None
    if rule:
        parts = rule.get("required_parts") or []
        effort = FmecaEffort(
            estimated_hours=float(rule.get("estimated_hours", 2.0) or 2.0),
            required_parts=[str(item) for item in parts] if isinstance(parts, (list, tuple)) else [],
        )
    payload = CostBenefitInput(
        machine=MachineEconomics(
            daily_output=float(machine.get("daily_output", 0) or 0),
            unit_cost=float(machine.get("unit_cost", 0) or 0),
        ),
        rul_p50_hours=max(4.0, float(rul.get("rul_p50", 4) or 4)),
        rule=effort,
    )
    return CostBenefitSkill().run(payload).model_dump()


class CostBenefitSkill(BaseSkill):
    """Price three maintenance strategies and recommend the cheapest one."""

    meta = SkillMeta(
        id="cost_benefit",
        name="维护成本收益分析",
        version="2.0.0",
        description=(
            "基于设备产量/成本与 RUL 区间，对「立即维修 / 计划窗口维修 / 减载运行20%」三种策略"
            "统一定价并给出总成本最低的推荐，同时输出 72h 故障概率等 What-if 指标。"
            "纯确定性计算，不调用大模型。"
        ),
        category="operations",
        deterministic=True,
        requires_llm=False,
        tags=("cost", "decision", "what-if", "E7"),
        examples=(
            SkillExample(
                description="日产 1200 件、单件成本 8 元的设备，剩余寿命 60 小时，轴承更换需 4.5 小时",
                payload={
                    "machine": {"daily_output": 1200, "unit_cost": 8.0},
                    "rul_p50_hours": 60.0,
                    "rule": {"estimated_hours": 4.5, "required_parts": ["SKF 7014CDGA/P4A×2"]},
                },
            ),
        ),
    )
    input_schema = CostBenefitInput
    output_schema = CostBenefitOutput

    def run(self, payload: CostBenefitInput) -> CostBenefitOutput:
        """Price the three strategies and return the recommendation."""

        daily_output = payload.machine.daily_output
        unit_cost = payload.machine.unit_cost
        hours = payload.rule.estimated_hours if payload.rule else 2.0
        parts = payload.rule.required_parts if payload.rule else []

        parts_cost = PART_UNIT_COST * len(parts)
        labor = hours * LABOR_RATE_PER_HOUR
        direct = parts_cost + labor
        downtime_loss = daily_output * unit_cost / 24.0 * hours

        p50 = payload.rul_p50_hours
        risk_probability = min(0.95, 72.0 / p50)
        failure_loss = daily_output * unit_cost * 2.0 + direct * 1.8

        immediate = DecisionOption(
            name="立即维修", downtime_hours=hours,
            direct_cost=round(direct, 2), risk_cost=0.0,
            total_cost=round(direct + downtime_loss, 2),
        )
        scheduled = DecisionOption(
            name="计划窗口维修", downtime_hours=hours,
            direct_cost=round(direct, 2), risk_cost=round(risk_probability * failure_loss, 2),
            total_cost=round(direct + risk_probability * failure_loss + downtime_loss * 0.6, 2),
        )
        derated = DecisionOption(
            name="减载运行20%", downtime_hours=0.0,
            direct_cost=round(direct * 0.25, 2), risk_cost=round(risk_probability * 0.75 * failure_loss, 2),
            total_cost=round(direct * 0.25 + risk_probability * 0.75 * failure_loss + daily_output * unit_cost * 0.2, 2),
        )

        options = [immediate, scheduled, derated]
        recommended = min(options, key=lambda item: item.total_cost)
        return CostBenefitOutput(
            recommendation=recommended.name,
            timing="立即" if recommended is immediate else "72小时内" if recommended is scheduled else "减载至维护窗口",
            action="repair" if recommended is not derated else "derate",
            downtime_hours=hours,
            direct_cost=round(direct, 2),
            risk_cost=round(risk_probability * failure_loss, 2),
            net_benefit=round(max(item.total_cost for item in options) - recommended.total_cost, 2),
            options=options,
            what_if={
                "derated_pct": 20.0,
                "rul_extension_hours": round(p50 * 0.40, 1),
                "failure_probability_72h": round(risk_probability * 0.75, 4),
            },
            parts=list(parts),
            estimated_hours=hours,
        )
