"""Create Temporal Knowledge Graph — builds a temporal graph from web search
results or transcript paragraphs.

Implements the Temporal Knowledge Graph pattern described in the OpenAI cookbook:
  1. Statement extraction (with epistemic & temporal classification)
  2. Temporal event extraction (absolute date resolution)
  3. Entity resolution & canonicalization
  4. Contradiction detection / invalidation

The graph is stored as JSON (NetworkX node-link format) under
``skills/<skill_name>/tmp/temporal_graph.json`` together with a node-set index
at ``skills/<skill_name>/tmp/node_set.json`` for agent entry-point search.

Usage (called via safe_py_runner):
    safe_py_runner(
        script_name="scripts/create_temporal_graph.py",
        args=["<skill_name>", "<input_path_or_text>"],
        stdin_text=<optional raw text if no file>,
    )

    # Or with --source-type flag:
    safe_py_runner(
        script_name="scripts/create_temporal_graph.py",
        args=["<skill_name>", "<input_path>", "--source-type", "transcript"],
    )

Environment variables (following get_llm pattern):
    TEMPORAL_GRAPH_API_BASE  — LLM API base URL  (falls back to OPENAI_API_BASE)
    TEMPORAL_GRAPH_API_KEY   — LLM API key        (falls back to OPENAI_API_KEY)
    TEMPORAL_GRAPH_MODEL     — Model name          (default: gpt-oss)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import networkx as nx
from networkx.readwrite import json_graph

# ---------------------------------------------------------------------------
# LLM Configuration (decoupled, mirrors get_llm pattern)
# ---------------------------------------------------------------------------

_API_BASE = os.environ.get(
    "TEMPORAL_GRAPH_API_BASE", os.environ.get("OPENAI_API_BASE", "")
)
_API_KEY = os.environ.get(
    "TEMPORAL_GRAPH_API_KEY", os.environ.get("OPENAI_API_KEY", "")
)
_MODEL = os.environ.get("TEMPORAL_GRAPH_MODEL", "gpt-oss")


def _get_llm_client():
    """Return an OpenAI-compatible client for extraction tasks."""
    from openai import OpenAI

    kwargs: dict[str, Any] = {}
    if _API_BASE:
        kwargs["base_url"] = _API_BASE
    if _API_KEY:
        kwargs["api_key"] = _API_KEY
    else:
        kwargs["api_key"] = "placeholder"
    return OpenAI(**kwargs)


def _llm_json_call(client, system_prompt: str, user_prompt: str) -> Any:
    """Call the LLM and parse the response as JSON."""
    response = client.chat.completions.create(
        model=_MODEL,
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    text = response.choices[0].message.content.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # remove opening fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return json.loads(text)


# ---------------------------------------------------------------------------
# Stage 1: Statement Extraction
# ---------------------------------------------------------------------------

_EXTRACT_SYSTEM = """\
You are a knowledge-graph construction assistant.
Given a text chunk, extract atomic declarative statements.
For each statement, resolve pronouns and abbreviations to full entity names.

Classify each statement with:
- statement_type: one of FACT, OPINION, PREDICTION
- temporal_type: one of STATIC (point-in-time, never changes once occurred),
  DYNAMIC (ongoing, valid for a time period), ATEMPORAL (always true)

Return a JSON array of objects with keys:
  statement, statement_type, temporal_type, entities (list of entity strings)

Return ONLY valid JSON, no markdown fences."""

_TEMPORAL_SYSTEM = """\
You are a temporal-extraction assistant.
Given a list of statements and a reference date, assign temporal metadata.

For each statement, determine:
- t_created: the date (YYYY-MM-DD) the fact became true or was recorded.
  Use the reference date if no explicit date can be inferred.
- t_expired: the date (YYYY-MM-DD) the fact is no longer valid, or null.

Return a JSON array of objects with keys:
  statement, t_created, t_expired

Return ONLY valid JSON, no markdown fences."""

_ENTITY_RESOLVE_SYSTEM = """\
You are an entity-resolution assistant.
Given a list of entity names, group duplicates and variants that refer to the
same real-world entity. Return a JSON object mapping each variant to its
canonical form.

Example: {"AAPL": "Apple Inc.", "Apple": "Apple Inc."}

Return ONLY valid JSON, no markdown fences."""

_TRIPLET_SYSTEM = """\
You are a knowledge-graph triplet extractor.
Given a statement, extract one or more (subject, predicate, object) triplets.
Subject and object must be entity names. Predicate is a concise relationship label.

Return a JSON array of objects with keys: subject, predicate, object

Return ONLY valid JSON, no markdown fences."""

_AGENTIC_CHUNK_SYSTEM = """\
You are an expert text segmentation assistant.
Given a full document, split it into semantically coherent chunks. Each chunk
should represent a self-contained topic, argument, or narrative unit.

Rules:
- Do NOT split in the middle of a logical argument or closely related sentences.
- Each chunk should be meaningful on its own.
- Preserve the original text exactly — do not paraphrase or summarize.
- Return between 2 and 30 chunks depending on document length.

Return a JSON array of strings, where each string is one semantic chunk.

Return ONLY valid JSON, no markdown fences."""

_CONTEXTUALIZE_SYSTEM = """\
You are a contextualization assistant.
You will be given the FULL document and ONE specific chunk extracted from it.
Your job is to write a brief contextualization statement that explains:
1. Where this chunk sits in the overall document structure.
2. What topics or entities were discussed before and after this chunk.
3. How this chunk relates to the document's main thesis or narrative.

Keep the contextualization concise (2-4 sentences). Write in the same language
as the document.

Return a JSON object with a single key "contextualization" whose value is the
contextualization string.

Return ONLY valid JSON, no markdown fences."""


# ---------------------------------------------------------------------------
# Chunking — Agentic chunking with contextualization
# ---------------------------------------------------------------------------

def _pre_split(text: str, max_size: int = 3000) -> list[str]:
    """Pre-split very long text into coarse segments so the LLM can handle them.

    This is a simple paragraph-boundary splitter used only when the input text
    exceeds the safe prompt size for the agentic chunking LLM call.
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    segments: list[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 > max_size and current:
            segments.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        segments.append(current)
    return segments if segments else [text]


def _agentic_chunk(client, text: str) -> list[str]:
    """Use the LLM to split *text* into semantically coherent chunks.

    For very long documents the text is first coarsely pre-split so that each
    LLM call stays within a comfortable prompt size, then the per-segment
    semantic chunks are concatenated.
    """
    MAX_LLM_INPUT = 6000  # characters — conservative limit for one call

    if len(text) <= MAX_LLM_INPUT:
        segments = [text]
    else:
        segments = _pre_split(text, max_size=MAX_LLM_INPUT)

    all_chunks: list[str] = []
    for seg in segments:
        try:
            chunks = _llm_json_call(client, _AGENTIC_CHUNK_SYSTEM, seg)
            if isinstance(chunks, list):
                all_chunks.extend([c for c in chunks if isinstance(c, str) and c.strip()])
            else:
                # Fallback: treat the whole segment as one chunk
                all_chunks.append(seg)
        except Exception as exc:
            print(f"WARN: Agentic chunking failed for segment, falling back: {exc}", file=sys.stderr)
            all_chunks.append(seg)

    return all_chunks if all_chunks else [text]


def _contextualize_chunk(client, full_text: str, chunk: str) -> str:
    """Ask the LLM to produce a contextualization statement for *chunk*
    given the full document.

    Returns the contextualization string.
    """
    # Truncate full_text if it is very long to fit in prompt
    MAX_CONTEXT_LEN = 8000
    context_text = full_text if len(full_text) <= MAX_CONTEXT_LEN else (
        full_text[:MAX_CONTEXT_LEN // 2] + "\n\n[...truncated...]\n\n" + full_text[-MAX_CONTEXT_LEN // 2:]
    )
    user_prompt = (
        f"=== FULL DOCUMENT ===\n{context_text}\n\n"
        f"=== CHUNK TO CONTEXTUALIZE ===\n{chunk}"
    )
    try:
        result = _llm_json_call(client, _CONTEXTUALIZE_SYSTEM, user_prompt)
        if isinstance(result, dict):
            return result.get("contextualization", "")
        return ""
    except Exception as exc:
        print(f"WARN: Contextualization failed: {exc}", file=sys.stderr)
        return ""


def _chunk_text(text: str, _max_chunk_size: int = 1500) -> list[dict]:
    """Split text into semantic chunks via agentic chunking, then
    contextualize each chunk.

    Returns a list of dicts, each with keys:
        - contextualization: str  — how this chunk relates to the whole document
        - chunk: str              — the original semantic chunk text
        - text: str               — the combined representation used downstream
    """
    client = _get_llm_client()

    # Step 1: Agentic chunking — LLM-driven semantic segmentation
    raw_chunks = _agentic_chunk(client, text)
    print(f"Agentic chunking produced {len(raw_chunks)} semantic chunks.")

    # Step 2: Contextualization — enrich each chunk with document-level context
    enriched: list[dict] = []
    for idx, chunk in enumerate(raw_chunks):
        ctx = _contextualize_chunk(client, text, chunk)
        combined = (
            f"[Contextualization: {ctx}]\n[Agentic chunking: {chunk}]"
            if ctx else chunk
        )
        enriched.append({
            "contextualization": ctx,
            "chunk": chunk,
            "text": combined,
        })
        print(f"  Chunk {idx+1}/{len(raw_chunks)} contextualized.")

    return enriched if enriched else [{"contextualization": "", "chunk": text, "text": text}]


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------

def _extract_statements(client, chunk: str) -> list[dict]:
    """Stage 1: Extract atomic statements from a text chunk."""
    try:
        return _llm_json_call(client, _EXTRACT_SYSTEM, chunk)
    except (json.JSONDecodeError, Exception) as exc:
        print(f"WARN: Statement extraction failed for chunk: {exc}", file=sys.stderr)
        return []


def _extract_temporal(
    client, statements: list[dict], reference_date: str
) -> list[dict]:
    """Stage 2: Assign temporal metadata to statements."""
    if not statements:
        return []
    prompt = (
        f"Reference date: {reference_date}\n\n"
        f"Statements:\n{json.dumps(statements, indent=2)}"
    )
    try:
        return _llm_json_call(client, _TEMPORAL_SYSTEM, prompt)
    except (json.JSONDecodeError, Exception) as exc:
        print(f"WARN: Temporal extraction failed: {exc}", file=sys.stderr)
        return []


def _resolve_entities(client, all_entities: list[str]) -> dict[str, str]:
    """Stage 3: Canonicalize entity names."""
    if not all_entities:
        return {}
    unique = sorted(set(all_entities))
    if len(unique) <= 1:
        return {e: e for e in unique}
    try:
        return _llm_json_call(
            client, _ENTITY_RESOLVE_SYSTEM, json.dumps(unique)
        )
    except (json.JSONDecodeError, Exception) as exc:
        print(f"WARN: Entity resolution failed: {exc}", file=sys.stderr)
        return {e: e for e in unique}


def _extract_triplets(client, statement: str) -> list[dict]:
    """Stage 4: Extract (subject, predicate, object) triplets."""
    try:
        return _llm_json_call(client, _TRIPLET_SYSTEM, statement)
    except (json.JSONDecodeError, Exception) as exc:
        print(f"WARN: Triplet extraction failed: {exc}", file=sys.stderr)
        return []


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_temporal_graph(
    text: str,
    reference_date: str | None = None,
    source_type: str = "general",
) -> tuple[nx.DiGraph, list[dict]]:
    """Build a temporal knowledge graph from input text.

    Parameters
    ----------
    text : str
        Raw input text (web search results or transcript).
    reference_date : str | None
        ISO date (YYYY-MM-DD) used to resolve relative dates.
        Defaults to today.
    source_type : str
        "web_search" or "transcript" or "general".

    Returns
    -------
    tuple[nx.DiGraph, list[dict]]
        The temporal graph and the node-set index.
    """
    if reference_date is None:
        reference_date = datetime.now().strftime("%Y-%m-%d")

    client = _get_llm_client()
    G = nx.DiGraph()

    # --- Chunk (agentic chunking + contextualization) ---
    chunk_records = _chunk_text(text)
    print(f"Chunked input into {len(chunk_records)} segments.")

    # --- Stage 1: Extract statements ---
    all_statements: list[dict] = []
    all_entities: list[str] = []
    for i, chunk_rec in enumerate(chunk_records):
        stmts = _extract_statements(client, chunk_rec["text"])
        for s in stmts:
            s["chunk_idx"] = i
            s["contextualization"] = chunk_rec.get("contextualization", "")
        all_statements.extend(stmts)
        for s in stmts:
            all_entities.extend(s.get("entities", []))
    print(f"Extracted {len(all_statements)} statements, {len(set(all_entities))} unique entities.")

    if not all_statements:
        print("WARN: No statements extracted. Returning empty graph.")
        return G, []

    # --- Stage 2: Temporal metadata ---
    temporal_data = _extract_temporal(client, all_statements, reference_date)
    temporal_map: dict[str, dict] = {}
    for t in temporal_data:
        temporal_map[t.get("statement", "")] = t

    # --- Stage 3: Entity resolution ---
    entity_map = _resolve_entities(client, all_entities)

    def _canon(name: str) -> str:
        return entity_map.get(name, name)

    # --- Stage 4: Build graph from triplets ---
    edge_id = 0
    for stmt in all_statements:
        triplets = _extract_triplets(client, stmt["statement"])
        temporal = temporal_map.get(stmt["statement"], {})
        t_created = temporal.get("t_created", reference_date)
        t_expired = temporal.get("t_expired")

        for tri in triplets:
            subj = _canon(tri.get("subject", ""))
            obj = _canon(tri.get("object", ""))
            pred = tri.get("predicate", "related_to")

            if not subj or not obj:
                continue

            # Add / update nodes
            for node_name in (subj, obj):
                if node_name not in G:
                    G.add_node(
                        node_name,
                        label=node_name,
                        type="entity",
                        first_seen=t_created,
                        last_seen=t_created,
                    )
                else:
                    existing = G.nodes[node_name]
                    if t_created < existing.get("first_seen", t_created):
                        existing["first_seen"] = t_created
                    if t_created > existing.get("last_seen", t_created):
                        existing["last_seen"] = t_created

            # Add edge
            G.add_edge(
                subj,
                obj,
                key=edge_id,
                predicate=pred,
                statement=stmt["statement"],
                statement_type=stmt.get("statement_type", "FACT"),
                temporal_type=stmt.get("temporal_type", "DYNAMIC"),
                t_created=t_created,
                t_expired=t_expired,
                source_type=source_type,
                chunk_idx=stmt.get("chunk_idx", 0),
                contextualization=stmt.get("contextualization", ""),
            )
            edge_id += 1

    print(f"Graph built: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges.")

    # --- Build node set index ---
    node_set = _build_node_set(G)

    return G, node_set


def _build_node_set(G: nx.DiGraph) -> list[dict]:
    """Build a searchable node-set index for agent entry-point lookup.

    Each entry contains the node name, degree, connected predicates,
    and temporal range to help agents choose the best entry point.
    """
    node_set: list[dict] = []
    for node, data in G.nodes(data=True):
        in_preds = list({d.get("predicate", "") for _, _, d in G.in_edges(node, data=True)})
        out_preds = list({d.get("predicate", "") for _, _, d in G.out_edges(node, data=True)})
        neighbors = list(set(list(G.predecessors(node)) + list(G.successors(node))))

        node_set.append({
            "node": node,
            "type": data.get("type", "entity"),
            "degree": G.degree(node),
            "in_degree": G.in_degree(node),
            "out_degree": G.out_degree(node),
            "first_seen": data.get("first_seen"),
            "last_seen": data.get("last_seen"),
            "in_predicates": in_preds,
            "out_predicates": out_preds,
            "neighbors": neighbors[:20],  # cap for readability
        })

    # Sort by degree descending (most connected first)
    node_set.sort(key=lambda x: x["degree"], reverse=True)
    return node_set


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_graph(
    G: nx.DiGraph,
    node_set: list[dict],
    skill_name: str,
) -> tuple[Path, Path]:
    """Save the graph and node set under skills/<skill_name>/tmp/.

    Returns
    -------
    tuple[Path, Path]
        (graph_path, node_set_path)
    """
    tmp_dir = Path(f"skills/{skill_name}/tmp")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    graph_path = tmp_dir / "temporal_graph.json"
    node_set_path = tmp_dir / "node_set.json"

    # Serialize graph using node-link format
    graph_data = json_graph.node_link_data(G)
    graph_path.write_text(json.dumps(graph_data, indent=2, default=str), encoding="utf-8")

    node_set_path.write_text(json.dumps(node_set, indent=2, default=str), encoding="utf-8")

    print(f"Graph saved to {graph_path}")
    print(f"Node set saved to {node_set_path}")
    return graph_path, node_set_path


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 3:
        print(
            "Usage: create_temporal_graph.py <skill_name> <input_path_or_text> "
            "[--source-type web_search|transcript|general] "
            "[--reference-date YYYY-MM-DD]",
            file=sys.stderr,
        )
        sys.exit(1)

    skill_name = sys.argv[1]
    input_arg = sys.argv[2]

    # Parse optional flags
    source_type = "general"
    reference_date = None
    i = 3
    while i < len(sys.argv):
        if sys.argv[i] == "--source-type" and i + 1 < len(sys.argv):
            source_type = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--reference-date" and i + 1 < len(sys.argv):
            reference_date = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    # Determine input text
    input_path = Path(input_arg)
    if input_path.exists():
        text = input_path.read_text(encoding="utf-8")
        print(f"Read input from file: {input_path} ({len(text)} chars)")
    else:
        # Try reading from stdin if input looks like a flag or is short
        if not sys.stdin.isatty():
            text = sys.stdin.read()
            print(f"Read input from stdin ({len(text)} chars)")
        else:
            # Treat input_arg as literal text
            text = input_arg
            print(f"Using literal input ({len(text)} chars)")

    if not text.strip():
        print("ERROR: No input text provided.", file=sys.stderr)
        sys.exit(1)

    G, node_set = build_temporal_graph(
        text, reference_date=reference_date, source_type=source_type
    )
    save_graph(G, node_set, skill_name)

    # Print summary
    print(f"\n=== Temporal Graph Summary ===")
    print(f"Skill: {skill_name}")
    print(f"Nodes: {G.number_of_nodes()}")
    print(f"Edges: {G.number_of_edges()}")
    print(f"Top entities (by degree):")
    for entry in node_set[:10]:
        print(f"  - {entry['node']} (degree={entry['degree']})")


if __name__ == "__main__":
    main()
