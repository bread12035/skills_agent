"""Inference with Temporal Graph — retrieve relevant information from a
temporal knowledge graph to assist agent tasks.

Given a question and a node-set entry point, this script:
  1. Loads the temporal graph from skills/<skill_name>/tmp/
  2. Finds the best entry-point nodes matching the query
  3. Performs multi-hop traversal with temporal filtering
  4. Returns context-rich subgraph information for the agent

Usage (called via safe_py_runner):
    safe_py_runner(
        script_name="scripts/inference_with_graph.py",
        args=["<skill_name>", "<query>"],
    )

    # With temporal filter:
    safe_py_runner(
        script_name="scripts/inference_with_graph.py",
        args=["<skill_name>", "<query>", "--as-of", "2024-06-01"],
    )

    # With specific entry nodes:
    safe_py_runner(
        script_name="scripts/inference_with_graph.py",
        args=["<skill_name>", "<query>", "--entry-nodes", "Apple Inc.,Tim Cook"],
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
    """Return an OpenAI-compatible client for inference tasks."""
    from openai import OpenAI

    kwargs: dict[str, Any] = {}
    if _API_BASE:
        kwargs["base_url"] = _API_BASE
    if _API_KEY:
        kwargs["api_key"] = _API_KEY
    else:
        kwargs["api_key"] = "placeholder"
    return OpenAI(**kwargs)


def _llm_call(client, system_prompt: str, user_prompt: str) -> str:
    """Call the LLM and return the response text."""
    response = client.chat.completions.create(
        model=_MODEL,
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    return response.choices[0].message.content.strip()


def _llm_json_call(client, system_prompt: str, user_prompt: str) -> Any:
    """Call the LLM and parse the response as JSON."""
    text = _llm_call(client, system_prompt, user_prompt)
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return json.loads(text)


# ---------------------------------------------------------------------------
# Graph loading
# ---------------------------------------------------------------------------

def load_graph(skill_name: str) -> tuple[nx.DiGraph, list[dict]]:
    """Load the temporal graph and node set from disk.

    Parameters
    ----------
    skill_name : str
        The skill whose tmp/ directory contains the graph.

    Returns
    -------
    tuple[nx.DiGraph, list[dict]]
        The graph and the node-set index.
    """
    tmp_dir = Path(f"skills/{skill_name}/tmp")
    graph_path = tmp_dir / "temporal_graph.json"
    node_set_path = tmp_dir / "node_set.json"

    if not graph_path.exists():
        print(f"ERROR: Graph not found at {graph_path}", file=sys.stderr)
        sys.exit(1)

    graph_data = json.loads(graph_path.read_text(encoding="utf-8"))
    G = json_graph.node_link_graph(graph_data)

    node_set: list[dict] = []
    if node_set_path.exists():
        node_set = json.loads(node_set_path.read_text(encoding="utf-8"))

    return G, node_set


# ---------------------------------------------------------------------------
# Entry-point selection
# ---------------------------------------------------------------------------

_ENTRY_POINT_SYSTEM = """\
You are a graph-search assistant. Given a user query and a list of graph nodes
with their metadata, select the most relevant entry-point nodes to begin
traversal.

Return a JSON array of node names (strings), ordered by relevance (most
relevant first). Select 1-5 nodes.

Return ONLY valid JSON, no markdown fences."""


def find_entry_nodes(
    client,
    query: str,
    node_set: list[dict],
    explicit_nodes: list[str] | None = None,
) -> list[str]:
    """Identify the best entry-point nodes for a query.

    If explicit_nodes are provided, validate and return them.
    Otherwise, use the LLM to pick from the node set.
    """
    if explicit_nodes:
        # Validate against actual node set
        valid_names = {n["node"] for n in node_set}
        validated = [n for n in explicit_nodes if n in valid_names]
        if validated:
            return validated
        # Fuzzy match: case-insensitive
        lower_map = {n["node"].lower(): n["node"] for n in node_set}
        validated = [lower_map[n.lower()] for n in explicit_nodes if n.lower() in lower_map]
        if validated:
            return validated
        print(f"WARN: Explicit nodes not found in graph. Falling back to LLM selection.", file=sys.stderr)

    # Truncate node set for prompt (top 50 by degree)
    summary = [
        {
            "node": n["node"],
            "degree": n["degree"],
            "predicates": n.get("out_predicates", [])[:5] + n.get("in_predicates", [])[:5],
            "first_seen": n.get("first_seen"),
            "last_seen": n.get("last_seen"),
        }
        for n in node_set[:50]
    ]
    prompt = f"Query: {query}\n\nAvailable nodes:\n{json.dumps(summary, indent=2)}"
    try:
        result = _llm_json_call(client, _ENTRY_POINT_SYSTEM, prompt)
        if isinstance(result, list):
            return [str(r) for r in result[:5]]
    except (json.JSONDecodeError, Exception) as exc:
        print(f"WARN: Entry-point selection failed: {exc}", file=sys.stderr)

    # Fallback: return top-degree nodes
    return [n["node"] for n in node_set[:3]]


# ---------------------------------------------------------------------------
# Temporal filtering
# ---------------------------------------------------------------------------

def _is_temporally_valid(edge_data: dict, as_of: str | None) -> bool:
    """Check if an edge is valid at the given date.

    An edge is valid if:
      t_created <= as_of AND (t_expired IS NULL OR t_expired > as_of)
    """
    if as_of is None:
        return True
    t_created = edge_data.get("t_created")
    t_expired = edge_data.get("t_expired")
    if t_created and t_created > as_of:
        return False
    if t_expired and t_expired <= as_of:
        return False
    return True


# ---------------------------------------------------------------------------
# Multi-hop traversal
# ---------------------------------------------------------------------------

def traverse_from_nodes(
    G: nx.DiGraph,
    entry_nodes: list[str],
    max_hops: int = 2,
    as_of: str | None = None,
) -> list[dict]:
    """Perform multi-hop traversal from entry nodes with temporal filtering.

    Returns a list of statement records reachable within max_hops,
    filtered for temporal validity.
    """
    visited_edges: set[tuple] = set()
    results: list[dict] = []
    frontier = set(entry_nodes)
    all_visited_nodes: set[str] = set(entry_nodes)

    for hop in range(max_hops):
        next_frontier: set[str] = set()
        for node in frontier:
            if node not in G:
                continue
            # Outgoing edges
            for _, target, data in G.out_edges(node, data=True):
                edge_key = (node, target, data.get("key", 0))
                if edge_key in visited_edges:
                    continue
                visited_edges.add(edge_key)
                if not _is_temporally_valid(data, as_of):
                    continue
                results.append({
                    "hop": hop + 1,
                    "source": node,
                    "target": target,
                    "predicate": data.get("predicate", ""),
                    "statement": data.get("statement", ""),
                    "statement_type": data.get("statement_type", ""),
                    "temporal_type": data.get("temporal_type", ""),
                    "t_created": data.get("t_created"),
                    "t_expired": data.get("t_expired"),
                    "source_type": data.get("source_type", ""),
                })
                if target not in all_visited_nodes:
                    next_frontier.add(target)
                    all_visited_nodes.add(target)

            # Incoming edges
            for source, _, data in G.in_edges(node, data=True):
                edge_key = (source, node, data.get("key", 0))
                if edge_key in visited_edges:
                    continue
                visited_edges.add(edge_key)
                if not _is_temporally_valid(data, as_of):
                    continue
                results.append({
                    "hop": hop + 1,
                    "source": source,
                    "target": node,
                    "predicate": data.get("predicate", ""),
                    "statement": data.get("statement", ""),
                    "statement_type": data.get("statement_type", ""),
                    "temporal_type": data.get("temporal_type", ""),
                    "t_created": data.get("t_created"),
                    "t_expired": data.get("t_expired"),
                    "source_type": data.get("source_type", ""),
                })
                if source not in all_visited_nodes:
                    next_frontier.add(source)
                    all_visited_nodes.add(source)

        frontier = next_frontier

    return results


# ---------------------------------------------------------------------------
# Relevance ranking
# ---------------------------------------------------------------------------

_RANK_SYSTEM = """\
You are a relevance-ranking assistant. Given a user query and a list of
graph-retrieved statements, rank them by relevance to the query.

Return a JSON array of indices (0-based) sorted by relevance (most relevant
first). Only include indices of statements that are actually relevant.

Return ONLY valid JSON, no markdown fences."""


def rank_results(
    client, query: str, results: list[dict], top_k: int = 15
) -> list[dict]:
    """Re-rank traversal results by relevance to the query."""
    if len(results) <= top_k:
        return results

    # Prepare condensed list for ranking
    stmts_for_ranking = [
        {"idx": i, "statement": r["statement"], "predicate": r["predicate"]}
        for i, r in enumerate(results)
    ]
    prompt = f"Query: {query}\n\nStatements:\n{json.dumps(stmts_for_ranking, indent=2)}"
    try:
        ranked_indices = _llm_json_call(client, _RANK_SYSTEM, prompt)
        if isinstance(ranked_indices, list):
            ranked = []
            for idx in ranked_indices[:top_k]:
                if isinstance(idx, int) and 0 <= idx < len(results):
                    ranked.append(results[idx])
            return ranked if ranked else results[:top_k]
    except (json.JSONDecodeError, Exception) as exc:
        print(f"WARN: Ranking failed, returning unranked: {exc}", file=sys.stderr)

    return results[:top_k]


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------

_SYNTHESIZE_SYSTEM = """\
You are a knowledge-graph-grounded Q&A assistant.
Given a user query and a set of temporally-filtered graph statements,
synthesize a comprehensive answer. Cite specific facts and their temporal
validity. If information conflicts, note which is more recent.

Be precise and factual. Only use information from the provided statements."""


def synthesize_answer(
    client, query: str, ranked_results: list[dict], as_of: str | None = None
) -> str:
    """Generate a synthesized answer from ranked graph results."""
    if not ranked_results:
        return "No relevant information found in the temporal graph."

    context_lines = []
    for r in ranked_results:
        validity = f"(valid from {r['t_created']}"
        if r.get("t_expired"):
            validity += f" to {r['t_expired']}"
        validity += ")"
        context_lines.append(
            f"- [{r['predicate']}] {r['source']} → {r['target']}: "
            f"{r['statement']} {validity} [{r['statement_type']}/{r['temporal_type']}]"
        )

    context = "\n".join(context_lines)
    temporal_note = f"\nTemporal filter: as of {as_of}" if as_of else ""
    prompt = f"Query: {query}{temporal_note}\n\nGraph-retrieved facts:\n{context}"

    return _llm_call(client, _SYNTHESIZE_SYSTEM, prompt)


# ---------------------------------------------------------------------------
# Main inference pipeline
# ---------------------------------------------------------------------------

def inference(
    skill_name: str,
    query: str,
    entry_nodes: list[str] | None = None,
    as_of: str | None = None,
    max_hops: int = 2,
    top_k: int = 15,
    synthesize: bool = True,
) -> dict[str, Any]:
    """Run inference on a temporal knowledge graph.

    Parameters
    ----------
    skill_name : str
        Skill whose tmp/ contains the graph.
    query : str
        The user's question.
    entry_nodes : list[str] | None
        Explicit entry-point nodes. Auto-selected if None.
    as_of : str | None
        Temporal filter date (YYYY-MM-DD). None = no filter.
    max_hops : int
        Maximum traversal depth.
    top_k : int
        Number of top results to return.
    synthesize : bool
        Whether to generate a synthesized answer.

    Returns
    -------
    dict
        Keys: entry_nodes, results, answer (if synthesize=True), metadata.
    """
    client = _get_llm_client()
    G, node_set = load_graph(skill_name)
    print(f"Loaded graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges.")

    # Step 1: Find entry points
    selected_nodes = find_entry_nodes(client, query, node_set, entry_nodes)
    print(f"Entry nodes: {selected_nodes}")

    # Step 2: Multi-hop traversal with temporal filter
    results = traverse_from_nodes(G, selected_nodes, max_hops=max_hops, as_of=as_of)
    print(f"Traversal returned {len(results)} statements.")

    # Step 3: Rank by relevance
    ranked = rank_results(client, query, results, top_k=top_k)
    print(f"Top {len(ranked)} relevant statements selected.")

    output: dict[str, Any] = {
        "entry_nodes": selected_nodes,
        "results": ranked,
        "metadata": {
            "skill_name": skill_name,
            "query": query,
            "as_of": as_of,
            "max_hops": max_hops,
            "total_traversed": len(results),
            "total_ranked": len(ranked),
            "graph_nodes": G.number_of_nodes(),
            "graph_edges": G.number_of_edges(),
        },
    }

    # Step 4: Synthesize answer
    if synthesize:
        answer = synthesize_answer(client, query, ranked, as_of=as_of)
        output["answer"] = answer

    return output


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 3:
        print(
            "Usage: inference_with_graph.py <skill_name> <query> "
            "[--entry-nodes node1,node2] "
            "[--as-of YYYY-MM-DD] "
            "[--max-hops N] "
            "[--top-k N] "
            "[--no-synthesize]",
            file=sys.stderr,
        )
        sys.exit(1)

    skill_name = sys.argv[1]
    query = sys.argv[2]

    # Parse optional flags
    entry_nodes: list[str] | None = None
    as_of: str | None = None
    max_hops = 2
    top_k = 15
    synthesize = True

    i = 3
    while i < len(sys.argv):
        if sys.argv[i] == "--entry-nodes" and i + 1 < len(sys.argv):
            entry_nodes = [n.strip() for n in sys.argv[i + 1].split(",")]
            i += 2
        elif sys.argv[i] == "--as-of" and i + 1 < len(sys.argv):
            as_of = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--max-hops" and i + 1 < len(sys.argv):
            max_hops = int(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == "--top-k" and i + 1 < len(sys.argv):
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
        entry_nodes=entry_nodes,
        as_of=as_of,
        max_hops=max_hops,
        top_k=top_k,
        synthesize=synthesize,
    )

    # Print answer if synthesized
    if "answer" in result:
        print(f"\n=== Answer ===\n{result['answer']}")

    # Print structured output
    print(f"\n=== Inference Result ===")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
