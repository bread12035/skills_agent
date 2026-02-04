"""Sample script demonstrating the safe_py_runner execution path."""

import sys


def main():
    print("Hello from skills_agent scripts!")
    if len(sys.argv) > 1:
        print(f"Arguments: {sys.argv[1:]}")


if __name__ == "__main__":
    main()
