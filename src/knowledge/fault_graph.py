"""Causal fault-propagation graph used by diagnostics and the topology UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import networkx as nx


@dataclass(frozen=True, slots=True)
class GraphPath:
    """A causal path with edge probability and approximate time lag."""

    nodes: list[str]
    probability: float
    lag_hours: str


class FaultGraph:
    """Build a compact explainable graph from FMECA domain relationships."""

    def __init__(self) -> None:
        self.graph = nx.DiGraph()
        self._build()

    def _build(self) -> None:
        """Create symptom→fault→consequence relationships from engineering knowledge."""

        nodes = {
            "voltage_instability": ("symptom", "电压波动"), "current_spike": ("symptom", "电流升高"), "temperature_rise": ("symptom", "温度升高"), "vibration_increase": ("symptom", "振动升高"), "speed_drop": ("symptom", "转速下降"),
            "power_module_failure": ("fault", "电源模块故障"), "bearing_wear": ("fault", "轴承磨损"), "motor_overload": ("fault", "电机过载"), "cooling_failure": ("fault", "散热系统故障"), "rotor_fault": ("fault", "转子故障"),
            "motor_overheating": ("consequence", "电机过热"), "spindle_failure": ("consequence", "主轴失效"), "quality_degradation": ("consequence", "加工质量下降"), "machine_downtime": ("consequence", "设备停机"),
        }
        self.graph.add_nodes_from((name, {"type": kind, "label": label}) for name, (kind, label) in nodes.items())
        edges = (
            ("voltage_instability", "power_module_failure", 0.78, "2-6h"), ("power_module_failure", "current_spike", 0.70, "1-3h"), ("current_spike", "motor_overheating", 0.65, "1-2h"), ("motor_overheating", "motor_overload", 0.85, "0.5-2h"), ("motor_overload", "machine_downtime", 0.92, "0.1-0.5h"),
            ("temperature_rise", "bearing_wear", 0.72, "24-168h"), ("vibration_increase", "bearing_wear", 0.82, "24-168h"), ("speed_drop", "rotor_fault", 0.76, "2-24h"), ("bearing_wear", "spindle_failure", 0.78, "24-96h"), ("rotor_fault", "quality_degradation", 0.70, "1-12h"), ("cooling_failure", "temperature_rise", 0.88, "0.5-4h"), ("spindle_failure", "machine_downtime", 0.95, "0.1-2h"),
        )
        self.graph.add_edges_from((source, target, {"probability": probability, "lag_hours": lag}) for source, target, probability, lag in edges)

    def forward_trace(self, start_node: str, max_depth: int = 5) -> list[GraphPath]:
        """Trace consequences forward from a symptom or fault."""

        return self._trace(start_node, forward=True, max_depth=max_depth)

    def backward_trace(self, node: str, max_depth: int = 5) -> list[GraphPath]:
        """Trace likely causes backward from an observed symptom or consequence."""

        return self._trace(node, forward=False, max_depth=max_depth)

    def get_path(self, start: str, end: str) -> GraphPath | None:
        """Return the highest-probability simple path between two nodes."""

        if start not in self.graph or end not in self.graph:
            return None
        candidates = list(nx.all_simple_paths(self.graph, start, end))
        if not candidates:
            return None
        path = max(candidates, key=self._path_probability)
        return self._path(path)

    def topology(self) -> dict[str, list[dict[str, object]]]:
        """Serialize graph nodes and edges for the knowledge-graph screen."""

        return {"nodes": [{"id": node, **data} for node, data in self.graph.nodes(data=True)], "edges": [{"source": source, "target": target, **data} for source, target, data in self.graph.edges(data=True)]}

    def _trace(self, start: str, forward: bool, max_depth: int) -> list[GraphPath]:
        """Internal bounded traversal that keeps causal probability explainable."""

        if start not in self.graph:
            return []
        paths: list[GraphPath] = []
        neighbors = nx.descendants(self.graph, start) if forward else nx.ancestors(self.graph, start)
        for target in neighbors:
            candidates = nx.all_simple_paths(self.graph, start, target) if forward else nx.all_simple_paths(self.graph.reverse(copy=False), start, target)
            for path in candidates:
                if len(path) <= max_depth + 1:
                    normalized = list(path) if forward else list(reversed(path))
                    paths.append(self._path(normalized))
        paths.sort(key=lambda item: item.probability, reverse=True)
        return paths[:10]

    def _path(self, path: Iterable[str]) -> GraphPath:
        """Convert a sequence of graph nodes to an explainable path."""

        nodes = list(path)
        probability = self._path_probability(nodes)
        lags = [str(self.graph.edges[nodes[i], nodes[i + 1]].get("lag_hours", "")) for i in range(len(nodes) - 1)]
        return GraphPath(nodes, round(probability, 4), " → ".join(lags))

    def _path_probability(self, path: Iterable[str]) -> float:
        """Multiply edge probabilities along one path."""

        nodes = list(path)
        probability = 1.0
        for index in range(len(nodes) - 1):
            probability *= float(self.graph.edges[nodes[index], nodes[index + 1]].get("probability", 1.0))
        return probability
