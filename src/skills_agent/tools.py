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

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "tools_config.yaml"


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
    """Execute a whitelisted CLI command through the Security Gateway.

    Available tools and their parameters:
    - list_files(path): List files at a path
    - read_file(path): Read file contents
    - search_text(pattern, path): Grep for patterns
    - git_status(): Show git status
    - git_diff(path): Show git diff
    - git_add(path): Stage files
    - git_commit(message): Create commit
    - make_directory(path): Create directory
    - tree(path): Show directory tree
    - head_file(lines, path): First N lines
    - tail_file(lines, path): Last N lines
    - word_count(path): Count lines/words/chars
    - pip_install(package): Install Python package
    - python_run(script): Run script from scripts/ dir
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
    """Execute a Python script from the approved scripts/ directory.

    Only scripts located in the scripts/ directory are allowed.
    Arguments and env vars are validated for safety.
    """
    import os

    if args is None:
        args = []
    if env_vars is None:
        env_vars = {}

    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"

    # Validate script path — must be inside scripts/ and end with .py
    script_path = (scripts_dir / script_name).resolve()
    if not script_path.is_relative_to(scripts_dir):
        return "[SECURITY BLOCKED] Script path escapes the scripts/ directory."
    if not script_path.suffix == ".py":
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
    if not script_path.exists():
        return f"[ERROR] Script not found: {script_name}"

    # Execute
    env = {**os.environ, **env_vars}
    cmd = ["python", str(script_path)] + [shlex.quote(a) for a in args]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
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
    whitelist = _CONFIG.get("cli_whitelist", {})
    for name, spec in whitelist.items():
        desc = spec.get("description", "")
        template = spec.get("template", "")
        params = spec.get("params", {})
        param_str = ", ".join(f"{k}" for k in params)
        lines.append(f"- {name}({param_str}): {desc}  [template: {template}]")
    lines.append("")
    lines.append("- safe_py_runner(script_name, args, env_vars): Run a Python script from scripts/")
    return "\n".join(lines)
