# Skill: Earnings Call Transcript Summarizer

Summarize an earnings call transcript by retrieving data, extracting key snippets, and producing a structured AI summary from a template.

## Environment

The following environment variables must be set before execution:
- `TRANSCRIPT_API_URL` — Base URL for the transcript retrieval API.
- `TRANSCRIPT_API_TOKEN` — Bearer token for API authentication.

These are injected automatically into `safe_py_runner` from the host environment.

## Path Convention

All paths in this skill are **relative to the project root** (the repository root
where `pyproject.toml` lives). Both `safe_cli_executor` and `safe_py_runner`
execute commands with `cwd = PROJECT_ROOT`, so every path must start from there.

- **CLI paths** (for `safe_cli_executor`): use Windows-style backslashes.
  Example: `skills\ects_skill\tmp\transcript.txt`
- **Python script paths** (for `safe_py_runner`): use forward slashes or
  backslashes — both are accepted.
  Example: `scripts/retrieve_transcript.py` or `scripts\retrieve_transcript.py`

## Artifact Directory

All intermediate and final artifacts are saved to `skills\ects_skill\tmp\`:
- `raw_response.json` — Raw API response from retrieve_transcript.py
- `metadata.json` — Extracted company, calendar_year, calendar_quarter
- `transcript.txt` — Extracted transcript text
- `extracted_snippets.json` — Structured snippets per topic
- `ai_summary.md` — Final filled summary template

## Context Strategy

The agent uses a three-layer memory system:

- **L1 (Global)**: Project context from claude.md, injected into all prompts
- **L2 (Skill Memory)**: Cross-step data passing. The Evaluator extracts data from
  completed steps and stores it in skill_memory for subsequent steps to use. This
  minimizes redundant file I/O.
- **L3 (Loop Context)**: Optimizer-Evaluator messages. Retains last 3 conversation
  rounds between steps to maintain recent execution context while preventing topic drift.

Files written to disk (skills\ects_skill\tmp\) serve as persistent backups and
verification targets, but the primary data flow between steps is via skill_memory.

## Steps

### Step 1 — Retrieve and parse transcript
- **Instruction**: Run `scripts\retrieve_transcript.py` (via `safe_py_runner` with script_name `scripts/retrieve_transcript.py`) with the company ticker, fiscal year, and fiscal quarter to fetch the raw transcript from the API. The script reads `TRANSCRIPT_API_URL` and `TRANSCRIPT_API_TOKEN` from environment variables automatically. Then run `scripts\parse_transcript.py` (via `safe_py_runner` with script_name `scripts/parse_transcript.py`) to extract the transcript text and metadata. Both scripts save outputs to `skills\ects_skill\tmp\`. Once `skills\ects_skill\tmp\transcript.txt` and `skills\ects_skill\tmp\metadata.json` exist and are valid, immediately stop executing tools and provide a plain-text summary to hand off to the Evaluator.
- **Criteria**: `skills\ects_skill\tmp\raw_response.json` exists and is valid JSON. `skills\ects_skill\tmp\transcript.txt` exists and is non-empty. `skills\ects_skill\tmp\metadata.json` exists and contains all fields (company, calendar_year, calendar_quarter) with no missing data. **The Evaluator must extract metadata fields (company, calendar_year, calendar_quarter) and the transcript file path into key_outputs for use by subsequent steps.**
- **Tools**: `safe_py_runner`

### Step 2 — Extract snippets of interest
- **Instruction**: This step requires YOU (the LLM) to do the text analysis — do NOT delegate extraction to CLI tools or scripts. Follow these phases strictly:
  1. **Read phase (tools)**: Check skill_memory for metadata (company, calendar_year, calendar_quarter) from Step 1. Use `safe_cli_executor` `read_file` to load `skills\ects_skill\tmp\transcript.txt` (path should be in skill_memory). Load `skills\ects_skill\tmp\metadata.json` only if metadata is not already in skill_memory.
  2. **Analyse phase (LLM reasoning — NO tool calls)**: With the transcript text now in your context, use your own language comprehension to identify and extract structured snippets for each of these five topics: **Financial Numbers**, **Financial Description**, **Guidance**, **Product Performance**, **QA**. For each topic, locate the relevant passages, pull exact quotes and figures directly from the transcript text, and compose the structured JSON object entirely within your reasoning. Every number and fact you include MUST appear verbatim in the transcript — do not infer, round, or fabricate any figures.
  3. **Write phase (tools)**: Once you have composed the complete JSON in your reasoning, make a SINGLE call to `safe_cli_executor` with `write_json` to save the full JSON to `skills\ects_skill\tmp\extracted_snippets.json`. Do NOT write the file incrementally or topic-by-topic — write the entire JSON in one call.
  4. **Stop**: Immediately stop executing tools and provide a plain-text summary to hand off to the Evaluator.
- **Criteria**: `skills\ects_skill\tmp\extracted_snippets.json` exists and contains all five topics (Financial Numbers, Financial Description, Guidance, Product Performance, QA). Every number extracted must be verifiable in the original transcript — no hallucinated figures. Cross-check each numeric value against `skills\ects_skill\tmp\transcript.txt`. **The Evaluator must extract the snippets file path and a count of topics into key_outputs for use by subsequent steps.**
- **Tools**: `safe_cli_executor` (read_file for input, write_json for output)

### Step 3 — Fill template and format check
- **Instruction**: This step requires YOU (the LLM) to do the text composition — do NOT delegate template filling to CLI tools or scripts. Follow these phases strictly:
  1. **Read phase (tools)**: Use `safe_cli_executor` `read_file` to load both `skills\ects_skill\tmp\extracted_snippets.json` and `skills\ects_skill\reference\template.md` into your context. These are the ONLY tool calls needed for input.
  2. **Compose phase (LLM reasoning — NO tool calls)**: With the snippets and template now in your context, use your own language ability to fill in every `[placeholder]` in the template with the corresponding data from the snippets JSON. Preserve all headings, section order, markdown formatting, and bold markers exactly as they appear in the template. Replace ONLY the bracketed placeholders — do not add, remove, or reorder any sections. Compose the entire filled markdown document within your reasoning before writing it.
  3. **Write phase (tools)**: Make a SINGLE call to `safe_cli_executor` with `write_md` to save the complete filled template to `skills\ects_skill\tmp\ai_summary.md`. Write the entire document in one call.
  4. **Verify phase (tools)**: Run `format_check.py` (via `safe_py_runner` with script_name `scripts/format_check.py` and args `["skills/ects_skill/tmp/ai_summary.md"]`) to validate structure. If it fails, read the error, fix the markdown in your reasoning, and re-write with `write_md` — do NOT attempt to patch the file with CLI text tools.
  5. **Stop**: Once `format_check.py` exits with code 0, immediately stop executing tools and provide a plain-text summary to hand off to the Evaluator.
- **Criteria**: `skills\ects_skill\tmp\ai_summary.md` exists and strictly follows the template structure without any alterations to headings or section order. `format_check.py` exits with code 0. **The Evaluator must extract the summary file path and format check result into key_outputs for use by the verification step.**
- **Tools**: `safe_cli_executor` (read_file for input, write_md for output), `safe_py_runner` (format_check.py for validation)

### Step 4 — Verify final output
- **Instruction**: This is a verification step combining tool I/O with LLM-native analysis.
  1. **Read phase (tools)**: Use `safe_cli_executor` `read_file` to load `skills\ects_skill\tmp\ai_summary.md` and `skills\ects_skill\tmp\transcript.txt` into your context.
  2. **Verify phase (LLM reasoning — NO tool calls)**: With both documents in your context, confirm: (a) the summary is non-empty and contains all expected sections from the template (Financial Highlights, Briefing of Key Message, Key Message, Key insights from Q&A session); (b) spot-check that key figures and facts in the summary actually appear in the transcript — flag any that look fabricated or cannot be found.
  3. **Stop**: Immediately provide a plain-text summary of your verification findings to hand off to the Evaluator.
- **Criteria**: `skills\ects_skill\tmp\ai_summary.md` exists, is non-empty, and contains all expected sections from the template. No hallucinated figures — key numbers must be traceable to `skills\ects_skill\tmp\transcript.txt`. **The Evaluator must extract the final verification status and any flagged issues into key_outputs.**
- **Tools**: `safe_cli_executor` (read_file only)
