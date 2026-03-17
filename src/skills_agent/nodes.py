"""LangGraph node implementations for the Skills Agent workflow.

Nodes:
    1. planner             — context-aware planning from skill definitions
    2. prepare_step_context — prepare context for the next step
    3. optimizer_agent      — execute the step using tools
    4. evaluator_agent      — verify step completion and generate report
    5. commit_step          — persist outputs, append report, and advance index
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
    load_role_context,
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
from skills_agent.tools import (
    ALL_TOOLS,
    EVALUATOR_TOOLS,
    READONLY_TOOLS,
    filter_tools_by_hint,
    get_tool_descriptions,
    get_tool_descriptions_for_hint,
)

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
# Decoupled LLM Factory — Model Specialization
# ---------------------------------------------------------------------------

_THINKING_MODEL = os.environ.get("THINKING_MODEL", "gpt-oss")
_DENSE_MODEL = os.environ.get("DENSE_MODEL", "gpt-oss")


def _get_llm_base(model: str, **kwargs: Any) -> ChatOpenAI:
    """Base LLM constructor shared by all factories."""
    base_url = os.environ.get("OPENAI_API_BASE")
    api_key = os.environ.get("OPENAI_API_KEY")
    if base_url:
        kwargs.setdefault("openai_api_base", base_url)
    if api_key:
        kwargs.setdefault("openai_api_key", api_key)
    kwargs.setdefault("temperature", 0)
    return ChatOpenAI(model=model, **kwargs)


def get_planner_llm(**kwargs: Any) -> ChatOpenAI:
    """LLM for the Planner — uses thinking models for high-level reasoning."""
    return _get_llm_base(_THINKING_MODEL, **kwargs)


def get_optimizer_llm(**kwargs: Any) -> ChatOpenAI:
    """LLM for the Optimizer — uses dense models for fast tool calling."""
    return _get_llm_base(_DENSE_MODEL, **kwargs)


def get_evaluator_llm(**kwargs: Any) -> ChatOpenAI:
    """LLM for the Evaluator — uses thinking models for strict verification."""
    return _get_llm_base(_THINKING_MODEL, **kwargs)


# Backward-compatible alias
def _get_llm(model: str | None = None, **kwargs: Any) -> ChatOpenAI:
    return _get_llm_base(model or _DENSE_MODEL, **kwargs)


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
# Trajectory summarization helper
# ---------------------------------------------------------------------------


def _summarize_trajectory(messages: list) -> str:
    """Summarize the Optimizer's tool calls and reasoning from L3 messages.

    Returns a concise trajectory string capturing the sequence of actions taken.
    """
    trajectory_parts: list[str] = []
    for msg in messages:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                tool_name = tc.get("name", "unknown")
                tool_args = tc.get("args", {})
                args_summary = ", ".join(
                    f"{k}={repr(v)[:40]}" for k, v in tool_args.items()
                )
                trajectory_parts.append(f"Tool: {tool_name}({args_summary})")
        elif (
            hasattr(msg, "content")
            and isinstance(msg.content, str)
            and msg.content.strip()
            and not msg.content.startswith("[Evaluator]")
            and hasattr(msg, "type")
            and getattr(msg, "type", "") == "ai"
        ):
            # Optimizer reasoning text
            preview = msg.content[:100].replace("\n", " ")
            trajectory_parts.append(f"Reasoning: {preview}")

    return " → ".join(trajectory_parts) if trajectory_parts else "(no actions recorded)"


# ---------------------------------------------------------------------------
# Node 0: Planner (formerly skill_parser)
# ---------------------------------------------------------------------------


def planner(state: AgentState) -> dict[str, Any]:
    """Context-aware Planner: parse skill definition into a structured SkillPlan.

    The Planner has access to:
    - Role-specific context from config/planner.md
    - Tool definitions (safe_py_runner scripts, safe_cli_executor sub-commands)
    - Available scripts in scripts/ and skills/*/
    - Historical execution data (Success Cases, Failure Cases, Human Feedback)

    It produces steps with distinct optimizer_instruction and evaluator_instruction.
    """
    logger.info("[planner] Node Input — raw_input length: %d", len(state.get("raw_input", "")))
    _log_memory_state("planner", state)

    raw_input = state["raw_input"]

    # Gather context for the Planner
    role_context = load_role_context("planner")
    tool_docs = get_tool_descriptions()
    available_scripts = _discover_available_scripts()
    historical_context = _extract_historical_sections(raw_input)

    # Build the planner system prompt with role context and tool awareness
    system_prompt = PLANNER_SYSTEM.format(
        role_context=role_context,
        tool_docs=tool_docs,
        available_scripts=available_scripts,
    )

    # Include historical context in the user message alongside the skill definition
    user_content = raw_input
    if historical_context != "(no historical execution data available)":
        user_content += f"\n\n---\n## Extracted Historical Context\n{historical_context}"


    llm = get_planner_llm().with_structured_output(SkillPlan)

    result: SkillPlan = llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content),
        ]
    )

    logger.info(
        "[planner] Parsed plan — goal: %s | steps: %d",
        result.goal,
        len(result.steps),
    )
    for step in result.steps:
        logger.info(
            "[planner]   Step %d: optimizer=%s | evaluator=%s | tools_hint=%s",
            step.index,
            step.optimizer_instruction[:80],
            step.evaluator_instruction[:80],
            step.tools_hint,
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
        "report_state": [],
        "current_report": "",
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

    Uses role-specific context from config/optimizer.md and dynamically filters
    tool documentation based on the step's tools_hint.

    L2 skill memory is injected into the **User Prompt** (wrapped in
    ``<skill_memory>`` XML tags) so that the model can clearly distinguish
    global behaviour rules (System) from step-specific context data (User).

    Step-specific instructions are also placed in the User Prompt using
    ``<instruction>`` XML tags.
    """
    step: StepSchema = state["steps"][state["current_step_index"]]

    logger.info(
        "[prepare_step_context] Node Input — step_index: %d | instruction: %s | tools_hint: %s",
        state["current_step_index"],
        step.optimizer_instruction[:100],
        step.tools_hint,
    )
    _log_memory_state("prepare_step_context", state)

    # Load role-specific context instead of monolithic claude.md
    role_context = load_role_context("optimizer")

    # Dynamic tool hinting: only inject docs for hinted tools
    tool_docs = get_tool_descriptions_for_hint(step.tools_hint)

    # System prompt: role context + filtered tool docs (NO skill_memory here)
    system_prompt = OPTIMIZER_SYSTEM.format(
        role_context=role_context,
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
        "current_report": "",
    }

    logger.info(
        "[prepare_step_context] Node Output — cleared %d old messages, injected system + user prompt (tools_hint=%s)",
        len(remove_msgs),
        step.tools_hint,
    )
    _log_memory_state("prepare_step_context/after", state)
    return output


# ---------------------------------------------------------------------------
# Node 2: Optimizer Agent
# ---------------------------------------------------------------------------


def optimizer_agent(state: AgentState) -> dict[str, Any]:
    """Invoke the Optimizer LLM to execute the current step.

    Uses the dense model and dynamically binds only the tools specified
    in the step's tools_hint.

    Logging: Only log "Step X: Tool Call [Name] with [Args]" — do not log
    the result/output of the tool call to keep logs clean.
    """
    step: StepSchema = state["steps"][state["current_step_index"]]

    # Dynamic tool binding based on Planner's tools_hint
    step_tools = filter_tools_by_hint(step.tools_hint)
    llm = get_optimizer_llm().bind_tools(step_tools)

    response: AIMessage = llm.invoke(state["messages"])

    tool_call_count = len(response.tool_calls) if response.tool_calls else 0
    step_index = state.get("current_step_index", 0)

    if response.tool_calls:
        # Log only the tool call name and args (not results)
        for tc in response.tool_calls:
            logger.info(
                "[optimizer_agent] Step %d: Tool Call [%s] with [%s]",
                step_index,
                tc.get("name", "unknown"),
                json.dumps(tc.get("args", {})),
            )
    else:
        # Log the optimizer's completion text
        text = response.content if isinstance(response.content, str) else "(non-text)"
        if text.strip().startswith(_ATTEMPTS_COMPLETE_SIGNAL):
            logger.info("[optimizer_agent] Step %d: Completed", step_index)
        else:
            logger.warning(
                "[optimizer_agent] Step %d: Text output (missing [ATTEMPTS_COMPLETE])",
                step_index,
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
    """Wrapper around ToolNode that logs tool inputs.

    Dynamically selects tools based on the current step's tools_hint.

    Increments current_loop_count to track how many tool-call
    iterations have occurred within the current step.

    Note: Only the tool call name/args are logged by optimizer_agent.
    Tool results are NOT logged to keep logs clean per SDD §3.1.
    """
    last_msg = state["messages"][-1]
    if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
        for tc in last_msg.tool_calls:
            logger.info(
                "[tool_executor] Executing — %s",
                tc.get("name", "unknown"),
            )

    # Use filtered tools based on the step's hint
    step: StepSchema = state["steps"][state["current_step_index"]]
    step_tools = filter_tools_by_hint(step.tools_hint)
    _tool_node = ToolNode(step_tools)
    result = _tool_node.invoke(state)

    # Increment loop counter
    new_count = state.get("current_loop_count", 0) + 1
    result["current_loop_count"] = new_count
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

    Uses the thinking model and role-specific context from config/evaluator.md.

    Generates a step report including:
    - Trajectory: summary of Optimizer's tool calls and reasoning
    - Verdict: PASS or FAIL
    - Feedback: why it passed or what went wrong

    On PASS: extracts key_outputs for L2 (path-centric), sets current_report.
    On FAIL: returns report + feedback to Optimizer via message history.

    L2 skill memory is injected into the **User Prompt** (wrapped in
    ``<skill_memory>`` XML tags), and the evaluator_instruction is placed
    inside ``<success_criteria>`` tags.
    """
    step: StepSchema = state["steps"][state["current_step_index"]]

    # Load role-specific context and tool docs for evaluator
    role_context = load_role_context("evaluator")
    tool_docs = get_tool_descriptions_for_hint(["safe_py_runner", "safe_cli_executor"])

    # System prompt: evaluator role context + tool docs
    system_prompt = EVALUATOR_SYSTEM.format(
        role_context=role_context,
        tool_docs=tool_docs,
    )

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
    tool_llm = get_evaluator_llm().bind_tools(EVALUATOR_TOOLS)
    messages = [SystemMessage(content=system_prompt)] + list(state["messages"]) + [evaluator_user_msg]

    eval_tool_call_count = 0
    for round_num in range(_EVALUATOR_MAX_TOOL_ROUNDS):
        response: AIMessage = tool_llm.invoke(messages)
        messages.append(response)

        if not (hasattr(response, "tool_calls") and response.tool_calls):
            break  # No tool calls — evaluator is ready to give verdict

        eval_tool_call_count += len(response.tool_calls)

        for tc in response.tool_calls:
            logger.info(
                "[evaluator_agent] Verification Call — %s | args: %s",
                tc.get("name", "unknown"),
                json.dumps(tc.get("args", {})),
            )

        tool_result = _evaluator_tool_node.invoke({"messages": messages})
        for msg in tool_result["messages"]:
            content = msg.content if hasattr(msg, "content") else str(msg)
            logger.info(
                "[evaluator_agent] Verification Result — %s",
                content if isinstance(content, str) else str(content),
            )
        messages.extend(tool_result["messages"])

        # L3 Anchoring for evaluator: inject <primary_directive> every N tool calls
        if eval_tool_call_count > 0 and eval_tool_call_count % _ANCHOR_EVERY_N_TOOL_CALLS == 0:
            anchor_content = PRIMARY_DIRECTIVE_ANCHOR.format(
                instruction=step.evaluator_instruction,
            )
            messages.append(HumanMessage(content=anchor_content))

    # Phase 2: Structured verdict — ask the LLM for its final evaluation
    verdict_llm = get_evaluator_llm().with_structured_output(EvaluationOutput)
    evaluation: EvaluationOutput = verdict_llm.invoke(messages)

    # Summarize the Optimizer's trajectory from L3 messages
    trajectory = _summarize_trajectory(state["messages"])
    evaluation.trajectory = trajectory

    # Generate the step report
    step_report = (
        f"--- Step {step.index} Report ---\n"
        f"Trajectory: {trajectory}\n"
        f"Verdict: {evaluation.verdict.value}\n"
        f"Feedback: {evaluation.feedback}\n"
    )
    if evaluation.key_outputs:
        step_report += f"Key Outputs: {json.dumps(evaluation.key_outputs)}\n"
    step_report += "---"

    # Log the report template
    logger.info(
        "[evaluator_agent] Report Template:\n%s",
        step_report,
    )

    # Log verdict, feedback, and key outputs for supervisory clarity
    logger.info(
        "[evaluator_agent] Verdict: %s | Feedback: %s",
        evaluation.verdict.value,
        evaluation.feedback,
    )
    if evaluation.key_outputs:
        logger.info(
            "[evaluator_agent] Key Outputs: %s",
            json.dumps(evaluation.key_outputs),
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
        "current_report": step_report,
    }


# ---------------------------------------------------------------------------
# Node 4: Commit Step
# ---------------------------------------------------------------------------


def commit_step(state: AgentState) -> dict[str, Any]:
    """Commit the current step's outputs to skill memory, append report, and advance index."""
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

    # Append the current step's report to the cumulative report_state
    current_report = state.get("current_report", "")
    report_state = list(state.get("report_state", []))
    if current_report:
        report_state.append(current_report)

    output = {
        "skill_memory": new_memory,
        "current_step_index": state["current_step_index"] + 1,
        "report_state": report_state,
        "current_report": "",
    }
    logger.info(
        "[commit_step] Node Output — new skill_memory: %r | next_step_index: %d | reports: %d",
        new_memory[:200] if new_memory else "(empty)",
        output["current_step_index"],
        len(report_state),
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
