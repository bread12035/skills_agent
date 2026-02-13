"""Write content from stdin to a file path.

Bypasses shell quoting entirely — content flows through Python's subprocess
stdin pipe and is written directly via Python file I/O, so arbitrary
characters (quotes, brackets, newlines, etc.) are handled safely.

Usage (called via safe_py_runner):
    safe_py_runner(
        script_name="scripts/write_file.py",
        args=["skills/ects_skill/tmp/ai_summary.md"],
        stdin_text=<content string>,
    )
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: write_file.py <output_path>", file=sys.stderr)
        sys.exit(1)

    output_path = Path(sys.argv[1])
    output_path.parent.mkdir(parents=True, exist_ok=True)

    content = sys.stdin.read()
    output_path.write_text(content, encoding="utf-8")
    print(f"Wrote {len(content)} characters to {output_path}")


if __name__ == "__main__":
    main()
