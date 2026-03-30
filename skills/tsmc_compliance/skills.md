# Skill: TSMC Compliance Temporal Analyzer

Analyze TSMC's 2026 export compliance status by combining web search with temporal knowledge graph construction and inference, producing a decision-ready compliance report.

## Goal

Search for US export control regulation changes from 2023 to 2026, build a temporal knowledge graph capturing the evolution of rules (Entity List, CHIPS Act Guardrails, C79/VEU exemptions, TPP/PD thresholds), and generate a structured compliance report that determines what TSMC can and cannot ship to specific customers as of 2026/03/30.

## Environment

The following environment variables must be set before execution:
- `ANTHROPIC_API_KEY` — API key for Claude web search (`scripts/web_search.py`).
- `OPENAI_API_KEY` — API key for temporal graph construction and inference LLM calls.
- `OPENAI_API_BASE` — (Optional) Custom OpenAI-compatible API base URL.
- `TEMPORAL_GRAPH_MODEL` — (Optional) Model name for graph LLM calls (default: `gpt-oss`).

These are injected automatically into `safe_py_runner` from the host environment.

## Path Convention

All paths in this skill are **relative to the project root** (the repository root
where `pyproject.toml` lives). Both `safe_cli_executor` and `safe_py_runner`
execute commands with `cwd = PROJECT_ROOT`, so every path must start from there.

- **Python script paths** (for `safe_py_runner`): use forward slashes.
  Example: `scripts/web_search.py`

## Artifact Directory

All intermediate and final artifacts are saved to `skills/tsmc_compliance/tmp/`:
- `search_results_entity_list.txt` — Raw web search results for Entity List updates
- `search_results_chips_act.txt` — Raw web search results for CHIPS Act Guardrails
- `search_results_c79_veu.txt` — Raw web search results for C79/VEU license policies
- `search_results_tpp_pd.txt` — Raw web search results for TPP/PD threshold changes
- `search_results_supplemental.txt` — Additional search results from follow-up queries
- `consolidated_search_data.txt` — All search results merged into a single document
- `temporal_graph.json` — Temporal knowledge graph (NetworkX node-link format)
- `node_set.json` — Node-set index for graph entry-point search
- `inference_entity_list.json` — Graph inference results for Entity List analysis
- `inference_thresholds.json` — Graph inference results for TPP/PD threshold analysis
- `inference_exemptions.json` — Graph inference results for C79/VEU exemption analysis
- `compliance_report.md` — Final 2026 compliance decision report

## Available Resources

- **Script**: `scripts/web_search.py` — Claude-powered web search.
  Takes args: `[query]`. Returns search-informed response to stdout.

- **Script**: `scripts/create_temporal_graph.py` — Build temporal knowledge graph from text.
  Takes args: `[skill_name, input_path, --source-type web_search, --reference-date YYYY-MM-DD]`.
  Saves `temporal_graph.json` and `node_set.json` to `skills/<skill_name>/tmp/`.

- **Script**: `scripts/inference_with_graph.py` — Query the temporal knowledge graph.
  Takes args: `[skill_name, query, --as-of YYYY-MM-DD, --entry-nodes node1,node2, --max-hops N, --top-k N]`.
  Returns synthesized answer and structured inference results.

- **Script**: `scripts/write_file.py` — Writes content from stdin to a file.
  Takes args: `[file_path]`. Reads content from `stdin_text`.

- **Script**: `scripts/write_txt.py` — Writes text content to a file.
  Takes args: `[file_path, text_content]`.

- **Script**: `scripts/write_md.py` — Writes markdown content to a file.
  Takes args: `[file_path, md_content]`.

- **Script**: `scripts/read.py` — Reads a file and prints contents to stdout.
  Takes args: `[file_path]`.

- **Reference**: `skills/tsmc_compliance/reference/search_queries.json` — Predefined
  search query templates organized by analysis layer and time period.

- **Reference**: `skills/tsmc_compliance/reference/report_template.md` — Markdown
  template for the final compliance report with section placeholders.

## Analysis Layers

The skill analyzes four dimensions, each extracted from search results and encoded into the temporal graph:

1. **Computing Power (算力)**: TPP (Total Processing Performance) numerical threshold changes across 2023-2026. Track how BIS shifted from flat TPP caps to density-based limits.

2. **Performance Density (效能密度)**: PD (Performance Density) limit thresholds introduced in later rule updates. Identify when PD was first introduced and its evolution.

3. **Process Node (製程技術)**: Technology node restrictions — FinFET, GAAFET, 16nm, 7nm, 3nm. Track which nodes are controlled and how TSMC's fab capabilities intersect with restrictions.

4. **End-User (終端用戶)**: Entity List entries and associated enterprises. Track additions/removals of entities such as Huawei, Biren, Moore Threads, SMIC, and their subsidiaries.

## What Needs to Happen

1. **Lateral web search — regulation categories**: Perform broad web searches across the four analysis layers to capture the current regulatory landscape. Execute the following search queries (Optimizer runs multiple queries, Evaluator verifies coverage):

   - Entity List: `"BIS Entity List additions China semiconductors 2024 2025 2026"`, `"Huawei TSMC export license status 2025 2026"`
   - CHIPS Act: `"CHIPS Act guardrails China expansion restrictions TSMC 2025 2026"`, `"CHIPS Act fab investment China restrictions 2025"`
   - C79/VEU: `"TSMC Nanjing fab C79 license renewal 2025 2026"`, `"Validated End-User VEU program semiconductor 2025 2026"`
   - TPP/PD: `"BIS TPP Total Processing Performance threshold update 2024 2025"`, `"performance density PD limit semiconductor export control 2025 2026"`, `"Nvidia AI chip export control TPP thresholds 2025"`

   Save each category's results to its corresponding file in `skills/tsmc_compliance/tmp/`. The Evaluator must confirm that results span all four layers and cover the 2023-2026 time range.

2. **Longitudinal web search — temporal tracking**: For any regulatory changes identified in Step 1 that show significant evolution (e.g., TPP threshold changes, Entity List additions), perform targeted follow-up searches to trace the full 2023 → 2024 → 2025 → 2026 timeline:

   - `"EAR 744.23 advanced computing rule history 2023 2024 2025"`
   - `"October 2022 October 2023 January 2025 semiconductor export control updates"`
   - `"TSMC China license C79 status 2024 2025 2026"`

   Append supplemental findings to `search_results_supplemental.txt`. The Evaluator checks that the time series is continuous (no year gaps).

3. **Consolidate search data**: Merge all search result files into a single `consolidated_search_data.txt` document. Each section should be clearly labeled with its source query and retrieval date. This consolidated file becomes the input for temporal graph construction.

4. **Build temporal knowledge graph**: Run `create_temporal_graph.py` with:
   - `skill_name = tsmc_compliance`
   - `input_path = skills/tsmc_compliance/tmp/consolidated_search_data.txt`
   - `--source-type web_search`
   - `--reference-date 2026-03-30`

   The graph encodes:
   - **Nodes**: Regulations (EAR 744.23), Entities (Huawei, TSMC), Parameters (TPP 600, PD threshold), Dates (2024-10-17).
   - **Edges**: "Affects", "Exempts", "Supersedes" with `valid_from` / `valid_to` temporal attributes.

   The Evaluator verifies the graph has nodes from all four analysis layers and edges with temporal metadata.

5. **Graph-based compliance inference**: Run three targeted inference queries against the temporal graph, each filtered to `--as-of 2026-03-30`:

   a. **Entity List query**: `"Which Chinese companies are currently on the Entity List and what restrictions apply to TSMC shipments to them?"` → Save to `inference_entity_list.json`.

   b. **Threshold query**: `"What are the current TPP and Performance Density thresholds for semiconductor exports to China as of 2026?"` → Save to `inference_thresholds.json`.

   c. **Exemption query**: `"What is the current status of TSMC Nanjing C79 license and VEU exemptions for mature process nodes?"` → Save to `inference_exemptions.json`.

   The Evaluator checks that each inference result references specific graph edges with temporal validity dates.

6. **Compose compliance report**: Using the inference results from Step 5 and the report template from `skills/tsmc_compliance/reference/report_template.md`, compose a comprehensive 2026 compliance report containing:

   - **Source List (禁令來源清單)**: All referenced BIS, Federal Register, Commerce Department URLs with effective dates.
   - **Regulation Comparison Table (新舊禁令對比表)**: 2023-2026 evolution of computing power thresholds (TPP 4800 → density-based limits).
   - **2026 Whitelist/Blacklist (銷售白名單與黑名單)**:
     - **Can sell to**: Customers with annual C79 licenses for mature process nodes, high-end AI customers passing 2026 tariff review.
     - **Cannot sell to**: Entity List entities without case-by-case licenses, Chinese military-affiliated enterprises for sub-7nm supercomputing.
   - **Exemption Status (豁免現狀)**: TSMC Nanjing fab annual license validity period and restriction scope.

   Every "can sell / cannot sell" conclusion must cite the specific graph source node (regulation + effective date). Write the report to `skills/tsmc_compliance/tmp/compliance_report.md`.

7. **Final verification**: Read the completed report and verify:
   - Every conclusion traces back to a cited regulation with a date.
   - The regulation comparison table has entries for each year (2023, 2024, 2025, 2026).
   - No conclusions are based on expired regulations (check `t_expired` in graph).
   - Whitelist/blacklist entries are mutually exclusive and exhaustive for known entities.

## Success Cases

## Failure Cases
