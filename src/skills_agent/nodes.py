"""LangGraph node implementations for the Skills Agent workflow.

Nodes:
    1. skill_parser       — parse user input into a SkillPlan
    2. prepare_step_context — prepare context for the next step
    3. optimizer_agent     — execute the step using tools
    4. evaluator_agent     — verify step completion (with tool execution loop)
    5. commit_step         — persist outputs and advance index
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage
from langgraph.prebuilt import ToolNode
from openai import OpenAI

from skills_agent.memory import (
    append_skill_memory,
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
from skills_agent.tools import ALL_TOOLS, EVALUATOR_TOOLS, get_tool_descriptions

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenAI Client
# ---------------------------------------------------------------------------


def setup_openai_client() -> OpenAI:
    """Create and return an OpenAI client from environment variables.

    Required env vars:
        OPENAI_API_KEY  — API key for OpenAI (or compatible) service

    Optional env vars:
        OPENAI_BASE_URL — Custom API base URL (for OpenAI-compatible services)
    """
    return OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
    )


_client: OpenAI | None = None


def _get_client() -> OpenAI:
    """Return the singleton OpenAI client (lazy-initialized)."""
    global _client
    if _client is None:
        _client = setup_openai_client()
    return _client


def _get_model() -> str:
    """Return the model name from OPENAI_MODEL env var (default: gpt-4o)."""
    return os.getenv("OPENAI_MODEL", "gpt-4o")


# ---------------------------------------------------------------------------
# Message format conversion helpers
# ---------------------------------------------------------------------------


def _langchain_to_openai_messages(messages: list) -> list[dict[str, Any]]:
    """Convert a list of LangChain messages to OpenAI chat completion format."""
    result: list[dict[str, Any]] = []
    for m in messages:
        if isinstance(m, SystemMessage):
            result.append({"role": "system", "content": m.content})
        elif isinstance(m, HumanMessage):
            result.append({"role": "user", "content": m.content})
        elif isinstance(m, AIMessage):
            msg: dict[str, Any] = {"role": "assistant"}
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["args"]),
                        },
                    }
                    for tc in m.tool_calls
                ]
                msg["content"] = m.content if m.content else None
            else:
                msg["content"] = m.content or ""
            result.append(msg)
        elif hasattr(m, "tool_call_id"):
            # ToolMessage
            content = m.content if isinstance(m.content, str) else json.dumps(m.content)
            result.append({
                "role": "tool",
                "tool_call_id": m.tool_call_id,
                "content": content,
            })
    return result


def _langchain_tools_to_openai(tools: list) -> list[dict[str, Any]]:
    """Convert LangChain @tool objects to OpenAI function-calling tool specs."""
    openai_tools: list[dict[str, Any]] = []
    for t in tools:
        schema = t.args_schema.model_json_schema()
        schema.pop("title", None)
        openai_tools.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": schema,
            },
        })
    return openai_tools


def _openai_response_to_ai_message(response: Any) -> AIMessage:
    """Convert an OpenAI ChatCompletion response to a LangChain AIMessage."""
    choice = response.choices[0]
    msg = choice.message

    tool_calls: list[dict[str, Any]] = []
    if msg.tool_calls:
        for tc in msg.tool_calls:
            tool_calls.append({
                "id": tc.id,
                "name": tc.function.name,
                "args": json.loads(tc.function.arguments),
            })

    return AIMessage(
        content=msg.content or "",
        tool_calls=tool_calls,
    )


# ---------------------------------------------------------------------------
# Node 0: Skill Parser
# ---------------------------------------------------------------------------


def skill_parser(state: AgentState) -> dict[str, Any]:
    """Parse raw user input into a structured SkillPlan.

    Uses OpenAI chat.completions with JSON mode.
    """
    client = _get_client()

    response = client.chat.completions.create(
        model=_get_model(),
        messages=[
            {"role": "system", "content": SKILL_PARSER_SYSTEM},
            {"role": "user", "content": state["raw_input"]},
        ],
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content
    result = SkillPlan.model_validate_json(raw)

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
    """Invoke the Optimizer LLM via OpenAI chat.completions.

    The Optimizer has access to all tools (safe_cli_executor, safe_py_runner).
    It will either make tool calls or return a text summary when done.
    """
    client = _get_client()
    openai_messages = _langchain_to_openai_messages(state["messages"])
    openai_tools = _langchain_tools_to_openai(ALL_TOOLS)

    response = client.chat.completions.create(
        model=_get_model(),
        messages=openai_messages,
        tools=openai_tools,
    )

    ai_message = _openai_response_to_ai_message(response)

    logger.info(
        "Optimizer response: tool_calls=%d, text_len=%d",
        len(ai_message.tool_calls) if ai_message.tool_calls else 0,
        len(ai_message.content) if isinstance(ai_message.content, str) else 0,
    )

    return {"messages": [ai_message]}


# ---------------------------------------------------------------------------
# Node 2b: Tool Execution (Security Gateway)
# ---------------------------------------------------------------------------

# The ToolNode automatically routes tool calls through our @tool-decorated
# functions which embed the Security Gateway validation.
tool_executor = ToolNode(ALL_TOOLS)


# ---------------------------------------------------------------------------
# Node 3: Evaluator Agent (with internal tool execution loop)
# ---------------------------------------------------------------------------

_EVALUATOR_MAX_TOOL_ROUNDS = 5


def evaluator_agent(state: AgentState) -> dict[str, Any]:
    """Evaluate whether the Optimizer successfully completed the step.

    The evaluator can use tools (CLI inspection, Python script execution,
    inline eval scripts) in an internal loop before returning the final
    structured verdict.
    """
    client = _get_client()
    step: StepSchema = state["steps"][state["current_step_index"]]

    system_prompt = EVALUATOR_SYSTEM.format(
        instruction=step.instruction,
        criteria=step.criteria,
        skill_memory=format_skill_memory(state["skill_memory"]),
    )

    eval_messages = [SystemMessage(content=system_prompt)] + state["messages"]
    openai_messages = _langchain_to_openai_messages(eval_messages)
    openai_tools = _langchain_tools_to_openai(EVALUATOR_TOOLS)

    # Build tool name -> function lookup for local execution
    tool_map = {t.name: t for t in EVALUATOR_TOOLS}

    # Internal tool-use loop: evaluator can call tools before giving verdict
    choice = None
    for _round in range(_EVALUATOR_MAX_TOOL_ROUNDS):
        kwargs: dict[str, Any] = {
            "model": _get_model(),
            "messages": openai_messages,
        }
        if openai_tools:
            kwargs["tools"] = openai_tools

        response = client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        if not choice.message.tool_calls:
            # No tool calls — this is the final evaluation response
            break

        # Append assistant message with tool calls
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": choice.message.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in choice.message.tool_calls
            ],
        }
        openai_messages.append(assistant_msg)

        # Execute each tool call and append results
        for tc in choice.message.tool_calls:
            args = json.loads(tc.function.arguments)
            tool_fn = tool_map.get(tc.function.name)
            if tool_fn:
                try:
                    result = tool_fn.invoke(args)
                except Exception as exc:
                    result = f"[ERROR] {exc}"
            else:
                result = f"[ERROR] Unknown tool: {tc.function.name}"

            openai_messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result if isinstance(result, str) else json.dumps(result),
            })

        logger.info("Evaluator tool round %d completed", _round + 1)

    # Parse the final evaluation from the last response
    raw_content = choice.message.content
    evaluation = EvaluationOutput.model_validate_json(raw_content)

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
    """Router: decide whether to continue to next step or finish."""
    if state["current_step_index"] >= len(state["steps"]):
        return "end"
    return "prepare_step_context"


def route_optimizer_output(state: AgentState) -> str:
    """After Optimizer: route to tool execution or evaluator."""
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
