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
from skills_agent.tools import ALL_TOOLS, EVALUATOR_TOOLS, READONLY_TOOLS, get_tool_descriptions

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------


def _log_memory_state(label: str, state: AgentState) -> None:
    """Log the current skill_memory and step_memory (messages) state."""
    skill_mem = state.get("skill_memory", "")
    step_retry = state.get("step_retry_count", 0)
    msg_count = len(state.get("messages", []))
    logger.info(
        "[%s] Memory State — skill_memory: %r | step_retry_count: %d | messages: %d",
        label,
        skill_mem[:200] if skill_mem else "(empty)",
        step_retry,
        msg_count,
    )


def _log_node_io(node_name: str, direction: str, data: Any) -> None:
    """Log node input or output data."""
    if isinstance(data, dict):
        # Summarize large fields
        summary = {}
        for k, v in data.items():
            if k == "messages":
                summary[k] = f"[{len(v)} message(s)]" if isinstance(v, list) else str(v)[:100]
            elif isinstance(v, str) and len(v) > 200:
                summary[k] = v[:200] + "..."
            elif isinstance(v, list) and len(v) > 3:
                summary[k] = f"[{len(v)} items]"
            else:
                summary[k] = v
        logger.info("[%s] %s: %s", node_name, direction, summary)
    else:
        logger.info("[%s] %s: %s", node_name, direction, str(data)[:500])


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
    logger.info("[skill_parser] Node Input — raw_input length: %d", len(state.get("raw_input", "")))
    _log_memory_state("skill_parser", state)

    llm = _get_llm().with_structured_output(SkillPlan)

    result: SkillPlan = llm.invoke(
        [
            SystemMessage(content=SKILL_PARSER_SYSTEM),
            HumanMessage(content=state["raw_input"]),
        ]
    )

    logger.info(
        "[skill_parser] Parsed plan — goal: %s | steps: %d",
        result.goal,
        len(result.steps),
    )
    for step in result.steps:
        logger.info(
            "[skill_parser]   Step %d: %s (criteria: %s)",
            step.index,
            step.instruction[:80],
            step.criteria[:80],
        )

    output = {
        "steps": result.steps,
        "current_step_index": 0,
        "step_retry_count": 0,
        "skill_memory": "",
        "last_evaluation": "",
        "plan_approved": False,
    }
    _log_node_io("skill_parser", "Node Output", output)
    return output


# ---------------------------------------------------------------------------
# Node 1: Prepare Step Context
# ---------------------------------------------------------------------------


def prepare_step_context(state: AgentState) -> dict[str, Any]:
    """Prepare context for the current step — clear L3 messages, build prompt."""
    step: StepSchema = state["steps"][state["current_step_index"]]

    logger.info(
        "[prepare_step_context] Node Input — step_index: %d | instruction: %s",
        state["current_step_index"],
        step.instruction[:100],
    )
    _log_memory_state("prepare_step_context", state)

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

    output = {
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

    logger.info(
        "[prepare_step_context] Node Output — cleared %d old messages, injected system + human prompt",
        len(remove_msgs),
    )
    _log_memory_state("prepare_step_context/after", state)
    return output


# ---------------------------------------------------------------------------
# Node 2: Optimizer Agent
# ---------------------------------------------------------------------------


def optimizer_agent(state: AgentState) -> dict[str, Any]:
    """Invoke the Optimizer LLM to execute the current step.

    The Optimizer has access to all tools (safe_cli_executor, safe_py_runner).
    It will either make tool calls or return a text summary when done.
    """
    logger.info(
        "[optimizer_agent] Node Input — messages: %d | step_index: %d",
        len(state["messages"]),
        state.get("current_step_index", 0),
    )
    _log_memory_state("optimizer_agent", state)

    llm = _get_llm().bind_tools(ALL_TOOLS)
    response: AIMessage = llm.invoke(state["messages"])

    tool_call_count = len(response.tool_calls) if response.tool_calls else 0
    text_len = len(response.content) if isinstance(response.content, str) else 0

    logger.info(
        "[optimizer_agent] Agent Response — tool_calls: %d | text_len: %d",
        tool_call_count,
        text_len,
    )

    if response.tool_calls:
        for tc in response.tool_calls:
            logger.info(
                "[optimizer_agent]   Tool Call — name: %s | args: %s",
                tc.get("name", "unknown"),
                json.dumps(tc.get("args", {}))[:300],
            )
    else:
        logger.info(
            "[optimizer_agent]   Text Response: %s",
            (response.content[:200] if isinstance(response.content, str) else "(non-text)"),
        )

    return {"messages": [response]}


# ---------------------------------------------------------------------------
# Node 2b: Tool Execution (Security Gateway) — with logging wrapper
# ---------------------------------------------------------------------------


def _logging_tool_executor(state: AgentState) -> dict[str, Any]:
    """Wrapper around ToolNode that logs tool inputs and raw outputs."""
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        for tc in last_msg.tool_calls:
            logger.info(
                "[tool_executor] Tool Input — name: %s | args: %s",
                tc.get("name", "unknown"),
                json.dumps(tc.get("args", {}))[:500],
            )

    _tool_node = ToolNode(ALL_TOOLS)
    result = _tool_node.invoke(state)

    # Log tool outputs
    if "messages" in result:
        for msg in result["messages"]:
            content = msg.content if hasattr(msg, "content") else str(msg)
            logger.info(
                "[tool_executor] Tool Output — %s",
                (content[:500] if isinstance(content, str) else str(content)[:500]),
            )

    _log_memory_state("tool_executor", state)
    return result


tool_executor = _logging_tool_executor


# ---------------------------------------------------------------------------
# Node 3: Evaluator Agent
# ---------------------------------------------------------------------------


_EVALUATOR_MAX_TOOL_ROUNDS = 5

# ToolNode for evaluator-internal tool execution (not a graph node)
_evaluator_tool_node = ToolNode(EVALUATOR_TOOLS)


def evaluator_agent(state: AgentState) -> dict[str, Any]:
    """Evaluate whether the Optimizer successfully completed the step.

    The evaluator can run safe Python verification scripts (via safe_py_runner)
    and read-only CLI commands in an internal tool loop before producing its
    final PASS/FAIL verdict as structured EvaluationOutput.
    """
    step: StepSchema = state["steps"][state["current_step_index"]]

    logger.info(
        "[evaluator_agent] Node Input — step_index: %d | criteria: %s",
        state["current_step_index"],
        step.criteria[:150],
    )
    _log_memory_state("evaluator_agent", state)

    system_prompt = EVALUATOR_SYSTEM.format(
        instruction=step.instruction,
        criteria=step.criteria,
        skill_memory=format_skill_memory(state["skill_memory"]),
    )

    # Phase 1: Tool-calling loop — let the evaluator invoke verification tools
    tool_llm = _get_llm().bind_tools(EVALUATOR_TOOLS)
    messages = [SystemMessage(content=system_prompt)] + list(state["messages"])

    for round_num in range(_EVALUATOR_MAX_TOOL_ROUNDS):
        response: AIMessage = tool_llm.invoke(messages)
        messages.append(response)

        if not (hasattr(response, "tool_calls") and response.tool_calls):
            logger.info("[evaluator_agent] No more tool calls after round %d", round_num + 1)
            break  # No tool calls — evaluator is ready to give verdict

        for tc in response.tool_calls:
            logger.info(
                "[evaluator_agent] Tool Call (round %d) — name: %s | args: %s",
                round_num + 1,
                tc.get("name", "unknown"),
                json.dumps(tc.get("args", {}))[:300],
            )

        tool_result = _evaluator_tool_node.invoke({"messages": messages})
        for msg in tool_result["messages"]:
            content = msg.content if hasattr(msg, "content") else str(msg)
            logger.info(
                "[evaluator_agent] Tool Output (round %d) — %s",
                round_num + 1,
                (content[:500] if isinstance(content, str) else str(content)[:500]),
            )
        messages.extend(tool_result["messages"])

    # Phase 2: Structured verdict — ask the LLM for its final evaluation
    verdict_llm = _get_llm().with_structured_output(EvaluationOutput)
    evaluation: EvaluationOutput = verdict_llm.invoke(messages)

    logger.info(
        "[evaluator_agent] Verdict: %s — feedback: %s",
        evaluation.verdict.value,
        evaluation.feedback[:200],
    )
    if evaluation.key_outputs:
        logger.info(
            "[evaluator_agent] Key Outputs: %s",
            json.dumps(evaluation.key_outputs)[:300],
        )

    # Inject feedback into message stream for the Optimizer to see on retry
    feedback_msg = HumanMessage(
        content=(
            f"[Evaluator] Verdict: {evaluation.verdict.value}\n"
            f"Feedback: {evaluation.feedback}"
        )
    )

    output = {
        "messages": [feedback_msg],
        "last_evaluation": evaluation.model_dump_json(),
        "step_retry_count": state["step_retry_count"] + 1,
    }
    _log_node_io("evaluator_agent", "Node Output", output)
    _log_memory_state("evaluator_agent/after", state)
    return output


# ---------------------------------------------------------------------------
# Node 4: Commit Step
# ---------------------------------------------------------------------------


def commit_step(state: AgentState) -> dict[str, Any]:
    """Commit the current step's outputs to skill memory and advance the index."""
    logger.info(
        "[commit_step] Node Input — step_index: %d | last_evaluation: %s",
        state["current_step_index"],
        state["last_evaluation"][:200] if state["last_evaluation"] else "(empty)",
    )
    _log_memory_state("commit_step", state)

    evaluation = EvaluationOutput.model_validate_json(state["last_evaluation"])

    new_memory = append_skill_memory(
        state["skill_memory"], evaluation.key_outputs
    )

    step = state["steps"][state["current_step_index"]]
    logger.info(
        "[commit_step] Committed step %d: %s (outputs: %s)",
        step.index,
        step.instruction[:60],
        list(evaluation.key_outputs.keys()),
    )

    output = {
        "skill_memory": new_memory,
        "current_step_index": state["current_step_index"] + 1,
    }
    logger.info(
        "[commit_step] Node Output — new skill_memory: %r | next_step_index: %d",
        new_memory[:200] if new_memory else "(empty)",
        output["current_step_index"],
    )
    return output


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------


def route_step(state: AgentState) -> str:
    """Router: decide whether to continue to next step or finish.

    Returns the name of the next node.
    """
    decision = "end" if state["current_step_index"] >= len(state["steps"]) else "prepare_step_context"
    logger.info(
        "[route_step] step_index: %d / %d → %s",
        state["current_step_index"],
        len(state["steps"]),
        decision,
    )
    _log_memory_state("route_step", state)
    return decision


def route_optimizer_output(state: AgentState) -> str:
    """After Optimizer: route to tool execution or evaluator.

    If the last message has tool_calls -> run tools.
    Otherwise (plain text) -> evaluate.
    """
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        logger.info("[route_optimizer_output] → tool_executor (%d tool calls)", len(last_msg.tool_calls))
        return "tool_executor"
    logger.info("[route_optimizer_output] → evaluator_agent (text response)")
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
        logger.info("[route_evaluator_output] PASS → commit_step")
        return "commit_step"

    max_retries = state.get("max_retries", 3)
    if state["step_retry_count"] < max_retries:
        logger.info(
            "[route_evaluator_output] FAIL → optimizer_agent (retry %d/%d)",
            state["step_retry_count"],
            max_retries,
        )
        return "optimizer_agent"

    logger.warning("[route_evaluator_output] FAIL → human_intervention (retries exhausted)")
    return "human_intervention"
