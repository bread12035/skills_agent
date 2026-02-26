"""Write markdown content to a file safely.

Usage:
    python write_md.py <file_path> <md_content>

Arguments:
    file_path  — Destination file path (relative to project root).
    md_content — Markdown string to write.

Creates parent directories if needed and writes with UTF-8 encoding.
Handles UnicodeEncodeError gracefully by replacing unencodable characters.
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

    try:
        dest.write_text(md_content, encoding="utf-8")
    except UnicodeEncodeError:
        # Fallback: encode with replacement for surrogate or unencodable characters
        safe_bytes = md_content.encode("utf-8", errors="replace")
        dest.write_bytes(safe_bytes)
        print(
            "Warning: some characters were replaced due to UnicodeEncodeError",
            file=sys.stderr,
        )

    try:
        print(f"Markdown written to {dest.relative_to(PROJECT_ROOT)}")
    except UnicodeEncodeError:
        rel = str(dest.relative_to(PROJECT_ROOT)).encode("ascii", errors="replace").decode("ascii")
        print(f"Markdown written to {rel}")


if __name__ == "__main__":
    main()
