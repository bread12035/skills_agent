"""Retrieve an earnings call transcript from the API."""

import json
import os
import sys
from pathlib import Path

import requests


# ── Configuration — reads from environment variables ─────────────
API_URL = os.environ.get("TRANSCRIPT_API_URL", "")
API_TOKEN = os.environ.get("TRANSCRIPT_API_TOKEN", "")
# ────────────────────────────────────────────────────────────────

OUTPUT_DIR = Path(__file__).parent / "tmp"


def retrieve_transcript(company: str, fiscal_year: str, fiscal_quarter: str) -> dict:
    """Call the transcript API and return the raw JSON response.

    Parameters
    ----------
    company : str
        Company ticker / identifier (e.g. "AAPL").
    fiscal_year : str
        Fiscal year (e.g. "2024").
    fiscal_quarter : str
        Fiscal quarter (e.g. "Q1").

    Returns
    -------
    dict
        Raw API response as a Python dict/list.
    """
    if not API_URL:
        print("ERROR: TRANSCRIPT_API_URL environment variable is not set.")
        sys.exit(1)
    if not API_TOKEN:
        print("ERROR: TRANSCRIPT_API_TOKEN environment variable is not set.")
        sys.exit(1)

    headers = {
        "Accept": "application/json",
        "apikey": API_TOKEN,
    }
    params = {
        "TRANSCRIPT_CO_CD": company,
        "FISCAL_YEAR_NO_TRANSCRIPT": fiscal_year,
        "FISCAL_QTR_NO_TRANSCRIPT": fiscal_quarter,
    }

    resp = requests.post(API_URL, headers=headers, params=params, timeout=60, verify=False)
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    if len(sys.argv) < 4:
        print("Usage: python retrieve_transcript.py <company> <fiscal_year> <fiscal_quarter>")
        sys.exit(1)

    company = sys.argv[1]
    fiscal_year = sys.argv[2]
    fiscal_quarter = sys.argv[3]

    print(f"Retrieving transcript for {company} FY{fiscal_year} {fiscal_quarter} ...")
    response = retrieve_transcript(company, fiscal_year, fiscal_quarter)

    # Save raw response to standardized tmp/ directory
    OUTPUT_DIR = Path(__file__).parent.parent / "skills" / "ects_skill" / "tmp"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "raw_response.json"
    output_path.write_text(json.dumps(response, indent=2), encoding="utf-8")
    print(f"Raw response saved to {output_path}")


if __name__ == "__main__":
    main()
