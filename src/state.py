"""
==========================================
 LangGraph 状态定义 — state.py
==========================================
★ 这是理解 LangGraph 最核心的概念 ★

【什么是 State（状态）？】
想象一条生产线：
- 原材料进入（原始数据）
- 每个工位加工后，产品状态发生变化
- 最终出成品（分析报告）

在 LangGraph 中，State 就是"产品的当前状态"。
每个智能体（Agent）都是一个工位，读取状态、加工、写入新的状态信息。

【LangGraph 状态 vs 普通变量】
普通Python:   result = func(data)         # 函数调用，一次返回
LangGraph:    state = agent1(state)       # 智能体读取并修改状态
              state = agent2(state)       # 下一个智能体接力
              state = agent3(state)       # ...直到结束

【TypedDict 是什么？】
TypedDict 是 Python 的类型注解机制，定义了 State 里每个字段的类型。
好处：
1. IDE 自动补全（写代码时提示有哪些字段）
2. 类型检查（运行时不会报"字段不存在"的错）
3. 自我文档化（看这里就知道系统有哪些数据）

【Annotated 是什么？】
Annotated[类型, 操作符] 是 LangGraph 的"魔法"——它告诉 LangGraph：
- 这个字段的数据类型是什么
- 当多个智能体写入这个字段时，应该怎么合并？

最重要的操作符：
- add_messages:    消息追加（不是覆盖）
                   Agent1写一条消息，Agent2再写一条，消息列表变长
                   → 用于聊天历史、推理过程

- operator.add:    列表合并
                   Agent1返回["结果A"]，Agent2返回["结果B"]
                   最终变成["结果A", "结果B"]
                   → 用于并行分析的结果收集

- 默认（不写）：    直接覆盖
                   Agent1写入后，Agent2再写入就直接替换
                   → 用于最终结果、状态标记
"""

from typing import TypedDict, Annotated, Any, Optional
from langgraph.graph import add_messages, MessagesState
from langchain_core.messages import AnyMessage
import operator


class AgentState(MessagesState):
    """
    ==========================================
    全局状态定义（继承自 MessagesState）
    ==========================================
    MessagesState 是 LangGraph 内置的状态基类，
    它已经包含了 messages 字段（用于存储聊天消息），
    我们在这里扩展项目所需的额外字段。

    【字段说明】
    每个字段的注释格式：
    字段名: 数据类型 | 合并方式
    用途：这个字段存什么
    谁写入：哪些智能体会修改这个字段
    """

    # ==================== 输入层 ====================

    # device_ids: list[str]  |  覆盖模式
    # 用途：需要分析的设备ID列表
    # 谁写入：用户/Supervisor 写入一次，后续只读
    device_ids: list[str]

    # current_device: str | None  |  覆盖模式
    # 用途：当前正在分析的设备ID（单设备分析时使用）
    # 谁写入：Supervisor 在分发任务时设置
    current_device: Optional[str]

    # raw_data: dict  |  覆盖模式
    # 用途：从CSV加载的原始设备数据，key为设备ID，value为DataFrame
    # 谁写入：Data Pipeline Agent
    raw_data: dict

    # ==================== 分析层 ====================

    # analysis_tasks: list[dict]  |  覆盖模式
    # 用途：Supervisor 分解后的分析任务列表
    # 谁写入：Supervisor
    analysis_tasks: list[dict]

    # parallel_results: Annotated[dict, operator.add]  |  合并模式
    # 用途：并行分析的结果汇总（每台设备一个子字典）
    # 谁写入：多个 Analysis Agent 并行写入后自动合并
    # ★ 关键设计：因为用了 operator.add（列表合并），
    #    parallel_results 实际上是一个列表
    #    但为了方便使用，我们把它设计成 dict 类型
    parallel_results: Annotated[list, operator.add]

    # feature_vectors: dict  |  覆盖模式
    # 用途：每台设备的22维特征向量
    # 谁写入：Data Pipeline Agent
    feature_vectors: dict

    # ==================== 诊断层 ====================

    # analysis_result: dict  |  覆盖模式
    # 用途：Analysis Agent 的分析结果（异常检测、RUL预测）
    # 谁写入：Analysis Agent
    analysis_result: dict

    # diagnosis_result: dict  |  覆盖模式
    # 用途：Diagnosis Agent 的诊断结果（故障类型、证据链）
    # 谁写入：Diagnosis Agent
    diagnosis_result: dict

    # decision_result: dict  |  覆盖模式
    # 用途：Decision Agent 的决策结果（成本分析、工单）
    # 谁写入：Decision Agent
    decision_result: dict

    # ==================== 质量层 ====================

    # verification_status: str  |  覆盖模式
    # 用途：自验收状态
    # 取值: "pending" → "pass" / "fail" / "max_retry"
    # 谁写入：Self-Check Agent
    verification_status: str

    # verification_notes: str  |  覆盖模式
    # 用途：自验收的详细检查记录
    # 谁写入：Self-Check Agent
    verification_notes: str

    # iteration_count: int  |  覆盖模式
    # 用途：自验收循环的迭代次数（最大3次）
    # 谁写入：Self-Check Agent（每轮+1）
    iteration_count: int

    # ==================== 输出层 ====================

    # final_report: str  |  覆盖模式
    # 用途：最终的分析报告（Markdown格式）
    # 谁写入：Report Agent（最后一步）
    final_report: str

    # charts_data: dict  |  覆盖模式
    # 用途：前端图表数据（JSON可序列化）
    # 谁写入：Report Agent
    charts_data: dict

    # rag_context: str  |  覆盖模式
    # 用途：RAG检索到的相关案例知识
    # 谁写入：RAG Agent
    rag_context: str

    # ==================== 元信息 ====================

    # error: str | None  |  覆盖模式
    # 用途：错误信息（任何一个智能体出错时设置）
    # 取值：None 表示无错误，非None 表示出错
    error: Optional[str]

    # messages: 这个字段来自 MessagesState（父类）
    # 用途：存储聊天消息历史（LLM对话记录）
    # 合并方式：add_messages（自动追加，不会覆盖）


def create_initial_state(device_ids: list[str]) -> dict:
    """
    创建初始状态——就像流水线开工前的"空状态"。

    【参数】
    device_ids: 要分析的设备ID列表，如 ["CNC_007", "CNC_012"]

    【返回】
    一个符合 AgentState 定义的字典

    【学习要点】
    这个函数的作用就是给 State 的所有字段设置初始值。
    注意：LangGraph 用 dict（普通字典）作为状态，
    不是用 AgentState 类的实例——TypedDict 只做类型检查。
    """
    return {
        # 输入
        "device_ids": device_ids,
        "current_device": None,
        "raw_data": {},
        "feature_vectors": {},

        # 分析任务与结果
        "analysis_tasks": [],
        "analysis_result": {},
        "diagnosis_result": {},
        "decision_result": {},

        # 并行结果（列表，每条一个dict）
        "parallel_results": [],

        # 质量验收
        "verification_status": "pending",
        "verification_notes": "",
        "iteration_count": 0,

        # 输出
        "final_report": "",
        "charts_data": {},
        "rag_context": "",

        # 错误
        "error": None,

        # 消息（MessagesState 继承来的）
        "messages": [],
    }
