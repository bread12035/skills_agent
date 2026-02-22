"""LangGraph node implementations for the Skills Agent workflow.

Nodes:
    1. planner             — context-aware planning from skill definitions
    2. prepare_step_context — prepare context for the next step
    3. optimizer_agent      — execute the step using tools
    4. evaluator_agent      — verify step completion
    5. commit_step          — persist outputs and advance index
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any
from dotenv import load_dotenv

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
    PLANNER_SYSTEM,
    PRIMARY_DIRECTIVE_ANCHOR,
)
from skills_agent.tools import ALL_TOOLS, EVALUATOR_TOOLS, READONLY_TOOLS, get_tool_descriptions

logger = logging.getLogger(__name__)
load_dotenv()

# ---------------------------------------------------------------------------
# L3 anchoring configuration
# ---------------------------------------------------------------------------

_ANCHOR_EVERY_N_TOOL_CALLS = 3  # re-inject primary directive every N tool calls

# ---------------------------------------------------------------------------
# Project root for script discovery
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]

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
    api_key = os.environ.get("OPENAI_API_KEY")
    if base_url:
        kwargs.setdefault("openai_api_base", base_url)
    if api_key:
        kwargs.setdefault("openai_api_key", api_key)
    return ChatOpenAI(model=model, **kwargs)


# ---------------------------------------------------------------------------
# Path normalisation helper
# ---------------------------------------------------------------------------

# Matches UNIX-style relative paths such as "ects_skill/tmp/output.json" or
# "hello_skill/output.txt" but avoids protocol prefixes like "https://".
_UNIX_PATH_RE = re.compile(
    r'(?<![a-zA-Z]:)'        # not preceded by a drive-letter colon (e.g. C:)
    r'(?<!/)'                 # not preceded by another slash (avoids "//", "://")
    r'(?:[a-zA-Z0-9_.][a-zA-Z0-9_.\-]*)'   # first path segment
    r'(?:/[a-zA-Z0-9_.][a-zA-Z0-9_.\-]*)+' # one or more "/segment" continuations
)


def _to_windows_paths(text: str) -> str:
    """Replace UNIX-style forward-slash paths with Windows-style backslash paths.

    Only converts path-like tokens (e.g. ``ects_skill/tmp/output.json``) and
    leaves URLs, prose, and other content untouched.
    """
    def _replace(m: re.Match) -> str:
        return m.group(0).replace("/", "\\")

    return _UNIX_PATH_RE.sub(_replace, text)


# ---------------------------------------------------------------------------
# Script discovery helper (for Planner tool awareness)
# ---------------------------------------------------------------------------


def _discover_available_scripts() -> str:
    """Discover Python scripts available in scripts/ and skills/*/."""
    lines: list[str] = []

    # Shared scripts
    scripts_dir = PROJECT_ROOT / "scripts"
    if scripts_dir.exists():
        for py_file in sorted(scripts_dir.glob("*.py")):
            # Read first docstring line for description
            desc = _extract_script_description(py_file)
            lines.append(f"  - scripts/{py_file.name}: {desc}")

    # Skill-specific scripts
    skills_dir = PROJECT_ROOT / "skills"
    if skills_dir.exists():
        for skill_dir in sorted(skills_dir.iterdir()):
            if skill_dir.is_dir():
                for py_file in sorted(skill_dir.glob("*.py")):
                    desc = _extract_script_description(py_file)
                    lines.append(f"  - skills/{skill_dir.name}/{py_file.name}: {desc}")

    return "\n".join(lines) if lines else "  (no scripts found)"


def _extract_script_description(py_file: Path) -> str:
    """Extract a one-line description from a Python script's docstring."""
    try:
        content = py_file.read_text(encoding="utf-8")
        # Look for module docstring
        match = re.search(r'^"""(.+?)"""', content, re.DOTALL)
        if match:
            first_line = match.group(1).strip().split("\n")[0]
            return first_line[:100]
    except Exception:
        pass
    return "(no description)"


# ---------------------------------------------------------------------------
# Historical context extraction
# ---------------------------------------------------------------------------


def _extract_historical_sections(skill_content: str) -> str:
    """Extract Success Cases, Failure Cases, and Human Feedback from skill content."""
    sections = []
    for section_name in ("Success Cases", "Failure Cases", "Human Feedback"):
        pattern = rf"## {section_name}\s*\n(.*?)(?=\n## |\Z)"
        match = re.search(pattern, skill_content, re.DOTALL)
        if match and match.group(1).strip():
            sections.append(f"### {section_name}\n{match.group(1).strip()}")

    if sections:
        return "\n\n".join(sections)
    return "(no historical execution data available)"


# ---------------------------------------------------------------------------
# Node 0: Planner (formerly skill_parser)
# ---------------------------------------------------------------------------


def planner(state: AgentState) -> dict[str, Any]:
    """Context-aware Planner: parse skill definition into a structured SkillPlan.

    The Planner has access to:
    - Tool definitions (safe_cli_executor sub-commands, safe_py_runner)
    - Available scripts in scripts/ and skills/*/
    - Historical execution data (Success Cases, Failure Cases, Human Feedback)

    It produces steps with distinct optimizer_instruction and evaluator_instruction.
    """
    logger.info("[planner] Node Input — raw_input length: %d", len(state.get("raw_input", "")))
    _log_memory_state("planner", state)

    raw_input = state["raw_input"]

    # Gather context for the Planner
    tool_docs = get_tool_descriptions()
    available_scripts = _discover_available_scripts()
    historical_context = _extract_historical_sections(raw_input)

    # Build the planner system prompt with tool awareness
    system_prompt = PLANNER_SYSTEM.format(
        tool_docs=tool_docs,
        available_scripts=available_scripts,
    )

    # Include historical context in the user message alongside the skill definition
    user_content = raw_input
    if historical_context != "(no historical execution data available)":
        user_content += f"\n\n---\n## Extracted Historical Context\n{historical_context}"

    # Append available tools so the LLM sees them at the end of context when
    # generating tools_hint values (recency bias).
    user_content += (
        f"\n\n---\n"
        "## Available Tools (for tools_hint generation)\n\n"
        f"{tool_docs}"
    )

    llm = _get_llm().with_structured_output(SkillPlan)

    result: SkillPlan = llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content),
        ]
    )

    # Post-process: convert any remaining UNIX-style paths to Windows backslashes
    for step in result.steps:
        step.optimizer_instruction = _to_windows_paths(step.optimizer_instruction)
        step.evaluator_instruction = _to_windows_paths(step.evaluator_instruction)

    logger.info(
        "[planner] Parsed plan — goal: %s | steps: %d",
        result.goal,
        len(result.steps),
    )
    for step in result.steps:
        logger.info(
            "[planner]   Step %d: optimizer=%s | evaluator=%s",
            step.index,
            step.optimizer_instruction[:80],
            step.evaluator_instruction[:80],
        )

    output = {
        "steps": result.steps,
        "current_step_index": 0,
        "step_retry_count": 0,
        "current_loop_count": 0,
        "skill_memory": "",
        "last_evaluation": "",
        "plan_approved": False,
        "step_tool_call_count": 0,
    }
    _log_node_io("planner", "Node Output", output)
    return output


# Backward-compatible alias
skill_parser = planner


# ---------------------------------------------------------------------------
# Node 1: Prepare Step Context
# ---------------------------------------------------------------------------


def prepare_step_context(state: AgentState) -> dict[str, Any]:
    """Prepare context for the current step — clear L3 messages, build prompt.

    L2 skill memory is injected into the **User Prompt** (wrapped in
    ``<skill_memory>`` XML tags) so that the model can clearly distinguish
    global behaviour rules (System) from step-specific context data (User).

    Step-specific instructions are also placed in the User Prompt using
    ``<instruction>`` XML tags.
    """
    step: StepSchema = state["steps"][state["current_step_index"]]

    logger.info(
        "[prepare_step_context] Node Input — step_index: %d | instruction: %s",
        state["current_step_index"],
        step.optimizer_instruction[:100],
    )
    _log_memory_state("prepare_step_context", state)

    global_context = load_global_context()
    tool_docs = get_tool_descriptions()

    # System prompt: global behaviour + tool docs (NO skill_memory here)
    system_prompt = OPTIMIZER_SYSTEM.format(
        global_context=global_context,
        tool_docs=tool_docs,
    )

    # User prompt: <skill_memory> at the top, then <instruction>
    skill_memory_block = format_skill_memory(state["skill_memory"])
    user_content = (
        f"<skill_memory>\n{skill_memory_block}\n</skill_memory>\n\n"
        f"<instruction>\n"
        f"## Step {step.index} — Your Task\n\n"
        f"{step.optimizer_instruction}\n\n"
        f"When you have completed this task, stop making tool calls and "
        f"respond with `[ATTEMPTS_COMPLETE]` followed by a plain-text summary "
        f"of what you accomplished."
    )

    # Clear L3: remove all existing messages and start fresh with system context
    remove_msgs = [RemoveMessage(id=m.id) for m in state["messages"]]

    output = {
        "messages": remove_msgs
        + [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content),
        ],
        "step_retry_count": 0,
        "step_tool_call_count": 0,  # reset tool call counter for new step
        "last_evaluation": "",
        "current_loop_count": 0,
    }

    logger.info(
        "[prepare_step_context] Node Output — cleared %d old messages, injected system + user prompt (L2 in user prompt)",
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

    # Update cumulative tool call count for L3 anchoring
    new_tool_call_count = state.get("step_tool_call_count", 0) + tool_call_count

    return {
        "messages": [response],
        "step_tool_call_count": new_tool_call_count,
    }


# ---------------------------------------------------------------------------
# Node 2b: Tool Execution (Security Gateway) — with logging wrapper
# ---------------------------------------------------------------------------


def _logging_tool_executor(state: AgentState) -> dict[str, Any]:
    """Wrapper around ToolNode that logs tool inputs and raw outputs.

    Also increments current_loop_count to track how many tool-call
    iterations have occurred within the current step.
    """
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

    # Increment loop counter
    new_count = state.get("current_loop_count", 0) + 1
    result["current_loop_count"] = new_count
    logger.info("[tool_executor] current_loop_count incremented to %d", new_count)

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

    L2 skill memory is injected into the **User Prompt** (wrapped in
    ``<skill_memory>`` XML tags), and the evaluator_instruction is placed
    inside ``<success_criteria>`` tags.
    """
    step: StepSchema = state["steps"][state["current_step_index"]]

    logger.info(
        "[evaluator_agent] Node Input — step_index: %d | evaluator_instruction: %s",
        state["current_step_index"],
        step.evaluator_instruction[:150],
    )
    _log_memory_state("evaluator_agent", state)

    # System prompt: general evaluator behaviour (NO skill_memory)
    system_prompt = EVALUATOR_SYSTEM

    # Build evaluator user message with <skill_memory> and <success_criteria>
    skill_memory_block = format_skill_memory(state["skill_memory"])
    evaluator_user_msg = HumanMessage(
        content=(
            f"<skill_memory>\n{skill_memory_block}\n</skill_memory>\n\n"
            f"<success_criteria>\n"
            f"## Verification Task for Step {step.index}\n\n"
            f"{step.evaluator_instruction}\n\n"
            f"Review the Optimizer's work above and verify according to these instructions. "
            f"Use tools if needed to inspect files or run validation scripts.\n"
            f"</success_criteria>"
        )
    )

    # Phase 1: Tool-calling loop — let the evaluator invoke verification tools
    tool_llm = _get_llm().bind_tools(EVALUATOR_TOOLS)
    messages = [SystemMessage(content=system_prompt)] + list(state["messages"]) + [evaluator_user_msg]

    eval_tool_call_count = 0
    for round_num in range(_EVALUATOR_MAX_TOOL_ROUNDS):
        response: AIMessage = tool_llm.invoke(messages)
        messages.append(response)

        if not (hasattr(response, "tool_calls") and response.tool_calls):
            logger.info("[evaluator_agent] No more tool calls after round %d", round_num + 1)
            break  # No tool calls — evaluator is ready to give verdict

        eval_tool_call_count += len(response.tool_calls)

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

        # L3 Anchoring for evaluator: inject <primary_directive> every N tool calls
        if eval_tool_call_count > 0 and eval_tool_call_count % _ANCHOR_EVERY_N_TOOL_CALLS == 0:
            anchor_content = PRIMARY_DIRECTIVE_ANCHOR.format(
                instruction=step.evaluator_instruction,
            )
            messages.append(HumanMessage(content=anchor_content))
            logger.info(
                "[evaluator_agent] L3 Anchor injected at eval_tool_call_count=%d",
                eval_tool_call_count,
            )

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
        step.optimizer_instruction[:60],
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


_STUCK_LOOP_THRESHOLD = 8

# Signal prefix the Optimizer must emit to trigger evaluation
_ATTEMPTS_COMPLETE_SIGNAL = "[ATTEMPTS_COMPLETE]"


def route_optimizer_output(state: AgentState) -> str:
    """After Optimizer: route to tool execution, evaluator, or replan.

    Priority:
    1. Stuck-loop guard — if current_loop_count exceeds threshold, replan
       by routing back to prepare_step_context (clears L3, preserves L2).
    2. Tool calls present — route to tool_executor.
    3. [ATTEMPTS_COMPLETE] signal in text — route to evaluator_agent.
    4. Plain text without signal — treat as incomplete, route back to
       optimizer_agent so the model can continue or finalize.
    """
    loop_count = state.get("current_loop_count", 0)
    last_msg = state["messages"][-1]

    # 1. Stuck-loop detection — replan
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls and loop_count > _STUCK_LOOP_THRESHOLD:
        logger.warning(
            "[route_optimizer_output] STUCK LOOP detected (loop_count=%d > %d) → "
            "replan via prepare_step_context",
            loop_count,
            _STUCK_LOOP_THRESHOLD,
        )
        return "prepare_step_context"

    # 2. Tool calls — execute tools
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        logger.info(
            "[route_optimizer_output] → tool_executor (%d tool calls, loop_count=%d)",
            len(last_msg.tool_calls),
            loop_count,
        )
        return "tool_executor"

    # 3. Completion signal — proceed to evaluation
    content = last_msg.content if hasattr(last_msg, "content") and isinstance(last_msg.content, str) else ""
    if content.strip().startswith(_ATTEMPTS_COMPLETE_SIGNAL):
        logger.info("[route_optimizer_output] → evaluator_agent ([ATTEMPTS_COMPLETE] signal detected)")
        return "evaluator_agent"

    # 4. Fallback — plain text without signal; still route to evaluator
    #    but log a warning so the signal gap is visible in diagnostics.
    logger.warning(
        "[route_optimizer_output] → evaluator_agent (text response WITHOUT "
        "[ATTEMPTS_COMPLETE] signal — treating as implicit completion)"
    )
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
