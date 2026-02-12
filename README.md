# Skills Agent

An **Evaluator-Optimizer loop** powered by [LangGraph](https://github.com/langchain-ai/langgraph).
A Skill Parser decomposes user instructions into steps, then an inner Optimizer-Evaluator loop executes and verifies each step before advancing.

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
# Edit .env and set your OPENAI_API_KEY
```

### 3. Run

Pass a **skill directory** (containing a `skills.md`) or a direct path to a markdown file:

```bash
# Run the bundled example skill
skills-agent skills/hello_skill

# Or point to any markdown file
skills-agent path/to/your/skills.md
```

The agent will parse the skill into an execution plan, ask for your approval, then run each step through the Optimizer-Evaluator loop.

## Project Layout

```
skills_agent/
├── src/skills_agent/    # Package source (graph, nodes, tools, models, prompts, memory)
├── skills/              # Skill definitions (each subfolder has a skills.md + scripts)
│   └── hello_skill/
│       ├── skills.md    # Step-by-step instructions for the agent
│       └── greet.py     # Script used by the skill
├── scripts/             # Shared scripts available to the Evaluator via safe_py_runner
├── config/
│   └── tools_config.yaml  # CLI whitelist and blocked patterns
├── tests/               # Pytest suite
├── pyproject.toml       # Build config & entry point
├── requirements.txt     # Pinned dependencies
└── .env.example         # Environment variable template
```

## Path Conventions for `skills.md`

Both `safe_cli_executor` and `safe_py_runner` execute commands with
**`cwd` = project root** (the directory containing `pyproject.toml`).
All paths in skill definitions must be **relative to the project root**.

### CLI paths (`safe_cli_executor`)

Use **Windows-style backslashes** starting from the project root:

```
# Reading a file
safe_cli_executor(tool_name="read_file", params={"path": "skills\\ects_skill\\tmp\\transcript.txt"})

# Listing a directory
safe_cli_executor(tool_name="list_files", params={"path": "skills\\ects_skill\\tmp"})

# Writing a file
safe_cli_executor(tool_name="write_json", params={"path": "skills\\ects_skill\\tmp\\output.json", "content": "..."})
```

### Python script paths (`safe_py_runner`)

Pass the **project-root-relative path** as `script_name`. Both forward slashes
and backslashes are accepted. Allowed directories:

| Directory | Description |
|---|---|
| `scripts/` | Shared utility scripts (e.g. `format_check.py`) |
| `skills/<skill>/` | Skill-specific scripts (e.g. `retrieve_transcript.py`) |

```
# Shared script
safe_py_runner(script_name="scripts/format_check.py")

# Skill-specific script
safe_py_runner(script_name="skills/ects_skill/retrieve_transcript.py", args=["AAPL", "2024", "Q1"])
```

### Writing a `skills.md`

In the **Instruction** and **Criteria** fields of each step, always use
the full project-root-relative path:

```markdown
# CORRECT — includes the full path from project root
- **Instruction**: Read `skills\ects_skill\tmp\transcript.txt` ...
- **Criteria**: `skills\ects_skill\tmp\transcript.txt` exists and is non-empty.

# WRONG — missing the `skills\ects_skill\` prefix
- **Instruction**: Read `ects_skill\tmp\transcript.txt` ...
```

## Development

```bash
# Install with dev extras
pip install -e ".[dev]"

# Run tests
pytest
```
