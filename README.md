# Skills Agent

A **Planner → Optimizer → Evaluator** agentic loop powered by [LangGraph](https://github.com/langchain-ai/langgraph).
A context-aware Planner decomposes a skill definition into steps, then an inner Optimizer-Evaluator loop executes and verifies each step before advancing.

## Quick Start

### 1. Install

```bash
# Clone the repo and install in editable mode
git clone <repo-url> && cd skills_agent
pip install -e .
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env and set OPENAI_API_KEY (and optionally OPENAI_API_BASE)
```

### 3. Run

Pass a **skill directory** (containing a `skills.md`) or a direct path to a markdown file:

```bash
# Run the bundled example skill
skills-agent skills/hello_skill

# Or point to any markdown file
skills-agent path/to/your/skills.md
```

The agent will:
1. Parse the skill into an execution plan (Planner)
2. Display the plan and ask for your approval
3. Execute each step through the Optimizer-Evaluator loop
4. Persist success/failure cases and human feedback back to `skills.md`

## Project Layout

```
skills_agent/
├── src/skills_agent/
│   ├── graph.py     # LangGraph graph assembly (Planner + Execution graphs)
│   ├── nodes.py     # Node implementations (planner, optimizer, evaluator, etc.)
│   ├── tools.py     # Security Gateway: safe_cli_executor + safe_py_runner
│   ├── models.py    # Pydantic models and AgentState schema
│   ├── memory.py    # Three-layer memory helpers (L1/L2/L3)
│   ├── prompts.py   # Prompt templates for Planner, Optimizer, Evaluator
│   └── main.py      # CLI entry point + skill persistence (success/failure cases)
├── skills/          # Skill definitions (each subfolder has a skills.md + scripts)
│   ├── hello_skill/
│   │   ├── skills.md    # Step-by-step skill instructions
│   │   └── greet.py     # Skill-specific script
│   └── ects_skill/
│       ├── skills.md    # Earnings Call Transcript Summarizer skill
│       └── reference/   # Reference templates
├── scripts/             # Shared utility scripts (callable via safe_py_runner)
│   ├── write_file.py    # Write arbitrary content via stdin
│   ├── write_json.py    # Write JSON to file
│   ├── write_txt.py     # Write plain text to file
│   ├── write_md.py      # Write markdown to file
│   ├── format_check.py  # Validate summary against template structure
│   ├── parse_transcript.py
│   └── retrieve_transcript.py
├── config/
│   └── tools_config.yaml  # CLI whitelist and blocked patterns
├── tests/               # Pytest suite
├── pyproject.toml       # Build config & entry point
├── requirements.txt     # Pinned dependencies
└── .env.example         # Environment variable template
```

## Architecture

### Execution Flow

```
Skill File (skills.md)
       │
       ▼
   [Planner]  ← context-aware: reads tool docs, available scripts, historical data
       │
       ▼
 Human Approval
       │
       ▼
  ┌────────────────────────────────────────────┐
  │  For each step:                            │
  │                                            │
  │  prepare_step_context                      │
  │         │                                  │
  │         ▼                                  │
  │   [Optimizer Agent] ←──────────────────┐  │
  │         │ (tool calls)                  │  │
  │         ▼                              │  │
  │   [tool_executor]                      │  │
  │         │                              │  │
  │         └──────────── loop ────────────┘  │
  │         │ ([ATTEMPTS_COMPLETE] signal)     │
  │         ▼                                  │
  │   [Evaluator Agent]  ← uses tools too     │
  │         │                                  │
  │    PASS │ FAIL                             │
  │         ▼    ▼                             │
  │   commit_step  optimizer_agent (retry)     │
  │         │       or human_intervention      │
  └────────────────────────────────────────────┘
       │
       ▼
  Human Feedback → persisted to skills.md
```

### Three-Layer Memory

| Layer | Name | Description | Scope |
|---|---|---|---|
| L1 | Global Context | Loaded from `claude.md`, injected into every system prompt | Permanent |
| L2 | Skill Memory | Cross-step KV store; Evaluator extracts `key_outputs` into it on PASS | Append-only per run |
| L3 | Loop Context | Optimizer ↔ tool messages within a step | Cleared at each new step |

**Data flow:** L3 is cleared between steps. Cross-step data flows **exclusively** through L2 (`skill_memory`). L2 is injected into the **User Prompt** inside `<skill_memory>` XML tags.

### Completion Signal

The Optimizer must emit `[ATTEMPTS_COMPLETE]` as the prefix of its final text response to trigger the evaluation phase. Without this signal, the router will forward the response to the evaluator anyway (with a warning), but explicit use of the signal is required.

### Stuck-Loop Guard

If the Optimizer makes more than 8 tool-call iterations in a single step without completing, the router replans by routing back to `prepare_step_context`, which clears L3 and reinjects the step instruction.

### Skills.md Self-Learning

After each step completes, the agent automatically appends the outcome to `skills.md`:
- **Success Cases** — execution sequence, tool calls, and key outputs
- **Failure Cases** — the evaluator's feedback on what went wrong

Human feedback is solicited at the end of each run and stored under **Human Feedback**.

The Planner reads these historical sections and uses them to generate better plans on subsequent runs.

## Security Gateway

All tool execution goes through a parametric whitelist defined in `config/tools_config.yaml`:

- **Regex validation** on every parameter before execution
- **Blocked-pattern scanning** on the assembled command string
- **Per-tool timeouts** (default 10s for CLI, 120s for Python scripts)
- **Path-escape prevention** for Python scripts (must be inside `scripts/` or `skills/<skill>/`)
- **Automatic path normalisation**: forward slashes in path params are converted to backslashes

## Path Conventions

Both `safe_cli_executor` and `safe_py_runner` execute with **`cwd` = project root** (the directory containing `pyproject.toml`). All paths must be **relative to the project root**.

### CLI paths (`safe_cli_executor`)

Use **Windows-style backslashes** starting from the project root:

```
safe_cli_executor(tool_name="read_file", params={"path": "skills\\ects_skill\\tmp\\transcript.txt"})
safe_cli_executor(tool_name="list_files", params={"path": "skills\\ects_skill\\tmp"})
```

### File Writing

All file writing must go through Python scripts via `safe_py_runner` (CLI write tools have been removed):

```
# Write markdown (with stdin to avoid shell quoting issues)
safe_py_runner(
    script_name="scripts/write_file.py",
    args=["skills/ects_skill/tmp/ai_summary.md"],
    stdin_text=filled_markdown_content,
)

# Write JSON
safe_py_runner(script_name="scripts/write_json.py", args=["path/to/output.json", json_string])
```

### Python script paths (`safe_py_runner`)

Pass the **project-root-relative path** as `script_name`. Both forward slashes and backslashes are accepted. Allowed directories:

| Directory | Description |
|---|---|
| `scripts/` | Shared utility scripts (e.g. `format_check.py`, `write_file.py`) |
| `skills/<skill>/` | Skill-specific scripts (e.g. `skills/ects_skill/retrieve_transcript.py`) |

```
# Shared script
safe_py_runner(script_name="scripts/format_check.py", args=["skills/ects_skill/tmp/ai_summary.md"])

# Skill-specific script
safe_py_runner(script_name="skills/ects_skill/retrieve_transcript.py", args=["AAPL", "2024", "Q1"])

# Pass large content via stdin
safe_py_runner(script_name="scripts/write_file.py", args=["output.md"], stdin_text="# Content\n...")
```

### Writing a `skills.md`

In the **Instruction** and **Criteria** fields of each step, always use the full project-root-relative path:

```markdown
# CORRECT — includes the full path from project root
- **Instruction**: Read `skills\ects_skill\tmp\transcript.txt` ...
- **Criteria**: `skills\ects_skill\tmp\transcript.txt` exists and is non-empty.

# WRONG — missing the `skills\ects_skill\` prefix
- **Instruction**: Read `ects_skill\tmp\transcript.txt` ...
```

## Available CLI Sub-commands

The following sub-commands are available via `safe_cli_executor(tool_name=..., params={...})`:

| Sub-command | Params | Description |
|---|---|---|
| `list_files` | `path` | List files and directories |
| `read_file` | `path` | Read file contents |
| `make_directory` | `path` | Create a directory (including parents) |
| `tree` | `path` | Show directory tree |
| `copy_file` | `src`, `dst` | Copy a file |
| `move_file` | `src`, `dst` | Move or rename a file |
| `python_run` | `script` | Run a Python script (legacy; prefer `safe_py_runner`) |

**Note:** Text analysis operations (search, count, head, tail) are intentionally absent — the LLM handles these natively by reading the file and reasoning over its content.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | API key for the LLM provider |
| `OPENAI_API_BASE` | No | Custom base URL (e.g. for Azure or proxy endpoints) |
| `TRANSCRIPT_API_URL` | For ects_skill | Base URL for the transcript retrieval API |
| `TRANSCRIPT_API_TOKEN` | For ects_skill | Bearer token for transcript API authentication |

## Development

```bash
# Install with dev extras
pip install -e ".[dev]"

# Run tests
pytest
```
