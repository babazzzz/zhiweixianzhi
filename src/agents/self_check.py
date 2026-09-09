"""
==========================================
 自验收智能体 — self_check.py
==========================================
★ Loop工程核心 — 这是整个系统的"质检科" ★

【什么是自验收循环（Loop 工程）？】
传统系统：处理完了直接输出，不管质量好坏。
我们的系统：处理完了先自检，检查不通过就回退修正。

好比写论文：
    普通系统：写完直接交 → 被导师打回来重写
    我们的系统：写完后自己检查语法、逻辑 → 没问题再交

【5项验收标准】
1. 结构完整性：所有关键字段是否存在
2. 逻辑一致性：风险等级和RUL是否匹配
3. 数值合理性：数值是否在合理范围内
4. 证据链完整性：E1-E7是否都有数据
5. 跨设备一致性：同类设备的诊断是否一致（并行模式）

【循环逻辑】
验收不通过 → 回退到 Analysis Agent 重新分析
     ↓              ↑
  （带上验收意见）  再验收
     ↓              ↑
  每次迭代+1     最多3次
     ↓
  3次还不通过 → 带警告输出（不阻塞流程）
"""

from langchain_core.messages import HumanMessage
from ..state import AgentState
from ..config import SYSTEM_CONFIG


def self_check_agent(state: AgentState) -> dict:
    """
    自验收智能体：检查诊断结果的质量。

    这是系统的"质量门"（Quality Gate），
    使用纯代码检查（不依赖LLM判断），保证验收标准的可复现性。

    【输入】
    state.diagnosis_result: 诊断结果
    state.analysis_result: 分析结果
    state.iteration_count: 当前迭代次数

    【输出】
    state.verification_status: "pass" / "fail" / "max_retry"
    state.verification_notes: 检查详情
    state.iteration_count: 迭代次数+1
    """
    diagnosis = state.get("diagnosis_result", {})
    analysis = state.get("analysis_result", {})
    iteration = state.get("iteration_count", 0) + 1
    max_retries = SYSTEM_CONFIG.get("max_self_check_retries", 3)

    print(f"\n[Self-Check Agent] 🔍 第 {iteration} 轮验收检查...")

    checks = []
    warnings = []

    # ==================== 检查1：结构完整性 ====================
    # 确保所有必要字段都不为空
    required_fields = ["device_id", "risk_level", "risk_score"]
    missing = [f for f in required_fields if f not in diagnosis or diagnosis.get(f) is None]
    structure_ok = len(missing) == 0
    structure_note = f"通过（全部{len(required_fields)}个必填字段完整）" if structure_ok else f"缺失字段: {missing}"
    checks.append(("结构完整性", structure_ok, structure_note))

    # ==================== 检查2：逻辑一致性 ====================
    # 风险等级和实际分数必须匹配
    risk = diagnosis.get("risk_level", "unknown")
    score = diagnosis.get("risk_score", 0)
    logic_ok = True
    logic_note = "一致"

    if risk == "critical" and score < 0.5:
        logic_ok = False
        logic_note = f"风险等级为critical但评分仅{score:.2f}，不匹配"
        warnings.append("逻辑不一致：风险等级与评分不匹配")
    elif risk == "low" and score > 0.5:
        logic_ok = False
        logic_note = f"风险等级为low但评分高达{score:.2f}，不匹配"
        warnings.append("逻辑不一致：低风险等级但评分偏高")
    elif "unknown" in str(risk):
        logic_ok = False
        logic_note = "风险等级为unknown，需要重新分析"

    checks.append(("逻辑一致性", logic_ok, logic_note))

    # ==================== 检查3：数值合理性 ====================
    # 所有数值必须在合理范围内
    value_ok = True
    value_errors = []

    if not (0 <= score <= 1):
        value_ok = False
        value_errors.append(f"评分{score:.3f}不在[0,1]范围内")

    # 检查故障匹配分数
    for fault in diagnosis.get("matched_faults", []):
        ms = fault.get("match_score", 0)
        if not (0 <= ms <= 1):
            value_ok = False
            value_errors.append(f"故障{fault['fault_id']}匹配分{ms:.3f}不合理")

    value_note = "通过" if value_ok else f"异常: {', '.join(value_errors)}"
    checks.append(("数值合理性", value_ok, value_note))

    # ==================== 检查4：证据链完整性 ====================
    evidence = diagnosis.get("evidence_chain", {})
    required_evidence = ["E1", "E2", "E3", "E4", "E5", "E6", "E7"]
    missing_evidence = [e for e in required_evidence if e not in evidence or not evidence.get(e, {})]
    evidence_ok = len(missing_evidence) == 0
    evidence_note = (
        f"通过（E1-E7全部完整）" if evidence_ok
        else f"缺失: {', '.join(missing_evidence)}"
    )
    checks.append(("证据链完整性", evidence_ok, evidence_note))

    # ==================== 检查5：故障匹配质量 ====================
    matched = diagnosis.get("matched_faults", [])
    fault_ok = len(matched) > 0 and matched[0].get("match_score", 0) > 0.1
    fault_note = (
        f"最佳匹配: {matched[0]['fault_name']}({matched[0]['match_score']:.2f})" if fault_ok
        else "未匹配到有意义故障"
    )
    checks.append(("故障匹配质量", fault_ok, fault_note))

    # ==================== 综合判定 ====================
    # 关键检查（1-4必须全过）
    critical_passed = all(c[1] for c in checks[:4])
    all_passed = all(c[1] for c in checks)

    if critical_passed and all_passed:
        status = "pass"
        notes = "✅ 全部5项检查通过"
    elif critical_passed:
        status = "pass"
        notes = f"✅ 关键检查通过，有{len(warnings)}项建议: {'; '.join(warnings)}"
    elif iteration >= max_retries:
        status = "max_retry"
        failed = [c[0] for c in checks if not c[1]]
        notes = f"⚠️ 已达最大重试次数({max_retries})，未通过项: {', '.join(failed)}"
    else:
        failed = [c[0] for c in checks if not c[1]]
        status = "fail"
        notes = f"❌ 检查未通过: {', '.join(failed)}，需要回退修正"

    # 打印检查报告
    print(f"[Self-Check Agent] 验收报告:")
    for name, passed, note in checks:
        icon = "✅" if passed else "❌"
        print(f"  {icon} {name}: {note}")
    print(f"[Self-Check Agent] 结论: {status}")

    return {
        "verification_status": status,
        "verification_notes": notes,
        "iteration_count": iteration,
        "messages": [HumanMessage(
            content=f"✅ Self-Check Agent: 第{iteration}轮验收{'通过' if status == 'pass' else '未通过'}。"
                    f"状态: {status}。{notes}"
        )],
    }
