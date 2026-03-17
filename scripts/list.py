"""List directory contents.

Cross-platform replacement for CLI 'dir' / 'ls' commands.
Lists files and directories at the given path relative to the project root.

Usage (called via safe_py_runner):
    safe_py_runner(
        script_name="scripts/list.py",
        args=["skills/ects_skill/tmp"],
    )
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: list.py <directory_path>", file=sys.stderr)
        sys.exit(1)

    dir_path = Path(sys.argv[1])
    if not dir_path.exists():
        print(f"Error: Directory not found: {dir_path}", file=sys.stderr)
        sys.exit(1)

    if not dir_path.is_dir():
        print(f"Error: Not a directory: {dir_path}", file=sys.stderr)
        sys.exit(1)

    entries = sorted(dir_path.iterdir())
    for entry in entries:
        kind = "DIR " if entry.is_dir() else "FILE"
        print(f"  {kind}  {entry.name}")

    print(f"\n  {len(entries)} item(s) in {dir_path}")


if __name__ == "__main__":
    main()
