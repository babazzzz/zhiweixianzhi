"""Async Ollama/DeepSeek client with retries, JSON parsing and audit logging."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Mapping, Sequence

import httpx

from src.config.model_routing import AgentModelConfig, EMBEDDING_MODEL, get_agent_config
from src.observability import get_logger, llm_audit, traced

LOGGER = get_logger("llm")


@dataclass(slots=True)
class LLMCallResult:
    """Normalized result returned by either model provider."""

    content: str
    tool_calls: list[dict[str, object]]
    usage: dict[str, int]
    latency_ms: int
    provider: str
    model: str


class UnifiedLLMClient:
    """Route calls to local Ollama or DeepSeek without exposing secrets in code."""

    def __init__(self, timeout_seconds: float = 90.0) -> None:
        self._ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
        self._deepseek_base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
        # 凭据只能来自环境变量：绝不在源码里写默认值，避免 Key 随仓库泄漏。
        # 未配置时 deepseek_enabled 为 False，依赖云端模型的 Agent 会自动走本地/规则降级。
        self._deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        self._timeout = httpx.Timeout(timeout_seconds, connect=10.0)

    @property
    def deepseek_enabled(self) -> bool:
        """Report whether a DeepSeek credential is configured."""

        return bool(self._deepseek_key)

    async def chat(
        self,
        config: AgentModelConfig | str,
        messages: Sequence[Mapping[str, str]],
    ) -> LLMCallResult:
        """Call the configured chat model and persist non-secret audit metadata."""

        resolved = get_agent_config(config) if isinstance(config, str) else config
        started = time.perf_counter()
        try:
            with traced("llm", "chat", agent=resolved.name, provider=resolved.provider, model=resolved.model):
                result = await self._retry_chat(resolved, messages)
        except Exception as error:
            latency = round((time.perf_counter() - started) * 1000)
            self._audit(resolved, messages, "", latency, {}, ok=False, error=str(error))
            raise
        self._audit(resolved, messages, result.content, result.latency_ms, result.usage)
        return result

    async def embed(self, text: str) -> list[float]:
        """Generate a local bge-m3 embedding and validate its numeric shape."""

        payload = {"model": EMBEDDING_MODEL, "input": text}
        with traced("llm", "embed", model=EMBEDDING_MODEL, chars=len(text)):
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(f"{self._ollama_base_url}/api/embed", json=payload)
                response.raise_for_status()
                body = response.json()
        embeddings = body.get("embeddings", [])
        if not embeddings or not isinstance(embeddings[0], list):
            raise ValueError("Ollama returned no embedding vector")
        return [float(value) for value in embeddings[0]]

    async def health_check(self) -> dict[str, bool]:
        """Check local and cloud endpoints without transmitting model prompts."""

        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            try:
                ollama = (await client.get(f"{self._ollama_base_url}/api/tags")).is_success
            except httpx.HTTPError:
                ollama = False
            if not self._deepseek_key:
                return {"ollama": ollama, "deepseek": False}
            try:
                deepseek = (await client.get(f"{self._deepseek_base_url}/models", headers=self._deepseek_headers())).is_success
            except httpx.HTTPError:
                deepseek = False
        return {"ollama": ollama, "deepseek": deepseek}

    async def _retry_chat(self, config: AgentModelConfig, messages: Sequence[Mapping[str, str]]) -> LLMCallResult:
        """Retry transient provider failures using bounded exponential backoff."""

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return await self._invoke(config, messages)
            except (httpx.HTTPError, ValueError) as error:
                last_error = error
                if attempt < 2:
                    await asyncio.sleep(0.4 * (2**attempt))
        assert last_error is not None
        raise last_error

    async def _invoke(self, config: AgentModelConfig, messages: Sequence[Mapping[str, str]]) -> LLMCallResult:
        """Dispatch one provider-specific request."""

        started = time.perf_counter()
        if config.provider == "ollama":
            body = await self._ollama_chat(config, messages)
            content = str(body.get("message", {}).get("content", ""))
            usage = {"prompt_tokens": int(body.get("prompt_eval_count", 0)), "completion_tokens": int(body.get("eval_count", 0))}
        else:
            body = await self._deepseek_chat(config, messages)
            choice = body.get("choices", [{}])[0]
            content = str(choice.get("message", {}).get("content", ""))
            raw_usage = body.get("usage", {})
            usage = {"prompt_tokens": int(raw_usage.get("prompt_tokens", 0)), "completion_tokens": int(raw_usage.get("completion_tokens", 0))}
        latency = round((time.perf_counter() - started) * 1000)
        return LLMCallResult(content, self._extract_tool_calls(content), usage, latency, config.provider, config.model)

    async def _ollama_chat(self, config: AgentModelConfig, messages: Sequence[Mapping[str, str]]) -> dict[str, object]:
        """Call Ollama's chat endpoint with deterministic model options."""

        payload = {"model": config.model, "messages": [{"role": "system", "content": config.system_prompt}, *[dict(message) for message in messages]], "stream": False, "format": "json", "keep_alive": config.keep_alive, "options": {"temperature": config.temperature, "num_predict": config.max_tokens, "num_ctx": config.context_window}}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(f"{self._ollama_base_url}/api/chat", json=payload)
            response.raise_for_status()
            return dict(response.json())

    async def _deepseek_chat(self, config: AgentModelConfig, messages: Sequence[Mapping[str, str]]) -> dict[str, object]:
        """Call the OpenAI-compatible DeepSeek endpoint only when a key exists."""

        if not self._deepseek_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured")
        payload = {"model": config.model, "messages": [{"role": "system", "content": config.system_prompt}, *[dict(message) for message in messages]], "temperature": config.temperature, "max_tokens": config.max_tokens, "response_format": {"type": "json_object"}}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(f"{self._deepseek_base_url}/chat/completions", headers=self._deepseek_headers(), json=payload)
            response.raise_for_status()
            return dict(response.json())

    def _deepseek_headers(self) -> dict[str, str]:
        """Return request headers without ever logging the authorization value."""

        return {"Authorization": f"Bearer {self._deepseek_key}", "Content-Type": "application/json"}

    def _extract_tool_calls(self, content: str) -> list[dict[str, object]]:
        """Parse structured pseudo-tool calls emitted by local models, if present."""

        parsed = self.parse_json(content)
        if isinstance(parsed, dict) and isinstance(parsed.get("tool"), str):
            args = parsed.get("args", {})
            return [{"name": parsed["tool"], "args": args if isinstance(args, dict) else {}}]
        return []

    @staticmethod
    def parse_json(content: str) -> dict[str, object] | list[object] | None:
        """Parse raw or markdown-fenced JSON returned by a model."""

        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, (dict, list)) else None

    def _audit(
        self,
        config: AgentModelConfig,
        messages: Sequence[Mapping[str, str]],
        output: str,
        latency_ms: int,
        usage: Mapping[str, object],
        *,
        ok: bool = True,
        error: str = "",
    ) -> None:
        """Append an auditable LLM event, excluding credentials and full raw payloads."""

        counted = dict(usage)
        llm_audit(
            agent=config.name,
            provider=config.provider,
            model=config.model,
            latency_ms=latency_ms,
            prompt_tokens=int(counted.get("prompt_tokens", 0)),
            completion_tokens=int(counted.get("completion_tokens", 0)),
            ok=ok,
            # 只落摘要，不落全量 prompt：既控制日志体积，也避免敏感内容入库
            prompt_preview=str(messages[-1].get("content", "")) if messages else "",
            output_preview=output,
            error=error,
        )
