"""
==========================================
 LangGraph 状态图构建 — build.py
==========================================
★ 这是整个 LangGraph 框架的核心 ★
★ 理解了这个文件，你就理解了 LangGraph ★

【LangGraph 三要素】
1. State（状态）     — 整个系统的"共享黑板"
   → 见 state.py

2. Node（节点）      — 在黑板上"写东西"的人
   → 每个智能体 = 一个节点
   → 每个节点是一个函数：输入 State，输出 State 的部分更新

3. Edge（边）        — 谁写完传给谁
   → 普通边：A → B（A做完自动传给B）
   → 条件边：A → 判断 → B或C（根据结果选择路径）
   → 并行边：A → 同时传给B和C（并行处理）

【状态图 vs 传统程序】
传统程序：
    result_a = func_a(input)      # 线性执行
    result_b = func_b(result_a)    # 必须等a完成
    result_c = func_c(result_b)    # 必须等b完成

LangGraph 状态图：
    节点A → 节点B → 节点C         # 也是线性
    节点A ─┬→ 节点B               # 也是并行！
           └→ 节点C
    节点B → 判断 → 通过→ 节点D    # 还能循环！
                → 不通过→ 节点B   # 自验收循环！

【为什么用图而不是函数调用？】
1. 并行执行：多个设备可以同时分析
2. 循环控制：自验收不通过时自动回退
3. 可视化：图结构天然可画图展示
4. 可中断：可以在任意节点暂停/恢复
5. 流式输出：可以边执行边推送结果
"""

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage
from ..state import AgentState, create_initial_state
from ..agents.supervisor import supervisor_agent
from ..agents.data_pipeline import data_pipeline_agent
from ..agents.analysis import analysis_agent
from ..agents.diagnosis import diagnosis_agent
from ..agents.decision import decision_agent
from ..agents.self_check import self_check_agent
from ..agents.rag import rag_agent
from ..tools.type_converter import to_native


# ==========================================
# 条件边路由函数
# ==========================================

def wrap_node(node_fn):
    """包装节点函数，自动转换 numpy 类型为 Python 原生类型"""
    def wrapped(state):
        result = node_fn(state)
        return to_native(result)
    return wrapped


def route_after_check(state: AgentState) -> str:
    """
    自验收循环的路由函数。

    【什么是条件边？】
    条件边 = "根据当前状态决定下一步走哪条路"

    LangGraph 的条件边函数接收 state，返回一个字符串，
    这个字符串对应图中某条边的名称。

    【路由逻辑】
    pass      → 验收通过，去生成报告
    fail      → 验收未通过，回退到 Analysis 重新分析
    max_retry → 超过最大重试次数，带警告输出

    【为什么需要条件边？】
    如果没有条件边，Sellf-Check 只能做"检查"不能做"回退"。
    条件边让系统有了"决策能力"——这是 LangGraph 的核心优势。
    """
    status = state.get("verification_status", "fail")

    if status == "pass":
        print("[路由] 验收通过 → 生成最终报告")
        return "pass"
    elif status == "max_retry":
        print("[路由] 已达最大重试 → 带警告输出报告")
        return "pass"  # 虽然失败但放行
    else:
        print(f"[路由] 验收不通过(第{state.get('iteration_count', 0)}轮) → 回退重新分析")
        return "fail"


# ==========================================
# 报告生成函数（图中的一个"节点"）
# ==========================================

def report_generator(state: AgentState) -> dict:
    """
    生成最终的分析报告。

    这是流程图中的最后一个节点——把前面的所有结果汇总成一份报告。
    不需要LLM调用，纯Python拼接，确保报告格式一致。
    """
    device_ids = state.get("device_ids", [])
    diagnosis = state.get("diagnosis_result", {})
    decision = state.get("decision_result", {})
    analysis = state.get("analysis_result", {})
    evidence = diagnosis.get("evidence_chain", {})
    verification_notes = state.get("verification_notes", "")
    iteration = state.get("iteration_count", 0)

    device_id = diagnosis.get("device_id", device_ids[0] if device_ids else "?")

    # 生成报告
    report = f"""# 智维先知 — 设备预测性维护分析报告

## 📋 基本信息
- **设备ID**: {device_id}
- **分析时间**: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
- **自验收轮次**: {iteration}/3

## 🚨 风险总览
- **风险等级**: **{diagnosis.get('risk_level', 'N/A').upper()}**
- **综合评分**: {diagnosis.get('risk_score', 0):.3f}
- **健康指标**: {diagnosis.get('healthy_count', 0)}正常 / {diagnosis.get('warning_count', 0)}告警 / {diagnosis.get('critical_count', 0)}危急

## 🔍 主要故障
- **故障类型**: {diagnosis.get('primary_fault', {}).get('fault_name', '未检测到')}
- **相关系统**: {diagnosis.get('primary_fault', {}).get('system', 'N/A')}
- **相关部件**: {diagnosis.get('primary_fault', {}).get('part', 'N/A')}
- **严重程度**: {diagnosis.get('primary_fault', {}).get('severity', 'N/A')}

## 📊 证据链 (E1-E7)
"""
    for key, val in evidence.items():
        status_icon = {"normal": "✅", "warning": "⚠️", "critical": "🔴"}
        icon = status_icon.get(val.get("status", "normal"), "❓")
        report += f"- **{key}**: {icon} {val.get('status', 'N/A')} (评分: {val.get('score', 0):.3f})\n"
        report += f"  - {val.get('detail', '')}\n"

    report += f"""
## 💰 决策建议
- **建议行动**: {decision.get('action', 'N/A')}
- **优先级**: {decision.get('action_priority', 'N/A')}
- **预防性维护成本**: {decision.get('cost_analysis', {}).get('preventive_cost', 'N/A')} 元
- **纠正性维护成本**: {decision.get('cost_analysis', {}).get('corrective_cost', 'N/A')} 元
- **可节省**: {decision.get('cost_analysis', {}).get('savings', 0)} 元

## 📝 自验收记录
{verification_notes}

## 📌 推荐理由
{decision.get('recommendation', 'N/A')}
"""

    # 图表数据
    charts_data = _build_charts_data(diagnosis)

    print(f"[Report] ✅ 报告生成完成")

    return {
        "final_report": report,
        "charts_data": charts_data,
        "messages": [HumanMessage(content="📄 Report Generator: 分析报告已生成")],
    }


def _build_charts_data(diagnosis: dict) -> dict:
    """构建前端图表所需的数据"""
    evidence = diagnosis.get("evidence_chain", {})
    return {
        "radar_chart": {
            "indicators": [
                {"name": k, "max": 1.0, "value": v.get("score", 0)}
                for k, v in evidence.items()
            ],
        },
        "risk_score": diagnosis.get("risk_score", 0),
        "risk_level": diagnosis.get("risk_level", "unknown"),
        "evidence_labels": list(evidence.keys()),
        "evidence_scores": [v.get("score", 0) for v in evidence.values()],
        "healthy_count": diagnosis.get("healthy_count", 0),
        "warning_count": diagnosis.get("warning_count", 0),
        "critical_count": diagnosis.get("critical_count", 0),
    }


# ==========================================
# 构建完整状态图
# ==========================================

def build_analysis_graph() -> StateGraph:
    """
    构建完整的 LangGraph 状态图。

    【返回】
    StateGraph 实例（已编译，可直接执行）

    【图结构（可视化）】
    START
      ↓
    [Supervisor]           ← 任务调度中心
      ↓
    [Data Pipeline]        ← 数据加载+特征工程
      ↓
    [Analysis]             ← ReAct异常检测+RUL预测
      ↓
    [Diagnosis]            ← 故障诊断+证据链
      ↓
    [Decision]             ← 成本分析+工单生成
      ↓
    [Self-Check]           ← 自验收（质量门）
      ↓                    ↘
    通过 → [RAG] → [Report] → END
    不通过 → 回退到 [Analysis]（最多3次）
    """
    print("[Graph] 正在构建状态图...")

    # Step 1: 创建图实例
    # AgentState 定义了整个图的"共享状态"结构
    graph = StateGraph(AgentState)

    # Step 2: 注册节点（每个智能体是一个节点）
    # 使用 wrap_node 自动转换 numpy 类型为 Python 原生类型
    print("[Graph] 注册节点...")
    graph.add_node("supervisor", wrap_node(supervisor_agent))
    graph.add_node("data_pipeline", wrap_node(data_pipeline_agent))
    graph.add_node("analysis", wrap_node(analysis_agent))
    graph.add_node("diagnosis", wrap_node(diagnosis_agent))
    graph.add_node("decision", wrap_node(decision_agent))
    graph.add_node("self_check", wrap_node(self_check_agent))
    graph.add_node("rag", wrap_node(rag_agent))
    graph.add_node("report", wrap_node(report_generator))

    # Step 3: 定义边（执行顺序）
    print("[Graph] 定义边...")

    # 起始 → Supervisor
    graph.add_edge(START, "supervisor")
    # Supervisor → 数据管道
    graph.add_edge("supervisor", "data_pipeline")
    # 数据管道 → 分析
    graph.add_edge("data_pipeline", "analysis")
    # 分析 → 诊断
    graph.add_edge("analysis", "diagnosis")
    # 诊断 → 决策
    graph.add_edge("diagnosis", "decision")
    # 决策 → 自验收
    graph.add_edge("decision", "self_check")

    # Step 4: 条件边（自验收循环的核心！）
    # 根据 Self-Check 的结果选择下一步：
    # - "pass": 通过 → 去 RAG 检索知识 → 生成报告
    # - "fail": 不通过 → 回退到 analysis 重新分析
    graph.add_conditional_edges(
        "self_check",
        route_after_check,      # 路由函数
        {
            "pass": "rag",      # 验收通过 → RAG → Report
            "fail": "analysis", # 验收失败 → 回退到 Analysis
        }
    )
    # RAG → Report → END
    graph.add_edge("rag", "report")
    graph.add_edge("report", END)

    # Step 5: 编译图
    # MemorySaver 允许保存和恢复检查点
    print("[Graph] 编译中...")
    compiled = graph.compile(checkpointer=MemorySaver())

    print("[Graph] ✅ 状态图构建完成！")
    print(f"[Graph] 节点数: {len(compiled.nodes)}")

    return compiled


def run_analysis(
    device_ids: list[str],
    graph: StateGraph = None,
    thread_id: str = None,
) -> dict:
    """
    执行设备分析。

    【参数】
    device_ids: 设备ID列表
    graph:      状态图实例（没有则新建）
    thread_id:  会话ID（用于恢复对话）

    【返回】
    最终状态（包含所有分析结果）
    """
    if graph is None:
        graph = build_analysis_graph()

    if thread_id is None:
        import uuid
        thread_id = str(uuid.uuid4())[:8]

    # 创建初始状态
    initial_state = create_initial_state(device_ids)

    # 配置（LangGraph 的配置参数）
    config = {
        "configurable": {"thread_id": thread_id},
    }

    print(f"\n{'='*50}")
    print(f"🚀 开始分析设备: {device_ids}")
    print(f"📋 会话ID: {thread_id}")
    print(f"{'='*50}\n")

    # 执行图
    result = graph.invoke(initial_state, config)

    print(f"\n{'='*50}")
    print(f"✅ 分析完成！")
    print(f"{'='*50}\n")

    return result
