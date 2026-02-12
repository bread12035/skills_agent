"""Parse the raw API response and extract transcript text and metadata."""

import json
import sys
from pathlib import Path

# Output paths are relative to cwd (PROJECT_ROOT), set by safe_py_runner.
SKILL_TMP = Path("skills/ects_skill/tmp")


def parse_response(raw_path: str | Path | None = None) -> dict:
    """Parse the raw API response and return transcript + metadata.

    Parameters
    ----------
    raw_path : str | Path | None
        Path to raw_response.json.
        Defaults to ``skills/ects_skill/tmp/raw_response.json`` (relative to PROJECT_ROOT).

    Returns
    -------
    dict
        Keys: transcript, company, calendar_year, calendar_quarter.
    """
    if raw_path is None:
        raw_path = SKILL_TMP / "raw_response.json"
    raw_path = Path(raw_path)

    response = json.loads(raw_path.read_text(encoding="utf-8"))

    # Extract fields from the first record
    transcript = response[0]["doc_cont"]
    company = response[0]["bbg_co_cd"]
    calendar_year = response[0]["cal_year_no"]
    calendar_quarter = response[0]["cal_qtr_no"]

    return {
        "transcript": transcript,
        "company": company,
        "calendar_year": calendar_year,
        "calendar_quarter": calendar_quarter,
    }


def main() -> None:
    raw_path = sys.argv[1] if len(sys.argv) > 1 else None
    data = parse_response(raw_path)

    # Validate no missing fields
    missing = [k for k, v in data.items() if not v]
    if missing:
        print(f"ERROR: Missing fields: {', '.join(missing)}")
        sys.exit(1)

    # Save transcript text
    SKILL_TMP.mkdir(parents=True, exist_ok=True)
    transcript_path = SKILL_TMP / "transcript.txt"
    transcript_path.write_text(data["transcript"], encoding="utf-8")
    print(f"Transcript saved to {transcript_path}")

    # Save metadata alongside
    meta_path = SKILL_TMP / "metadata.json"
    meta = {k: v for k, v in data.items() if k != "transcript"}
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Metadata saved to {meta_path}")

    print(f"Company: {data['company']}")
    print(f"Calendar Year: {data['calendar_year']}")
    print(f"Calendar Quarter: {data['calendar_quarter']}")
    print(f"Transcript length: {len(data['transcript'])} chars")


if __name__ == "__main__":
    main()
