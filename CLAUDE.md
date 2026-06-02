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

The system runs a fixed 5-stage pipeline per query — each stage is a single LLM call with structured output:

```
POST /query
  → Classifier  → labels query (smalltalk | doctrine_question | ontology_question | unclear)
  → Retriever   → RAG semantic retrieval from ChromaDB; expands terms, sets needs_graph/needs_doctrine flags
  → Planner     → emits an ordered ReWOO plan of tool calls; may invoke sparql_from_nl sub-agent
  → Executor    → runs tools sequentially, resolves {{Ek}} cross-step references, may trigger re-plan (≤2×)
  → Synthesizer → formats final response with citations, detected pattern, and entity list
```

**Short-circuit paths:**
- `smalltalk` → Classifier → Synthesizer (skips Retriever, Planner, Executor)
- `unclear` → Classifier returns `clarification_question` directly to the user; pipeline stops

**Key design invariants:**
- No agent loops on tool calls — each agent calls the LLM once and returns structured Pydantic output.
- The only exception is `sparql_from_nl`, a nested sub-agent inside the Planner that generates raw SPARQL for queries the catalog can't cover.
- The Executor makes zero LLM calls; it only runs the pre-generated plan deterministically.
- The Retriever activates for `ontology_with_context` and `doctrine_only` always; for `ontology_only` only when the query lacks recognized IRIs.

## Key Files

| File | Role |
|------|------|
| [api.py](api.py) | FastAPI app — `/login`, `/query`, `/session`, `/health` |
| [orchestration/orchestrator.py](orchestration/orchestrator.py) | Wires all 5 pipeline stages; stateless, all state lives in `Session` |
| [models.py](models.py) | All Pydantic models shared across agents |
| [session.py](session.py) | In-memory session store; `Session` holds domain, operator_id, conversation history |
| [agents/classifier_agent.py](agents/classifier_agent.py) | Query routing with confidence threshold |
| [agents/retriever_agent.py](agents/retriever_agent.py) | ChromaDB RAG + LLM-based term expansion; maps query terms to ontology vocabulary |
| [agents/planner_agent.py](agents/planner_agent.py) | ReWOO planner + `sparql_from_nl` sub-agent tool |
| [agents/execution/executor.py](agents/execution/executor.py) | Executes tool plans with topological ordering, transient retries, re-planning |
| [agents/execution/tool_runner.py](agents/execution/tool_runner.py) | Maps tool names → Python functions; classifies errors as TRANSIENT vs SEMANTIC |
| [agents/execution/reference_resolver.py](agents/execution/reference_resolver.py) | Resolves `{{E1}}` and `{{E1.field}}` references between plan steps |
| [agents/synthesizer_agent.py](agents/synthesizer_agent.py) | Response synthesis, pattern detection, citations |
| [tools/tool_catalog.py](tools/tool_catalog.py) | Tool descriptors exposed to the Planner; `get_tools_for_category()` filters by query category |
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
- `NL_SPARQL_MODEL` — model for the `sparql_from_nl` sub-agent, default `gpt-4.1`
- `RETRIEVER_MODEL` — model for term expansion in the Retriever, default `gpt-4.1-mini`
- `RETRIEVER_TOP_K` — number of ChromaDB chunks retrieved, default `6`
- `RETRIEVER_MIN_SCORE` — minimum similarity score to keep a chunk, default `0.10`
- `CLASSIFIER_CONFIDENCE_THRESHOLD=0.6` — below this, query is classified as `unclear`
- `CLASSIFIER_HISTORY_TURNS` / `PLANNER_HISTORY_TURNS` — conversation turns passed to each agent, default `5`
- `FUSEKI_ENDPOINT=http://localhost:3030/dron/query`
- `DOCTRINE_SEARCH_K=8` — top-K chunks retrieved per RAG call (legacy; prefer `RETRIEVER_TOP_K`)
- `EXECUTOR_TRANSIENT_RETRIES=3` — max retries for transient errors (timeout, connection)
- `EXECUTOR_BACKOFF_BASE=1.0` — exponential backoff base in seconds
- `EXECUTOR_MAX_REPLANS=2` — max re-planning cycles per query

## Session Model

Sessions are **in-memory only** (no database). `POST /login` creates a session with `operator_id` and `domain` (Air/Land/Maritime). All subsequent requests require the `X-Session-ID` header. The domain is **immutable** for the lifetime of a session. The last N turns (configurable per agent) are passed as context to every agent.

Domain-to-doctrine mapping used by the Retriever: `air → AJP-3.3`, `land → AJP-3.2`, `maritime → AJP-3.1`.

## Adding or Modifying Tools

1. Define a parametric SPARQL function in [sparql_templates.py](sparql_templates.py) if the tool needs a query.
2. Register a tool descriptor in [tools/tool_catalog.py](tools/tool_catalog.py) (tool name, description, parameters, template reference, family).
3. Check `get_tools_for_category()` in the same file — it filters tools by query category. `doctrine_only` gets only `family=="doctrine"` tools; `ontology_only` gets all non-doctrine tools; `ontology_with_context` gets all tools. Set the `family` field accordingly.
4. The Planner picks up tools from the catalog automatically — no other wiring needed.
5. For NL→SPARQL fallback paths, update [prompts/nl_to_sparql_system_prompt.py](prompts/nl_to_sparql_system_prompt.py).

## Ontology

The ontology (`ontologia/ontologia.ttl`) describes drone network topology, C2 nodes, data flows, and operational roles. SPARQL queries run against Fuseki, which loads the ontology at startup via [scripts/entrypoint.sh](scripts/entrypoint.sh). To modify the ontology, edit the `.ttl` file and rebuild/restart the Fuseki container.

IRI references in SPARQL use the `ex:` prefix (bound to `<urn:scenario:static#>`). The `FUSEKI_GRAPH` env var controls which named graph is queried (default `<urn:scenario:static>`).
