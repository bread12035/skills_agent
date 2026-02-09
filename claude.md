## Skills Agent — Optimizer-Evaluator Architecture

This project implements a **two-layer loop agent** using LangGraph. A Skill Parser decomposes
user instructions into steps, then an inner Optimizer-Evaluator loop executes and verifies
each step before advancing.

### Graph Flow

```
User Input
  → Skill Parser (decompose into steps)
  → [for each step]:
      Prepare Step Context (build prompts, clear L3 messages)
        → Optimizer Agent ←──── tool calls ────→ Tool Executor
              │ (text response — done)
              ▼
        → Evaluator Agent ←── verification tool calls (read-only + py scripts) ──→ internal tool loop
              │
         PASS → Commit Step → next step (or END)
         FAIL → retry (up to max_retries) → Optimizer again
         FAIL (exhausted) → Human Intervention → retry
```

### Agent Roles

| Agent | Tools | Purpose |
|-------|-------|---------|
| **Optimizer** | `ALL_TOOLS` (safe_cli_executor, safe_py_runner) | Execute the step instruction using available tools |
| **Evaluator** | `EVALUATOR_TOOLS` (safe_cli_executor, safe_py_runner) | Verify step completion; can run Python verification scripts from `scripts/` |

The Evaluator runs an **internal tool execution loop** — it can make up to 5 tool-call rounds
to inspect filesystem state or run verification scripts before producing its final PASS/FAIL verdict.

### Three-Layer Memory

| Layer | Name | Scope | Lifecycle |
|-------|------|-------|-----------|
| **L1** | Global Context | Project-wide (`claude.md`) | Permanent, injected into every prompt |
| **L2** | Skill Memory | Cross-step KV store | Append-only, committed after each PASS |
| **L3** | Loop Context | Optimizer↔Evaluator messages | Cleared at start of each step |

### Key Files

- `src/skills_agent/graph.py` — LangGraph assembly (nodes, edges, routing)
- `src/skills_agent/nodes.py` — Node implementations (parser, optimizer, evaluator, commit)
- `src/skills_agent/tools.py` — Security Gateway, tool definitions, tool registries
- `src/skills_agent/prompts.py` — System prompts for each agent
- `src/skills_agent/models.py` — Pydantic schemas (AgentState, SkillPlan, EvaluationOutput)
- `src/skills_agent/memory.py` — Three-layer memory helpers
- `config/tools_config.yaml` — CLI whitelist and blocked patterns

### Security Gateway

All tool execution goes through a parametric whitelist (`config/tools_config.yaml`):
1. Regex validation on every parameter
2. shlex quoting for shell safety
3. Blocked-pattern scanning on assembled commands
4. Per-tool configurable timeouts

`safe_py_runner` adds path-escape prevention, `.py`-only restriction, and argument validation
for scripts in the `scripts/` directory.
