"""
retriever_agent.py

Agente RAG semántico que se ejecuta entre el Classifier y el Planner.

Responsabilidades:
  1. Recuperar chunks doctrinales relevantes de ChromaDB filtrados por
     el dominio del operador (air→AJP-3.3, land→AJP-3.2, maritime→AJP-3.1).
  2. Identificar términos del dominio en la query y mapearlos al vocabulario
     de la ontología (ej. "UAV" → "Dron") usando los propios chunks como
     contexto, sin léxico hardcodeado.
  3. Decidir si el plan necesita consultar el grafo (needs_graph) y/o si
     hay chunks de valor para el Synthesizer (needs_doctrine).

Cuándo se activa:
  - Siempre para category == ontology_with_context
  - Siempre para category == doctrine_only
  - Para category == ontology_only cuando el Orchestrator detecta términos
    no reconocidos (flag unknown_terms=True)

El Retriever NO hace llamadas LLM directas para la expansión de términos.
En su lugar usa un LLM pequeño con los chunks recuperados como contexto,
de modo que la expansión semántica refleje el vocabulario real de las AJP,
no un léxico predefinido.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from pydantic_ai import Agent

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from models import RetrieverOutput

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

RETRIEVER_MODEL    = os.getenv("RETRIEVER_MODEL", "openai:gpt-4.1-mini")
RETRIEVER_TOP_K    = int(os.getenv("RETRIEVER_TOP_K", "6"))
RETRIEVER_MIN_SCORE = float(os.getenv("RETRIEVER_MIN_SCORE", "0.10"))

_DOMAIN_TO_SOURCE_DOC = {
    "air":      "AJP-3.3",
    "land":     "AJP-3.2",
    "maritime": "AJP-3.1",
}

# ---------------------------------------------------------------------------
# Agente de expansión semántica
# ---------------------------------------------------------------------------

_EXPANSION_SYSTEM_PROMPT = """\
Eres un especialista en doctrina militar NATO. Se te proporciona:
  1. Una consulta de un operador.
  2. Fragmentos de doctrina AJP recuperados por similitud semántica.

Tu tarea es:
A) Identificar términos operacionales en la consulta que correspondan a
   entidades del grafo C2 (Dron, Nodo, Servidor, Modelo, C2, Piloto,
   Operador, Datos, Organizacion). Devuelve el mapeo como dict JSON:
   {"término_original": "clase_ontologia"}.
   Si no hay términos que mapear, devuelve {}.

B) Decidir si la respuesta requiere datos del grafo C2 (needs_graph: bool).
   True si la consulta pregunta por estados, entidades, relaciones o impacto
   en la red C2 concreta. False si es puramente doctrinal.

C) Decidir si los chunks son relevantes para enriquecer la respuesta
   (needs_doctrine: bool). True si los chunks contienen procedimientos,
   definiciones o principios directamente aplicables a la consulta.

Responde ÚNICAMENTE en JSON con esta estructura exacta, sin texto adicional:
{
  "relevant_terms": {"UAV": "Dron", "C2 node": "Nodo"},
  "needs_graph": true,
  "needs_doctrine": true
}
"""

_expansion_agent: Agent[None, dict] = Agent(
    model=RETRIEVER_MODEL,
    output_type=dict,
    system_prompt=_EXPANSION_SYSTEM_PROMPT,
)


# ---------------------------------------------------------------------------
# Lazy singleton del retriever
# ---------------------------------------------------------------------------

def _get_retriever():
    from tools.doctrine_retriever import DoctrineRetriever
    return DoctrineRetriever(
        chroma_dir=os.getenv("CHROMA_DIR", "rag/data/chroma"),
        collection=os.getenv("CHROMA_COLLECTION", "ajp_doctrine_chunks"),
        lexicons_dir=os.getenv("LEXICONS_DIR", "rag/data/processed/lexicons"),
        embedding_model=os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_chunks_context(chunks: list[dict]) -> str:
    """Formatea los chunks para el prompt del agente de expansión."""
    if not chunks:
        return "(sin fragmentos recuperados)"
    parts = []
    for i, ch in enumerate(chunks, 1):
        src   = ch.get("source_doc", "")
        page  = ch.get("page", "")
        score = ch.get("score")
        text  = ch.get("text", "")
        score_str = f" [score={score:.2f}]" if score else ""
        parts.append(
            f"[{i}] {src} p.{page}{score_str}\n{text[:400]}"
        )
    return "\n\n".join(parts)


def _safe_expand(raw: Any) -> tuple[dict[str, str], bool, bool]:
    """Extrae relevant_terms, needs_graph, needs_doctrine del output LLM."""
    if not isinstance(raw, dict):
        return {}, True, False
    terms        = raw.get("relevant_terms", {})
    needs_graph   = bool(raw.get("needs_graph", True))
    needs_doctrine= bool(raw.get("needs_doctrine", False))
    # Sanitizar: solo str→str en terms
    terms = {str(k): str(v) for k, v in terms.items() if k and v}
    return terms, needs_graph, needs_doctrine


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

async def retrieve(
    query: str,
    domain: str,
    category: str,
) -> RetrieverOutput:
    """Recupera contexto doctrinal y expande términos semánticamente.

    Args:
        query:    consulta original del usuario.
        domain:   dominio operacional ('air', 'land', 'maritime').
        category: categoría del Classifier.

    Returns:
        RetrieverOutput con chunks, mapeo de términos y flags de fuentes.
    """
    source_doc  = _DOMAIN_TO_SOURCE_DOC.get(domain)
    filter_meta = {"source_doc": source_doc} if source_doc else None

    # 1. Recuperar chunks por similitud semántica
    retriever = _get_retriever()
    try:
        result = retriever.retrieve(
            query=query,
            k=RETRIEVER_TOP_K,
            filter_meta=filter_meta,
        )
        docs = result.get("docs_final", [])
    except Exception as e:
        logger.warning("Retriever ChromaDB error: %s", e)
        docs = []

    # Convertir a dicts y filtrar por score mínimo
    chunks: list[dict] = []
    for doc in docs:
        score = (doc.metadata or {}).get("score")
        if score is not None and score < RETRIEVER_MIN_SCORE:
            continue
        chunks.append({
            "text":       doc.page_content,
            "source_doc": (doc.metadata or {}).get("source_doc", ""),
            "page":       (doc.metadata or {}).get("page", ""),
            "score":      score,
        })

    logger.info(
        "Retriever: domain=%s source=%s chunks=%d",
        domain, source_doc, len(chunks),
    )

    # 2. Expansión semántica vía LLM usando los chunks como contexto
    chunks_ctx = _build_chunks_context(chunks)
    expansion_prompt = (
        f"Consulta del operador ({domain}):\n{query}\n\n"
        f"Fragmentos doctrinales recuperados:\n{chunks_ctx}"
    )

    relevant_terms: dict[str, str] = {}
    needs_graph   = category in ("ontology_only", "ontology_with_context")
    needs_doctrine = len(chunks) > 0

    try:
        result_raw = await _expansion_agent.run(expansion_prompt)
        relevant_terms, needs_graph, needs_doctrine = _safe_expand(
            result_raw.output
        )
    except Exception as e:
        logger.warning("Retriever expansion LLM error: %s", e)
        # Fallback conservador: mantener los flags por defecto

    return RetrieverOutput(
        domain=domain,
        query_used=query,
        chunks=chunks,
        relevant_terms=relevant_terms,
        needs_graph=needs_graph,
        needs_doctrine=needs_doctrine,
    )