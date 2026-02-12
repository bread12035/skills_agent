"""Tests for the memory management module."""

from skills_agent.memory import (
    append_skill_memory,
    clear_loop_messages,
    format_skill_memory,
    load_global_context,
)


class TestGlobalContext:
    def test_load_existing(self):
        ctx = load_global_context()
        # claude.md exists in the repo
        assert len(ctx) > 0
        assert "Skills Agent" in ctx


class TestSkillMemory:
    def test_append_to_empty(self):
        result = append_skill_memory("", {"host": "localhost", "port": "8080"})
        assert "host=localhost" in result
        assert "port=8080" in result

    def test_append_to_existing(self):
        existing = "step1_result=ok"
        result = append_skill_memory(existing, {"step2_result": "done"})
        assert result.startswith("step1_result=ok")
        assert "step2_result=done" in result

    def test_append_empty_outputs(self):
        result = append_skill_memory("existing", {})
        assert result == "existing"

    def test_format_empty(self):
        assert "empty" in format_skill_memory("")

    def test_format_with_data(self):
        result = format_skill_memory("key=value")
        assert result == "key=value"

    def test_append_with_inline_data(self):
        """Test that evaluator can pass inline data for next steps."""
        result = append_skill_memory(
            "",
            {
                "company": "AAPL",
                "calendar_year": "2024",
                "transcript_path": "skills\\ects_skill\\tmp\\transcript.txt",
                "transcript_length": "45230",
            },
        )
        assert "company=AAPL" in result
        assert "calendar_year=2024" in result
        assert "transcript_path=" in result
        assert "transcript_length=45230" in result


class TestLoopMessages:
    def test_clear_returns_empty(self):
        assert clear_loop_messages() == []
