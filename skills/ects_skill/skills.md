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
  Example: `skills/ects_skill/retrieve_transcript.py` or
  `skills\ects_skill\retrieve_transcript.py`

## Artifact Directory

All intermediate and final artifacts are saved to `skills\ects_skill\tmp\`:
- `raw_response.json` — Raw API response from retrieve_transcript.py
- `metadata.json` — Extracted company, calendar_year, calendar_quarter
- `transcript.txt` — Extracted transcript text
- `extracted_snippets.json` — Structured snippets per topic
- `ai_summary.md` — Final filled summary template

## Context Strategy

After each step completes, `step_memory` (L3 loop messages) is cleared. Subsequent steps reload any required context by reading the standardized files from `skills\ects_skill\tmp\`. This keeps each step's context window clean and deterministic.

## Steps

### Step 1 — Retrieve and parse transcript
- **Instruction**: Run `skills\ects_skill\retrieve_transcript.py` (via `safe_py_runner` with script_name `skills/ects_skill/retrieve_transcript.py`) with the company ticker, fiscal year, and fiscal quarter to fetch the raw transcript from the API. The script reads `TRANSCRIPT_API_URL` and `TRANSCRIPT_API_TOKEN` from environment variables automatically. Then run `skills\ects_skill\parse_transcript.py` (via `safe_py_runner` with script_name `skills/ects_skill/parse_transcript.py`) to extract the transcript text and metadata. Both scripts save outputs to `skills\ects_skill\tmp\`. Once `skills\ects_skill\tmp\transcript.txt` and `skills\ects_skill\tmp\metadata.json` exist and are valid, immediately stop executing tools and provide a plain-text summary to hand off to the Evaluator.
- **Criteria**: `skills\ects_skill\tmp\raw_response.json` exists and is valid JSON. `skills\ects_skill\tmp\transcript.txt` exists and is non-empty. `skills\ects_skill\tmp\metadata.json` exists and contains all fields (company, calendar_year, calendar_quarter) with no missing data.
- **Tools**: `safe_py_runner`

### Step 2 — Extract snippets of interest
- **Instruction**: Read the transcript from `skills\ects_skill\tmp\transcript.txt` and the metadata from `skills\ects_skill\tmp\metadata.json` (reload context from files since step_memory was cleared). Extract structured snippets for each of the following topics: **Financial Numbers**, **Financial Description**, **Guidance**, **Product Performance**, **QA**. Generate a structured JSON output and save it to `skills\ects_skill\tmp\extracted_snippets.json` using the `write_json` CLI tool or `safe_py_runner`. Once the JSON file is written and contains all five topics, immediately stop executing tools and provide a plain-text summary to hand off to the Evaluator.
- **Criteria**: `skills\ects_skill\tmp\extracted_snippets.json` exists and contains all five topics (Financial Numbers, Financial Description, Guidance, Product Performance, QA). Every number extracted must be verifiable in the original transcript — no hallucinated figures. Cross-check each numeric value against `skills\ects_skill\tmp\transcript.txt`.
- **Tools**: `safe_cli_executor`, `safe_py_runner`

### Step 3 — Fill template and format check
- **Instruction**: Read the extracted snippets from `skills\ects_skill\tmp\extracted_snippets.json` and the template from `skills\ects_skill\reference\template.md` (reload context from files since step_memory was cleared). Fill in the template blanks with the extracted snippets. Save the filled template to `skills\ects_skill\tmp\ai_summary.md` using the `write_md` CLI tool or `safe_py_runner`. Run `format_check.py` (via `safe_py_runner` with script_name `scripts/format_check.py`) to verify the filled template strictly follows the expected structure. Once `format_check.py` exits with code 0, immediately stop executing tools and provide a plain-text summary to hand off to the Evaluator.
- **Criteria**: `skills\ects_skill\tmp\ai_summary.md` exists and strictly follows the template structure without any alterations to headings or section order. `format_check.py` exits with code 0.
- **Tools**: `safe_py_runner`, `safe_cli_executor`

### Step 4 — Verify final output
- **Instruction**: Read `skills\ects_skill\tmp\ai_summary.md` to confirm it exists and is non-empty (reload context from file since step_memory was cleared). Verify the summary is well-formed and complete. Once verified, immediately stop executing tools and provide a plain-text summary to hand off to the Evaluator.
- **Criteria**: `skills\ects_skill\tmp\ai_summary.md` exists, is non-empty, and contains all expected sections from the template.
- **Tools**: `safe_cli_executor`
