"""Tests for Pydantic models and schemas."""

import json

from skills_agent.models import (
    EvalResult,
    EvaluationOutput,
    SkillPlan,
    StepSchema,
)


class TestStepSchema:
    def test_basic_step(self):
        step = StepSchema(
            index=0,
            instruction="List all Python files",
            criteria="Output contains at least one .py file",
        )
        assert step.index == 0
        assert step.tools_hint == []
        assert step.depends_on == []

    def test_step_with_hints(self):
        step = StepSchema(
            index=1,
            instruction="Deploy service",
            criteria="Service is running on port 8080",
            tools_hint=["safe_cli_executor", "safe_py_runner"],
            depends_on=[0],
        )
        assert step.tools_hint == ["safe_cli_executor", "safe_py_runner"]
        assert step.depends_on == [0]


class TestSkillPlan:
    def test_plan_creation(self):
        plan = SkillPlan(
            goal="Set up a web server",
            steps=[
                StepSchema(
                    index=0,
                    instruction="Create project directory",
                    criteria="Directory exists",
                ),
                StepSchema(
                    index=1,
                    instruction="Install dependencies",
                    criteria="pip install succeeds",
                    depends_on=[0],
                ),
            ],
        )
        assert len(plan.steps) == 2
        assert plan.goal == "Set up a web server"

    def test_plan_serialization(self):
        plan = SkillPlan(
            goal="Test",
            steps=[
                StepSchema(index=0, instruction="Do X", criteria="X is done"),
            ],
        )
        data = json.loads(plan.model_dump_json())
        assert data["goal"] == "Test"
        assert len(data["steps"]) == 1


class TestEvaluationOutput:
    def test_pass_result(self):
        result = EvaluationOutput(
            verdict=EvalResult.PASS,
            feedback="All criteria met.",
            key_outputs={"server_url": "http://localhost:8080"},
        )
        assert result.verdict == EvalResult.PASS
        assert result.key_outputs["server_url"] == "http://localhost:8080"

    def test_fail_result(self):
        result = EvaluationOutput(
            verdict=EvalResult.FAIL,
            feedback="File not found in output.",
        )
        assert result.verdict == EvalResult.FAIL
        assert result.key_outputs == {}

    def test_roundtrip_json(self):
        original = EvaluationOutput(
            verdict=EvalResult.PASS,
            feedback="OK",
            key_outputs={"key": "value"},
        )
        json_str = original.model_dump_json()
        restored = EvaluationOutput.model_validate_json(json_str)
        assert restored.verdict == original.verdict
        assert restored.key_outputs == original.key_outputs
