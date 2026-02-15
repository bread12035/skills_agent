# Skill: Earnings Call Transcript Summarizer

Summarize an earnings call transcript by retrieving data, extracting key snippets, and producing a structured AI summary from a template.

## Goal

Retrieve an earnings call transcript from the API, analyze it to extract financial insights across five key topics, compose a structured summary using the provided template, and verify the output for accuracy and completeness.

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

## Available Resources

- **Script**: `scripts/retrieve_transcript.py` — Fetches raw transcript from the API.
  Takes args: `[company, year, quarter]`. Saves `raw_response.json`.
- **Script**: `scripts/parse_transcript.py` — Parses the raw API response.
  Reads `raw_response.json`, extracts `transcript.txt` and `metadata.json`.
- **Script**: `scripts/format_check.py` — Validates a filled summary against the
  template structure. Takes arg: path to the summary file. Exits 0 on success.
- **Script**: `scripts/write_json.py` — Writes JSON content to a specified file path.
  Takes args: `[file_path, json_content]`.
- **Script**: `scripts/write_md.py` — Writes markdown content to a specified file path.
  Takes args: `[file_path, md_content]`.
- **Template**: `skills\ects_skill\reference\template.md` — The summary template
  with placeholders to be filled.

## What Needs to Happen

1. **Retrieve the transcript**: Use the retrieval and parsing scripts to fetch
   the earnings call data from the API and extract the transcript text along
   with metadata (company, year, quarter).

2. **Load the transcript into context**: Read the extracted transcript file so
   its content is available for analysis.

3. **Extract key snippets**: Analyze the transcript and extract structured
   snippets for five topics: Financial Numbers, Financial Description, Guidance,
   Product Performance, and QA. Every number and fact must appear verbatim in
   the transcript — no inference, rounding, or fabrication.

4. **Persist the extracted snippets**: Write the structured snippets JSON to disk
   for reference and downstream use.

5. **Load the summary template**: Read the template file so its structure is
   available for the composition step.

6. **Compose the summary**: Using the extracted snippets and the template
   structure, fill every placeholder in the template with corresponding data.
   Preserve all headings, section order, and markdown formatting exactly.

7. **Write and validate the summary**: Write the composed summary to disk and
   run the format checker to validate structural compliance. If validation
   fails, fix and re-write.

8. **Final verification**: Read both the final summary and original transcript,
   then verify that key figures in the summary are traceable to the transcript.
   Flag any potentially hallucinated data.

## Success Cases

## Failure Cases
