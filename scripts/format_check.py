"""Format checker — validates that a filled template follows the expected structure.

Reads a filled summary markdown file and checks it against the template
defined in reference/template.md using regex patterns.

Usage:
    python format_check.py <filled_summary.md>

Exit codes:
    0 — file is compliant
    1 — validation errors found
"""

import re
import sys

# The expected sections in order, matching reference/template.md
EXPECTED_TITLE_PATTERN = r"^# Earnings Call Summary: .+ — \d{4} Q[1-4]$"

EXPECTED_SECTIONS = [
    "Financial Numbers",
    "Financial Description",
    "Guidance",
    "Product Performance",
    "QA Highlights",
]


def validate(text: str) -> list[str]:
    """Return a list of error messages. Empty list means the file is compliant."""
    errors: list[str] = []
    lines = text.strip().splitlines()

    if not lines:
        errors.append("File is empty.")
        return errors

    # --- Title line ---
    title_line = lines[0].strip()
    if not re.match(EXPECTED_TITLE_PATTERN, title_line):
        errors.append(
            f"Title does not match expected pattern. Got: '{title_line}'. "
            f"Expected format: '# Earnings Call Summary: <COMPANY> — <YEAR> Q<1-4>'"
        )

    # --- Section headings in order ---
    heading_pattern = re.compile(r"^## (.+)$")
    found_sections: list[str] = []
    for line in lines[1:]:
        m = heading_pattern.match(line.strip())
        if m:
            found_sections.append(m.group(1))

    if found_sections != EXPECTED_SECTIONS:
        missing = [s for s in EXPECTED_SECTIONS if s not in found_sections]
        extra = [s for s in found_sections if s not in EXPECTED_SECTIONS]
        if missing:
            errors.append(f"Missing sections: {missing}")
        if extra:
            errors.append(f"Unexpected sections: {extra}")
        if not missing and not extra:
            errors.append(
                f"Sections are out of order. "
                f"Expected: {EXPECTED_SECTIONS}, got: {found_sections}"
            )

    # --- Each section must have non-empty content ---
    section_content_pattern = re.compile(
        r"^## (?P<heading>.+?)\s*\n(?P<body>.*?)(?=\n## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    for m in section_content_pattern.finditer(text):
        heading = m.group("heading").strip()
        body = m.group("body").strip()
        if heading in EXPECTED_SECTIONS and not body:
            errors.append(f"Section '{heading}' has no content.")

    # --- No leftover template placeholders ---
    placeholders = re.findall(r"\{\{[A-Z_]+\}\}", text)
    if placeholders:
        errors.append(f"Unresolved template placeholders found: {placeholders}")

    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <filled_summary.md>", file=sys.stderr)
        return 1

    filepath = sys.argv[1]
    try:
        with open(filepath, "r") as f:
            text = f.read()
    except FileNotFoundError:
        print(f"Error: file not found: {filepath}", file=sys.stderr)
        return 1

    errors = validate(text)
    if errors:
        print("Validation FAILED:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print("Validation PASSED: file is template-compliant.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
