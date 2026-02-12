# Skill: Greet User

A simple skill that greets the user by name and logs the interaction.

## Path Format

All file paths in this skill use Windows-style backslashes (`\`).

## Steps

### Step 1 — Generate greeting
- **Instruction**: Run the `greet.py` script with the user's name to produce a personalised greeting.
- **Criteria**: The script exits with code 0 and prints a greeting containing the user's name.
- **Tools**: `safe_py_runner`

### Step 2 — Confirm output
- **Instruction**: Read the generated `hello_skill\output.txt` file and verify it contains the greeting.
- **Criteria**: `hello_skill\output.txt` exists and its content includes "Hello".
- **Tools**: `safe_cli_executor`
