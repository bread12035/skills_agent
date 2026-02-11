"""Retrieve an earnings call transcript from the API."""

import json
import sys
from pathlib import Path

import requests


# ── Configuration (fill in before use) ──────────────────────────────
API_URL = "<FILL_IN_API_URL>"
API_TOKEN = "<FILL_IN_API_TOKEN>"
# ────────────────────────────────────────────────────────────────────


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
    headers = {
        "Authorization": f"Bearer {API_TOKEN}",
        "Content-Type": "application/json",
    }
    params = {
        "company": company,
        "fiscal_year": fiscal_year,
        "fiscal_quarter": fiscal_quarter,
    }

    resp = requests.get(API_URL, headers=headers, params=params, timeout=60)
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

    # Save raw response for downstream parsing
    output_path = Path(__file__).parent / "tmp" / "raw_response.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(response, indent=2), encoding="utf-8")
    print(f"Raw response saved to {output_path}")


if __name__ == "__main__":
    main()
