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
from pathlib import Path

from skills_agent.graph import build_graph
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


def run(skill_content: str) -> dict:
    """Run the Skills Agent with content read from a skill file.

    Args:
        skill_content: Markdown content from the skill file.

    Returns:
        Final agent state.
    """
    graph = build_graph()

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

    # Phase 1: Parse and present plan
    result = None
    for event in graph.stream(initial_state, stream_mode="values"):
        result = event

    if result and result.get("steps"):
        _print_plan(result)

        # Human approval gate
        approval = input("\nApprove this plan? [Y/n]: ").strip().lower()
        if approval in ("n", "no"):
            print("Plan rejected. Exiting.")
            return result

    # Phase 2: Continue execution
    result = None
    for event in graph.stream(None, stream_mode="values"):
        result = event
        _print_step_status(result)

    # Final summary
    print("\n" + "=" * 60)
    print("  EXECUTION COMPLETE")
    print("=" * 60)
    print(f"  Steps completed: {result.get('current_step_index', 0)}/{len(result.get('steps', []))}")
    print(f"  Skill Memory:\n{result.get('skill_memory', '(empty)')}")
    print("=" * 60)

    return result


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
    run(skill_content)


if __name__ == "__main__":
    main()
