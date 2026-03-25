"""Gemini YouTube Search — multimodal video RAG ingestion & indexing.

Uses Gemini's native video understanding to semantically chunk YouTube videos,
extract key frames, and build a multimodal vector index in ChromaDB for
retrieval-augmented generation.

Pipeline:
  1. Download YouTube video via yt-dlp
  2. Upload to Gemini File API for video analysis
  3. Semantic chunking — Gemini identifies topic transition points
  4. Key frame extraction via ffmpeg at identified timestamps
  5. Dual-path embedding (text-embedding-004 + multimodal-embedding)
  6. Persist vectors + metadata in ChromaDB

Usage (called via safe_py_runner):
    safe_py_runner(
        script_name="scripts/gemini_youtube_search.py",
        args=["<skill_name>", "<youtube_url>", "<query>"],
    )

    # With multiple URLs (comma-separated):
    safe_py_runner(
        script_name="scripts/gemini_youtube_search.py",
        args=["<skill_name>", "<url1>,<url2>", "<query>"],
    )

Environment variables:
    GEMINI_API_KEY — Google Gemini API key (required)
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import time
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
# YouTube download
# ---------------------------------------------------------------------------

def _download_youtube(url: str, output_dir: Path) -> Path:
    """Download a YouTube video using yt-dlp.

    Returns the path to the downloaded video file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    output_template = str(output_dir / "%(id)s.%(ext)s")
    cmd = [
        "yt-dlp",
        "-f", "worst[ext=mp4]/worst",  # smallest mp4 to save bandwidth
        "--no-playlist",
        "-o", output_template,
        "--print", "after_move:filepath",
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp failed: {result.stderr.strip()}")

    # Last non-empty line of stdout is the file path
    filepath = result.stdout.strip().split("\n")[-1].strip()
    return Path(filepath)


def _extract_video_id(url: str) -> str:
    """Extract video ID from a YouTube URL."""
    patterns = [
        r"(?:v=|/v/|youtu\.be/)([a-zA-Z0-9_-]{11})",
        r"(?:embed/)([a-zA-Z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return url.split("/")[-1].split("?")[0][:11]


# ---------------------------------------------------------------------------
# Key frame extraction
# ---------------------------------------------------------------------------

def _extract_keyframe(
    video_path: Path,
    timestamp_sec: float,
    output_path: Path,
) -> Path:
    """Extract a single frame from a video at a given timestamp using ffmpeg."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(timestamp_sec),
        "-i", str(video_path),
        "-frames:v", "1",
        "-q:v", "2",
        str(output_path),
    ]
    subprocess.run(cmd, capture_output=True, timeout=60)
    return output_path


# ---------------------------------------------------------------------------
# Gemini video analysis — semantic chunking
# ---------------------------------------------------------------------------

_SEMANTIC_CHUNK_PROMPT = """\
You are a video analysis assistant. Analyze this video and identify semantic \
segments where topics or activities change (e.g., intro -> demonstration -> \
conclusion, or unboxing -> parts review -> assembly).

For each segment, provide:
1. A 100-200 word detailed summary of what happens
2. start_timestamp and end_timestamp in seconds
3. Three key moments (as timestamps in seconds) that best represent the \
segment visually (e.g., close-up of a part, a key action, a result)

The user's search query for context: "{query}"

Return ONLY valid JSON with this structure:
{{
  "video_title": "...",
  "total_duration_sec": <number>,
  "segments": [
    {{
      "segment_index": 0,
      "topic": "short topic label",
      "summary": "100-200 word detailed summary...",
      "start_sec": <number>,
      "end_sec": <number>,
      "key_moments_sec": [<ts1>, <ts2>, <ts3>]
    }}
  ]
}}

Return ONLY valid JSON, no markdown fences."""


def _analyze_video(
    client: genai.Client,
    video_file: Any,
    query: str,
) -> dict:
    """Use Gemini to semantically chunk a video.

    Parameters
    ----------
    client : genai.Client
        The Gemini API client.
    video_file : Any
        The uploaded file object from Gemini File API.
    query : str
        The user's search query for context-aware chunking.

    Returns
    -------
    dict
        Parsed JSON with video_title, total_duration_sec, and segments.
    """
    prompt = _SEMANTIC_CHUNK_PROMPT.format(query=query)
    response = client.models.generate_content(
        model=_GEMINI_MODEL,
        contents=[video_file, prompt],
    )

    text = response.text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return json.loads(text)


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def _embed_texts(
    client: genai.Client,
    texts: list[str],
) -> list[list[float]]:
    """Embed a batch of texts using Gemini text-embedding-004."""
    result = client.models.embed_content(
        model=_TEXT_EMBED_MODEL,
        contents=texts,
    )
    return [e.values for e in result.embeddings]


def _embed_image(
    client: genai.Client,
    image_path: Path,
) -> list[float] | None:
    """Embed an image using Gemini multimodal content understanding.

    Generates a detailed visual description via Gemini, then embeds the
    description text. This approach ensures compatibility across all
    Gemini API tiers without requiring Vertex AI multimodal embedding.
    """
    if not image_path.exists():
        return None

    img_bytes = image_path.read_bytes()
    img_b64 = base64.b64encode(img_bytes).decode("utf-8")

    # Generate a visual description
    response = client.models.generate_content(
        model=_GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
            "Describe this video frame in detail (objects, actions, text, "
            "context) in 2-3 sentences. Be specific and factual.",
        ],
    )
    description = response.text.strip() if response.text else ""
    if not description:
        return None

    # Embed the visual description
    vectors = _embed_texts(client, [description])
    return vectors[0] if vectors else None


# ---------------------------------------------------------------------------
# ChromaDB storage
# ---------------------------------------------------------------------------

def _get_chroma_collection(
    skill_name: str,
    collection_name: str = "video_rag",
):
    """Get or create a ChromaDB collection for video RAG.

    Stores data persistently under skills/<skill_name>/tmp/chromadb/.
    """
    import chromadb

    persist_dir = str(Path(f"skills/{skill_name}/tmp/chromadb"))
    Path(persist_dir).mkdir(parents=True, exist_ok=True)
    chroma_client = chromadb.PersistentClient(path=persist_dir)
    collection = chroma_client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )
    return collection


def _store_segment(
    collection,
    video_id: str,
    video_url: str,
    segment: dict,
    segment_idx: int,
    text_vector: list[float],
    visual_vectors: list[tuple[str, list[float]]],
) -> int:
    """Store a segment's text and visual vectors into ChromaDB.

    Parameters
    ----------
    collection
        ChromaDB collection.
    video_id : str
        YouTube video ID.
    video_url : str
        Original YouTube URL.
    segment : dict
        Segment data from Gemini analysis.
    segment_idx : int
        Segment index.
    text_vector : list[float]
        Text embedding of the segment summary.
    visual_vectors : list[tuple[str, list[float]]]
        List of (img_path, vector) tuples for key frames.

    Returns
    -------
    int
        Number of documents stored.
    """
    start_sec = int(segment.get("start_sec", 0))
    end_sec = int(segment.get("end_sec", 0))
    timestamped_url = f"{video_url}&t={start_sec}s" if "?" in video_url else f"{video_url}?t={start_sec}s"

    base_metadata = {
        "video_id": video_id,
        "url": timestamped_url,
        "start_sec": start_sec,
        "end_sec": end_sec,
        "topic": segment.get("topic", ""),
    }

    ids = []
    embeddings = []
    documents = []
    metadatas = []

    # Text vector
    text_id = f"{video_id}_chunk_{segment_idx:02d}_text"
    ids.append(text_id)
    embeddings.append(text_vector)
    documents.append(segment.get("summary", ""))
    metadatas.append({**base_metadata, "type": "text"})

    # Visual vectors
    for vis_idx, (img_path, vis_vec) in enumerate(visual_vectors):
        vis_id = f"{video_id}_chunk_{segment_idx:02d}_vis_{vis_idx:02d}"
        ids.append(vis_id)
        embeddings.append(vis_vec)
        documents.append(f"[Visual] {segment.get('topic', '')} - keyframe {vis_idx}")
        metadatas.append({**base_metadata, "type": "visual", "img_path": img_path})

    collection.upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)
    return len(ids)


# ---------------------------------------------------------------------------
# Main ingestion pipeline
# ---------------------------------------------------------------------------

def ingest_video(
    client: genai.Client,
    video_url: str,
    query: str,
    skill_name: str,
    keyframe_dir: Path,
    download_dir: Path,
) -> dict:
    """Ingest a single YouTube video into the multimodal RAG index.

    Parameters
    ----------
    client : genai.Client
        Gemini API client.
    video_url : str
        YouTube URL.
    query : str
        The user's search query (guides semantic chunking).
    skill_name : str
        Skill name for ChromaDB storage path.
    keyframe_dir : Path
        Directory to store extracted key frames.
    download_dir : Path
        Directory for downloaded video files.

    Returns
    -------
    dict
        Ingestion summary with segment count and stored document count.
    """
    video_id = _extract_video_id(video_url)
    print(f"Processing video: {video_id} ({video_url})")

    # Step 1: Download video
    print("  Downloading video...")
    video_path = _download_youtube(video_url, download_dir)
    print(f"  Downloaded to: {video_path}")

    # Step 2: Upload to Gemini File API
    print("  Uploading to Gemini File API...")
    uploaded_file = client.files.upload(file=video_path)

    # Wait for processing to complete
    while uploaded_file.state.name == "PROCESSING":
        time.sleep(5)
        uploaded_file = client.files.get(name=uploaded_file.name)

    if uploaded_file.state.name == "FAILED":
        raise RuntimeError(f"Gemini file processing failed for {video_id}")

    print(f"  File ready: {uploaded_file.name}")

    # Step 3: Semantic chunking via Gemini
    print("  Analyzing video (semantic chunking)...")
    analysis = _analyze_video(client, uploaded_file, query)
    segments = analysis.get("segments", [])
    print(f"  Found {len(segments)} semantic segments.")

    if not segments:
        print("  WARN: No segments identified. Skipping.", file=sys.stderr)
        return {"video_id": video_id, "segments": 0, "stored": 0}

    # Step 4 & 5: Extract key frames + embed
    collection = _get_chroma_collection(skill_name)
    total_stored = 0

    for seg_idx, segment in enumerate(segments):
        topic = segment.get("topic", f"segment_{seg_idx}")
        print(f"  Processing segment {seg_idx}: {topic}")

        # Embed text summary
        summary = segment.get("summary", "")
        text_vectors = _embed_texts(client, [summary])
        text_vector = text_vectors[0] if text_vectors else []

        if not text_vector:
            print(f"    WARN: Text embedding failed for segment {seg_idx}", file=sys.stderr)
            continue

        # Extract and embed key frames
        visual_vectors: list[tuple[str, list[float]]] = []
        for km_idx, ts in enumerate(segment.get("key_moments_sec", [])):
            frame_name = f"{video_id}_t{int(ts)}.jpg"
            frame_path = keyframe_dir / frame_name
            _extract_keyframe(video_path, ts, frame_path)

            if frame_path.exists():
                vis_vec = _embed_image(client, frame_path)
                if vis_vec:
                    visual_vectors.append((str(frame_path), vis_vec))

        # Store in ChromaDB
        stored = _store_segment(
            collection,
            video_id=video_id,
            video_url=video_url,
            segment=segment,
            segment_idx=seg_idx,
            text_vector=text_vector,
            visual_vectors=visual_vectors,
        )
        total_stored += stored
        print(f"    Stored {stored} vectors (1 text + {len(visual_vectors)} visual)")

    # Clean up uploaded file
    try:
        client.files.delete(name=uploaded_file.name)
    except Exception:
        pass

    return {
        "video_id": video_id,
        "video_title": analysis.get("video_title", ""),
        "segments": len(segments),
        "stored": total_stored,
    }


def ingest_videos(
    urls: list[str],
    query: str,
    skill_name: str,
) -> list[dict]:
    """Ingest multiple YouTube videos into the RAG index.

    Parameters
    ----------
    urls : list[str]
        List of YouTube URLs.
    query : str
        Search query for context-aware chunking.
    skill_name : str
        Skill name for storage paths.

    Returns
    -------
    list[dict]
        Ingestion summaries for each video.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("ERROR: GEMINI_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)

    base_dir = Path(f"skills/{skill_name}/tmp")
    keyframe_dir = base_dir / "keyframes"
    download_dir = base_dir / "videos"
    keyframe_dir.mkdir(parents=True, exist_ok=True)
    download_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for url in urls:
        url = url.strip()
        if not url:
            continue
        try:
            result = ingest_video(
                client, url, query, skill_name, keyframe_dir, download_dir
            )
            results.append(result)
        except Exception as exc:
            print(f"ERROR: Failed to process {url}: {exc}", file=sys.stderr)
            results.append({"video_url": url, "error": str(exc)})

    return results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 4:
        print(
            "Usage: gemini_youtube_search.py <skill_name> <youtube_url(s)> <query>\n"
            "\n"
            "  skill_name     — name of the skill (storage namespace)\n"
            "  youtube_url(s) — single URL or comma-separated URLs\n"
            "  query          — search query for context-aware video analysis",
            file=sys.stderr,
        )
        sys.exit(1)

    skill_name = sys.argv[1]
    urls = sys.argv[2].split(",")
    query = " ".join(sys.argv[3:])

    results = ingest_videos(urls, query, skill_name)

    # Print summary
    print(f"\n=== Video RAG Ingestion Summary ===")
    print(f"Skill: {skill_name}")
    print(f"Query: {query}")
    print(f"Videos processed: {len(results)}")
    for r in results:
        if "error" in r:
            print(f"  FAIL: {r.get('video_url', '?')} — {r['error']}")
        else:
            print(
                f"  OK: {r.get('video_id', '?')} — "
                f"{r.get('segments', 0)} segments, "
                f"{r.get('stored', 0)} vectors stored"
            )

    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
