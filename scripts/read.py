"""Read file content and print to stdout.

Cross-platform replacement for CLI 'type' / 'cat' commands.
Reads the file at the given path relative to the project root.

Usage (called via safe_py_runner):
    safe_py_runner(
        script_name="scripts/read.py",
        args=["skills/ects_skill/tmp/transcript.txt"],
    )
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: read.py <file_path>", file=sys.stderr)
        sys.exit(1)

    file_path = Path(sys.argv[1])
    if not file_path.exists():
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    content = file_path.read_text(encoding="utf-8")
    print(content)


if __name__ == "__main__":
    main()
