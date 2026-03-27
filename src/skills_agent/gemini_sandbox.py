"""Gemini Code Execution Sandbox — internal module for evaluator-exclusive sandbox tool.

Leverages Gemini's native code execution tool to run Python code in a secure
cloud sandbox environment. This module is NOT a standalone script — it lives
inside src/skills_agent/ to prevent Optimizer access via safe_py_runner.

Called exclusively by the run_in_sandbox tool (evaluator-only).

Supports two input modes:
1. **Prompt mode**: Pass a natural-language prompt describing the code to
   generate and execute.
2. **File mode**: Upload a large file via Gemini File API, then reference it
   in the prompt for processing.

Returns dict with keys:
    - code: the generated Python code
    - output: execution output (stdout/stderr from sandbox)
    - error: error message if execution failed (empty string on success)
"""

from __future__ import annotations

import os
import time

from google import genai
from google.genai import types


# ---------------------------------------------------------------------------
# Gemini sandbox model — configurable via env var
# ---------------------------------------------------------------------------

_SANDBOX_MODEL = os.environ.get("GEMINI_SANDBOX_MODEL", "gemini-2.5-flash")


def _upload_file(client: genai.Client, file_path: str) -> types.File:
    """Upload a file via Gemini File API and wait until it's ready.

    Parameters
    ----------
    client : genai.Client
        Authenticated Gemini client.
    file_path : str
        Local path to the file to upload.

    Returns
    -------
    types.File
        The uploaded file reference for use in generate_content.
    """
    uploaded = client.files.upload(file=file_path)

    # Poll until the file is processed (ACTIVE state)
    max_wait = 120  # seconds
    poll_interval = 2
    elapsed = 0
    while uploaded.state == "PROCESSING" and elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval
        uploaded = client.files.get(name=uploaded.name)

    if uploaded.state != "ACTIVE":
        raise RuntimeError(
            f"File upload failed: state={uploaded.state} after {elapsed}s "
            f"(file: {file_path})"
        )

    return uploaded


def run_sandbox(
    prompt: str,
    file_path: str | None = None,
) -> dict[str, str]:
    """Execute code in Gemini's cloud sandbox.

    Parameters
    ----------
    prompt : str
        Natural-language prompt describing what code to generate and execute.
        Should be specific about inputs, processing logic, and expected output.
    file_path : str | None
        Optional local file path to upload via File API. The file will be
        available to the generated code in the sandbox.

    Returns
    -------
    dict[str, str]
        Keys: code, output, error
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return {
            "code": "",
            "output": "",
            "error": "GEMINI_API_KEY environment variable is not set.",
        }

    client = genai.Client(api_key=api_key)

    # Build the content parts
    contents: list = []

    # If a file is provided, upload it and include as a part
    if file_path:
        try:
            uploaded_file = _upload_file(client, file_path)
            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_uri(
                            file_uri=uploaded_file.uri,
                            mime_type=uploaded_file.mime_type,
                        ),
                        types.Part.from_text(text=prompt),
                    ],
                )
            )
        except Exception as e:
            return {
                "code": "",
                "output": "",
                "error": f"File upload failed: {e}",
            }
    else:
        contents.append(prompt)

    # Configure with code execution tool
    config = types.GenerateContentConfig(
        tools=[types.Tool(code_execution=types.ToolCodeExecution())],
    )

    try:
        response = client.models.generate_content(
            model=_SANDBOX_MODEL,
            contents=contents,
            config=config,
        )
    except Exception as e:
        return {
            "code": "",
            "output": "",
            "error": f"Gemini API call failed: {e}",
        }

    # Extract code and execution results from response parts
    code_parts: list[str] = []
    output_parts: list[str] = []
    text_parts: list[str] = []

    if response.candidates:
        for candidate in response.candidates:
            if candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if part.executable_code:
                        code_parts.append(part.executable_code.code)
                    if part.code_execution_result:
                        output_parts.append(part.code_execution_result.output)
                    if part.text:
                        text_parts.append(part.text)

    code = "\n\n# --- next code block ---\n\n".join(code_parts) if code_parts else ""
    output = "\n".join(output_parts) if output_parts else ""

    # Append text explanation if present
    if text_parts:
        output = output + "\n\n--- Gemini Explanation ---\n" + "\n".join(text_parts) if output else "\n".join(text_parts)

    return {
        "code": code,
        "output": output,
        "error": "",
    }
