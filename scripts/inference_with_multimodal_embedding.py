"""Inference with Multimodal Embedding — retrieve video knowledge from the
multimodal RAG index and synthesize structured reports.

Given a user query, this script:
  1. Embeds the query via text-embedding-004
  2. Performs hybrid retrieval (text + visual vectors) from ChromaDB
  3. Aggregates matched segments with key frames and timestamped URLs
  4. Synthesizes a structured Markdown report via Gemini

Usage (called via safe_py_runner):
    safe_py_runner(
        script_name="scripts/inference_with_multimodal_embedding.py",
        args=["<skill_name>", "<query>"],
    )

    # With options:
    safe_py_runner(
        script_name="scripts/inference_with_multimodal_embedding.py",
        args=[
            "<skill_name>", "<query>",
            "--top-k", "10",
            "--no-synthesize",
        ],
    )

Environment variables:
    GEMINI_API_KEY — Google Gemini API key (required)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_GEMINI_MODEL = os.environ.get("GEMINI_VIDEO_MODEL", "gemini-2.0-flash")
_TEXT_EMBED_MODEL = os.environ.get("GEMINI_TEXT_EMBED_MODEL", "text-embedding-004")


# ---------------------------------------------------------------------------
# ChromaDB access
# ---------------------------------------------------------------------------

def _get_chroma_collection(skill_name: str, collection_name: str = "video_rag"):
    """Load an existing ChromaDB collection for video RAG."""
    import chromadb

    persist_dir = str(Path(f"skills/{skill_name}/tmp/chromadb"))
    if not Path(persist_dir).exists():
        print(f"ERROR: ChromaDB not found at {persist_dir}", file=sys.stderr)
        sys.exit(1)

    chroma_client = chromadb.PersistentClient(path=persist_dir)
    try:
        return chroma_client.get_collection(name=collection_name)
    except Exception:
        print(
            f"ERROR: Collection '{collection_name}' not found. "
            "Run gemini_youtube_search.py first to ingest videos.",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Query embedding
# ---------------------------------------------------------------------------

def _embed_query(client: genai.Client, query: str) -> list[float]:
    """Embed a query string using text-embedding-004."""
    result = client.models.embed_content(
        model=_TEXT_EMBED_MODEL,
        contents=[query],
    )
    return result.embeddings[0].values


# ---------------------------------------------------------------------------
# Hybrid retrieval
# ---------------------------------------------------------------------------

def hybrid_retrieve(
    client: genai.Client,
    collection,
    query: str,
    top_k: int = 10,
) -> list[dict]:
    """Perform hybrid retrieval combining text and visual vector search.

    Queries the ChromaDB collection with the query embedding, retrieving
    from both text and visual modalities, then deduplicates and merges
    results by segment.

    Parameters
    ----------
    client : genai.Client
        Gemini API client.
    collection
        ChromaDB collection.
    query : str
        User's search query.
    top_k : int
        Number of top results per modality.

    Returns
    -------
    list[dict]
        Merged segment results sorted by relevance score.
    """
    query_vector = _embed_query(client, query)

    # Query with larger n to get both text and visual hits
    results = collection.query(
        query_embeddings=[query_vector],
        n_results=min(top_k * 3, 50),
        include=["documents", "metadatas", "distances"],
    )

    if not results["ids"] or not results["ids"][0]:
        return []

    # Group by segment (video_id + start_sec)
    segment_map: dict[str, dict] = {}

    for i, doc_id in enumerate(results["ids"][0]):
        metadata = results["metadatas"][0][i]
        document = results["documents"][0][i]
        distance = results["distances"][0][i]
        # ChromaDB cosine distance: lower is better; convert to similarity
        similarity = 1.0 - distance

        segment_key = f"{metadata.get('video_id', '')}_{metadata.get('start_sec', 0)}"

        if segment_key not in segment_map:
            segment_map[segment_key] = {
                "video_id": metadata.get("video_id", ""),
                "url": metadata.get("url", ""),
                "topic": metadata.get("topic", ""),
                "start_sec": metadata.get("start_sec", 0),
                "end_sec": metadata.get("end_sec", 0),
                "text_summary": "",
                "text_score": 0.0,
                "visual_matches": [],
                "best_score": 0.0,
            }

        entry = segment_map[segment_key]

        if metadata.get("type") == "text":
            entry["text_summary"] = document
            entry["text_score"] = similarity
            entry["best_score"] = max(entry["best_score"], similarity)
        elif metadata.get("type") == "visual":
            entry["visual_matches"].append({
                "img_path": metadata.get("img_path", ""),
                "score": similarity,
            })
            entry["best_score"] = max(entry["best_score"], similarity)

    # Sort by best score descending
    segments = sorted(segment_map.values(), key=lambda x: x["best_score"], reverse=True)
    return segments[:top_k]


# ---------------------------------------------------------------------------
# Report synthesis
# ---------------------------------------------------------------------------

_SYNTHESIZE_SYSTEM = """\
You are a video knowledge synthesis assistant. Given a user query and \
retrieved video segments with summaries and key frame references, produce a \
comprehensive structured Markdown report.

The report MUST include:
1. **Summary** — A concise answer to the query based on video evidence.
2. **Detailed Findings** — For each relevant segment:
   - The topic and time range
   - Key insights from the text summary
   - References to key frame images (use the provided paths)
   - A clickable YouTube link with timestamp
3. **Sources** — A list of all referenced videos with links.

Use clear Markdown formatting. Include image references as: ![description](path)
Include YouTube links as: [Topic (MM:SS)](url)

Be precise and factual. Only use information from the provided segments."""


def _format_timestamp(seconds: int) -> str:
    """Format seconds as MM:SS or HH:MM:SS."""
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


def synthesize_report(
    client: genai.Client,
    query: str,
    segments: list[dict],
) -> str:
    """Generate a structured Markdown report from retrieved segments.

    Parameters
    ----------
    client : genai.Client
        Gemini API client.
    query : str
        User's original query.
    segments : list[dict]
        Retrieved and ranked segment data.

    Returns
    -------
    str
        A Markdown-formatted report.
    """
    if not segments:
        return "No relevant video segments found for the query."

    # Build context for the LLM
    context_parts = []
    for i, seg in enumerate(segments):
        start_ts = _format_timestamp(seg.get("start_sec", 0))
        end_ts = _format_timestamp(seg.get("end_sec", 0))

        part = (
            f"### Segment {i + 1}: {seg.get('topic', 'Unknown')}\n"
            f"- Video ID: {seg.get('video_id', '?')}\n"
            f"- Time range: {start_ts} - {end_ts}\n"
            f"- YouTube link: {seg.get('url', '')}\n"
            f"- Relevance score: {seg.get('best_score', 0):.3f}\n"
            f"- Summary: {seg.get('text_summary', 'N/A')}\n"
        )

        if seg.get("visual_matches"):
            part += "- Key frames:\n"
            for vm in seg["visual_matches"]:
                part += f"  - {vm.get('img_path', '')} (score: {vm.get('score', 0):.3f})\n"

        context_parts.append(part)

    context = "\n".join(context_parts)
    prompt = f"Query: {query}\n\nRetrieved video segments:\n\n{context}"

    response = client.models.generate_content(
        model=_GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=_SYNTHESIZE_SYSTEM,
            temperature=0.2,
        ),
    )

    return response.text if response.text else "Report generation failed."


# ---------------------------------------------------------------------------
# Main inference pipeline
# ---------------------------------------------------------------------------

def inference(
    skill_name: str,
    query: str,
    top_k: int = 10,
    synthesize: bool = True,
) -> dict[str, Any]:
    """Run multimodal retrieval and optional synthesis.

    Parameters
    ----------
    skill_name : str
        Skill whose tmp/ contains the ChromaDB index.
    query : str
        The user's question.
    top_k : int
        Number of top segments to retrieve.
    synthesize : bool
        Whether to generate a synthesized report.

    Returns
    -------
    dict
        Keys: segments, report (if synthesize=True), metadata.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("ERROR: GEMINI_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    collection = _get_chroma_collection(skill_name)

    print(f"Collection loaded: {collection.count()} documents indexed.")

    # Step 1: Hybrid retrieval
    segments = hybrid_retrieve(client, collection, query, top_k=top_k)
    print(f"Retrieved {len(segments)} relevant segments.")

    output: dict[str, Any] = {
        "segments": segments,
        "metadata": {
            "skill_name": skill_name,
            "query": query,
            "top_k": top_k,
            "total_indexed": collection.count(),
            "total_retrieved": len(segments),
        },
    }

    # Step 2: Synthesize report
    if synthesize and segments:
        print("Synthesizing report...")
        report = synthesize_report(client, query, segments)
        output["report"] = report

    return output


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 3:
        print(
            "Usage: inference_with_multimodal_embedding.py <skill_name> <query> "
            "[--top-k N] [--no-synthesize]",
            file=sys.stderr,
        )
        sys.exit(1)

    skill_name = sys.argv[1]
    query = sys.argv[2]

    # Parse optional flags
    top_k = 10
    synthesize = True

    i = 3
    while i < len(sys.argv):
        if sys.argv[i] == "--top-k" and i + 1 < len(sys.argv):
            top_k = int(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == "--no-synthesize":
            synthesize = False
            i += 1
        else:
            i += 1

    result = inference(
        skill_name=skill_name,
        query=query,
        top_k=top_k,
        synthesize=synthesize,
    )

    # Print report if synthesized
    if "report" in result:
        print(f"\n=== Video RAG Report ===\n{result['report']}")

    # Print structured output
    print(f"\n=== Retrieval Result ===")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
