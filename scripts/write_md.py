"""Write markdown content to a file safely.

Usage:
    python write_md.py <file_path> <md_content>

Arguments:
    file_path  — Destination file path (relative to project root).
    md_content — Markdown string to write.

Creates parent directories if needed and writes with UTF-8 encoding.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python write_md.py <file_path> <md_content>", file=sys.stderr)
        sys.exit(1)

    file_path = sys.argv[1]
    md_content = sys.argv[2]

    # Resolve path relative to project root
    dest = PROJECT_ROOT / file_path.replace("\\", "/")
    dest.parent.mkdir(parents=True, exist_ok=True)

    dest.write_text(md_content, encoding="utf-8")
    print(f"Markdown written to {dest.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
