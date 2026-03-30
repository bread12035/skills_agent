# Skill: Pre-Earnings Financial Data Report

Extract financial data from a company's public investor relations website before the earnings press conference and produce a structured report.

## Goal

Given a company ticker, fiscal year, and fiscal quarter, retrieve financial data from the company's investor relations URLs for the target quarter, previous quarter, and year-ago quarter. Fill a company-specific report template with the extracted data and output a complete pre-earnings report.

## Environment

The following environment variables must be set before execution:
- `ANTHROPIC_API_KEY` — API key for Claude web search.

These are injected automatically into `safe_py_runner` from the host environment.

## Path Convention

All paths in this skill are **relative to the project root** (the repository root
where `pyproject.toml` lives). Both `safe_cli_executor` and `safe_py_runner`
execute commands with `cwd = PROJECT_ROOT`, so every path must start from there.

- **CLI paths** (for `safe_cli_executor`): use Windows-style backslashes.
  Example: `skills\preearnings\tmp\report.md`
- **Python script paths** (for `safe_py_runner`): use forward slashes or
  backslashes — both are accepted.
  Example: `scripts/web_search.py`

## Artifact Directory

All intermediate and final artifacts are saved to `skills\preearnings\tmp\`:
- `urls.json` — Resolved URLs for the target quarter, previous quarter, and year-ago quarter
- `raw_data.json` — Raw financial data retrieved from each URL
- `report.md` — Final filled report

## Inputs

| Parameter        | Example   | Description                          |
|------------------|-----------|--------------------------------------|
| `company`        | `AAPL`    | Company ticker symbol                |
| `fiscal_year`    | `2026`    | Fiscal year                          |
| `fiscal_quarter` | `Q1`      | Fiscal quarter (Q1, Q2, Q3, Q4)     |

## Available Resources

- **Company URLs**: `skills/preearnings/reference/company_urls/{company}.json`
  Contains the URL patterns for the company's financial data pages. URLs follow
  a fiscal-year/quarter naming convention so that adjacent quarters can be derived.

  Example (`AAPL.json`):
  ```json
  {
    "company": "AAPL",
    "base_url": "https://investor.apple.com",
    "quarterly_report_url_pattern": "https://investor.apple.com/sec-filings/quarterly-reports/FY{fiscal_year}Q{quarter_number}",
    "press_release_url_pattern": "https://investor.apple.com/press-releases/press-release-details/FY{fiscal_year}Q{quarter_number}",
    "financial_statements": {
      "income_statement": "https://investor.apple.com/financial-data/income-statement",
      "balance_sheet": "https://investor.apple.com/financial-data/balance-sheet",
      "cash_flow": "https://investor.apple.com/financial-data/cash-flow"
    },
    "notes": "Apple fiscal year ends in September. FY Q1 = Oct-Dec, Q2 = Jan-Mar, Q3 = Apr-Jun, Q4 = Jul-Sep."
  }
  ```

- **Report Template**: `skills/preearnings/reference/company_template/{company}.md`
  A markdown template with placeholders to be filled with financial data.

- **Script**: `scripts/web_search.py` — Performs web search using Claude API.
  Takes args: `[query]`. Returns search-informed response.

- **Script**: `scripts/write_file.py` — Writes content from stdin to a file.
  Takes args: `[file_path]`. Reads content from `stdin_text`.

- **Script**: `scripts/write_json.py` — Writes JSON content to a file.
  Takes args: `[file_path, json_string]`.

- **Script**: `scripts/write_md.py` — Writes markdown content to a file.
  Takes args: `[file_path, md_content]`.

## What Needs to Happen

1. **Resolve URLs for the three target periods**: Read the company URL config
   from `skills/preearnings/reference/company_urls/{company}.json`. Using the
   input `fiscal_year` and `fiscal_quarter`, derive the URLs for:
   - **Current quarter** (e.g., AAPL FY2026 Q1 = Oct–Dec 2025)
   - **Previous quarter** (e.g., AAPL FY2025 Q4 = Jul–Sep 2025)
   - **Year-ago quarter** (e.g., AAPL FY2025 Q1 = Oct–Dec 2024)

   Apply the URL patterns by substituting `{fiscal_year}` and `{quarter_number}`.
   Persist the resolved URLs to `skills/preearnings/tmp/urls.json`.

2. **Fetch financial data from each URL**: For each resolved URL, call
   `scripts/web_search.py` with a prompt that instructs Claude to visit the
   specific URL and extract the relevant financial data (revenue, gross margin,
   operating income, net income, EPS, segment breakdowns, etc.).

   Store all retrieved data into L2 memory for cross-step access. Also persist
   to `skills/preearnings/tmp/raw_data.json`.

3. **Load the report template**: Read the company report template from
   `skills/preearnings/reference/company_template/{company}.md`.

4. **Compose the report**: Using the extracted financial data and the template,
   fill every placeholder in the template with the corresponding data from the
   three periods. Preserve all headings, section order, and markdown formatting.
   **Placeholder rule**: If a placeholder value cannot be found in the retrieved
   data, write `[placeholder]` for that value. After filling all placeholders,
   delete every line that still contains `[placeholder]`.

5. **Write the report**: Write the composed report to
   `skills/preearnings/tmp/report.md` using `scripts/write_file.py` via
   `safe_py_runner` with `stdin_text`.

6. **Final verification**: Read the final report and verify that key financial
   figures are consistent across sections and traceable to the retrieved data.
   Flag any potentially missing or inconsistent data.

## Example Walkthrough (AAPL, Q1, 2026)

Given inputs: `company=AAPL`, `fiscal_year=2026`, `fiscal_quarter=Q1`

1. Read `skills/preearnings/reference/company_urls/AAPL.json`
2. Resolve URLs:
   - Current: FY2026 Q1 URLs (covers Oct–Dec 2025)
   - Previous: FY2025 Q4 URLs (covers Jul–Sep 2025)
   - Year-ago: FY2025 Q1 URLs (covers Oct–Dec 2024)
3. Call `web_search.py` for each URL to extract financial data
4. Read `skills/preearnings/reference/company_template/AAPL.md`
5. Fill template placeholders with extracted data
6. Output `skills/preearnings/tmp/report.md`

## Success Cases

## Failure Cases
