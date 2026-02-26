## Skills Agent — Planner-Optimizer-Evaluator Architecture

Two-graph agentic system using LangGraph: a context-aware Planner decomposes a skill definition
into steps, then an Optimizer-Evaluator loop executes and verifies each step before advancing.

### Execution Flow

```
Skill File → Planner Graph → Human Approval → Execution Graph → Human Feedback
```

**Planner Graph** (runs once, before approval):
- Reads tool docs, available scripts, and historical data (Success/Failure Cases, Human Feedback) from `skills.md`
- Produces a `SkillPlan` with distinct `optimizer_instruction` and `evaluator_instruction` per step

**Execution Graph** (runs after approval):
```
prepare_step_context → optimizer_agent ↔ tool_executor (loop)
                                    │
                          [ATTEMPTS_COMPLETE] signal
                                    │
                              evaluator_agent
                             /               \
                        PASS                FAIL
                           │                  │ (< max_retries)
                      commit_step        optimizer_agent (retry)
                           │                  │ (≥ max_retries)
                    route_step          human_intervention
                   /          \
         next step          END
```

Optimizer uses `safe_cli_executor` and `safe_py_runner` to execute step instructions.
Evaluator also uses both tools for verification, plus produces a structured `EvaluationOutput`:
- **PASS** → commit `key_outputs` to L2 skill memory, advance to next step
- **FAIL** → retry (up to `max_retries=3`) or route to `human_intervention`

### Completion Signal

The Optimizer **must** emit `[ATTEMPTS_COMPLETE]` as the prefix of its final text response
to trigger evaluation. Without it, the router treats the response as an implicit completion
(logs a warning and proceeds to evaluation anyway).

### Stuck-Loop Guard

If `current_loop_count` exceeds 8 tool-call iterations within a step, the router replans
by routing back to `prepare_step_context`, which clears L3 messages and reinjects
the step instruction. L2 skill memory is preserved.

### L3 Directive Anchoring

Every 3 tool calls within a step, a `<primary_directive>` reminder is injected into the
message stream to prevent context drift in long tool-calling sequences.

### Three-Layer Memory

**L1 — Global Context (this file)**: Loaded from `claude.md` by `load_global_context()`.
Injected into the Optimizer's **System Prompt** inside `<global_context>` tags. Permanent and read-only.

**L2 — Skill Memory**: Cross-step append-only KV store (`skill_memory` field in `AgentState`).
Evaluator extracts `key_outputs` from completed steps; committed by `commit_step`. Injected into
the **User Prompt** of both Optimizer and Evaluator inside `<skill_memory>` XML tags.
Format: `KEY=VALUE` lines. L3 is cleared between steps — L2 is the ONLY cross-step data bridge.

**L3 — Loop Context**: `messages` list in `AgentState` holding the Optimizer ↔ tool ↔ Evaluator
dialogue for the current step. Cleared at the start of each new step by `prepare_step_context`
(via `RemoveMessage` for all existing messages).

### Skills.md Self-Learning

After each step evaluation, the agent persists outcomes to `skills.md` automatically:
- **Success Cases**: execution sequence (tool calls + args), key outputs
- **Failure Cases**: evaluator feedback on what went wrong

Human feedback is solicited after the full run and stored under **Human Feedback**.
The Planner reads all three sections and uses them to improve subsequent plans.

### Security Gateway

All tool execution via parametric whitelist (`config/tools_config.yaml`):
- Regex validation on every parameter before execution
- Blocked-pattern scanning on the assembled command string
- Per-tool configurable timeouts (10s CLI, 120s Python scripts)
- Path-escape prevention for Python scripts (must be inside `scripts/` or `skills/<skill>/`)
- Automatic path normalisation: forward slashes in path params → backslashes

### Tools

**safe_cli_executor**: Dispatches to whitelisted CLI sub-commands (Windows CMD syntax).
Available sub-commands: `list_files`, `read_file`, `make_directory`, `tree`, `copy_file`, `move_file`, `python_run`.
Text-analysis operations (search, count, head, tail) are intentionally absent — the LLM reasons over file content directly.

**safe_py_runner**: Executes Python scripts from approved directories.
Supports `args` (positional args), `env_vars` (injected environment), and `stdin_text`
(pipe large or quote-sensitive content into the script's stdin — preferred for file writing).
Allowed directories: `scripts/` and `skills/<skill>/`.

**All file writing must use safe_py_runner** with scripts in `scripts/`:
- `scripts/write_file.py` — write arbitrary content from stdin (preferred for markdown/JSON)
- `scripts/write_json.py` — write JSON via args
- `scripts/write_txt.py` — write plain text via args
- `scripts/write_md.py` — write markdown via args

### Path Conventions

All paths relative to PROJECT_ROOT (repository root containing `pyproject.toml`).

**CLI tools (safe_cli_executor)**: Windows-style backslashes required.
Path params containing forward slashes are auto-normalised to backslashes.
- CORRECT: `skills\\ects_skill\\tmp\\output.json`
- WRONG: `skills/ects_skill/tmp/output.json`
- WRONG: `ects_skill\\tmp\\output.json` ← missing `skills\\` prefix

**Python scripts (safe_py_runner)**: Forward slashes or backslashes accepted.
- CORRECT: `scripts/write_file.py`
- CORRECT: `skills/ects_skill/retrieve_transcript.py`

### Step Schema

Each step in a `SkillPlan` has two separate instruction fields:
- `optimizer_instruction`: what the Optimizer must do (tool calls, actions, explicit stop signal)
- `evaluator_instruction`: how the Evaluator verifies success + which `key_outputs` to extract into L2

One action per step: either a single tool call OR a pure text-processing task (no mixing).
