"""Tool Security Gateway — parametric whitelist for safe CLI and Python execution.

Protection layers:
    1. Regex validation on every parameter.
    2. shlex quoting for shell safety.
    3. Configurable timeout per command.
    4. Blocked-pattern scanning before execution.
"""

from __future__ import annotations

import re
import shlex
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


def _normalise_path_params(params: dict[str, str], param_rules: dict[str, str]) -> dict[str, str]:
    """Convert forward slashes to backslashes in path-like parameters.

    Any parameter whose validation regex allows backslashes (indicating a path
    parameter) gets its forward slashes replaced with backslashes automatically.
    This acts as a safety net when the LLM emits UNIX-style paths despite
    instructions to use Windows-style paths.
    """
    normalised = {}
    for key, value in params.items():
        rule = param_rules.get(key, "")
        # If the regex contains \\\\ (escaped backslash), it's a path parameter
        if "\\\\" in rule or key in ("path", "src", "dst"):
            normalised[key] = value.replace("/", "\\")
        else:
            normalised[key] = value
    return normalised


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

    # Normalise path parameters: forward slashes → backslashes
    params = _normalise_path_params(params, param_rules)

    # Validate every parameter
    for pname, regex in param_rules.items():
        value = params.get(pname, "")
        if not re.fullmatch(regex, value):
            raise ToolSecurityError(
                f"Parameter {pname!r} value {value!r} does not match "
                f"allowed pattern {regex!r}"
            )

    # Build command with shlex quoting
    quoted = {k: shlex.quote(v) for k, v in params.items()}
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
            "(e.g. 'list_files', 'read_file', 'git_status')."
        )
    )
    params: dict[str, str] = Field(
        default_factory=dict,
        description="Parameter key-value pairs matching the tool's template.",
    )


@tool("safe_cli_executor", args_schema=SafeCliInput)
def safe_cli_executor(tool_name: str, params: dict[str, str] | None = None) -> str:
    """Execute a whitelisted CLI sub-command through the Security Gateway.

    IMPORTANT: This is the ONLY way to run CLI commands. Do NOT call sub-commands
    (read_file, list_files, etc.) as separate tools — they must be passed as tool_name.

    All commands execute with cwd = PROJECT ROOT (the repository root).
    All path values in params MUST be relative to the project root and use
    Windows-style backslashes (\\). Forward slashes (/) are NOT allowed.

    Usage: safe_cli_executor(tool_name="<sub_command>", params={"path": "folder\\\\file.txt"})

    Examples:
      safe_cli_executor(tool_name="read_file", params={"path": "skills\\\\ects_skill\\\\tmp\\\\transcript.txt"})
      safe_cli_executor(tool_name="list_files", params={"path": "skills\\\\ects_skill\\\\tmp"})
      safe_cli_executor(tool_name="write_json", params={"path": "skills\\\\ects_skill\\\\tmp\\\\output.json", "content": "..."})

    Available sub-commands (pass as tool_name):
    - list_files: params={path}
    - read_file: params={path}
    - search_text: params={pattern, path}
    - make_directory: params={path}
    - tree: params={path}
    - head_file: params={lines, path}
    - tail_file: params={lines, path}
    - word_count: params={path}
    - write_json: params={path, content}
    - write_txt: params={path, content}
    - write_md: params={path, content}
    - copy_file: params={src, dst}
    - move_file: params={src, dst}
    - python_run: params={script}  (e.g. script="scripts\\\\format_check.py")
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
        description="Script filename inside the scripts/ directory (e.g. 'deploy.py')."
    )
    args: list[str] = Field(
        default_factory=list,
        description="Positional arguments to pass to the script.",
    )
    env_vars: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables to set for the script execution.",
    )


@tool("safe_py_runner", args_schema=SafePyInput)
def safe_py_runner(
    script_name: str,
    args: list[str] | None = None,
    env_vars: dict[str, str] | None = None,
) -> str:
    """Execute a Python script from approved directories.

    Allowed directories:
      - scripts\\           — shared utility scripts
      - skills\\<skill>\\   — skill-specific scripts (e.g. skills\\ects_skill\\parse_transcript.py)

    Arguments and env vars are validated for safety.
    """
    import os

    if args is None:
        args = []
    if env_vars is None:
        env_vars = {}

    scripts_dir = PROJECT_ROOT / "scripts"
    skills_dir = PROJECT_ROOT / "skills"

    # Normalise Windows backslashes to forward slashes for Path resolution
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

    # Validate args — no shell metacharacters (before checking file existence)
    arg_pattern = re.compile(r"^[a-zA-Z0-9_./:@=-]+$")
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
    for key in ("TRANSCRIPT_API_URL", "TRANSCRIPT_API_TOKEN"):
        if key in os.environ and key not in env_vars:
            env[key] = os.environ[key]
    cmd = ["python", str(script_path)] + [shlex.quote(a) for a in args]
    try:
        result = subprocess.run(
            cmd,
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


def get_tool_descriptions() -> str:
    """Return human-readable tool documentation for prompt injection."""
    lines: list[str] = []
    lines.append("Sub-commands for safe_cli_executor (call via tool_name + params):")
    whitelist = _CONFIG.get("cli_whitelist", {})
    for name, spec in whitelist.items():
        desc = spec.get("description", "")
        params = spec.get("params", {})
        param_str = ", ".join(f"{k}" for k in params)
        lines.append(
            f'  - tool_name="{name}", params={{ {", ".join(f"{k!r}: <value>" for k in params)} }}: {desc}'
        )
    lines.append("")
    lines.append("IMPORTANT: All path values MUST be relative to the PROJECT ROOT and use Windows-style backslashes (\\\\).")
    lines.append("  CORRECT: 'skills\\\\ects_skill\\\\tmp\\\\output.json'")
    lines.append("  WRONG:   'skills/ects_skill/tmp/output.json'")
    lines.append("  WRONG:   'ects_skill\\\\tmp\\\\output.json'  ← missing 'skills\\\\' prefix")
    lines.append("Forward slashes in paths are NOT allowed and will be rejected.")
    return "\n".join(lines)
