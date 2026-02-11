"""CLI entry point for the Skills Agentic Executor.

Usage:
    skills-agent input/my_skill        # path to a skill directory containing skills.md
    skills-agent path/to/skills.md     # direct path to a markdown file
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from skills_agent.graph import build_execution_graph, build_parser_graph
from skills_agent.models import AgentState, EvaluationOutput

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _resolve_skill_path(raw_path: str) -> Path:
    """Resolve a skill path to the actual markdown file.

    Accepts either:
        - A directory containing a ``skills.md`` file
        - A direct path to a ``.md`` file
    """
    p = Path(raw_path)
    if p.is_dir():
        md = p / "skills.md"
        if not md.exists():
            print(f"Error: No skills.md found in directory '{p}'.")
            sys.exit(1)
        return md
    if p.is_file():
        return p
    print(f"Error: Path '{p}' does not exist.")
    sys.exit(1)


def _print_plan(state: dict) -> None:
    """Pretty-print the parsed plan for human approval."""
    steps = state.get("steps", [])
    print("\n" + "=" * 60)
    print("  EXECUTION PLAN")
    print("=" * 60)
    for step in steps:
        prefix = f"  Step {step.index}"
        print(f"\n{prefix}: {step.instruction}")
        print(f"    Criteria: {step.criteria}")
        if step.tools_hint:
            print(f"    Tools: {', '.join(step.tools_hint)}")
    print("\n" + "=" * 60)


def _print_step_status(state: dict) -> None:
    """Print current step execution status."""
    idx = state.get("current_step_index", 0)
    steps = state.get("steps", [])
    total = len(steps)

    if idx < total:
        step = steps[idx]
        print(f"\n>>> Step {idx + 1}/{total}: {step.instruction}")
    else:
        print(f"\n>>> All {total} steps completed!")


# ---------------------------------------------------------------------------
# Skill memory persistence — append success/failure cases to skills.md
# ---------------------------------------------------------------------------


def _append_skill_learning(md_path: Path, section: str, content: str) -> None:
    """Append a learning entry (success case, failure case, or feedback) to skills.md."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    existing = md_path.read_text(encoding="utf-8")

    # Check if the section header already exists
    if f"## {section}" not in existing:
        existing += f"\n\n## {section}\n"

    # Append the entry under the section
    entry = f"\n### [{timestamp}]\n{content}\n"

    # Insert entry at the end of the section (before next ## or at EOF)
    section_header = f"## {section}"
    header_pos = existing.index(section_header)
    # Find the next section header after this one
    rest = existing[header_pos + len(section_header):]
    next_section = rest.find("\n## ")

    if next_section == -1:
        # No next section — append at end of file
        updated = existing + entry
    else:
        insert_pos = header_pos + len(section_header) + next_section
        updated = existing[:insert_pos] + entry + existing[insert_pos:]

    md_path.write_text(updated, encoding="utf-8")


def _save_step_evaluation(md_path: Path, step_info: str, evaluation: EvaluationOutput) -> None:
    """Save a step evaluation result to skills.md as a success or failure case."""
    if evaluation.verdict.value == "PASS":
        section = "Success Cases"
        content = (
            f"**Step:** {step_info}\n"
            f"**Feedback:** {evaluation.feedback}\n"
            f"**Key Outputs:** {json.dumps(evaluation.key_outputs, indent=2)}\n"
        )
    else:
        section = "Failure Cases"
        content = (
            f"**Step:** {step_info}\n"
            f"**Feedback:** {evaluation.feedback}\n"
        )
    _append_skill_learning(md_path, section, content)


def _save_human_feedback(md_path: Path, feedback: str) -> None:
    """Save human feedback to skills.md for future reference."""
    _append_skill_learning(md_path, "Human Feedback", feedback)


# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------


def run(skill_content: str, md_path: Path) -> dict:
    """Run the Skills Agent with content read from a skill file.

    Args:
        skill_content: Markdown content from the skill file.
        md_path: Path to the skills.md file (for persisting learnings).

    Returns:
        Final agent state.
    """
    print(f"Skill content length: {len(skill_content)} chars")

    initial_state: AgentState = {
        "raw_input": skill_content,
        "steps": [],
        "current_step_index": 0,
        "step_retry_count": 0,
        "max_retries": 3,
        "skill_memory": "",
        "messages": [],
        "last_evaluation": "",
        "plan_approved": False,
    }

    # Phase 1: Parse the skill into a plan
    parser_graph = build_parser_graph()
    parsed_state = None
    for event in parser_graph.stream(initial_state, stream_mode="values"):
        parsed_state = event

    if not parsed_state or not parsed_state.get("steps"):
        print("Error: Skill parser produced no steps.")
        return parsed_state or initial_state

    # Phase 2: Present plan and ask for human approval (right after parsing)
    _print_plan(parsed_state)

    approval = input("\nApprove this plan? [Y/n]: ").strip().lower()
    if approval in ("n", "no"):
        print("Plan rejected. Exiting.")
        return parsed_state

    print("\nPlan approved. Starting execution...\n")

    # Phase 3: Execute the approved plan
    execution_graph = build_execution_graph()
    result = None
    prev_step_index = parsed_state.get("current_step_index", 0)

    for event in execution_graph.stream(parsed_state, stream_mode="values"):
        result = event
        _print_step_status(result)

        # Persist evaluation results to skills.md after each evaluator pass
        current_idx = result.get("current_step_index", 0)
        last_eval_json = result.get("last_evaluation", "")
        if last_eval_json and current_idx != prev_step_index:
            try:
                evaluation = EvaluationOutput.model_validate_json(last_eval_json)
                steps = result.get("steps", [])
                # The completed step is at prev_step_index
                if prev_step_index < len(steps):
                    step = steps[prev_step_index]
                    step_info = f"Step {step.index}: {step.instruction}"
                    _save_step_evaluation(md_path, step_info, evaluation)
                    logger.info(
                        "Saved %s case for step %d to %s",
                        evaluation.verdict.value,
                        step.index,
                        md_path,
                    )
            except Exception as exc:
                logger.warning("Failed to save evaluation to skills.md: %s", exc)
            prev_step_index = current_idx

    # Check for final step evaluation (the last step's commit)
    if result and result.get("last_evaluation"):
        try:
            evaluation = EvaluationOutput.model_validate_json(result["last_evaluation"])
            steps = result.get("steps", [])
            final_idx = result.get("current_step_index", 0) - 1
            if 0 <= final_idx < len(steps):
                step = steps[final_idx]
                step_info = f"Step {step.index}: {step.instruction}"
                # Only save if we haven't already (check if index advanced past prev)
                if final_idx >= prev_step_index - 1:
                    pass  # already saved in the loop above
        except Exception:
            pass

    # Final summary
    print("\n" + "=" * 60)
    print("  EXECUTION COMPLETE")
    print("=" * 60)
    if result:
        print(f"  Steps completed: {result.get('current_step_index', 0)}/{len(result.get('steps', []))}")
        print(f"  Skill Memory:\n{result.get('skill_memory', '(empty)')}")
    print("=" * 60)

    # Phase 4: Ask for human feedback after workflow completion
    print("\n--- Feedback ---")
    feedback = input(
        "Please provide feedback on this skill execution (or press Enter to skip): "
    ).strip()
    if feedback:
        _save_human_feedback(md_path, feedback)
        print(f"Feedback saved to {md_path}")
    else:
        print("No feedback provided. Skipping.")

    return result or parsed_state


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Skills Agentic Executor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  skills-agent input/my_skill          # directory with skills.md\n"
            "  skills-agent path/to/skills.md       # direct markdown file\n"
        ),
    )
    parser.add_argument(
        "skill_path",
        help="Path to a skill directory (containing skills.md) or a markdown file.",
    )

    args = parser.parse_args()

    md_path = _resolve_skill_path(args.skill_path)
    skill_content = md_path.read_text(encoding="utf-8")

    if not skill_content.strip():
        print(f"Error: Skill file '{md_path}' is empty.")
        sys.exit(1)

    print(f"Loading skill from: {md_path}")
    run(skill_content, md_path)


if __name__ == "__main__":
    main()
