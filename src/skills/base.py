"""Skill contract and auto-discovering registry for 智维先知.

为什么需要这一层
----------------
此前 ``src/skills/`` 下既有"包"（anomaly_detect/）又有"单个函数文件"（cost_benefit.py），
两套风格并存，导致：

* ``__init__.py`` 只手工导出 2 个函数，其余 skill 靠各 Agent 自行 import，无法被统一发现；
* 无法在运行时列举"系统具备哪些能力"，聊天 Agent 与前端页面都无法动态调用；
* 无法统一记录"谁调用了什么、耗时多少、是否确定性"。

现在的约定（所有 skill 一律是包，不再有平铺 .py）
-------------------------------------------------
每个 skill 是一个**包目录**，固定包含 4 个文件：

===============  ==========================================================
``__init__.py``  只导出一个实例：``SKILL = XxxSkill()``（外加必要的向后兼容函数）
``skill.py``     ``XxxSkill`` 类，继承 :class:`BaseSkill`，实现 ``run()``
``schema.py``    pydantic 输入/输出模型，是 skill 对外的数据契约
``SKILL.md``     人读文档：用途、入参、出参、示例、确定性声明、副作用
===============  ==========================================================

注册是**自动**的：:meth:`SkillRegistry.discover` 扫描 ``src/skills/`` 下的子包，
凡导出了 ``SKILL`` 实例的都会被登记。单个 skill 导入失败只会跳过它并记日志，
不会拖垮整个启动流程。

三层调用方式（按场景选，不要混用）
----------------------------------
1. ``SkillRegistry.run(skill_id, payload)``  —— 统一入口，带审计日志，给 API / Agent 用；
2. ``SkillRegistry.get(id).execute(**kwargs)`` —— 直接调用，给内部代码用（仍走 pydantic 校验）；
3. ``skill.to_langchain_tool()`` —— 暴露为 LangChain Tool，给 ReAct 风格的 LLM 用。
"""

from __future__ import annotations

import importlib
import pkgutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from src.observability import get_logger, tool_call

LOGGER = get_logger("skills")


@dataclass(frozen=True, slots=True)
class SkillExample:
    """One documented invocation, used by /api/skills and by LLM tool selection."""

    description: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation."""

        return {"description": self.description, "payload": self.payload}


@dataclass(frozen=True, slots=True)
class SkillMeta:
    """Declared identity of one skill — the contract the registry and the API rely on."""

    id: str
    name: str
    version: str
    description: str
    #: 能力分组：analysis | prediction | knowledge | report | operations
    category: str
    #: True 表示纯确定性算法（无 LLM），同样输入必然得到同样输出
    deterministic: bool = True
    #: True 表示该 skill 内部会调用大模型
    requires_llm: bool = False
    tags: tuple[str, ...] = ()
    #: 该 skill 依赖的其他 skill id（用于依赖图展示，registry 不自动解析依赖）
    dependencies: tuple[str, ...] = ()
    #: 副作用声明：read（只读）/ write（会写库或落盘）。前端据此提示用户。
    side_effects: str = "read"
    examples: tuple[SkillExample, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation for the API and the frontend."""

        return {
            "id": self.id, "name": self.name, "version": self.version, "description": self.description,
            "category": self.category, "deterministic": self.deterministic, "requires_llm": self.requires_llm,
            "tags": list(self.tags), "dependencies": list(self.dependencies), "side_effects": self.side_effects,
            "examples": [item.as_dict() for item in self.examples],
        }


class BaseSkill:
    """Base class every skill must implement.

    子类用 ``ClassVar`` 声明三件套，registry 与 API 才能在**不实例化内部逻辑**的前提下
    读取元数据、生成 LangChain Tool、校验输入输出。
    """

    meta: ClassVar[SkillMeta]
    input_schema: ClassVar[type[BaseModel]]
    output_schema: ClassVar[type[BaseModel]]

    def run(self, payload: BaseModel) -> BaseModel:
        """Execute the skill. Subclasses override this with the real algorithm."""

        raise NotImplementedError

    def execute(self, **kwargs: Any) -> BaseModel:
        """Validate keyword arguments against the input schema, then run."""

        return self.run(self.input_schema(**kwargs))

    # ------------------------------------------------------------------ introspection

    def describe(self) -> dict[str, Any]:
        """Return full metadata plus the JSON schemas of input/output."""

        payload = self.meta.as_dict()
        payload["input_schema"] = self.input_schema.model_json_schema()
        payload["output_schema"] = self.output_schema.model_json_schema()
        return payload

    def health_check(self) -> dict[str, Any]:
        """Report whether the skill can run right now.

        默认只检查"能否实例化输入模型"。需要外部依赖（数据库、Ollama、向量库）的
        skill 应重写此方法，返回真实的可用性探测结果。
        """

        try:
            self.input_schema.model_construct()
        except Exception as error:  # noqa: BLE001 - 健康检查要把任何异常变成状态
            return {"id": self.meta.id, "healthy": False, "reason": f"{type(error).__name__}: {error}"}
        return {"id": self.meta.id, "healthy": True, "reason": "ok"}

    # ------------------------------------------------------------------ interop

    def to_langchain_tool(self) -> StructuredTool:
        """Expose the skill as a LangChain ``StructuredTool`` for ReAct-style agents."""

        def _invoke(**kwargs: Any) -> dict[str, Any]:
            return self.run(self.input_schema(**kwargs)).model_dump()

        return StructuredTool(
            name=self.meta.id,
            description=self.meta.description,
            args_schema=self.input_schema,
            func=_invoke,
        )


class SkillRegistry:
    """Auto-discover skill packages and expose them by id.

    用法::

        from src.skills import SkillRegistry
        SkillRegistry.discover()                       # 启动时调用一次
        SkillRegistry.get("rul_weibull").execute(...)  # 或直接查 /api/skills
    """

    _skills: ClassVar[dict[str, BaseSkill]] = {}

    @classmethod
    def discover(cls, root: Path | None = None) -> list[SkillMeta]:
        """Scan skill sub-packages and register every exported ``SKILL`` instance."""

        root = root or Path(__file__).parent
        for module in pkgutil.iter_modules([str(root)]):
            if not module.ispkg:
                continue
            try:
                package = importlib.import_module(f"src.skills.{module.name}")
            except Exception as error:  # noqa: BLE001 - 单个 skill 失败不能拖垮启动
                LOGGER.error(f"skill discover failed: {module.name}: {type(error).__name__}: {error}")
                continue
            skill = getattr(package, "SKILL", None)
            if isinstance(skill, BaseSkill):
                cls._skills[skill.meta.id] = skill
        LOGGER.info(f"discovered {len(cls._skills)} skills: {sorted(cls._skills)}")
        return cls.all()

    @classmethod
    def register(cls, skill: BaseSkill) -> None:
        """Register one skill instance manually (useful for tests and dynamic plugins)."""

        cls._skills[skill.meta.id] = skill

    @classmethod
    def get(cls, skill_id: str) -> BaseSkill:
        """Return a skill by id, raising a helpful error listing valid ids."""

        try:
            return cls._skills[skill_id]
        except KeyError as error:
            raise KeyError(f"Unknown skill '{skill_id}'. Known skills: {sorted(cls._skills)}") from error

    @classmethod
    def all(cls) -> list[SkillMeta]:
        """Return metadata for every registered skill, sorted by id."""

        return [skill.meta for skill in sorted(cls._skills.values(), key=lambda item: item.meta.id)]

    @classmethod
    def categories(cls) -> list[str]:
        """Return every distinct skill category, sorted."""

        return sorted({skill.meta.category for skill in cls._skills.values()})

    @classmethod
    def describe_all(cls) -> list[dict[str, Any]]:
        """Return full metadata (including JSON schemas) for every skill."""

        return [cls._skills[meta.id].describe() for meta in cls.all()]

    @classmethod
    def health_check_all(cls) -> list[dict[str, Any]]:
        """Probe every skill and report availability — surfaced on the Skills page."""

        reports: list[dict[str, Any]] = []
        for skill_id in sorted(cls._skills):
            try:
                reports.append(cls._skills[skill_id].health_check())
            except Exception as error:  # noqa: BLE001 - 健康检查不应抛出异常打断整体
                reports.append({"id": skill_id, "healthy": False, "reason": f"{type(error).__name__}: {error}"})
        return reports

    @classmethod
    def run(cls, skill_id: str, payload: dict[str, Any], *, caller: str = "api") -> dict[str, Any]:
        """Validate, execute and audit one skill call.

        这是**唯一**的 skill 执行入口：所有调用都会留下 tool_calls.jsonl 审计记录，
        于是"Agent 到底调了什么"是可查的。
        """

        skill = cls.get(skill_id)
        started = time.perf_counter()
        try:
            result = skill.run(skill.input_schema(**payload))
        except Exception as error:  # noqa: BLE001 - 记录后原样抛出，交由上层决定降级策略
            tool_call(
                caller=caller, tool=skill_id, args=payload,
                latency_ms=(time.perf_counter() - started) * 1000, ok=False,
                deterministic=skill.meta.deterministic, error=f"{type(error).__name__}: {error}",
            )
            raise
        output = result.model_dump() if hasattr(result, "model_dump") else dict(result)
        tool_call(
            caller=caller, tool=skill_id, args=payload,
            latency_ms=(time.perf_counter() - started) * 1000, ok=True,
            deterministic=skill.meta.deterministic, output_preview=str(output)[:800],
        )
        return output

    @classmethod
    def as_langchain_tools(cls, *, categories: tuple[str, ...] | None = None) -> list[StructuredTool]:
        """Return registered skills as LangChain tools, optionally filtered by category."""

        return [
            skill.to_langchain_tool()
            for skill in cls._skills.values()
            if categories is None or skill.meta.category in categories
        ]

    @classmethod
    def clear(cls) -> None:
        """Drop every registration — used by tests to get a clean slate."""

        cls._skills.clear()
