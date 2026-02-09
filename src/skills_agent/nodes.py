"""LangGraph node implementations for the Skills Agent workflow.

Nodes:
    1. skill_parser       — parse user input into a SkillPlan
    2. prepare_step_context — prepare context for the next step
    3. optimizer_agent     — execute the step using tools
    4. evaluator_agent     — verify step completion
    5. commit_step         — persist outputs and advance index
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage
from langgraph.prebuilt import ToolNode

from skills_agent.memory import (
    append_skill_memory,
    clear_loop_messages,
    format_skill_memory,
    load_global_context,
)
from skills_agent.models import (
    AgentState,
    EvalResult,
    EvaluationOutput,
    SkillPlan,
    StepSchema,
)
from skills_agent.prompts import (
    EVALUATOR_SYSTEM,
    OPTIMIZER_SYSTEM,
    SKILL_PARSER_SYSTEM,
)
from skills_agent.tools import ALL_TOOLS, READONLY_TOOLS, get_tool_descriptions

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM instances
# ---------------------------------------------------------------------------

_DEFAULT_MODEL = "gpt-oss"


def _get_llm(model: str = _DEFAULT_MODEL, **kwargs: Any) -> ChatOpenAI:
    base_url = os.environ.get("OPENAI_API_BASE")
    if base_url:
        kwargs.setdefault("openai_api_base", base_url)
    return ChatOpenAI(model=model, **kwargs)


# ---------------------------------------------------------------------------
# Node 0: Skill Parser
# ---------------------------------------------------------------------------


def skill_parser(state: AgentState) -> dict[str, Any]:
    """Parse raw user input into a structured SkillPlan.

    Uses LLM with structured output to decompose instructions into steps.
    """
    llm = _get_llm().with_structured_output(SkillPlan)

    result: SkillPlan = llm.invoke(
        [
            SystemMessage(content=SKILL_PARSER_SYSTEM),
            HumanMessage(content=state["raw_input"]),
        ]
    )

    logger.info("Parsed plan with %d steps: %s", len(result.steps), result.goal)

    return {
        "steps": result.steps,
        "current_step_index": 0,
        "step_retry_count": 0,
        "skill_memory": "",
        "last_evaluation": "",
        "plan_approved": False,
    }


# ---------------------------------------------------------------------------
# Node 1: Prepare Step Context
# ---------------------------------------------------------------------------


def prepare_step_context(state: AgentState) -> dict[str, Any]:
    """Prepare context for the current step — clear L3 messages, build prompt."""
    step: StepSchema = state["steps"][state["current_step_index"]]
    global_context = load_global_context()
    tool_docs = get_tool_descriptions()

    system_prompt = OPTIMIZER_SYSTEM.format(
        instruction=step.instruction,
        skill_memory=format_skill_memory(state["skill_memory"]),
        global_context=global_context,
        tool_docs=tool_docs,
    )

    # Clear L3: remove all existing messages and start fresh with system context
    remove_msgs = [RemoveMessage(id=m.id) for m in state["messages"]]

    return {
        "messages": remove_msgs
        + [
            SystemMessage(content=system_prompt),
            HumanMessage(
                content=(
                    f"Execute Step {step.index}: {step.instruction}\n\n"
                    f"Success criteria: {step.criteria}"
                )
            ),
        ],
        "step_retry_count": 0,
        "last_evaluation": "",
    }


# ---------------------------------------------------------------------------
# Node 2: Optimizer Agent
# ---------------------------------------------------------------------------


def optimizer_agent(state: AgentState) -> dict[str, Any]:
    """Invoke the Optimizer LLM to execute the current step.

    The Optimizer has access to all tools (safe_cli_executor, safe_py_runner).
    It will either make tool calls or return a text summary when done.
    """
    llm = _get_llm().bind_tools(ALL_TOOLS)
    response: AIMessage = llm.invoke(state["messages"])

    logger.info(
        "Optimizer response: tool_calls=%d, text_len=%d",
        len(response.tool_calls) if response.tool_calls else 0,
        len(response.content) if isinstance(response.content, str) else 0,
    )

    return {"messages": [response]}


# ---------------------------------------------------------------------------
# Node 2b: Tool Execution (Security Gateway)
# ---------------------------------------------------------------------------

# The ToolNode automatically routes tool calls through our @tool-decorated
# functions which embed the Security Gateway validation.
tool_executor = ToolNode(ALL_TOOLS)


# ---------------------------------------------------------------------------
# Node 3: Evaluator Agent
# ---------------------------------------------------------------------------


def evaluator_agent(state: AgentState) -> dict[str, Any]:
    """Evaluate whether the Optimizer successfully completed the step.

    Returns structured EvaluationOutput (PASS/FAIL + feedback).
    """
    step: StepSchema = state["steps"][state["current_step_index"]]

    system_prompt = EVALUATOR_SYSTEM.format(
        instruction=step.instruction,
        criteria=step.criteria,
        skill_memory=format_skill_memory(state["skill_memory"]),
    )

    llm = _get_llm().bind_tools(READONLY_TOOLS).with_structured_output(
        EvaluationOutput
    )

    evaluation: EvaluationOutput = llm.invoke(
        [SystemMessage(content=system_prompt)]
        + state["messages"],
    )

    logger.info(
        "Evaluator verdict: %s — %s",
        evaluation.verdict.value,
        evaluation.feedback[:100],
    )

    # Inject feedback into message stream for the Optimizer to see on retry
    feedback_msg = HumanMessage(
        content=(
            f"[Evaluator] Verdict: {evaluation.verdict.value}\n"
            f"Feedback: {evaluation.feedback}"
        )
    )

    return {
        "messages": [feedback_msg],
        "last_evaluation": evaluation.model_dump_json(),
        "step_retry_count": state["step_retry_count"] + 1,
    }


# ---------------------------------------------------------------------------
# Node 4: Commit Step
# ---------------------------------------------------------------------------


def commit_step(state: AgentState) -> dict[str, Any]:
    """Commit the current step's outputs to skill memory and advance the index."""
    evaluation = EvaluationOutput.model_validate_json(state["last_evaluation"])

    new_memory = append_skill_memory(
        state["skill_memory"], evaluation.key_outputs
    )

    step = state["steps"][state["current_step_index"]]
    logger.info(
        "Committed step %d: %s (outputs: %s)",
        step.index,
        step.instruction[:60],
        list(evaluation.key_outputs.keys()),
    )

    return {
        "skill_memory": new_memory,
        "current_step_index": state["current_step_index"] + 1,
    }


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------


def route_step(state: AgentState) -> str:
    """Router: decide whether to continue to next step or finish.

    Returns the name of the next node.
    """
    if state["current_step_index"] >= len(state["steps"]):
        return "end"
    return "prepare_step_context"


def route_optimizer_output(state: AgentState) -> str:
    """After Optimizer: route to tool execution or evaluator.

    If the last message has tool_calls -> run tools.
    Otherwise (plain text) -> evaluate.
    """
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        return "tool_executor"
    return "evaluator_agent"


def route_evaluator_output(state: AgentState) -> str:
    """After Evaluator: decide PASS/FAIL routing.

    PASS  -> commit_step
    FAIL  -> check retry count
        under max -> optimizer_agent (retry)
        over max  -> human_intervention (interrupt)
    """
    evaluation = EvaluationOutput.model_validate_json(state["last_evaluation"])

    if evaluation.verdict == EvalResult.PASS:
        return "commit_step"

    max_retries = state.get("max_retries", 3)
    if state["step_retry_count"] < max_retries:
        logger.info(
            "Retry %d/%d for current step",
            state["step_retry_count"],
            max_retries,
        )
        return "optimizer_agent"

    logger.warning("Max retries exhausted — requesting human intervention")
    return "human_intervention"
