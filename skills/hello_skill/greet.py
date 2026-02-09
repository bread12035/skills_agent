"""Greet skill — generates a personalised greeting and saves it to output.txt."""

import sys
from pathlib import Path


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "World"
    greeting = f"Hello, {name}! Welcome to the Skills Agent."
    print(greeting)

    output_path = Path("output.txt")
    output_path.write_text(greeting + "\n", encoding="utf-8")
    print(f"Greeting saved to {output_path}")


if __name__ == "__main__":
    main()
