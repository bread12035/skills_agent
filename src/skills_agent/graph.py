"""LangGraph graph assembly for the Skills Agent.

Constructs the Two-Layer Loop Architecture:
    Outer Loop: Step Router → Prepare → Inner Loop → Commit → Router
    Inner Loop: Optimizer → Tools → Evaluator → (PASS→Commit | FAIL→Optimizer)

Supports:
    - SqliteSaver for L4 checkpoint persistence
    - Human-in-the-loop interrupts (plan approval, retry exhaustion)
"""

from __future__ import annotations

import logging
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph

from skills_agent.models import AgentState
from skills_agent.nodes import (
    commit_step,
    evaluator_agent,
    optimizer_agent,
    prepare_step_context,
    route_evaluator_output,
    route_optimizer_output,
    route_step,
    skill_parser,
    tool_executor,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Human intervention node (placeholder for interrupt)
# ---------------------------------------------------------------------------


def human_intervention(state: AgentState) -> dict:
    """Placeholder node that triggers a LangGraph interrupt.

    When reached, the graph suspends and waits for external input.
    The caller can resume with updated state (e.g., manual fix, skip step).
    """
    logger.warning(
        "Human intervention requested at step %d (retries exhausted)",
        state["current_step_index"],
    )
    # The actual interrupt is configured via interrupt_before on this node.
    # When resumed, the graph continues from this node.
    # Reset retry count so the loop can try again after human input.
    return {"step_retry_count": 0}


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

_DB_PATH = Path(__file__).resolve().parents[2] / "checkpoints.sqlite"


def build_graph(
    db_path: Path = _DB_PATH,
    checkpointer: SqliteSaver | None = None,
) -> StateGraph:
    """Build and compile the Skills Agent LangGraph.

    Args:
        db_path: Path for the SQLite checkpoint database.
        checkpointer: Optional pre-built checkpointer (for testing).

    Returns:
        Compiled LangGraph ready for invocation.
    """
    graph = StateGraph(AgentState)

    # --- Add nodes ---
    graph.add_node("skill_parser", skill_parser)
    graph.add_node("prepare_step_context", prepare_step_context)
    graph.add_node("optimizer_agent", optimizer_agent)
    graph.add_node("tool_executor", tool_executor)
    graph.add_node("evaluator_agent", evaluator_agent)
    graph.add_node("commit_step", commit_step)
    graph.add_node("human_intervention", human_intervention)

    # --- Entry point ---
    graph.set_entry_point("skill_parser")

    # --- Edges ---

    # After parsing: interrupt for plan approval, then route to first step
    graph.add_conditional_edges(
        "skill_parser",
        route_step,
        {
            "prepare_step_context": "prepare_step_context",
            "end": END,
        },
    )

    # After preparing context: always go to optimizer
    graph.add_edge("prepare_step_context", "optimizer_agent")

    # After optimizer: tool call or evaluation
    graph.add_conditional_edges(
        "optimizer_agent",
        route_optimizer_output,
        {
            "tool_executor": "tool_executor",
            "evaluator_agent": "evaluator_agent",
        },
    )

    # After tool execution: always return to optimizer (to process results)
    graph.add_edge("tool_executor", "optimizer_agent")

    # After evaluator: pass/fail routing
    graph.add_conditional_edges(
        "evaluator_agent",
        route_evaluator_output,
        {
            "commit_step": "commit_step",
            "optimizer_agent": "optimizer_agent",
            "human_intervention": "human_intervention",
        },
    )

    # After commit: route to next step or end
    graph.add_conditional_edges(
        "commit_step",
        route_step,
        {
            "prepare_step_context": "prepare_step_context",
            "end": END,
        },
    )

    # After human intervention: retry the step
    graph.add_edge("human_intervention", "prepare_step_context")

    # --- Compile with checkpointer and interrupts ---
    if checkpointer is None:
        checkpointer = SqliteSaver.from_conn_string(str(db_path))

    compiled = graph.compile(
        checkpointer=checkpointer,
        interrupt_before=["human_intervention"],
        interrupt_after=["skill_parser"],
    )

    logger.info("Graph compiled with checkpoint DB: %s", db_path)
    return compiled
