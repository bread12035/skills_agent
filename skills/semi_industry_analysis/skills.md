# Skill: Semiconductor Industry Technical & Competitive Analysis

Analyze the competitive landscape of the "Big Three" semiconductor manufacturers (TSMC, Intel, Samsung) regarding FinFET/GAA transitions, Co-Packaged Optics (CPO), and Through Glass Via (TGV) technology, combining hardcore technical knowledge with industry financial analysis.

## Goal

Produce a professional 3x3 Competitor Matrix report — (TSMC, Intel, Samsung) vs. (GAA Progress, CPO Layout, TGV Adoption) — by ingesting and reasoning over multimodal video content from two specialized sources:

- **Chuboy (曲博)**: Hardcore semiconductor technical knowledge (physics, structures, manufacturing hurdles).
- **MacroMicro (財經M平方)**: Industry layout & financial competitive analysis (CapEx, market share, supply chain, mass production timelines).

## Environment

The following environment variables must be set before execution:
- `GEMINI_API_KEY` — Google Gemini API key (required for `gemini_youtube_search.py` and `inference_with_multimodal_embedding.py`).
- `ANTHROPIC_API_KEY` — API key for Claude web search (`scripts/web_search.py`), used to discover YouTube video URLs.

These are injected automatically into `safe_py_runner` from the host environment.

## Path Convention

All paths in this skill are **relative to the project root** (the repository root
where `pyproject.toml` lives). Both `safe_cli_executor` and `safe_py_runner`
execute commands with `cwd = PROJECT_ROOT`, so every path must start from there.

- **Python script paths** (for `safe_py_runner`): use forward slashes.
  Example: `scripts/gemini_youtube_search.py`

## Artifact Directory

All intermediate and final artifacts are saved to `skills/semi_industry_analysis/tmp/`:
- `chuboy_video_urls.txt` — Discovered YouTube URLs from Chuboy channel
- `macromicro_video_urls.txt` — Discovered YouTube URLs from MacroMicro channel
- `rag_technical_gaa.txt` — RAG retrieval results for GAA technical parameters
- `rag_technical_cpo.txt` — RAG retrieval results for CPO technical parameters
- `rag_technical_tgv.txt` — RAG retrieval results for TGV technical parameters
- `rag_market_gaa.txt` — RAG retrieval results for GAA market dynamics
- `rag_market_cpo.txt` — RAG retrieval results for CPO market dynamics
- `rag_market_tgv.txt` — RAG retrieval results for TGV market dynamics
- `raw_technical_draft.md` — Raw RAG-based technical draft
- `competitor_matrix_report.md` — Final 3x3 competitive matrix report

## Available Resources

- **Script**: `scripts/web_search.py` — Claude-powered web search.
  Takes args: `[query]`. Returns search-informed response to stdout.
  Used to discover relevant YouTube video URLs from target channels.

- **Script**: `scripts/gemini_youtube_search.py` — Multimodal video RAG ingestion & indexing.
  Takes args: `[skill_name, youtube_url, query]`. Downloads video, extracts
  transcripts/frames, segments them, and stores embeddings into ChromaDB.
  Multiple URLs can be comma-separated.

- **Script**: `scripts/inference_with_multimodal_embedding.py` — Multimodal embedding inference.
  Takes args: `[skill_name, query, --top-k N, --no-synthesize]`.
  Performs RAG queries against the indexed video content and returns synthesized results.

- **Script**: `scripts/write_file.py` — Writes content from stdin to a file.
  Takes args: `[file_path]`. Reads content from `stdin_text`.

- **Script**: `scripts/write_txt.py` — Writes text content to a file.
  Takes args: `[file_path, text_content]`.

- **Script**: `scripts/write_md.py` — Writes markdown content to a file.
  Takes args: `[file_path, md_content]`.

- **Script**: `scripts/read.py` — Reads a file and prints contents to stdout.
  Takes args: `[file_path]`.

- **Reference**: `skills/semi_industry_analysis/reference/tech_terms.md` — Technical
  reference library for GAA, CPO, and TGV definitions, specifications, and benchmarks.

## What Needs to Happen

1. **Discover YouTube video URLs from target channels**: Use `scripts/web_search.py` to find relevant YouTube video URLs from the two target channels. Execute the following searches:

   - **Chuboy technical videos**:
     - `"曲博 Chuboy YouTube FinFET GAA GAAFET 半導體 技術分析"`
     - `"曲博 Chuboy YouTube CPO Co-Packaged Optics 共封裝光學"`
     - `"曲博 Chuboy YouTube TGV Through Glass Via 玻璃通孔"`
   - **MacroMicro industry videos**:
     - `"財經M平方 MacroMicro YouTube 半導體 TSMC Intel Samsung 競爭分析"`
     - `"財經M平方 MacroMicro YouTube 半導體 資本支出 市佔率 量產時程"`

   Extract YouTube URLs (format: `https://www.youtube.com/watch?v=...`) from the search results. Save discovered URLs to `skills/semi_industry_analysis/tmp/chuboy_video_urls.txt` and `skills/semi_industry_analysis/tmp/macromicro_video_urls.txt` respectively.

   The Evaluator must confirm that at least one valid YouTube URL was found per channel. If no URLs are found for a channel, the Evaluator should flag this for a retry with alternative search queries.

2. **Index video content via multimodal embedding**: Run `scripts/gemini_youtube_search.py` to download, analyze, and vectorize the discovered videos into ChromaDB. Execute for each discovered URL:

   - For Chuboy videos: `args=["semi_industry_analysis", "<url>", "FinFET GAA CPO TGV semiconductor technical analysis"]`
   - For MacroMicro videos: `args=["semi_industry_analysis", "<url>", "semiconductor industry TSMC Intel Samsung competitive analysis CapEx market share"]`

   The Evaluator verifies that the indexing completed without errors and that the ChromaDB collection for `semi_industry_analysis` contains indexed segments.

3. **RAG-based technical knowledge retrieval**: Run `scripts/inference_with_multimodal_embedding.py` to extract specific technical parameters and industry dynamics. Execute the following queries:

   - **GAA Technical**: `"Samsung 3nm GAA yield rate vs TSMC N3 FinFET yield, Intel 20A RibbonFET gate-all-around transistor performance metrics"` → Save to `skills/semi_industry_analysis/tmp/rag_technical_gaa.txt`
   - **CPO Technical**: `"Co-Packaged Optics CPO integration progress TSMC Intel Samsung, silicon photonics bandwidth density power efficiency"` → Save to `skills/semi_industry_analysis/tmp/rag_technical_cpo.txt`
   - **TGV Technical**: `"Through Glass Via TGV thermal performance vs TSV, glass substrate warpage CTE mismatch, panel-level packaging"` → Save to `skills/semi_industry_analysis/tmp/rag_technical_tgv.txt`
   - **GAA Market**: `"GAAFET mass production timeline TSMC Intel Samsung, customer adoption N2 20A SF2, CapEx investment node transition"` → Save to `skills/semi_industry_analysis/tmp/rag_market_gaa.txt`
   - **CPO Market**: `"Co-Packaged Optics CPO commercialization timeline, data center AI cluster adoption, TSMC InFO_SoW Intel Foveros Samsung I-Cube"` → Save to `skills/semi_industry_analysis/tmp/rag_market_cpo.txt`
   - **TGV Market**: `"Through Glass Via TGV commercialization, glass substrate supply chain readiness, cost comparison organic substrate silicon interposer"` → Save to `skills/semi_industry_analysis/tmp/rag_market_tgv.txt`

   The Evaluator checks that each RAG result contains substantive content (not empty or error-only output) and that results reference specific video segments with timestamps.

4. **Compose raw technical draft**: Read all six RAG result files from Step 3 and the technical reference from `skills/semi_industry_analysis/reference/tech_terms.md`. Synthesize a raw technical draft that:

   - Correlates technical advantages (from Chuboy-sourced content) with market execution and financial impact (from MacroMicro-sourced content).
   - Organizes findings by the 3x3 matrix structure: rows = (TSMC, Intel, Samsung), columns = (GAA Progress, CPO Layout, TGV Adoption).
   - Preserves source attribution — every technical claim must note whether it originated from Chuboy or MacroMicro content.

   Write the draft to `skills/semi_industry_analysis/tmp/raw_technical_draft.md`. The Evaluator verifies the draft covers all 9 cells of the matrix and contains source attributions.

5. **Generate final competitor matrix report**: Refine the raw draft into a professional industry analyst report with the following structure:

   - **Executive Summary**: 3-5 sentence overview of the competitive landscape.
   - **3x3 Competitor Matrix Table**: Concise summary table with key metrics per cell.
   - **Detailed Analysis Sections** (one per technology):
     - **GAA Transition**: Node roadmaps, yield data, customer adoption timelines.
     - **CPO Layout**: Integration approaches, bandwidth targets, deployment timelines.
     - **TGV Adoption**: Substrate technology, thermal/electrical advantages, supply chain readiness.
   - **Strategic Outlook**: Forward-looking assessment of competitive positioning.
   - **Source Appendix**: List of all video sources used, with YouTube URLs and timestamps.

   Tone: Professional Industry Analyst. Write the final report to `skills/semi_industry_analysis/tmp/competitor_matrix_report.md`.

6. **Final verification**: Read the completed report and verify:
   - The report covers all three companies (TSMC, Intel, Samsung) across all three technology areas (GAA, CPO, TGV) — all 9 cells of the matrix are populated.
   - Technical descriptions (e.g., TGV cooling advantages, GAA nanosheet vs FinFET) are consistent with the definitions in `skills/semi_industry_analysis/reference/tech_terms.md`.
   - **Source Audit**: Fail the task if the report lacks specific insights attributable to "Chuboy" or "MacroMicro" sourced content. Both perspectives must be represented.
   - **Evidence Check**: Confirm the report utilizes data retrieved via the `inference_with_multimodal_embedding.py` script (RAG results), not fabricated content.
   - The Source Appendix contains at least one YouTube URL with timestamp reference.

## Success Cases

## Failure Cases
