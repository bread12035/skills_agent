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

Optimizer uses `safe_py_runner` (primary) and `safe_cli_executor` (legacy) to execute step instructions.
Evaluator also uses both tools for verification, plus produces a structured `EvaluationOutput`:
- **PASS** → commit `key_outputs` to L2 skill memory, append step report to `report_state`, advance to next step
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

**L2 — Report State**: Cumulative list of step reports (`report_state` field in `AgentState`).
Each time a step passes, the Evaluator's report (trajectory, verdict, feedback, key outputs)
is appended to `report_state` by `commit_step`. The `current_report` field holds the active
step's report before it is committed. Upon completing the entire plan, the agent's final
response includes the aggregated content of `report_state`.

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

All tool execution is strictly Python-script-based via `safe_py_runner`, with `safe_cli_executor`
retained as a legacy vector for the `python_run` sub-command only.

Security layers (`config/tools_config.yaml`):
- Regex validation on every parameter before execution
- Blocked-pattern scanning on the assembled command string
- Per-tool configurable timeouts (120s for Python scripts)
- Path-escape prevention for Python scripts (must be inside `scripts/` or `skills/<skill>/`)
- All CLI whitelist entries (list_files, read_file, etc.) have been migrated to Python scripts

### Tools

**safe_py_runner** (PRIMARY): Executes Python scripts from approved directories.
Supports `args` (positional args), `env_vars` (injected environment), and `stdin_text`
(pipe large or quote-sensitive content into the script's stdin — preferred for file writing).
Allowed directories: `scripts/` and `skills/<skill>/`.

Core I/O scripts:
- `scripts/read.py` — Read file content
- `scripts/list.py` — List directory contents
- `scripts/write_file.py` — Write arbitrary content from stdin (preferred for markdown/JSON)
- `scripts/write_json.py` — Write JSON via args
- `scripts/write_txt.py` — Write plain text via args
- `scripts/write_md.py` — Write markdown via args

**safe_cli_executor** (LEGACY): Dispatches to whitelisted CLI sub-commands.
Only `python_run` remains active. All other sub-commands (list_files, read_file, etc.)
have been commented out and migrated to Python scripts.

### Path Conventions

All paths relative to PROJECT_ROOT (repository root containing `pyproject.toml`).
All paths use forward slashes (/) — cross-platform compatible.

- CORRECT: `skills/ects_skill/tmp/output.json`
- WRONG: `skills\ects_skill\tmp\output.json` ← Windows backslashes are not required

### Step Schema

Each step in a `SkillPlan` has two separate instruction fields:
- `optimizer_instruction`: what the Optimizer must do (tool calls, actions, explicit stop signal)
- `evaluator_instruction`: how the Evaluator verifies success + which `key_outputs` to extract into L2

One action per step: either a single tool call OR a pure text-processing task (no mixing).

### Evaluator Reporting

For every step, the Evaluator generates a report containing:
- **Trajectory**: Summary of the Optimizer's tool calls and reasoning
- **Verdict**: PASS or FAIL
- **Feedback**: Why it passed or what went wrong
- **Key Outputs**: Data extracted for L2 memory (on PASS)

On PASS, the report is appended to `report_state`. On FAIL, the report + feedback
is returned to the Optimizer via message history for retry.
