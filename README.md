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

## Development

```bash
# Install with dev extras
pip install -e ".[dev]"

# Run tests
pytest
```
