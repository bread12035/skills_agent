"""Write JSON content to a file safely.

Usage:
    python write_json.py <file_path> <json_content>

Arguments:
    file_path    — Destination file path (relative to project root).
    json_content — JSON string to write.

The script validates that the content is valid JSON before writing,
creates parent directories if needed, and writes with UTF-8 encoding.
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python write_json.py <file_path> <json_content>", file=sys.stderr)
        sys.exit(1)

    file_path = sys.argv[1]
    json_content = sys.argv[2]

    # Validate JSON
    try:
        parsed = json.loads(json_content)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON content — {e}", file=sys.stderr)
        sys.exit(1)

    # Resolve path relative to project root
    dest = PROJECT_ROOT / file_path.replace("\\", "/")
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Write with consistent formatting
    dest.write_text(json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"JSON written to {dest.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
