"""Safe, explicit routing policy for the seven specialist agents."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class AgentModelConfig:
    """Configuration required to make one auditable LLM call."""

    name: str
    provider: str
    model: str
    temperature: float
    max_tokens: int
    context_window: int
    keep_alive: str
    system_prompt: str


# 默认值必须与 `ollama list` 中真实存在的模型一致，否则本地 Agent 会在启动后首次调用时报错。
# 此前默认的 zhiwei-monitor / zhiwei-knowledge / zhiwei-reflection 并未在本地创建过。
OLLAMA_MONITOR_MODEL: Final[str] = os.getenv("OLLAMA_MONITOR_MODEL", "qwen2.5:7b")
OLLAMA_KNOWLEDGE_MODEL: Final[str] = os.getenv("OLLAMA_KNOWLEDGE_MODEL", "qwen2.5:7b")
OLLAMA_REFLECTION_MODEL: Final[str] = os.getenv("OLLAMA_REFLECTION_MODEL", "qwen2.5:7b")
DEEPSEEK_MODEL: Final[str] = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
EMBEDDING_MODEL: Final[str] = os.getenv("OLLAMA_EMBEDDING_MODEL", "bge-m3")


def ollama_model_for(agent_name: str) -> str:
    """Resolve the concrete Ollama model name backing one local agent."""

    return get_agent_config(agent_name).model


def _prompt(role: str, output: str) -> str:
    """Build the shared safety instruction used by all agents."""

    return (
        f"你是智维先知系统中的{role}。只依据提供的传感器、FMECA、案例和物理约束推理。"
        "不得编造读数或维修记录。将不确定性明确写出。"
        f"严格只输出JSON，结构为：{output}。"
    )


AGENT_ROUTING: Final[dict[str, AgentModelConfig]] = {
    "monitor": AgentModelConfig("monitor", "ollama", OLLAMA_MONITOR_MODEL, 0.1, 512, 4096, "30m", _prompt("健康监控Agent", "health_score,is_anomaly,anomaly_dims,severity")),
    "diagnostic": AgentModelConfig("diagnostic", "deepseek", DEEPSEEK_MODEL, 0.3, 2048, 32768, "10m", _prompt("ReAct诊断Agent", "fault_code,fault_mode,root_cause,evidence_chain,confidence,severity")),
    "prediction": AgentModelConfig("prediction", "deepseek", DEEPSEEK_MODEL, 0.2, 1024, 32768, "10m", _prompt("RUL预测Agent", "rul_p10,rul_p50,rul_p90,explanation")),
    "decision": AgentModelConfig("decision", "deepseek", DEEPSEEK_MODEL, 0.4, 2048, 32768, "10m", _prompt("维护决策Agent", "recommendation,timing,action,cost_analysis")),
    "knowledge": AgentModelConfig("knowledge", "ollama", OLLAMA_KNOWLEDGE_MODEL, 0.1, 1024, 4096, "30m", _prompt("知识检索Agent", "summary,citations")),
    "report": AgentModelConfig("report", "deepseek", DEEPSEEK_MODEL, 0.5, 4096, 32768, "10m", _prompt("报告Agent", "title,summary,body")),
    "reflection": AgentModelConfig("reflection", "ollama", OLLAMA_REFLECTION_MODEL, 0.0, 512, 4096, "30m", _prompt("反思自检Agent", "checks,all_passed")),
    # 聊天窗口（需求 2）：默认走本地模型保证离线可用；配置 DeepSeek Key 后由 chat_agent 动态切换
    "chat": AgentModelConfig("chat", "ollama", OLLAMA_KNOWLEDGE_MODEL, 0.3, 1024, 8192, "30m", "你是智维先知的设备查询助手。只依据提供的工具结果回答，不得编造数据；回答用简洁中文，给出关键数字与出处。"),
}


def get_agent_config(agent_name: str) -> AgentModelConfig:
    """Return an agent routing configuration or raise a useful error."""

    try:
        return AGENT_ROUTING[agent_name]
    except KeyError as error:
        supported = ", ".join(sorted(AGENT_ROUTING))
        raise ValueError(f"Unknown agent '{agent_name}'. Supported: {supported}") from error
