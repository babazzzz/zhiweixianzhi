"""Input/output contracts for the causal fault-graph trace skill."""

from __future__ import annotations

from pydantic import BaseModel, Field


class FaultGraphTraceInput(BaseModel):
    """A starting node plus the traversal direction."""

    node: str = Field(min_length=1, max_length=64, description="起始节点 id，如 bearing_wear")
    direction: str = Field(default="forward", pattern=r"^(forward|backward)$", description="forward=顺推后果；backward=回溯根因")
    max_depth: int = Field(default=3, ge=1, le=6, description="最大传播深度")


class CausalPath(BaseModel):
    """One propagation path with its joint probability and lag.

    ``lag_hours`` 沿用因果图里的区间字符串（如 ``"24-168h"``），因为边的滞后本身
    就是一个范围，强行取中点会丢掉不确定性信息。
    """

    nodes: list[str] = Field(default_factory=list)
    probability: float = 0.0
    lag_hours: str = ""


class FaultGraphTraceOutput(BaseModel):
    """All discovered causal paths, ordered by descending probability."""

    node: str
    direction: str
    paths: list[CausalPath] = Field(default_factory=list)
    count: int = 0
    #: 起始节点在图中不存在时给出可用节点，避免调用方瞎猜
    known_nodes: list[str] = Field(default_factory=list)
