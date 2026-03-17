"""Web Search Agent — retrieves external information using the Anthropic Claude API.

Uses Claude's built-in web_search tool to perform internet searches and return
synthesized results. The API key is read from the ANTHROPIC_API_KEY env var.

Usage (called via safe_py_runner):
    safe_py_runner(
        script_name="scripts/web_search.py",
        args=["What is the current price of AAPL stock?"],
    )

Output: Prints the search-informed response to stdout.
"""

from __future__ import annotations

import json
import os
import sys

import anthropic


def web_search(query: str, *, max_tokens: int = 1024) -> str:
    """Perform a web search using Claude's native web_search tool.

    Parameters
    ----------
    query : str
        The search query string.
    max_tokens : int
        Maximum tokens for the response (default 1024).

    Returns
    -------
    str
        The search-informed response text.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=max_tokens,
        tools=[{"type": "web_search_20250305"}],
        messages=[{"role": "user", "content": query}],
    )

    # Extract text blocks from the response
    result_parts: list[str] = []
    for block in response.content:
        if block.type == "text":
            result_parts.append(block.text)

    return "\n".join(result_parts) if result_parts else "(no results)"


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: web_search.py <query>", file=sys.stderr)
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    result = web_search(query)
    print(result)


if __name__ == "__main__":
    main()
