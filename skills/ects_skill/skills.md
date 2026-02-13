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

- **L1 (Global)**: Project context from claude.md, injected into all prompts.
- **L2 (Skill Memory)**: Cross-step data passing. The Evaluator extracts data from
  completed steps into key_outputs, which are committed to skill_memory on PASS.
  Each step's criteria below defines exactly which fields the Evaluator must pass
  so the next step can operate without redundant file reads.
- **L3 (Loop Context)**: Optimizer-Evaluator messages. Fully cleared at the start
  of each new step. All cross-step data flows exclusively through L2 skill memory.

Files written to disk (`skills\ects_skill\tmp\`) serve as persistent backups and
verification targets. The primary data flow between steps is via L2 skill memory.

## Task Decomposition Rules

Each step is physically bounded by one of the following constraints:
- **Tool-bound step**: at most **5 tool calls** total (read + write + verify combined).
- **Text-processing step**: **0 tool calls** — the LLM performs extraction, summarization, rewriting, or composition entirely within its reasoning. Output is held in LLM context and passed to the next step via the Evaluator's key_outputs → L2 memory.

If a logical task requires more than 5 tool calls, it MUST be split into two or more steps. If a task mixes I/O and text processing, the I/O phase and text-processing phase are separate steps.

## Completion Protocol

- As soon as the step's success criteria are satisfied, the Optimizer MUST immediately **stop all tool calls** and emit a plain-text summary to hand off to the Evaluator.
- The Evaluator verifies criteria, then extracts **all data required by subsequent steps** into `key_outputs`. These key_outputs are committed to L2 skill memory and become the sole data bridge to the next step.
- No step should rely on re-reading files that a previous Evaluator already extracted into L2 memory.

## Steps

### Step 1 — Retrieve raw transcript (Tool-bound: max 2 tool calls)

- **Task type**: Tool-bound I/O
- **Tool sequence**:
  1. `safe_py_runner` — run `scripts/retrieve_transcript.py` with args `[company, year, quarter]`
  2. `safe_py_runner` — run `scripts/parse_transcript.py` (no args; reads `raw_response.json`)
- **Instruction**: Run the two scripts in order. The first fetches the raw transcript from the API and saves `skills\ects_skill\tmp\raw_response.json`. The second extracts `skills\ects_skill\tmp\transcript.txt` and `skills\ects_skill\tmp\metadata.json`. Once both scripts complete successfully, **stop immediately** — do NOT read the output files; the Evaluator will inspect them.
- **Criteria**: `skills\ects_skill\tmp\raw_response.json` exists and is valid JSON. `skills\ects_skill\tmp\transcript.txt` exists and is non-empty. `skills\ects_skill\tmp\metadata.json` exists and contains fields: company, calendar_year, calendar_quarter.
- **Evaluator key_outputs** (required by Step 2): `company`, `calendar_year`, `calendar_quarter` (extracted from metadata.json), `transcript_path=skills\ects_skill\tmp\transcript.txt`.
- **Evaluator data-passing responsibility**: Read `metadata.json` via `safe_cli_executor(read_file)`, extract the three metadata fields plus the transcript path, and store them all in `key_outputs` so Step 2 can consume them from L2 memory without re-reading metadata.json.

### Step 2 — Load transcript into context (Tool-bound: max 1 tool call)

- **Task type**: Tool-bound I/O (read only)
- **Tool sequence**:
  1. `safe_cli_executor(read_file)` — read `skills\ects_skill\tmp\transcript.txt` (path from L2 `transcript_path`)
- **Instruction**: Read the transcript file path from L2 skill memory (`transcript_path`). Use `safe_cli_executor` `read_file` to load the full transcript into context. Do NOT re-read `metadata.json` — company, calendar_year, calendar_quarter are already in L2 memory. After reading the transcript, **stop immediately** and provide the full transcript text in your plain-text summary to hand off to the Evaluator.
- **Criteria**: The transcript text has been loaded into context and is non-empty.
- **Evaluator key_outputs** (required by Step 3): `transcript_text` (the full transcript content, stored in L2 memory so Step 3 can process it without file I/O).
- **Evaluator data-passing responsibility**: The Evaluator receives the transcript text from the Optimizer's summary. Store the full transcript text in `key_outputs["transcript_text"]` for the next step. If the text exceeds L2 capacity, store a confirmation flag `transcript_loaded=true` and the path so Step 3 can re-read if needed.

### Step 3 — Extract snippets of interest (Text-processing: 0 tool calls)

- **Task type**: Text-processing (NO tool calls allowed)
- **Instruction**: This is a pure text-processing step. You MUST NOT call any tools. Using the transcript text available from L2 memory (or from the Evaluator's feedback in L3 context), plus company/year/quarter from L2, perform the following analysis entirely within your LLM reasoning:
  1. Identify and extract structured snippets for each of these five topics: **Financial Numbers**, **Financial Description**, **Guidance**, **Product Performance**, **QA**.
  2. For each topic, locate relevant passages, pull exact quotes and figures directly from the transcript text.
  3. Every number and fact MUST appear verbatim in the transcript — do not infer, round, or fabricate any figures.
  4. Compose the complete JSON object with all five topics in your reasoning.
  5. Output the complete JSON in your plain-text summary to hand off to the Evaluator.
- **Criteria**: The output contains a valid JSON structure with all five topics (Financial Numbers, Financial Description, Guidance, Product Performance, QA). Every number extracted is verifiable in the original transcript.
- **Evaluator key_outputs** (required by Step 4): `extracted_snippets_json` (the full JSON string), `topics_count` (number of topics).
- **Evaluator data-passing responsibility**: Parse the JSON from the Optimizer's output, validate it has 5 topics, verify at least a sample of numbers against the transcript text in L2 memory, then store the full JSON string in `key_outputs["extracted_snippets_json"]` for Step 4.

### Step 4 — Write snippets to disk (Tool-bound: max 1 tool call)

- **Task type**: Tool-bound I/O (write only)
- **Tool sequence**:
  1. `safe_cli_executor(write_json)` — write the extracted snippets JSON to `skills\ects_skill\tmp\extracted_snippets.json`
- **Instruction**: Retrieve the `extracted_snippets_json` from L2 skill memory. Use a SINGLE `safe_cli_executor` `write_json` call to write the complete JSON to `skills\ects_skill\tmp\extracted_snippets.json`. Then **stop immediately**.
- **Criteria**: `skills\ects_skill\tmp\extracted_snippets.json` exists and contains all five topics.
- **Evaluator key_outputs** (required by Step 5): `extracted_snippets_path=skills\ects_skill\tmp\extracted_snippets.json`.
- **Evaluator data-passing responsibility**: Verify the file exists and contains valid JSON with 5 topics. Store the path in key_outputs.

### Step 5 — Compose filled summary (Text-processing: 0 tool calls)

- **Task type**: Text-processing (NO tool calls allowed)
- **Instruction**: This is a pure text-processing step. You MUST NOT call any tools. Using the `extracted_snippets_json` from L2 memory and the template structure (you will read the template in the next step; for now, use the known template structure from the reference), compose the filled markdown document entirely within your LLM reasoning:
  1. Fill every `[placeholder]` in the template with corresponding data from the snippets JSON.
  2. Preserve all headings, section order, markdown formatting, and bold markers exactly as they appear in the template.
  3. Replace ONLY the bracketed placeholders — do not add, remove, or reorder sections.
  4. Output the complete filled markdown in your plain-text summary.
- **NOTE**: If the template structure is not in L2 memory, this step should be preceded by a read step. The parser should check and adjust accordingly.
- **Criteria**: The output contains a complete markdown document following the template structure with all placeholders filled.
- **Evaluator key_outputs** (required by Step 6): `filled_summary_md` (the complete markdown content).
- **Evaluator data-passing responsibility**: Validate the markdown has all required sections, store full content in `key_outputs["filled_summary_md"]`.

### Step 5a — Read template (Tool-bound: max 1 tool call)

- **Task type**: Tool-bound I/O (read only)
- **Tool sequence**:
  1. `safe_cli_executor(read_file)` — read `skills\ects_skill\reference\template.md`
- **Instruction**: Read the template file into context. **Stop immediately** and pass the template content to the Evaluator.
- **Criteria**: Template content is loaded and non-empty.
- **Evaluator key_outputs**: `template_content` (full template markdown).
- **Evaluator data-passing responsibility**: Store template content in L2 memory for the composition step.

### Step 6 — Write summary and format check (Tool-bound: max 3 tool calls)

- **Task type**: Tool-bound I/O
- **Tool sequence**:
  1. `safe_py_runner` — run `scripts/write_file.py` with args `["skills/ects_skill/tmp/ai_summary.md"]` and `stdin_text=<filled_summary_md>` to write the filled markdown from L2 memory to disk
  2. `safe_py_runner` — run `scripts/format_check.py` with args `["skills/ects_skill/tmp/ai_summary.md"]`
  3. (Only if format_check fails) `safe_py_runner` — re-run `scripts/write_file.py` with corrected markdown via `stdin_text`
- **Instruction**: Retrieve `filled_summary_md` from L2 skill memory. Write it to disk using `safe_py_runner` with `scripts/write_file.py` (pass the markdown content via `stdin_text` to avoid shell quoting issues), then run `format_check.py` to validate structure. If format_check fails, fix the markdown in your reasoning (NOT with CLI text-patching tools) and re-write. Once format_check exits 0, **stop immediately**.
- **Criteria**: `skills\ects_skill\tmp\ai_summary.md` exists and follows the template structure. `format_check.py` exits with code 0.
- **Evaluator key_outputs** (required by Step 7): `summary_path=skills\ects_skill\tmp\ai_summary.md`, `format_check_exit_code=0`.
- **Evaluator data-passing responsibility**: Verify the file exists, run format_check if not already run, store path and exit code in key_outputs.

### Step 7 — Verify final output (Tool-bound: max 2 tool calls + text analysis)

- **Task type**: Mixed (tool reads + LLM verification)
- **Tool sequence**:
  1. `safe_cli_executor(read_file)` — read `skills\ects_skill\tmp\ai_summary.md`
  2. `safe_cli_executor(read_file)` — read `skills\ects_skill\tmp\transcript.txt`
- **Instruction**: Read both the final summary and the original transcript into context. Then, using ONLY your LLM reasoning (NO further tool calls):
  1. Confirm the summary is non-empty and contains all expected sections (Financial Highlights, Briefing of Key Message, Key Message, Key insights from Q&A session).
  2. Spot-check that key figures and facts in the summary actually appear in the transcript.
  3. Flag any figures that look fabricated or cannot be found.
  After verification, **stop immediately** and provide your findings as plain text.
- **Criteria**: `skills\ects_skill\tmp\ai_summary.md` exists, is non-empty, and contains all expected template sections. No hallucinated figures — key numbers must be traceable to `skills\ects_skill\tmp\transcript.txt`.
- **Evaluator key_outputs** (final step): `verification_status` (pass/fail), `flagged_issues` (comma-separated list or "none").
- **Evaluator data-passing responsibility**: This is the final step. Store the verification status and any flagged issues in key_outputs for the final report.
