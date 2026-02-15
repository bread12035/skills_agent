"""Tests for graph routing logic and state transitions."""

from langchain_core.messages import AIMessage, HumanMessage

from skills_agent.models import (
    AgentState,
    EvalResult,
    EvaluationOutput,
    StepSchema,
)
from skills_agent.nodes import (
    route_evaluator_output,
    route_optimizer_output,
    route_step,
)


def _make_state(**overrides) -> AgentState:
    """Create a minimal AgentState with sensible defaults."""
    base: AgentState = {
        "steps": [
            StepSchema(index=0, optimizer_instruction="Do X", evaluator_instruction="X done"),
            StepSchema(index=1, optimizer_instruction="Do Y", evaluator_instruction="Y done"),
        ],
        "current_step_index": 0,
        "step_retry_count": 0,
        "max_retries": 3,
        "skill_memory": "",
        "messages": [],
        "step_tool_call_count": 0,
        "last_evaluation": "",
        "raw_input": "test",
        "plan_approved": False,
    }
    base.update(overrides)
    return base


class TestRouteStep:
    def test_next_step_exists(self):
        state = _make_state(current_step_index=0)
        assert route_step(state) == "prepare_step_context"

    def test_all_steps_done(self):
        state = _make_state(current_step_index=2)
        assert route_step(state) == "end"

    def test_exactly_at_boundary(self):
        state = _make_state(
            steps=[StepSchema(index=0, optimizer_instruction="X", evaluator_instruction="X")],
            current_step_index=1,
        )
        assert route_step(state) == "end"


class TestRouteOptimizerOutput:
    def test_tool_call_routes_to_executor(self):
        msg = AIMessage(content="", tool_calls=[{"name": "safe_cli_executor", "args": {}, "id": "1"}])
        state = _make_state(messages=[msg])
        assert route_optimizer_output(state) == "tool_executor"

    def test_text_routes_to_evaluator(self):
        msg = AIMessage(content="Step completed successfully.")
        state = _make_state(messages=[msg])
        assert route_optimizer_output(state) == "evaluator_agent"


class TestRouteEvaluatorOutput:
    def test_pass_routes_to_commit(self):
        evaluation = EvaluationOutput(
            verdict=EvalResult.PASS,
            feedback="All good.",
        )
        state = _make_state(last_evaluation=evaluation.model_dump_json())
        assert route_evaluator_output(state) == "commit_step"

    def test_fail_with_retries_left(self):
        evaluation = EvaluationOutput(
            verdict=EvalResult.FAIL,
            feedback="Not done yet.",
        )
        state = _make_state(
            last_evaluation=evaluation.model_dump_json(),
            step_retry_count=1,
            max_retries=3,
        )
        assert route_evaluator_output(state) == "optimizer_agent"

    def test_fail_retries_exhausted(self):
        evaluation = EvaluationOutput(
            verdict=EvalResult.FAIL,
            feedback="Still failing.",
        )
        state = _make_state(
            last_evaluation=evaluation.model_dump_json(),
            step_retry_count=3,
            max_retries=3,
        )
        assert route_evaluator_output(state) == "human_intervention"
