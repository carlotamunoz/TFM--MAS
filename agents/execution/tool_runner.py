"""
tool_runner.py

Dispatcher de tools para el Executor.

Responsabilidades:
  - Mapear tool name → función Python.
  - Usar sparql_templates.py para TODAS las queries (sin queries inline).
  - Normalizar IRIs de entrada con _normalize_iri_output().
  - Clasificar excepciones en TRANSIENT vs SEMANTIC.
  - NO hace reintentos (lógica en el Executor).
  - NO resuelve referencias {{Ek}} (lógica en reference_resolver).

Clasificación de errores:
  TRANSIENT  → requests.Timeout, ConnectionError, OSError, IOError
  SEMANTIC   → ValueError, KeyError, cualquier otro error no transitorio
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import requests

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from models import ErrorType

logger = logging.getLogger(__name__)

_TRANSIENT_EXCEPTIONS = (
    requests.Timeout,
    requests.ConnectionError,
    OSError,
    IOError,
)


def classify_error(exc: Exception) -> ErrorType:
    if isinstance(exc, _TRANSIENT_EXCEPTIONS):
        return ErrorType.TRANSIENT
    return ErrorType.SEMANTIC


# ---------------------------------------------------------------------------
# Lazy singletons
# ---------------------------------------------------------------------------

def _sparql():
    from tools.sparql_executor import SparqlExecutor
    endpoint = os.getenv("FUSEKI_ENDPOINT", "http://localhost:3030/dron/query")
    return SparqlExecutor(endpoint=endpoint)


def _retriever():
    from tools.doctrine_retriever import DoctrineRetriever
    return DoctrineRetriever(
        chroma_dir=os.getenv("CHROMA_DIR", "rag/data/chroma"),
        collection=os.getenv("CHROMA_COLLECTION", "ajp_doctrine_chunks"),
        lexicons_dir=os.getenv("LEXICONS_DIR", "rag/data/processed/lexicons"),
        embedding_model=os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
    )


_DOMAIN_TO_SOURCE_DOC = {
    "maritime": "AJP-3.1",
    "land":     "AJP-3.2",
    "air":      "AJP-3.3",
}

# Propiedades de objeto válidas (verificadas en el grafo)
_VALID_OBJECT_PROPS = {
    "belongs_to", "controls", "generates", "hosts_model",
    "interacts_with", "is_backed_up", "is_managed_by",
    "is_operated_by", "is_operated_in", "is_owned_by",
    "is_part_of", "is_used_by", "manages", "provides_data_to",
    "provides_visualization", "stores", "uses_model",
}

# Clases válidas de la ontología
_VALID_CLASSES = {
    "Dron", "Nodo", "Servidor", "Modelo", "Datos",
    "C2", "Operador", "Piloto", "Propietario", "Organizacion", "Persona",
}

_VALID_FILTER_OPERATORS = {"=", "!=", ">", "<", ">=", "<=", "contains"}


def _validate_prop(prop: str) -> str:
    if prop not in _VALID_OBJECT_PROPS:
        raise ValueError(
            f"Propiedad desconocida: {prop!r}. "
            f"Válidas: {sorted(_VALID_OBJECT_PROPS)}"
        )
    return prop


def _validate_class(cls: str) -> str:
    if cls not in _VALID_CLASSES:
        raise ValueError(
            f"Clase desconocida: {cls!r}. "
            f"Válidas: {sorted(_VALID_CLASSES)}"
        )
    return cls


def _validate_filters(filters: list[dict]) -> list[dict]:
    """Valida la lista de filtros: operadores y que los campos requeridos estén presentes."""
    for i, f in enumerate(filters):
        for key in ("property", "operator", "value"):
            if key not in f:
                raise ValueError(f"Filtro [{i}] falta campo requerido: {key!r}")
        if f["operator"] not in _VALID_FILTER_OPERATORS:
            raise ValueError(
                f"Filtro [{i}] operador inválido: {f['operator']!r}. "
                f"Válidos: {sorted(_VALID_FILTER_OPERATORS)}"
            )
    return filters


def _select(query: str) -> list[dict]:
    """Ejecuta un SELECT y devuelve bindings simplificados."""
    exe = _sparql()
    r   = exe.run(query)
    return exe.simplify_bindings(r.bindings)


def _any(query: str) -> dict:
    """Ejecuta SELECT o CONSTRUCT y devuelve resultado bruto."""
    return _sparql().run_any(query)


# ---------------------------------------------------------------------------
# Normalización de IRIs
# ---------------------------------------------------------------------------

def _normalize_iri_output(uri: str) -> str:
    """Convierte cualquier forma de IRI al formato corto ex:Fragmento.

    Garantiza que el output de resolve_entity y los args de entrada a tools
    siempre tengan IRIs cortos, independientemente de lo que devuelva Fuseki
    o escriba el LLM.
    """
    from sparql_templates import EX
    if not uri:
        return uri
    if uri.startswith(EX):
        return "ex:" + uri[len(EX):]
    if uri.startswith("ex:"):
        return uri
    return uri


# ---------------------------------------------------------------------------
# Implementaciones de tools
# ---------------------------------------------------------------------------

def _resolve_entity(args: dict) -> list[dict]:
    from sparql_templates import R1_resolve_entity, R2_resolve_entity_by_fragment
    name        = args["name"]
    entity_type = args.get("entity_type")
    if entity_type:
        _validate_class(entity_type)

    results = _select(R1_resolve_entity(name=name, entity_class=entity_type))
    if not results:
        results = _select(R2_resolve_entity_by_fragment(name=name, entity_class=entity_type))

    # Normalizar IRIs en output para que pasos posteriores reciban ex:Fragmento
    for row in results:
        if "x"    in row and row["x"]:    row["x"]    = _normalize_iri_output(row["x"])
        if "type" in row and row["type"]: row["type"] = _normalize_iri_output(row["type"])

    return results


def _describe_ontology_schema(args: dict) -> Any:
    from sparql_templates import (
        S1_list_classes, S2_list_properties_of_class,
        S3_list_relations_between_classes,
    )
    scope      = args["scope"]
    class_name = args.get("class_name")

    if scope == "classes":
        return _select(S1_list_classes())
    if scope == "properties":
        if not class_name:
            raise ValueError("class_name requerido cuando scope == 'properties'")
        _validate_class(class_name)
        return _select(S2_list_properties_of_class(class_name))
    if scope == "relations":
        return _select(S3_list_relations_between_classes())
    if scope == "all":
        return {
            "classes":   _select(S1_list_classes()),
            "relations": _select(S3_list_relations_between_classes()),
        }
    raise ValueError(f"scope inválido: {scope!r}. Válidos: classes, properties, relations, all")


def _entity_describe(args: dict) -> list[dict]:
    from sparql_templates import T1_describe
    entity = _normalize_iri_output(args["entity"])
    return _select(T1_describe(individual=entity))


def _traverse_graph(args: dict) -> list[dict]:
    from sparql_templates import N1_traverse_graph
    start    = _normalize_iri_output(args["start_entity"])
    relations = args["relations"]
    if not relations:
        raise ValueError("traverse_graph: relations no puede estar vacío")

    # Validar propiedades en cada salto
    for i, rel in enumerate(relations):
        if "property" not in rel:
            raise ValueError(f"traverse_graph: relación [{i}] falta campo 'property'")
        _validate_prop(rel["property"])
        direction = rel.get("direction", "outgoing")
        if direction not in ("outgoing", "incoming"):
            raise ValueError(
                f"traverse_graph: relación [{i}] direction inválida: {direction!r}"
            )

    target_class = args.get("target_class")
    if target_class:
        _validate_class(target_class)

    return_mode = args.get("return_mode", "entities")
    if return_mode not in ("entities", "count"):
        raise ValueError(f"traverse_graph: return_mode inválido: {return_mode!r}")

    return _select(N1_traverse_graph(
        start_entity=start,
        relations=relations,
        target_class=target_class,
        return_mode=return_mode,
    ))


def _filter_entities(args: dict) -> list[dict]:
    from sparql_templates import N2_filter_entities
    class_name  = _validate_class(args["class_name"])
    filters     = _validate_filters(args.get("filters", []))
    return_mode = args.get("return_mode", "entities")
    if return_mode not in ("entities", "count"):
        raise ValueError(f"filter_entities: return_mode inválido: {return_mode!r}")
    return _select(N2_filter_entities(
        class_iri=class_name,
        filters=filters,
        return_mode=return_mode,
    ))


def _aggregate_entities(args: dict) -> list[dict]:
    from sparql_templates import N3_aggregate_entities
    class_name  = _validate_class(args["class_name"])
    prop        = args["property"]
    aggregation = args["aggregation"].upper()
    if aggregation not in ("COUNT", "AVG", "SUM", "MIN", "MAX"):
        raise ValueError(f"aggregate_entities: agregación inválida: {aggregation!r}")

    filters  = _validate_filters(args.get("filters") or [])
    group_by = args.get("group_by")
    order_by = args.get("order_by", "desc")
    limit    = int(args.get("limit", 10))

    if order_by not in ("asc", "desc"):
        raise ValueError(f"aggregate_entities: order_by inválido: {order_by!r}")

    return _select(N3_aggregate_entities(
        class_iri=class_name,
        prop=prop,
        aggregation=aggregation,
        filters=filters or None,
        group_by=group_by,
        order_by=order_by,
        limit=limit,
    ))


def _impact_reachability(args: dict) -> list[dict]:
    from sparql_templates import F1_impact_reachability
    max_hops = int(args.get("max_hops", 3))
    if not 1 <= max_hops <= 10:
        raise ValueError(f"max_hops debe estar entre 1 y 10, recibido: {max_hops}")
    seed = _normalize_iri_output(args["seed"])
    return _select(F1_impact_reachability(seed=seed, max_hops=max_hops))


def _impact_subgraph(args: dict) -> dict:
    from sparql_templates import F2_impact_subgraph
    max_hops = int(args.get("max_hops", 3))
    if not 1 <= max_hops <= 10:
        raise ValueError(f"max_hops debe estar entre 1 y 10, recibido: {max_hops}")
    seed = _normalize_iri_output(args["seed"])
    return _any(F2_impact_subgraph(seed=seed, max_hops=max_hops))


def _retrieve_doctrine(args: dict) -> list[dict]:
    domain    = args["domain"]
    query     = args["query"]
    top_k     = int(args.get("top_k", 5))
    source_doc  = _DOMAIN_TO_SOURCE_DOC.get(domain)
    filter_meta = {"source_doc": source_doc} if source_doc else None
    result = _retriever().retrieve(query=query, k=top_k, filter_meta=filter_meta)
    return [
        {
            "text":       doc.page_content,
            "source_doc": (doc.metadata or {}).get("source_doc", ""),
            "page":       (doc.metadata or {}).get("page", ""),
            "score":      (doc.metadata or {}).get("score"),
        }
        for doc in result["docs_final"]
    ]



def _scenario_summary(args: dict) -> dict:
    from sparql_templates import N4_scenario_summary
    rows = _select(N4_scenario_summary())
    summary: dict[str, dict] = {}
    for row in rows:
        cls  = str(row.get("class", ""))
        brkd = str(row.get("breakdown", ""))
        cnt  = row.get("count", 0)
        if cls not in summary:
            summary[cls] = {}
        summary[cls][brkd] = cnt
    return summary


def _raw_sparql(args: dict) -> list[dict]:
    query = args["query"]
    if "SELECT" not in query.strip().upper():
        raise ValueError(
            f"raw_sparql: la query no contiene SELECT. "
            f"Primeros 100 chars: {query[:100]}"
        )
    result = _any(query)
    if result.get("type") == "select":
        from tools.sparql_executor import SparqlExecutor
        return SparqlExecutor.simplify_bindings(result.get("bindings", []))
    raise ValueError(
        f"raw_sparql: tipo de resultado inesperado: {result.get('type')!r}"
    )


# ---------------------------------------------------------------------------
# Registro de tools
# ---------------------------------------------------------------------------

_TOOL_DISPATCH: dict[str, Any] = {
    # resolution
    "resolve_entity":           _resolve_entity,
    # schema
    "describe_ontology_schema": _describe_ontology_schema,
    # graph
    "entity_describe":          _entity_describe,
    "traverse_graph":           _traverse_graph,
    "filter_entities":          _filter_entities,
    "aggregate_entities":       _aggregate_entities,
    # impact
    "impact_reachability":      _impact_reachability,
    "impact_subgraph":          _impact_subgraph,
    # doctrine
    "retrieve_doctrine":        _retrieve_doctrine,
    # data
    "scenario_summary":         _scenario_summary,
    # generated
    "raw_sparql":               _raw_sparql,
}


def run_tool(tool_name: str, args: dict[str, Any]) -> Any:
    """Punto de entrada único del Executor.

    Raises:
        ValueError: tool desconocido o args inválidos (error semántico).
        Exception:  error de ejecución (clasificar con classify_error).
    """
    if tool_name not in _TOOL_DISPATCH:
        raise ValueError(
            f"Tool desconocido: {tool_name!r}. "
            f"Disponibles: {sorted(_TOOL_DISPATCH.keys())}"
        )
    logger.debug("run_tool: tool=%s args_keys=%s", tool_name, list(args.keys()))
    return _TOOL_DISPATCH[tool_name](args)