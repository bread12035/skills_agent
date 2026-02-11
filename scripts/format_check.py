"""Format checker — validates that a filled template follows the expected structure.

Reads a filled summary markdown file and checks it against the template
defined in skills/ects_skill/reference/template.md using regex patterns.

Usage:
    python format_check.py <filled_summary.md>

Exit codes:
    0 — file is compliant
    1 — validation errors found
"""

import re
import sys

# ---------------------------------------------------------------------------
# Template structure derived from skills/ects_skill/reference/template.md
# ---------------------------------------------------------------------------

# Title line: ### AI Summarization (Calendar year: Q<quarter>, <year>)
TITLE_PATTERN = re.compile(
    r"^### AI Summarization \(Calendar year: Q[1-4], \d{4}\)\s*$"
)

# Required #### sections in order
EXPECTED_H4_SECTIONS = [
    "Financial Highlights:",
    "Briefing of Key Message:",
    "Key Message:",
    "Key insights from Q&A session",
]

# Under "Financial Highlights:" these bold bullet keys are required
FINANCIAL_HIGHLIGHT_KEYS = [
    "Revenue",
    "Gross Margin",
    "DOI",
    "Guidance",
]

# Under "Guidance" sub-bullets
GUIDANCE_SUB_KEYS = [
    "Revenue Guidance",
    "Gross-margin Guidance",
    "Overall Performance & Outlook",
]

# Under "Key insights from Q&A session" we expect a summary list and themes
QA_SUMMARY_PATTERN = re.compile(r"- \*\*Summary of Key Themes\*\*:")
QA_NUMBERED_THEME_PATTERN = re.compile(r"- \*\*\d+\.\s+.+\*\*")
QA_THEME_BLOCK_PATTERN = re.compile(r"- \*\*Theme \d+:\s+.+\*\*")
QA_ANALYST_PATTERN = re.compile(r"- \*\*Analyst Questions:\*\*")
QA_RESPONSE_PATTERN = re.compile(r"- \*\*Company Response:\*\*")


def validate(text: str) -> list[str]:
    """Return a list of error messages. Empty list means the file is compliant."""
    errors: list[str] = []
    lines = text.strip().splitlines()

    if not lines:
        errors.append("File is empty.")
        return errors

    # --- Title line ---
    title_line = lines[0].strip()
    if not TITLE_PATTERN.match(title_line):
        errors.append(
            f"Title does not match expected pattern. Got: '{title_line}'. "
            f"Expected: '### AI Summarization (Calendar year: Q<1-4>, <YYYY>)'"
        )

    # --- #### section headings in order ---
    h4_pattern = re.compile(r"^####\s+(.+)$")
    found_sections: list[str] = []
    for line in lines[1:]:
        m = h4_pattern.match(line.strip())
        if m:
            found_sections.append(m.group(1).strip())

    if found_sections != EXPECTED_H4_SECTIONS:
        missing = [s for s in EXPECTED_H4_SECTIONS if s not in found_sections]
        extra = [s for s in found_sections if s not in EXPECTED_H4_SECTIONS]
        if missing:
            errors.append(f"Missing sections: {missing}")
        if extra:
            errors.append(f"Unexpected sections: {extra}")
        if not missing and not extra:
            errors.append(
                f"Sections are out of order. "
                f"Expected: {EXPECTED_H4_SECTIONS}, got: {found_sections}"
            )

    # --- Financial Highlights required keys ---
    fin_section = _extract_section(text, "Financial Highlights:")
    if fin_section is not None:
        for key in FINANCIAL_HIGHLIGHT_KEYS:
            pattern = re.compile(rf"- \*\*{re.escape(key)}\*\*")
            if not pattern.search(fin_section):
                errors.append(
                    f"Financial Highlights: missing required key '{key}'."
                )

        # Guidance sub-keys
        guidance_match = re.search(
            r"- \*\*Guidance\*\*:.*?(?=\n- \*\*[A-Z]|\n####|\Z)",
            fin_section,
            re.DOTALL,
        )
        if guidance_match:
            guidance_block = guidance_match.group(0)
            for sub_key in GUIDANCE_SUB_KEYS:
                pat = re.compile(rf"- \*\*{re.escape(sub_key)}\*\*:")
                if not pat.search(guidance_block):
                    errors.append(
                        f"Financial Highlights > Guidance: missing sub-key "
                        f"'{sub_key}'."
                    )
    else:
        errors.append("Could not locate 'Financial Highlights:' section content.")

    # --- Briefing of Key Message: must have at least one product segment ---
    briefing_section = _extract_section(text, "Briefing of Key Message:")
    if briefing_section is not None:
        product_bullets = re.findall(
            r"^- \*\*.+\*\*:", briefing_section, re.MULTILINE
        )
        if not product_bullets:
            errors.append(
                "Briefing of Key Message: no product segment bullets found."
            )
    else:
        errors.append("Could not locate 'Briefing of Key Message:' section content.")

    # --- Key Message: must have at least one product segment ---
    key_msg_section = _extract_section(text, "Key Message:")
    if key_msg_section is not None:
        product_bullets = re.findall(
            r"^- \*\*.+\*\*:", key_msg_section, re.MULTILINE
        )
        if not product_bullets:
            errors.append("Key Message: no product segment bullets found.")
    else:
        errors.append("Could not locate 'Key Message:' section content.")

    # --- Key insights from Q&A session ---
    qa_section = _extract_section(text, "Key insights from Q&A session")
    if qa_section is not None:
        if not QA_SUMMARY_PATTERN.search(qa_section):
            errors.append("Q&A section: missing 'Summary of Key Themes'.")

        numbered = QA_NUMBERED_THEME_PATTERN.findall(qa_section)
        if not numbered:
            errors.append("Q&A section: no numbered theme summaries found.")

        theme_blocks = QA_THEME_BLOCK_PATTERN.findall(qa_section)
        if not theme_blocks:
            errors.append("Q&A section: no 'Theme N:' blocks found.")

        analyst_count = len(QA_ANALYST_PATTERN.findall(qa_section))
        response_count = len(QA_RESPONSE_PATTERN.findall(qa_section))
        if analyst_count == 0:
            errors.append("Q&A section: no 'Analyst Questions:' entries found.")
        if response_count == 0:
            errors.append("Q&A section: no 'Company Response:' entries found.")
        if analyst_count != response_count:
            errors.append(
                f"Q&A section: mismatched Analyst Questions ({analyst_count}) "
                f"vs Company Response ({response_count}) counts."
            )
    else:
        errors.append(
            "Could not locate 'Key insights from Q&A session' section content."
        )

    # --- No leftover template placeholders (square-bracket placeholders) ---
    placeholders = re.findall(
        r"\[(?:Revenue metric|Gross Margin metric|DOI metric|"
        r"Revenue Guidance|Gross-margin Guidance|Overall Performance|"
        r"summarized_key_message|key_message|Product segment|sub-segment|"
        r"Content of Earnings Call|First key theme|Second key theme|"
        r"Third key theme|Summary of themes|Financial data)"
        r"[^\]]*\]",
        text,
    )
    if placeholders:
        errors.append(
            f"Unresolved template placeholders found ({len(placeholders)}): "
            f"{placeholders[:5]}{'...' if len(placeholders) > 5 else ''}"
        )

    return errors


def _extract_section(text: str, heading: str) -> str | None:
    """Extract the body text under a #### heading until the next #### or end."""
    pattern = re.compile(
        rf"^####\s+{re.escape(heading)}\s*\n(?P<body>.*?)(?=\n####\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(text)
    if m:
        return m.group("body")
    return None


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
