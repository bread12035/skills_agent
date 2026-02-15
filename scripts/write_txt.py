"""Write plain text content to a file safely.

Usage:
    python write_txt.py <file_path> <text_content>

Arguments:
    file_path    — Destination file path (relative to project root).
    text_content — Text string to write.

Creates parent directories if needed and writes with UTF-8 encoding.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python write_txt.py <file_path> <text_content>", file=sys.stderr)
        sys.exit(1)

    file_path = sys.argv[1]
    text_content = sys.argv[2]

    # Resolve path relative to project root
    dest = PROJECT_ROOT / file_path.replace("\\", "/")
    dest.parent.mkdir(parents=True, exist_ok=True)

    dest.write_text(text_content, encoding="utf-8")
    print(f"Text written to {dest.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
