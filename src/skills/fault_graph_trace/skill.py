"""Causal fault-graph traversal — explain "how did we get here" or "what happens next".

两种方向
--------
* ``forward``  顺推：从症状/故障出发，看**会导致什么后果**（用于风险外推）；
* ``backward`` 回溯：从观测到的症状出发，看**可能的根因链**（用于根因定位）。

路径概率 = 边上概率的连乘；滞后时间保留区间字符串，不取中点，避免假装精确。
"""

from __future__ import annotations

from typing import Any

from src.knowledge.fault_graph import FaultGraph

from ..base import BaseSkill, SkillExample, SkillMeta
from .schema import CausalPath, FaultGraphTraceInput, FaultGraphTraceOutput

#: 中文故障名 → 因果图节点 id（与 evidence_chain 保持一致）
GRAPH_KEY_MAP: dict[str, str] = {
    "轴承磨损": "bearing_wear",
    "散热系统故障": "cooling_failure",
    "电源模块故障": "power_module_failure",
    "电机过载": "motor_overload",
    "转子故障": "rotor_fault",
}


def reason_fault(graph: FaultGraph, node: str, direction: str = "forward") -> list[dict[str, Any]]:
    """Return serialized causal paths in the requested direction.

    保留旧签名以兼容历史调用；新调用方请用 ``FaultGraphTraceSkill.run()``。
    """

    payload = FaultGraphTraceInput(node=node, direction=direction)
    return [item.model_dump() for item in FaultGraphTraceSkill(graph).run(payload).paths]


class FaultGraphTraceSkill(BaseSkill):
    """Traverse the FMECA causal graph forward (consequences) or backward (root causes)."""

    meta = SkillMeta(
        id="fault_graph_trace",
        name="故障因果图溯源",
        version="2.0.0",
        description=(
            "在 FMECA 因果传播图上做有向遍历：forward 顺推后果链，backward 回溯根因链。"
            "路径概率为边概率连乘，滞后时间保留区间（如 24-168h）而非取中点，避免假装精确。"
        ),
        category="analysis",
        deterministic=True,
        requires_llm=False,
        tags=("fault_graph", "causal", "E6", "root_cause"),
        examples=(
            SkillExample(
                description="回溯「主轴失效」可能的根因链",
                payload={"node": "spindle_failure", "direction": "backward", "max_depth": 3},
            ),
            SkillExample(
                description="顺推「散热系统故障」会导致什么",
                payload={"node": "cooling_failure", "direction": "forward"},
            ),
        ),
    )
    input_schema = FaultGraphTraceInput
    output_schema = FaultGraphTraceOutput

    def __init__(self, graph: FaultGraph | None = None) -> None:
        self._graph = graph

    @property
    def graph(self) -> FaultGraph:
        """Lazily build the shared causal graph so importing this skill stays cheap."""

        if self._graph is None:
            self._graph = FaultGraph()
        return self._graph

    def run(self, payload: FaultGraphTraceInput) -> FaultGraphTraceOutput:
        """Traverse the graph and return paths ordered by descending probability."""

        node = GRAPH_KEY_MAP.get(payload.node, payload.node)
        paths = (
            self.graph.forward_trace(node, max_depth=payload.max_depth)
            if payload.direction == "forward"
            else self.graph.backward_trace(node, max_depth=payload.max_depth)
        )
        items = [
            CausalPath(nodes=list(item.nodes), probability=float(item.probability), lag_hours=str(item.lag_hours))
            for item in paths
        ]
        known_nodes = sorted(self.graph.graph.nodes)
        return FaultGraphTraceOutput(
            node=node, direction=payload.direction, paths=items, count=len(items),
            known_nodes=[] if items else known_nodes,
        )

    def health_check(self) -> dict[str, Any]:
        """Report the graph size — a zero-node graph means the skill is unusable."""

        try:
            node_count = self.graph.graph.number_of_nodes()
        except Exception as error:  # noqa: BLE001 - 健康检查要把异常变成状态
            return {"id": self.meta.id, "healthy": False, "reason": f"{type(error).__name__}: {error}"}
        return {
            "id": self.meta.id, "healthy": node_count > 0,
            "reason": f"因果图已加载 {node_count} 个节点" if node_count else "因果图为空",
        }
