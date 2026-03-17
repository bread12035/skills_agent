"""Claude Native Skill — PDF generation via container-based execution.

Uses the Anthropic API with the 'pdf' skill to generate PDF documents.
The agent sends a prompt, Claude generates the PDF inside a container,
and this script downloads the resulting file via the Files API.

Usage (called via safe_py_runner):
    safe_py_runner(
        script_name="scripts/claude_pdf.py",
        args=["output/report.pdf"],
        stdin_text="Create a quarterly earnings report for Q1 2025 with charts",
    )

Output: Downloads the generated PDF to the specified path.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import anthropic


SKILL_ID = "pdf"
BETA_HEADERS = ["code-execution-2025-08-25", "skills-2025-10-02"]


def generate_pdf(prompt: str, output_path: str) -> str:
    """Generate a PDF using Claude's native PDF skill.

    Parameters
    ----------
    prompt : str
        Instructions for the PDF content to generate.
    output_path : str
        Path (relative to project root) where the PDF will be saved.

    Returns
    -------
    str
        Status message with the output file path.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    response = client.beta.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        betas=BETA_HEADERS,
        messages=[{"role": "user", "content": prompt}],
        container={"skill": SKILL_ID},
    )

    # Extract file_id from the response and download
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    file_downloaded = False
    text_parts: list[str] = []

    for block in response.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif hasattr(block, "file_id") and block.file_id:
            file_content = client.beta.files.content(block.file_id)
            out.write_bytes(file_content.read())
            file_downloaded = True

    if file_downloaded:
        return f"PDF saved to {output_path}"

    # Fallback: check for container result files
    if hasattr(response, "container") and response.container:
        for f in getattr(response.container, "files", []):
            file_content = client.beta.files.content(f.file_id)
            out.write_bytes(file_content.read())
            return f"PDF saved to {output_path}"

    return f"No file generated. Response: {' '.join(text_parts)}"


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: claude_pdf.py <output_path>", file=sys.stderr)
        print("  Prompt is read from stdin.", file=sys.stderr)
        sys.exit(1)

    output_path = sys.argv[1]
    prompt = sys.stdin.read().strip()
    if not prompt:
        print("ERROR: No prompt provided via stdin.", file=sys.stderr)
        sys.exit(1)

    result = generate_pdf(prompt, output_path)
    print(result)


if __name__ == "__main__":
    main()
