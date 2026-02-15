"""LangGraph graph assembly for the Skills Agent.

Constructs the Two-Layer Loop Architecture:
    Outer Loop: Step Router -> Prepare -> Inner Loop -> Commit -> Router
    Inner Loop: Optimizer -> Tools -> Evaluator -> (PASS->Commit | FAIL->Optimizer)

The planner runs separately before the execution graph so that
human approval can be requested immediately after planning — before any
steps are executed.
"""

from __future__ import annotations

import logging

from langgraph.graph import END, StateGraph

from skills_agent.models import AgentState
from skills_agent.nodes import (
    commit_step,
    evaluator_agent,
    optimizer_agent,
    planner,
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
    """Placeholder node reached when retries are exhausted.

    Resets retry count so the loop can try again.
    """
    logger.warning(
        "[human_intervention] Node Input — step_index: %d | retry_count: %d (retries exhausted)",
        state["current_step_index"],
        state["step_retry_count"],
    )
    logger.info(
        "[human_intervention] Memory State — skill_memory: %r | messages: %d",
        (state.get("skill_memory", "")[:200] or "(empty)"),
        len(state.get("messages", [])),
    )
    logger.info("[human_intervention] Node Output — reset step_retry_count to 0")
    return {"step_retry_count": 0}


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def build_parser_graph() -> StateGraph:
    """Build a minimal graph that only runs the planner node.

    Returns:
        Compiled LangGraph that plans input into a SkillPlan.
    """
    graph = StateGraph(AgentState)
    graph.add_node("planner", planner)
    graph.set_entry_point("planner")
    graph.add_edge("planner", END)
    compiled = graph.compile()
    logger.info("Planner graph compiled")
    return compiled


def build_execution_graph() -> StateGraph:
    """Build the execution graph (post-approval).

    Entry point is prepare_step_context — expects state already populated
    with parsed steps from the planner graph.

    Returns:
        Compiled LangGraph ready for step execution.
    """
    graph = StateGraph(AgentState)

    # --- Add nodes (no planner — already ran) ---
    graph.add_node("prepare_step_context", prepare_step_context)
    graph.add_node("optimizer_agent", optimizer_agent)
    graph.add_node("tool_executor", tool_executor)
    graph.add_node("evaluator_agent", evaluator_agent)
    graph.add_node("commit_step", commit_step)
    graph.add_node("human_intervention", human_intervention)

    # --- Entry point: start executing steps ---
    graph.set_entry_point("prepare_step_context")

    # --- Edges ---

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

    compiled = graph.compile()
    logger.info("Execution graph compiled")
    return compiled
