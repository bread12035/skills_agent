"""Pydantic models and LangGraph state schema for the Skills Agent system."""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from typing_extensions import TypedDict


# ---------------------------------------------------------------------------
# Step & Plan Models
# ---------------------------------------------------------------------------


class EvalResult(str, Enum):
    """Evaluator verdict for a step execution."""

    PASS = "PASS"
    FAIL = "FAIL"


class StepSchema(BaseModel):
    """A single executable step within a compiled plan."""

    index: int = Field(description="Zero-based step index.")
    instruction: str = Field(description="What the Optimizer should do.")
    criteria: str = Field(
        description="How the Evaluator verifies success (concrete, measurable)."
    )
    tools_hint: list[str] = Field(
        default_factory=list,
        description="Suggested tool names the Optimizer may use.",
    )
    depends_on: list[int] = Field(
        default_factory=list,
        description="Indices of steps this step depends on.",
    )


class SkillPlan(BaseModel):
    """The compiled plan output by the Skill Parser."""

    goal: str = Field(description="High-level goal summary.")
    steps: list[StepSchema] = Field(description="Ordered execution steps.")


# ---------------------------------------------------------------------------
# Evaluator Output
# ---------------------------------------------------------------------------


class EvaluationOutput(BaseModel):
    """Structured output from the Evaluator Agent."""

    verdict: EvalResult = Field(description="PASS or FAIL.")
    feedback: str = Field(
        description="Concrete feedback: why it passed or what went wrong."
    )
    key_outputs: dict[str, str] = Field(
        default_factory=dict,
        description="Key-value pairs to persist in skill_memory (only on PASS).",
    )


# ---------------------------------------------------------------------------
# LangGraph State
# ---------------------------------------------------------------------------


class AgentState(TypedDict):
    """Global state flowing through every LangGraph node.

    Layers:
        L1 - Global Context:  loaded from claude.md (injected into prompts, not stored here)
        L2 - Skill Memory:    `skill_memory` — cross-step variable passing, append-only
        L3 - Loop Context:    `messages` — Optimizer↔Evaluator dialogue, cleared per step
        L4 - Checkpoints:     persisted automatically by LangGraph SqliteSaver
    """

    # --- Workflow Control ---
    steps: list[StepSchema]
    current_step_index: int
    step_retry_count: int
    max_retries: int

    # --- Context & Memory ---
    skill_memory: str
    messages: Annotated[list[BaseMessage], add_messages]

    # --- Evaluator ---
    last_evaluation: str  # serialised EvaluationOutput JSON

    # --- Meta ---
    raw_input: str
    plan_approved: bool
