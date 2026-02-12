## Skills Agent — Optimizer-Evaluator Architecture

Two-layer loop agent using LangGraph: Parser decomposes user instructions into steps,
then an Optimizer-Evaluator loop executes and verifies each step before advancing.

### Execution Flow
User Input → Skill Parser → [For each step: Prepare Context → Optimizer ↔ Tools ↔ Evaluator → Commit] → End

Optimizer uses tools to execute step instructions. Evaluator verifies completion with
read-only tools + Python verification scripts, produces PASS/FAIL verdict.
- PASS → commit outputs to skill memory, advance to next step
- FAIL → retry (up to max_retries) or request human intervention

### Three-Layer Memory

**L1 - Global Context (this file)**: Project-wide context injected into every prompt. Permanent.

**L2 - Skill Memory**: Cross-step data store managed by Evaluator. Evaluator extracts data
from completed steps into key_outputs, which are passed to subsequent steps via skill_memory.
Append-only during task execution.

**L3 - Loop Context**: Optimizer-Evaluator message history. Retains last 3 conversation rounds
+ current step instruction. Partially cleared between steps to prevent topic drift.

### Security Gateway
All tool execution via parametric whitelist (config/tools_config.yaml):
- Regex validation on every parameter
- Blocked-pattern scanning on assembled commands
- Per-tool configurable timeouts
- Path-escape prevention for Python scripts

### Path Conventions
All paths relative to PROJECT_ROOT (repository root containing pyproject.toml).
CLI tools (safe_cli_executor): Windows-style backslashes required.
Python scripts (safe_py_runner): Forward slashes or backslashes accepted.

Examples:
- ✓ skills\\ects_skill\\tmp\\output.json
- ✗ skills/ects_skill/tmp/output.json (CLI tool would fail)
- ✓ scripts/format_check.py (Python script path)
