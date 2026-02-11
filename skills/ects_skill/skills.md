# Skill: Earnings Call Transcript Summarizer

Summarize an earnings call transcript by retrieving data, extracting key snippets, and producing a structured AI summary from a template.

## Steps

### Step 1 — Retrieve and parse transcript
- **Instruction**: Run `retrieve_transcript.py` with the company ticker, fiscal year, and fiscal quarter to fetch the raw transcript. Then run `parse_transcript.py` to extract the transcript text and metadata. Save the transcript to `ects_skill/tmp/transcript.txt`.
- **Criteria**: `ects_skill/tmp/transcript.txt` exists and is non-empty. All metadata fields (company, calendar_year, calendar_quarter) are present with no missing data.
- **Tools**: `safe_py_runner`

### Step 2 — Extract snippets of interest
- **Instruction**: Read the transcript from `ects_skill/tmp/transcript.txt`. Extract structured snippets for each of the following topics: **Financial Numbers**, **Financial Description**, **Guidance**, **Product Performance**, **QA**. Generate a structured JSON output and save it to `ects_skill/tmp/extracted_snippets.json`.
- **Criteria**: `ects_skill/tmp/extracted_snippets.json` exists and contains all five topics. Every number extracted must be verifiable in the original transcript — no hallucinated figures. Cross-check each numeric value against `ects_skill/tmp/transcript.txt`.
- **Tools**: `safe_cli_executor`

### Step 3 — Fill template and format check
- **Instruction**: Load the template from `ects_skill/reference/template.md`. Fill in the blanks with the extracted snippets from `ects_skill/tmp/extracted_snippets.json`. Run `format_check.py` to verify the filled template strictly follows the expected structure.
- **Criteria**: The output strictly follows the template structure without any alterations to headings or section order. `format_check.py` exits with code 0.
- **Tools**: `safe_py_runner`, `safe_cli_executor`

### Step 4 — Output AI summary
- **Instruction**: Write the final filled template as `ects_skill/tmp/ai_summary.md`.
- **Criteria**: `ects_skill/tmp/ai_summary.md` exists and is non-empty.
- **Tools**: `safe_cli_executor`
