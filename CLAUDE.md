# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SA-MultiAgent is a multi-agent operational intelligence system for analyzing military C2 (Command & Control) networks across Air, Land, and Maritime domains. It combines LLM agents (PydanticAI + GPT-4.1), ontology reasoning (RDF/OWL + SPARQL via Apache Fuseki), and doctrine RAG (ChromaDB + sentence-transformers).

## Running the System

**Docker (full stack):**
```bash
docker-compose up --build
# Fuseki triple store: http://localhost:3030
# FastAPI backend:     http://localhost:8000  (docs at /docs)
# Frontend:            http://localhost:8080
```

**Local development (requires Fuseki on localhost:3030):**
```bash
pip install -r requirements.txt
uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

**Rebuild the RAG index** (after changing doctrine files in `rag/data/processed/`):
```bash
python scripts/build_index.py
```

**Environment:** Copy `.env` and set `OPENAI_API_KEY` plus optional model/threshold overrides before running.

## Architecture

The system runs a fixed 4-stage **ReWOO pipeline** per query — each stage is a single LLM call with structured output:

```
POST /query
  → Classifier  → labels query (smalltalk | doctrine_question | ontology_question | unclear)
  → Planner     → emits an ordered list of tool calls as a plan; may invoke sparql_from_nl sub-agent
  → Executor    → runs tools sequentially, resolves cross-step IRI references, may trigger re-plan (≤2×)
  → Synthesizer → formats final response with citations, detected pattern, and entity list
```

Key design invariant: **no agent loops on tool calls** — each agent calls the LLM once and returns structured Pydantic output. The only exception is `sparql_from_nl`, which is a nested sub-agent inside the Planner.

## Key Files

| File | Role |
|------|------|
| [api.py](api.py) | FastAPI app — `/login`, `/query`, `/session`, `/health` |
| [orchestration/orchestrator.py](orchestration/orchestrator.py) | Runs the 4-stage pipeline, wires agents together |
| [models.py](models.py) | All Pydantic models shared across agents |
| [agents/classifier_agent.py](agents/classifier_agent.py) | Query routing with confidence threshold |
| [agents/planner_agent.py](agents/planner_agent.py) | ReWOO planner + `sparql_from_nl` tool |
| [agents/execution/executor.py](agents/execution/executor.py) | Executes tool plans, handles re-planning |
| [agents/synthesizer_agent.py](agents/synthesizer_agent.py) | Response synthesis, pattern detection |
| [tools/tool_catalog.py](tools/tool_catalog.py) | 50+ tool descriptors exposed to the Planner |
| [sparql_templates.py](sparql_templates.py) | Parametric SPARQL functions (T*, B*, C*, D*, F*, G*, R*, S* families) |
| [tools/doctrine_retriever.py](tools/doctrine_retriever.py) | ChromaDB RAG retrieval |
| [tools/sparql_executor.py](tools/sparql_executor.py) | Executes SPARQL against Fuseki |
| [prompts/](prompts/) | System prompts for each agent (edit here to tune behavior) |
| [ontologia/ontologia.ttl](ontologia/ontologia.ttl) | RDF/OWL ontology (drone networks, C2 structures) |
| [rag/data/processed/](rag/data/processed/) | JSONL doctrine chunks (AJP-3.1/3.2/3.3) |

## Configuration (.env)

Critical variables:
- `OPENAI_API_KEY` — required
- `CLASSIFIER_MODEL` / `PLANNER_MODEL` / `SYNTHESIZER_MODEL` — default `gpt-4.1-mini` / `gpt-4.1` / `gpt-4.1`
- `CLASSIFIER_CONFIDENCE_THRESHOLD=0.6` — below this, query is classified as `unclear`
- `FUSEKI_ENDPOINT=http://localhost:3030/dron/query`
- `DOCTRINE_SEARCH_K=8` — top-K chunks retrieved per RAG call

## Session Model

Sessions are **in-memory only** (no database). `POST /login` creates a session with `operator_id` and `domain` (Air/Land/Maritime). All subsequent requests require the `X-Session-ID` header. The last `CLASSIFIER_HISTORY_TURNS` turns are passed as context to every agent.

## Adding or Modifying Tools

1. Define a parametric SPARQL function in [sparql_templates.py](sparql_templates.py) if the tool needs a query.
2. Register a tool descriptor in [tools/tool_catalog.py](tools/tool_catalog.py) (tool name, description, parameters, template reference).
3. The Planner picks up tools from the catalog automatically — no other wiring needed.
4. For NL→SPARQL fallback paths, update [prompts/nl_to_sparql_system_prompt.py](prompts/nl_to_sparql_system_prompt.py).

## Ontology

The ontology (`ontologia/ontologia.ttl`) describes drone network topology, C2 nodes, data flows, and operational roles. SPARQL queries run against Fuseki, which loads the ontology at startup via [scripts/entrypoint.sh](scripts/entrypoint.sh). To modify the ontology, edit the `.ttl` file and rebuild/restart the Fuseki container.
