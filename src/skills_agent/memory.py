"""Three-layer memory management for the Skills Agent.

Layers:
    L1 — Role Context:   loaded from config/{role}.md (read-only, role-specific)
    L2 — Skill Memory:   cross-step KV store (append-only during task)
    L3 — Loop Context:   optimizer↔evaluator messages (cleared per step)
"""

from __future__ import annotations

from pathlib import Path


# ---------------------------------------------------------------------------
# L1: Role-Specific Context (replaces monolithic claude.md)
# ---------------------------------------------------------------------------

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

# Cache loaded contexts to avoid repeated disk reads
_role_context_cache: dict[str, str] = {}


def load_role_context(role: str) -> str:
    """Load role-specific context from config/{role}.md.

    Args:
        role: One of 'planner', 'optimizer', or 'evaluator'.

    Returns:
        The content of the role-specific context file, or a fallback message.
    """
    if role in _role_context_cache:
        return _role_context_cache[role]

    path = _CONFIG_DIR / f"{role}.md"
    if path.exists():
        content = path.read_text(encoding="utf-8").strip()
    else:
        content = f"(No context file found for role '{role}'.)"

    _role_context_cache[role] = content
    return content


# Backward-compatible alias — loads optimizer context as default
_CLAUDE_MD_PATH = Path(__file__).resolve().parents[2] / "claude.md"


def load_global_context(path: Path = _CLAUDE_MD_PATH) -> str:
    """Load the project-level global context from claude.md.

    Deprecated: Use load_role_context(role) instead.
    """
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
