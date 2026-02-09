"""Three-layer memory management for the Skills Agent.

Layers:
    L1 — Global Context:  loaded from claude.md (read-only, permanent)
    L2 — Skill Memory:    cross-step KV store (append-only during task)
    L3 — Loop Context:    optimizer↔evaluator messages (cleared per step)
"""

from __future__ import annotations

from pathlib import Path


# ---------------------------------------------------------------------------
# L1: Global Context
# ---------------------------------------------------------------------------

_CLAUDE_MD_PATH = Path(__file__).resolve().parents[2] / "claude.md"


def load_global_context(path: Path = _CLAUDE_MD_PATH) -> str:
    """Load the project-level global context from claude.md."""
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return "(No global context file found.)"


# ---------------------------------------------------------------------------
# L2: Skill Memory — append-only cross-step context
# ---------------------------------------------------------------------------


def append_skill_memory(current: str, key_outputs: dict[str, str]) -> str:
    """Append key-value outputs from a completed step to skill memory.

    Format:
        KEY=VALUE (one per line, easy for LLM to parse)
    """
    if not key_outputs:
        return current

    new_entries = "\n".join(f"{k}={v}" for k, v in key_outputs.items())
    if current:
        return f"{current}\n{new_entries}"
    return new_entries


def format_skill_memory(memory: str) -> str:
    """Format skill memory for prompt injection."""
    if not memory:
        return "(empty — no cross-step data yet)"
    return memory


# ---------------------------------------------------------------------------
# L3: Loop Context — message lifecycle helpers
# ---------------------------------------------------------------------------


def clear_loop_messages() -> list:
    """Return an empty message list to reset L3 context between steps."""
    return []
