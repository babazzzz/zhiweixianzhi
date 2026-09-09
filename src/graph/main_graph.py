"""LangGraph supervisor workflow for one event-driven machine diagnostic."""

from __future__ import annotations

from typing import Callable

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from src.agents.decision_agent import decision_node
from src.agents.diagnostic_agent import diagnostic_node
from src.agents.knowledge_agent import knowledge_node
from src.agents.monitor_agent import monitor_node
from src.agents.prediction_agent import prediction_node
from src.agents.reflection_agent import reflection_node
from src.agents.report_agent import report_node
from src.agents.state import DiagnosticState
from src.database.connection import Database
from src.knowledge.fault_graph import FaultGraph


def build_graph(database: Database, fault_graph: FaultGraph | None = None):
    """Compile the supervisor graph with a memory checkpointer."""

    causal_graph = fault_graph or FaultGraph()

    async def monitor(state: DiagnosticState) -> dict[str, object]:
        """Bind the database dependency to the monitor node."""

        return await monitor_node(state, database)

    async def diagnostic(state: DiagnosticState) -> dict[str, object]:
        """Bind repository and causal graph to the diagnostic node."""

        return await diagnostic_node(state, database, causal_graph)

    async def knowledge(state: DiagnosticState) -> dict[str, object]:
        """Bind repository and causal graph to the knowledge node."""

        return await knowledge_node(state, database, causal_graph)

    async def prediction(state: DiagnosticState) -> dict[str, object]:
        """Bind repository to the prediction node."""

        return await prediction_node(state, database)

    async def decision(state: DiagnosticState) -> dict[str, object]:
        """Bind repository to the decision node."""

        return await decision_node(state, database)

    workflow = StateGraph(DiagnosticState)
    workflow.add_node("monitor", monitor)
    workflow.add_node("diagnostic", diagnostic)
    workflow.add_node("knowledge", knowledge)
    workflow.add_node("prediction", prediction)
    workflow.add_node("decision", decision)
    workflow.add_node("report", report_node)
    workflow.add_node("reflection", reflection_node)
    workflow.add_edge(START, "monitor")
    workflow.add_conditional_edges("monitor", route_monitor, {"diagnostic": "diagnostic", "end": END})
    workflow.add_edge("diagnostic", "knowledge")
    workflow.add_edge("knowledge", "prediction")
    workflow.add_edge("prediction", "decision")
    workflow.add_edge("decision", "report")
    workflow.add_edge("report", "reflection")
    workflow.add_conditional_edges("reflection", route_reflection, {"retry": "diagnostic", "end": END})
    return workflow.compile(checkpointer=MemorySaver())


def route_monitor(state: DiagnosticState) -> str:
    """Route normal telemetry to monitoring end and anomalies to diagnosis."""

    return "diagnostic" if bool(state.get("is_anomaly")) and not state.get("error") else "end"


def route_reflection(state: DiagnosticState) -> str:
    """Retry diagnosis only while the bounded reflection budget remains."""

    return "end" if bool(state.get("reflection_passed")) or int(state.get("retry_count", 0)) >= int(state.get("max_retries", 3)) else "retry"


def run_graph(graph, machine_id: str, thread_id: str | None = None) -> DiagnosticState:
    """Invoke a compiled graph for a machine and return its final state."""

    run_id = thread_id or machine_id
    result = graph.invoke({"machine_id": machine_id, "retry_count": 0, "max_retries": 3, "events": [], "messages": []}, {"configurable": {"thread_id": run_id}})
    return result
