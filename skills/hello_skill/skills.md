# Skill: Greet User

A simple skill that greets the user by name and logs the interaction.

## Path Format

All file paths in this skill use Windows-style backslashes (`\`).

## Task Decomposition Rules

Each step is physically bounded by one of the following constraints:
- **Tool-bound step**: at most **5 tool calls** total.
- **Text-processing step**: **0 tool calls** — performed entirely by LLM reasoning.

## Completion Protocol

- As soon as the step's success criteria are met, the Optimizer MUST **stop all tool calls** and emit a plain-text summary to hand off to the Evaluator.
- The Evaluator verifies criteria, extracts data needed by later steps into `key_outputs`, and commits them to L2 skill memory.

## Steps

### Step 1 — Generate greeting (Tool-bound: max 1 tool call)

- **Task type**: Tool-bound I/O
- **Tool sequence**:
  1. `safe_py_runner` — run `skills/hello_skill/greet.py` with args `[<user_name>]`
- **Instruction**: Run the `greet.py` script via `safe_py_runner` with the user's name as the first argument. The script produces a greeting and writes it to `skills\hello_skill\output.txt`. After the script completes, **stop immediately** — do NOT read the output file; the Evaluator will inspect it.
- **Criteria**: The script exits with code 0 and prints a greeting containing the user's name.
- **Evaluator key_outputs** (required by Step 2): `greeting_file=skills\hello_skill\output.txt`, `user_name` (the name used).
- **Evaluator data-passing responsibility**: Confirm the script exited 0, extract the file path and user name, store them in `key_outputs` so Step 2 can locate the output without guessing.

### Step 2 — Confirm output (Tool-bound: max 1 tool call)

- **Task type**: Tool-bound I/O (read only)
- **Tool sequence**:
  1. `safe_cli_executor(read_file)` — read `skills\hello_skill\output.txt` (path from L2 `greeting_file`)
- **Instruction**: Read the path from L2 skill memory (`greeting_file`). Use `safe_cli_executor` `read_file` to load the file content. Verify it contains "Hello" and the user's name (from L2 `user_name`). After reading, **stop immediately** and provide a plain-text summary of the file content.
- **Criteria**: `skills\hello_skill\output.txt` exists and its content includes "Hello".
- **Evaluator key_outputs** (final step): `output_verified=true`, `greeting_content` (the actual greeting text).
- **Evaluator data-passing responsibility**: This is the final step. Read the file, confirm it contains "Hello", store the verification result and greeting content in key_outputs.
