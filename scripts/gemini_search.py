"""Gemini Google Search — web search using Google's Generative AI SDK.

Wraps Google Gemini's native Google Search tool to perform web searches
and return grounded results. The API key is read from GEMINI_API_KEY env var.

Usage (called via safe_py_runner):
    safe_py_runner(
        script_name="scripts/gemini_search.py",
        args=["What are the latest developments in quantum computing?"],
    )

Output: Prints the search-informed response to stdout.
"""

from __future__ import annotations

import os
import sys

from google import genai
from google.genai import types


def gemini_search(query: str) -> str:
    """Perform a web search using Gemini's Google Search tool.

    Parameters
    ----------
    query : str
        The search query string.

    Returns
    -------
    str
        The search-informed response text.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("ERROR: GEMINI_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=query,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )

    if response.text:
        return response.text

    return "(no results)"


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: gemini_search.py <query>", file=sys.stderr)
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    result = gemini_search(query)
    print(result)


if __name__ == "__main__":
    main()
