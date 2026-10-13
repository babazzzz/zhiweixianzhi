"""聊天查询 Agent：第三条独立 LangGraph 图（classify → dispatch → compose）。

与诊断主图的分工：主图做"分析决策"，本图做"信息查询"——LLM 只负责意图
兜底识别与把工具结果组织成人话，**所有数据获取都走确定性 SQL / RAG 检索**，
保证答案里的每个数字都可在数据库里复现。

意图路由（确定性优先，LLM 兜底）::

    machine_info     设备档案 / 当前读数 / 健康分
    machine_sop      维护 SOP（RAG 检索 manual + fmeca）
    machine_records  诊断记录 / 维修历史
    knowledge_search 通用知识查询（全库 RAG）
    general          兜底：设备列表概览 + 知识检索
    chitchat         闲聊 / 系统自身相关（"我是谁""你好"）：不检索，直接自述

回答组织：配置了 DeepSeek Key 时用 deepseek-chat，否则本地 qwen2.5:7b；
LLM 不可用时直接模板化拼装工具结果，聊天窗口永远有答案。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import replace as dataclass_replace
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from src.config.model_routing import DEEPSEEK_MODEL, get_agent_config
from src.database.connection import Database
from src.knowledge.index_builder import KnowledgeIndex
from src.knowledge.retrieval import citations_from, format_context
from src.llm.client import UnifiedLLMClient
from src.memory import MemoryManager
from src.observability import traced

LOGGER = logging.getLogger("zhiwei.chat")

_MACHINE_RE = re.compile(r"CNC[_\- ]?(\d{1,3})", re.IGNORECASE)

_INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "machine_sop": ("sop", "维护", "保养", "维修步骤", "怎么修", "如何处理", "处置", "规程", "手册"),
    "machine_records": ("记录", "历史", "上次", "诊断过", "修过", "维修过", "维护过", "何时", "什么时候"),
    "machine_info": ("信息", "档案", "概况", "状态", "健康", "产量", "成本", "哪条产线"),
    "general": ("全厂", "整体", "设备总数", "多少台", "高风险", "故障", "告警", " overview"),
}

#: 闲聊 / 元问题：问的是"你/系统"本身，而不是知识库内容。
#: 这类问题必须走独立意图——否则会掉进 knowledge_search，被检索到的
#: 无关手册条文冒充答案。命中任一关键词即归类（优先级高于知识检索）。
_CHITCHAT_KEYWORDS: tuple[str, ...] = (
    "我是谁", "你是谁", "你叫什么", "你能做什么", "你会什么", "你能干嘛", "你能干什么",
    "介绍一下你", "自我介绍", "你好", "您好", "hi", "hello", "嗨", "哈喽",
    "谢谢", "感谢", "多谢", "在吗", "在不在", "讲个笑话", "聊聊天", "测试一下", "随便聊聊",
)

#: 闲聊意图的确定性回答：不编造、不检索，直接说清系统身份与能查什么。
_CHITCHAT_REPLY = (
    "我是智维先知的设备查询助手，负责回答 CNC 设备相关的问题。"
    "你能查的东西有三类：\n"
    "1. 设备档案与实时状态——例如「CNC_078 现在状态怎么样」「全厂有多少台设备」；\n"
    "2. 维护 SOP 与故障处理——例如「主轴温度过高怎么处理」「轴承磨损的维护步骤」；\n"
    "3. 诊断与维修历史——例如「CNC_078 最近修过什么」「上次诊断是什么结论」。\n"
    "我没有个人信息，也答不了与设备无关的问题。"
)


class ChatState(TypedDict):
    """One chat turn flowing through the graph."""

    thread_id: str
    question: str
    intent: str
    machine_id: str | None
    facts: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    memory_context: str
    answer: str
    llm_used: bool


def extract_machine_id(question: str) -> str | None:
    """Pull ``CNC_078``-style ids out of natural language."""

    match = _MACHINE_RE.search(question)
    if not match:
        return None
    return f"CNC_{int(match.group(1)):03d}"


def classify_intent(question: str) -> str:
    """Keyword-based intent classification (fast, deterministic, offline).

    命中顺序即优先级：闲聊 → 设备操作类 → 其他。**没有命中任何关键词时返回
    ``knowledge_search``**——这是有意的：知识库检索是默认动作。但调用方
    （``_node_classify``）会用 LLM 再做一次兜底判定，因为纯规则打不准
    "今天天气怎么样"这类既不闲聊也不查库的问题。
    """

    lowered = question.lower()
    if any(keyword in lowered for keyword in _CHITCHAT_KEYWORDS):
        return "chitchat"
    for intent, keywords in _INTENT_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return intent
    return "knowledge_search"


class ChatAgent:
    """Query-oriented chat graph with memory and tool-audited retrieval."""

    def __init__(self, database: Database | None = None) -> None:
        self._db = database or Database()
        self._memory = MemoryManager(self._db)
        self._index = KnowledgeIndex(self._db)
        self._llm = UnifiedLLMClient(timeout_seconds=60.0)
        self._graph = self._build_graph()
        self._index_ready: bool | None = None

    # --- 对外入口 -----------------------------------------------------

    def index_retriever(self):
        """Expose the hybrid retriever for reuse (e.g. /api/knowledge/search)."""

        return self._index.retriever

    async def ensure_index(self) -> None:
        """Load the persisted knowledge index once."""

        if self._index_ready is None:
            await self._index.ensure(embed=False)
            self._index_ready = True

    async def ask(self, question: str, thread_id: str | None = None) -> dict[str, Any]:
        """Process one user question and persist the turn to conversation memory."""

        question = question.strip()
        if not thread_id:
            thread_id = self._memory.open_thread()
        self._memory.append_message(thread_id, "user", question)

        if self._index_ready is None:
            await self.ensure_index()

        state: ChatState = {
            "thread_id": thread_id, "question": question, "intent": "general",
            "machine_id": extract_machine_id(question), "facts": [], "citations": [],
            "memory_context": self._memory.build_context(
                subject=extract_machine_id(question), query=question, max_items=4,
            ),
            "answer": "", "llm_used": False,
        }
        final = await self._graph.ainvoke(state)
        self._memory.append_message(
            thread_id, "assistant", final["answer"],
            intent=final["intent"], citations=final["citations"],
        )
        return {
            "thread_id": thread_id, "intent": final["intent"], "machine_id": final["machine_id"],
            "answer": final["answer"], "citations": final["citations"],
            "facts": final["facts"], "llm_used": final["llm_used"],
        }

    # --- 图结构 -------------------------------------------------------

    def _build_graph(self):
        graph = StateGraph(ChatState)
        graph.add_node("classify", self._node_classify)
        graph.add_node("dispatch", self._node_dispatch)
        graph.add_node("compose", self._node_compose)
        graph.set_entry_point("classify")
        graph.add_edge("classify", "dispatch")
        graph.add_edge("dispatch", "compose")
        graph.add_edge("compose", END)
        return graph.compile()

    async def _node_classify(self, state: ChatState) -> dict[str, Any]:
        """Rule-first intent classification with optional LLM rescue.

        规则命中闲聊/设备操作类时直接采信，不再花 LLM 调用。只有规则落到
        ``knowledge_search`` 默认值（即真的没认出意图）时才让云端模型兜底——
        短问题同样要兜底：旧实现用 ``len(question) > 6`` 把"我是谁"这类
        3 字问题排除在外，导致它一路走到知识检索。
        """

        intent = classify_intent(state["question"])
        if intent == "knowledge_search" and self._llm.deepseek_enabled:
            try:
                result = await self._llm.chat("chat", [
                    {"role": "system", "content": "把用户问题分类为 machine_info/machine_sop/machine_records/knowledge_search/irrelevant 之一，只输出类别名。irrelevant 表示与 CNC 设备运维完全无关的闲聊或无关话题。"},
                    {"role": "user", "content": state["question"]},
                ])
                guess = result.content.strip().lower().strip('"').strip()
                if guess in {"machine_info", "machine_sop", "machine_records", "knowledge_search"}:
                    intent = guess
                elif guess == "irrelevant":
                    intent = "chitchat"
            except Exception as error:  # noqa: BLE001 - 意图兜底失败就用规则结果
                LOGGER.warning(f"intent fallback failed: {error}")
        return {"intent": intent}

    async def _node_dispatch(self, state: ChatState) -> dict[str, Any]:
        """Run the deterministic tools backing the classified intent."""

        intent = state["intent"]
        machine_id = state["machine_id"]
        facts: list[dict[str, Any]] = []
        citations: list[dict[str, Any]] = []

        if intent in {"machine_info", "machine_records"} and not machine_id:
            intent = "general"
        if intent == "machine_info" and machine_id:
            facts.append({"type": "machine", "data": self._db.get_machine(machine_id)})
        elif intent == "machine_sop":
            hits = await self._index.retriever.search(state["question"], top_k=5, doc_types=("manual", "fmeca"))
            facts.append({"type": "rag", "data": format_context(hits)})
            citations.extend(citations_from(hits))
        elif intent == "machine_records" and machine_id:
            facts.append({"type": "diagnostics", "data": self._db.list_diagnostics(machine_id=machine_id, limit=3)})
            facts.append({"type": "maintenance", "data": self._maintenance_history(machine_id)})
        elif intent == "general":
            machines = self._db.list_machines()
            summary = [
                {"id": item["id"], "status": item["status"],
                 "health": (item.get("latest_reading") or {}).get("health_score"),
                 "severity": (item.get("latest_diagnostic") or {}).get("severity")}
                for item in machines
            ]
            risky = [item for item in summary if (item.get("severity") or "") in {"high", "critical"}]
            facts.append({"type": "fleet", "data": {"total": len(summary), "at_risk": risky[:8]}})
        elif intent == "chitchat":
            # 闲聊/元问题不碰知识库：检索只会捞出无关条文冒充答案
            facts.append({"type": "chitchat", "data": _CHITCHAT_REPLY})
        else:  # knowledge_search 兜底：全库混合检索
            hits = await self._index.retriever.search(state["question"], top_k=5)
            # 检索可能返回空（相关性门槛判定"语料里没有能回应它的内容"）——
            # 空结果要显式记一条 fact，交给 compose 说"没找到"，而不是静默走模板
            facts.append({"type": "rag", "data": format_context(hits), "empty": not hits})
            citations.extend(citations_from(hits))

        return {"intent": intent, "facts": facts, "citations": citations}

    async def _node_compose(self, state: ChatState) -> dict[str, Any]:
        """Organize tool results into a human answer; template fallback included.

        **必须走 JSON 出参**：``UnifiedLLMClient`` 对 DeepSeek 固定带
        ``response_format={"type":"json_object"}``，而该接口要求 prompt 里出现
        "json" 字样，否则直接 400。旧实现发的是"请依据以上工具结果回答…"这种
        纯自然语言指令——每次 DeepSeek 组稿都必然 400 并静默降级到模板兜底，
        聊天窗口于是永远在吐检索原文。这里把指令写成 JSON 契约（要求模型把
        答案放进 ``answer`` 字段），既满足接口约束，也让输出可解析、可回退。
        """

        facts_block = "\n".join(
            f"[{fact['type']}]\n{self._stringify(fact['data'])}" for fact in state["facts"]
        )
        # 闲聊与"检索为空"两种情况都不该让模型自由发挥：前者是固定自述，
        # 后者必须如实说"没找到"。这两类直接走模板，省一次调用也更可控。
        if state["intent"] == "chitchat" or self._rag_is_empty(state):
            return {"answer": self._template_answer(state), "llm_used": False}

        prompt_messages = [
            {"role": "system", "content": get_agent_config("chat").system_prompt},
            {"role": "user", "content":
                f"用户问题：{state['question']}\n\n工具结果：\n{facts_block}\n\n"
                + (f"相关长期记忆：\n{state['memory_context']}\n" if state["memory_context"] else "")
                + "请依据以上工具结果回答，数字必须原样引用，不得编造。"
                + '严格只输出 JSON，结构为：{"answer": "给用户的完整中文回答"}。'},
        ]
        config = get_agent_config("chat")
        if self._llm.deepseek_enabled:
            config = dataclass_replace(config, provider="deepseek", model=DEEPSEEK_MODEL, context_window=32768)
        try:
            with traced("chat", "compose", intent=state["intent"]):
                result = await self._llm.chat(config, prompt_messages)
            answer = self._extract_answer(result.content) or self._template_answer(state)
            return {"answer": answer, "llm_used": True}
        except Exception as error:  # noqa: BLE001 - LLM 不可用时保证聊天可用
            LOGGER.warning(f"compose fallback: {type(error).__name__}: {error}")
            return {"answer": self._template_answer(state), "llm_used": False}

    @staticmethod
    def _extract_answer(content: str) -> str:
        """Pull the human answer out of the model's JSON envelope.

        模型可能返回 ``{"answer": "..."}``、裸字符串或夹带 markdown 围栏；
        解析失败时回退到原文（只要不是空串，仍比模板兜底更贴近问题）。
        """

        stripped = content.strip()
        if not stripped:
            return ""
        parsed = UnifiedLLMClient.parse_json(stripped)
        if isinstance(parsed, dict):
            for key in ("answer", "回答", "content", "text"):
                value = parsed.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return ""
        return stripped if not stripped.startswith("{") else ""

    @staticmethod
    def _rag_is_empty(state: ChatState) -> bool:
        """Report whether knowledge retrieval came back with nothing."""

        return any(
            fact["type"] == "rag" and fact.get("empty") for fact in state["facts"]
        )

    # --- 工具细节 -----------------------------------------------------

    def _maintenance_history(self, machine_id: str) -> list[dict[str, Any]]:
        with self._db.transaction() as connection:
            rows = connection.execute(
                """SELECT id, maintenance_type, start_time, end_time, downtime_hours,
                          parts_cost, labor_cost, technician, status
                   FROM maintenance_records WHERE machine_id=? ORDER BY start_time DESC LIMIT 5""",
                (machine_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _stringify(data: Any, limit: int = 2400) -> str:
        """Render one tool payload compactly for the prompt."""

        if data is None:
            return "（无数据）"
        if isinstance(data, str):
            return data[:limit]
        try:
            import json
            return json.dumps(data, ensure_ascii=False, default=str)[:limit]
        except (TypeError, ValueError):
            return str(data)[:limit]

    @staticmethod
    def _template_answer(state: ChatState) -> str:
        """Deterministic fallback so the chat window always answers.

        关键约束：**未命中时不许把检索原文当答案吐出去**。检索为空意味着
        语料里没有能支撑回答的内容，此时如实说明并提供改问建议，比贴一段
        无关手册更像一个能用的助手。
        """

        parts: list[str] = []
        for fact in state["facts"]:
            if fact["type"] == "machine":
                machine = fact["data"]
                if machine:
                    reading = machine.get("latest_reading") or {}
                    diag = machine.get("latest_diagnostic") or {}
                    parts.append(
                        f"{machine['id']}（{machine.get('model')}）：状态 {machine.get('status')}，"
                        f"健康分 {reading.get('health_score')}，最近诊断 {diag.get('severity') or '无'}。"
                    )
            elif fact["type"] == "fleet":
                fleet = fact["data"]
                parts.append(f"全厂共 {fleet['total']} 台设备，当前高风险 {len(fleet['at_risk'])} 台。")
            elif fact["type"] == "chitchat":
                parts.append(str(fact["data"]))
            elif fact["type"] == "rag":
                if fact.get("empty"):
                    parts.append(
                        "知识库里没有检索到与这个问题相关的内容。"
                        "请补充设备编号（如 CNC_078）或换成设备运维类问题，例如"
                        "「主轴温度过高怎么处理」「CNC_025 最近修过什么」。"
                    )
                else:
                    parts.append("知识库检索结果：\n" + str(fact["data"])[:800])
            elif fact["type"] == "diagnostics":
                parts.append("最近诊断记录已附（见引用）。\n" + ChatAgent._stringify(fact["data"], 600))
            elif fact["type"] == "maintenance":
                parts.append("最近维修记录：\n" + ChatAgent._stringify(fact["data"], 600))
        if not parts:
            parts.append("暂未找到与该问题直接相关的数据，请换个问法或补充设备编号（如 CNC_001）。")
        return "\n\n".join(parts)


def build_chat_agent(database: Database | None = None) -> ChatAgent:
    """Factory used by the API layer."""

    return ChatAgent(database)


if __name__ == "__main__":
    agent = ChatAgent()
    print(asyncio.run(agent.ask("CNC_078 的主轴温度高怎么处理？")))
