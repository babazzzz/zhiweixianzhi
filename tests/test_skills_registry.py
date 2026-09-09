"""Skills 注册表测试：自动发现、按 id 取用、统一执行入口、LangChain 工具暴露。

为保持零副作用，多数用例通过 ``SkillRegistry.register`` 注入一个本地定义的
测试 skill，不触碰生产数据库；仅 ``test_discover`` 走真实的自动发现路径。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.skills.base import BaseSkill, SkillExample, SkillMeta, SkillRegistry


class _In(BaseModel):
    value: float = Field(..., ge=0)


class _Out(BaseModel):
    squared: float


class _SquareSkill(BaseSkill):
    """A toy deterministic skill for exercising the registry contract."""

    meta = SkillMeta(
        id="test_square", name="平方", version="0.0.1",
        description="把输入平方，纯确定性。", category="analysis",
        deterministic=True, requires_llm=False,
        examples=(SkillExample(description="平方 3", payload={"value": 3.0}),),
    )
    input_schema = _In
    output_schema = _Out

    def run(self, payload: _In) -> _Out:
        return _Out(squared=payload.value * payload.value)


def test_register_and_get():
    """register 后可通过 get(id) 取回同一实例。"""
    SkillRegistry.clear()
    skill = _SquareSkill()
    SkillRegistry.register(skill)
    assert SkillRegistry.get("test_square") is skill
    SkillRegistry.clear()


def test_get_unknown_raises_helpful_error():
    """查询未注册 id 应抛出带可用清单的错误。"""
    SkillRegistry.clear()
    SkillRegistry.register(_SquareSkill())
    try:
        SkillRegistry.get("nope")
        raise AssertionError("应当抛出 KeyError")
    except KeyError as error:
        assert "test_square" in str(error)
    SkillRegistry.clear()


def test_registry_run_returns_dict():
    """统一执行入口 run(id, payload) 返回可 JSON 化的 dict。"""
    SkillRegistry.clear()
    SkillRegistry.register(_SquareSkill())
    out = SkillRegistry.run("test_square", {"value": 3.0})
    assert isinstance(out, dict)
    assert out["squared"] == 9.0
    SkillRegistry.clear()


def test_describe_includes_json_schemas():
    """describe 应附带输入/输出的 JSON Schema，供前端与 LLM 工具选择用。"""
    SkillRegistry.clear()
    SkillRegistry.register(_SquareSkill())
    desc = SkillRegistry.get("test_square").describe()
    assert desc["id"] == "test_square"
    assert "input_schema" in desc and "output_schema" in desc
    SkillRegistry.clear()


def test_to_langchain_tool():
    """skill 应能暴露为 LangChain StructuredTool，供 ReAct 型 agent 调用。"""
    SkillRegistry.clear()
    SkillRegistry.register(_SquareSkill())
    tool = SkillRegistry.get("test_square").to_langchain_tool()
    assert tool.name == "test_square"
    # StructuredTool 可直接调用（同步 func）
    result = tool.invoke({"value": 4.0})
    assert result["squared"] == 16.0
    SkillRegistry.clear()


def test_discover_finds_real_skills():
    """discover 应自动扫描 src/skills 子包并注册已实现的 skill。"""
    SkillRegistry.clear()
    metas = SkillRegistry.discover()
    ids = {m.id for m in metas}
    # 计划书要求的关键 skill 都在
    assert {"rul_weibull", "maintenance_report", "anomaly_detect"}.issubset(ids), ids
    assert "maintenance_report" in {s.meta.id for s in SkillRegistry._skills.values()}
    SkillRegistry.clear()
