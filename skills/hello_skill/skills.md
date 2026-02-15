# Skill: Greet User

A simple skill that greets the user by name and logs the interaction.

## Goal

Generate a personalized greeting for the user and confirm the output was written correctly.

## Environment

No special environment variables required.

## Path Convention

All paths in this skill are **relative to the project root** (the repository root
where `pyproject.toml` lives). Both `safe_cli_executor` and `safe_py_runner`
execute commands with `cwd = PROJECT_ROOT`, so every path must start from there.

- **CLI paths** (for `safe_cli_executor`): use Windows-style backslashes.
  Example: `skills\hello_skill\output.txt`
- **Python script paths** (for `safe_py_runner`): use forward slashes or
  backslashes — both are accepted.

## Artifact Directory

All artifacts are saved to `skills\hello_skill\`:
- `output.txt` — Generated greeting output

## Available Resources

- **Script**: `skills/hello_skill/greet.py` — Takes a username as argument,
  generates a greeting, and writes it to `skills\hello_skill\output.txt`.

## What Needs to Happen

1. **Generate the greeting**: Run the `greet.py` script with the user's name
   to produce the greeting file. The script handles both greeting generation
   and file writing.

2. **Verify the output**: Read the generated output file and confirm it contains
   a valid greeting with the word "Hello" and the user's name.

## Success Cases

## Failure Cases
