"""Tool Security Gateway — parametric whitelist for safe CLI and Python execution.

Protection layers:
    1. Regex validation on every parameter.
    2. Configurable timeout per command.
    3. Blocked-pattern scanning before execution.

All core I/O operations are now handled by Python scripts in scripts/,
executed via safe_py_runner. The safe_cli_executor retains only the
python_run sub-command as a legacy execution vector.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import yaml
from langchain_core.tools import tool
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = PROJECT_ROOT / "config" / "tools_config.yaml"


def _load_config(path: Path = _CONFIG_PATH) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)


_CONFIG: dict[str, Any] = _load_config()

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


class ToolSecurityError(Exception):
    """Raised when a tool call violates security constraints."""


def _check_blocked_patterns(command: str) -> None:
    """Scan assembled command against blocked patterns."""
    for pattern in _CONFIG.get("blocked_patterns", []):
        if re.search(pattern, command):
            raise ToolSecurityError(
                f"Command blocked by security policy (matched pattern: {pattern!r})"
            )


def _validate_and_build(tool_name: str, params: dict[str, str]) -> tuple[str, int]:
    """Validate parameters against whitelist and build the final command string.

    Returns:
        (command_string, timeout_seconds)
    """
    whitelist = _CONFIG.get("cli_whitelist", {})
    spec = whitelist.get(tool_name)
    if spec is None:
        raise ToolSecurityError(f"Tool {tool_name!r} is not in the whitelist.")

    template: str = spec["template"]
    param_rules: dict[str, str] = spec.get("params", {})
    timeout: int = spec.get("timeout", 30)

    # Validate every parameter
    for pname, regex in param_rules.items():
        value = params.get(pname, "")
        if not re.fullmatch(regex, value):
            raise ToolSecurityError(
                f"Parameter {pname!r} value {value!r} does not match "
                f"allowed pattern {regex!r}"
            )

    # Build command — parameters are already regex-validated
    quoted = {}
    for k, v in params.items():
        quoted[k] = v
    command = template.format(**quoted)

    # Final blocked-pattern scan on the assembled command
    _check_blocked_patterns(command)

    return command, timeout


def _run_command(command: str, timeout: int) -> str:
    """Execute a shell command with timeout and capture output."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(PROJECT_ROOT),
        )
        output = result.stdout
        if result.returncode != 0:
            output += f"\n[STDERR]\n{result.stderr}" if result.stderr else ""
            output += f"\n[EXIT CODE] {result.returncode}"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"[ERROR] Command timed out after {timeout}s: {command}"


# ---------------------------------------------------------------------------
# LangChain Tools (exposed to the Optimizer Agent)
# ---------------------------------------------------------------------------


class SafeCliInput(BaseModel):
    """Input schema for safe_cli_executor."""

    tool_name: str = Field(
        description=(
            "Name of the whitelisted CLI tool to execute "
            "(e.g. 'python_run')."
        )
    )
    params: dict[str, str] = Field(
        default_factory=dict,
        description="Parameter key-value pairs matching the tool's template.",
    )


@tool("safe_cli_executor", args_schema=SafeCliInput)
def safe_cli_executor(tool_name: str, params: dict[str, str] | None = None) -> str:
    """Execute a whitelisted CLI sub-command through the Security Gateway.

    NOTE: Most I/O operations have been migrated to Python scripts via safe_py_runner.
    This tool now primarily supports the python_run sub-command as a legacy vector.

    All commands execute with cwd = PROJECT ROOT (the repository root).
    All path values in params MUST use forward slashes (/) and be relative to project root.

    Available sub-commands (pass as tool_name):
    - python_run: params={script}  (e.g. script="scripts/parse_transcript.py")

    For file I/O, prefer safe_py_runner with the dedicated scripts:
    - scripts/read.py — read file content
    - scripts/list.py — list directory contents
    - scripts/write_json.py — write JSON files
    - scripts/write_txt.py — write text files
    - scripts/write_md.py — write markdown files
    - scripts/write_file.py — write arbitrary content from stdin
    """
    if params is None:
        params = {}
    try:
        command, timeout = _validate_and_build(tool_name, params)
        return _run_command(command, timeout)
    except ToolSecurityError as exc:
        return f"[SECURITY BLOCKED] {exc}"


class SafePyInput(BaseModel):
    """Input schema for safe_py_runner."""

    script_name: str = Field(
        description="Script path relative to project root (e.g. 'scripts/read.py')."
    )
    args: list[str] = Field(
        default_factory=list,
        description="Positional arguments to pass to the script.",
    )
    env_vars: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables to set for the script execution.",
    )
    stdin_text: str = Field(
        default="",
        description=(
            "Text to pipe into the script's stdin. Use this to pass large or "
            "quote-sensitive content (e.g. markdown, JSON) that cannot be safely "
            "passed as a CLI argument due to shell quoting issues."
        ),
    )


@tool("safe_py_runner", args_schema=SafePyInput)
def safe_py_runner(
    script_name: str,
    args: list[str] | None = None,
    env_vars: dict[str, str] | None = None,
    stdin_text: str = "",
) -> str:
    """Execute a Python script from approved directories.

    Allowed directories:
      - scripts/           — shared utility scripts
      - skills/<skill>/    — skill-specific scripts

    All paths use forward slashes (/) and are relative to the project root.

    Arguments and env vars are validated for safety.

    Use stdin_text to pass large content (markdown, JSON) to scripts that read
    from sys.stdin — this bypasses all shell quoting issues.

    Core I/O scripts available:
      - scripts/read.py       — read file content: args=[file_path]
      - scripts/list.py       — list directory: args=[dir_path]
      - scripts/write_file.py — write from stdin: args=[file_path], stdin_text=content
      - scripts/write_json.py — write JSON: args=[file_path, json_content]
      - scripts/write_txt.py  — write text: args=[file_path, text_content]
      - scripts/write_md.py   — write markdown: args=[file_path, md_content]

    Web search scripts:
      - scripts/web_search.py     — Claude-powered web search: args=[query]
      - scripts/gemini_search.py  — Gemini Google Search: args=[query]

    Anthropic native skills (container-based document generation):
      - scripts/claude_pdf.py              — generate PDF: args=[output_path], stdin_text=prompt
      - scripts/claude_docx.py             — generate DOCX: args=[output_path], stdin_text=prompt
      - scripts/claude_pptx.py             — generate PPTX: args=[output_path], stdin_text=prompt
      - scripts/claude_xlsx.py             — generate XLSX: args=[output_path], stdin_text=prompt
      - scripts/claude_frontend_design.py  — generate HTML/CSS/JS: args=[output_path], stdin_text=prompt

    Example:
      safe_py_runner(
          script_name="scripts/write_file.py",
          args=["skills/ects_skill/tmp/ai_summary.md"],
          stdin_text=filled_markdown_content,
      )
    """
    import os

    if args is None:
        args = []
    if env_vars is None:
        env_vars = {}

    scripts_dir = PROJECT_ROOT / "scripts"
    skills_dir = PROJECT_ROOT / "skills"

    # Normalise any backslashes to forward slashes for cross-platform Path resolution
    normalised_name = script_name.replace("\\", "/")

    # Determine which allowed directory the script belongs to
    candidate = (PROJECT_ROOT / normalised_name).resolve()

    allowed = False
    if candidate.is_relative_to(scripts_dir):
        allowed = True
    elif candidate.is_relative_to(skills_dir):
        allowed = True
    if not allowed:
        return (
            "[SECURITY BLOCKED] Script path must be inside scripts/ or skills/<skill>/. "
            f"Got: {script_name!r}"
        )

    if not candidate.suffix == ".py":
        return "[SECURITY BLOCKED] Only .py files are allowed."

    # Validate args — no shell metacharacters
    arg_pattern = re.compile(r"^[a-zA-Z0-9_./:@=\\-]+$")
    for arg in args:
        if not arg_pattern.match(arg):
            return f"[SECURITY BLOCKED] Argument contains forbidden characters: {arg!r}"

    # Validate env vars
    env_key_pattern = re.compile(r"^[A-Z_][A-Z0-9_]*$")
    env_val_pattern = re.compile(r"^[a-zA-Z0-9_./:@=-]*$")
    for k, v in env_vars.items():
        if not env_key_pattern.match(k):
            return f"[SECURITY BLOCKED] Env var key is invalid: {k!r}"
        if not env_val_pattern.match(v):
            return f"[SECURITY BLOCKED] Env var value is invalid: {v!r}"

    # Check file existence (after all security validations pass)
    script_path = candidate
    if not script_path.exists():
        return f"[ERROR] Script not found: {script_name}"

    # Execute — inherit TRANSCRIPT_API_* env vars for ects_skill scripts
    env = {**os.environ, **env_vars}
    for key in (
        "TRANSCRIPT_API_URL",
        "TRANSCRIPT_API_TOKEN",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
    ):
        if key in os.environ and key not in env_vars:
            env[key] = os.environ[key]

    # Normalise paths in args to forward slashes for cross-platform compatibility
    normalised_args = [a.replace("\\", "/") for a in args]
    cmd = ["python", str(script_path)] + normalised_args
    try:
        result = subprocess.run(
            cmd,
            input=stdin_text or None,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            cwd=str(PROJECT_ROOT),
        )
        output = result.stdout
        if result.returncode != 0:
            output += f"\n[STDERR]\n{result.stderr}" if result.stderr else ""
            output += f"\n[EXIT CODE] {result.returncode}"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"[ERROR] Script timed out after 120s: {script_name}"


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

ALL_TOOLS = [safe_cli_executor, safe_py_runner]
READONLY_TOOLS = [safe_cli_executor]  # Read-only inspection
EVALUATOR_TOOLS = [safe_cli_executor, safe_py_runner]  # Evaluator: read + py verification

# Mapping from tool hint names to actual tool objects for dynamic filtering
_TOOL_NAME_MAP = {
    "safe_cli_executor": safe_cli_executor,
    "safe_py_runner": safe_py_runner,
}


def filter_tools_by_hint(tools_hint: list[str]) -> list:
    """Filter the tool registry to include only hinted tools.

    If tools_hint is empty, returns ALL_TOOLS (no filtering).

    Args:
        tools_hint: List of tool names suggested by the Planner.

    Returns:
        List of LangChain tool objects matching the hints.
    """
    if not tools_hint:
        return list(ALL_TOOLS)

    filtered = []
    for hint in tools_hint:
        tool = _TOOL_NAME_MAP.get(hint)
        if tool and tool not in filtered:
            filtered.append(tool)

    # Fallback: if no valid hints matched, return all tools
    return filtered if filtered else list(ALL_TOOLS)


def get_tool_descriptions_for_hint(tools_hint: list[str]) -> str:
    """Return tool documentation filtered by tools_hint.

    If tools_hint is empty, returns full documentation.

    Args:
        tools_hint: List of tool names to document.

    Returns:
        Human-readable tool documentation string.
    """
    if not tools_hint:
        return get_tool_descriptions()

    lines: list[str] = []

    if "safe_py_runner" in tools_hint:
        lines.append("## Primary Tool: safe_py_runner")
        lines.append("All I/O operations use Python scripts executed via safe_py_runner.")
        lines.append("Available scripts (pass as script_name):")
        lines.append("  - scripts/read.py       — Read file content. args=[file_path]")
        lines.append("  - scripts/list.py       — List directory contents. args=[dir_path]")
        lines.append("  - scripts/write_file.py — Write content from stdin. args=[file_path], stdin_text=content")
        lines.append("  - scripts/write_json.py — Write JSON file. args=[file_path, json_content]")
        lines.append("  - scripts/write_txt.py  — Write text file. args=[file_path, text_content]")
        lines.append("  - scripts/write_md.py   — Write markdown file. args=[file_path, md_content]")
        lines.append("")
        lines.append("Web search scripts:")
        lines.append("  - scripts/web_search.py     — Claude-powered web search. args=[query]")
        lines.append("  - scripts/gemini_search.py  — Gemini Google Search. args=[query]")
        lines.append("")
        lines.append("Anthropic native skills (container-based document generation):")
        lines.append("  - scripts/claude_pdf.py              — Generate PDF. args=[output_path], stdin_text=prompt")
        lines.append("  - scripts/claude_docx.py             — Generate DOCX. args=[output_path], stdin_text=prompt")
        lines.append("  - scripts/claude_pptx.py             — Generate PPTX. args=[output_path], stdin_text=prompt")
        lines.append("  - scripts/claude_xlsx.py             — Generate XLSX. args=[output_path], stdin_text=prompt")
        lines.append("  - scripts/claude_frontend_design.py  — Generate HTML/CSS/JS. args=[output_path], stdin_text=prompt")
        lines.append("")

    if "safe_cli_executor" in tools_hint:
        lines.append("## Legacy Tool: safe_cli_executor")
        lines.append("Retains only the python_run sub-command:")
        whitelist = _CONFIG.get("cli_whitelist", {})
        for name, spec in whitelist.items():
            desc = spec.get("description", "")
            params = spec.get("params", {})
            lines.append(
                f'  - tool_name="{name}", params={{ {", ".join(f"{k!r}: <value>" for k in params)} }}: {desc}'
            )
        lines.append("")

    lines.append("IMPORTANT: All path values MUST use forward slashes (/) and be relative to the PROJECT ROOT.")
    return "\n".join(lines)


def get_tool_descriptions() -> str:
    """Return human-readable tool documentation for prompt injection."""
    lines: list[str] = []

    lines.append("## Primary Tool: safe_py_runner")
    lines.append("All I/O operations use Python scripts executed via safe_py_runner.")
    lines.append("Available scripts (pass as script_name):")
    lines.append("  - scripts/read.py       — Read file content. args=[file_path]")
    lines.append("  - scripts/list.py       — List directory contents. args=[dir_path]")
    lines.append("  - scripts/write_file.py — Write content from stdin. args=[file_path], stdin_text=content")
    lines.append("  - scripts/write_json.py — Write JSON file. args=[file_path, json_content]")
    lines.append("  - scripts/write_txt.py  — Write text file. args=[file_path, text_content]")
    lines.append("  - scripts/write_md.py   — Write markdown file. args=[file_path, md_content]")
    lines.append("")
    lines.append("Web search scripts:")
    lines.append("  - scripts/web_search.py     — Claude-powered web search. args=[query]")
    lines.append("  - scripts/gemini_search.py  — Gemini Google Search. args=[query]")
    lines.append("")
    lines.append("Anthropic native skills (container-based document generation):")
    lines.append("  - scripts/claude_pdf.py              — Generate PDF. args=[output_path], stdin_text=prompt")
    lines.append("  - scripts/claude_docx.py             — Generate DOCX. args=[output_path], stdin_text=prompt")
    lines.append("  - scripts/claude_pptx.py             — Generate PPTX. args=[output_path], stdin_text=prompt")
    lines.append("  - scripts/claude_xlsx.py             — Generate XLSX. args=[output_path], stdin_text=prompt")
    lines.append("  - scripts/claude_frontend_design.py  — Generate HTML/CSS/JS. args=[output_path], stdin_text=prompt")
    lines.append("")
    lines.append("## Legacy Tool: safe_cli_executor")
    lines.append("Retains only the python_run sub-command:")
    whitelist = _CONFIG.get("cli_whitelist", {})
    for name, spec in whitelist.items():
        desc = spec.get("description", "")
        params = spec.get("params", {})
        lines.append(
            f'  - tool_name="{name}", params={{ {", ".join(f"{k!r}: <value>" for k in params)} }}: {desc}'
        )
    lines.append("")
    lines.append("IMPORTANT: All path values MUST use forward slashes (/) and be relative to the PROJECT ROOT.")
    lines.append("  CORRECT: 'skills/ects_skill/tmp/output.json'")
    lines.append("  WRONG:   'skills\\\\ects_skill\\\\tmp\\\\output.json'  ← backslashes are not required")
    return "\n".join(lines)
