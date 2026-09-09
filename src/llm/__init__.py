"""LLM public surface; calls are routed through the unified audited client."""

from .client import LLMCallResult, UnifiedLLMClient


def get_llm(**overrides):
    """Backward-compatible lightweight ChatOllama factory for legacy modules."""

    from langchain_ollama import ChatOllama
    from src.config import PROJECT_ROOT
    import os
    config = {"base_url": os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"), "model": os.getenv("OLLAMA_MODEL", os.getenv("OLLAMA_MONITOR_MODEL", "zhiwei-monitor")), "temperature": 0.1, "timeout": 60.0}
    config.update(overrides)
    return ChatOllama(base_url=config["base_url"], model=config["model"], temperature=config["temperature"], timeout=config["timeout"])


def get_agent_llm(**overrides):
    """Backward-compatible deterministic agent LLM factory."""

    return get_llm(**overrides)


def get_creative_llm(**overrides):
    """Backward-compatible report LLM factory."""

    return get_llm(temperature=0.5, **overrides)

__all__ = ["LLMCallResult", "UnifiedLLMClient", "get_llm", "get_agent_llm", "get_creative_llm"]
