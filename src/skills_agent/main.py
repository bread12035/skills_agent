"""CLI entry point for the Claude Skills Agentic Executor.

Usage:
    skills-agent "Deploy the microservice and test it"
    skills-agent --resume <thread_id>
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid

from skills_agent.graph import build_graph
from skills_agent.models import AgentState, EvaluationOutput

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


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


def run(instruction: str, thread_id: str | None = None) -> dict:
    """Run the Skills Agent with the given instruction.

    Args:
        instruction: Natural language instruction.
        thread_id: Optional thread ID for resuming a previous execution.

    Returns:
        Final agent state.
    """
    graph = build_graph()
    tid = thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": tid}}

    print(f"Thread ID: {tid}")
    print(f"Instruction: {instruction}")

    # Initial invocation — will pause after skill_parser for plan approval
    initial_state: AgentState = {
        "raw_input": instruction,
        "steps": [],
        "current_step_index": 0,
        "step_retry_count": 0,
        "max_retries": 3,
        "skill_memory": "",
        "messages": [],
        "last_evaluation": "",
        "plan_approved": False,
    }

    # Phase 1: Parse and present plan (interrupted after skill_parser)
    result = None
    for event in graph.stream(initial_state, config, stream_mode="values"):
        result = event

    if result and result.get("steps"):
        _print_plan(result)

        # Human approval gate
        approval = input("\nApprove this plan? [Y/n]: ").strip().lower()
        if approval in ("n", "no"):
            print("Plan rejected. Exiting.")
            return result

    # Phase 2: Resume execution (the graph continues from where it paused)
    result = None
    for event in graph.stream(None, config, stream_mode="values"):
        result = event
        _print_step_status(result)

        # Check if human intervention is needed
        snapshot = graph.get_state(config)
        if snapshot.next and "human_intervention" in snapshot.next:
            idx = result.get("current_step_index", 0)
            step = result["steps"][idx]
            print(f"\n!!! Human intervention needed at Step {idx}: {step.instruction}")
            print(f"    Last evaluation: {result.get('last_evaluation', 'N/A')}")

            action = input("Enter fix or 'skip' to skip this step: ").strip()
            if action.lower() == "skip":
                # Skip by incrementing index
                graph.update_state(
                    config,
                    {"current_step_index": idx + 1, "step_retry_count": 0},
                )
            else:
                # Resume with human guidance injected into memory
                graph.update_state(
                    config,
                    {
                        "skill_memory": result.get("skill_memory", "")
                        + f"\nHUMAN_FIX={action}",
                        "step_retry_count": 0,
                    },
                )

            # Continue execution
            for event in graph.stream(None, config, stream_mode="values"):
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


def resume(thread_id: str) -> dict:
    """Resume a previously interrupted execution.

    Args:
        thread_id: The thread ID from a previous run.

    Returns:
        Final agent state.
    """
    graph = build_graph()
    config = {"configurable": {"thread_id": thread_id}}

    print(f"Resuming thread: {thread_id}")

    snapshot = graph.get_state(config)
    if not snapshot or not snapshot.values:
        print("No saved state found for this thread ID.")
        return {}

    _print_step_status(snapshot.values)

    result = None
    for event in graph.stream(None, config, stream_mode="values"):
        result = event
        _print_step_status(result)

    if result:
        print("\n  Execution resumed and completed.")
        print(f"  Final step index: {result.get('current_step_index', 0)}")

    return result or {}


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Claude Skills Agentic Executor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "instruction",
        nargs="?",
        help="Natural language instruction to execute.",
    )
    parser.add_argument(
        "--resume",
        metavar="THREAD_ID",
        help="Resume a previously interrupted execution by thread ID.",
    )
    parser.add_argument(
        "--thread-id",
        metavar="ID",
        help="Specify a thread ID for the new execution.",
    )

    args = parser.parse_args()

    if args.resume:
        resume(args.resume)
    elif args.instruction:
        run(args.instruction, thread_id=args.thread_id)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
