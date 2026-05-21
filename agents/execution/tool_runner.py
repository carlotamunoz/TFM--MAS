"""
tool_runner.py

Ejecuta un StepPlan con sus args ya resueltos (sin referencias {{Ek}})
y devuelve el output crudo del tool.

Responsabilidades:
  - Mapear tool name -> funcion Python.
  - Clasificar excepciones en TRANSIENT vs SEMANTIC.
  - NO hace reintentos: esa logica vive en el Executor.
  - NO resuelve referencias: eso lo hace reference_resolver.

Errores transitorios (se reintentaran con backoff):
  - requests.Timeout, requests.ConnectionError
  - OSError, IOError (ChromaDB inaccesible)

Errores semanticos (activan re-planning):
  - ValueError: args invalidos, propiedad no existe, clase desconocida
  - KeyError: campo no existe en el resultado
  - Cualquier otro error no transitorio
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import requests

# Asegurar que el raiz del proyecto esta en sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from models import ErrorType

logger = logging.getLogger(__name__)

# Errores que se consideran transitorios (reintentables)
_TRANSIENT_EXCEPTIONS = (
    requests.Timeout,
    requests.ConnectionError,
    OSError,
    IOError,
)


def classify_error(exc: Exception) -> ErrorType:
    """Clasifica una excepcion en TRANSIENT o SEMANTIC."""
    if isinstance(exc, _TRANSIENT_EXCEPTIONS):
        return ErrorType.TRANSIENT
    return ErrorType.SEMANTIC


# ---------------------------------------------------------------------------
# Importaciones lazy de dependencias pesadas
# ---------------------------------------------------------------------------

def _get_sparql_executor():
    from tools.sparql_executor import SparqlExecutor
    endpoint = os.getenv("FUSEKI_ENDPOINT", "http://localhost:3030/dron/query")
    return SparqlExecutor(endpoint=endpoint)


def _get_doctrine_retriever():
    from tools.doctrine_retriever import DoctrineRetriever
    return DoctrineRetriever(
        chroma_dir=os.getenv("CHROMA_DIR", "rag/data/chroma"),
        collection=os.getenv("CHROMA_COLLECTION", "ajp_doctrine_chunks"),
        lexicons_dir=os.getenv("LEXICONS_DIR", "rag/data/processed/lexicons"),
        embedding_model=os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
    )


# Mapeo dominio -> source_doc en ChromaDB
_DOMAIN_TO_SOURCE_DOC = {
    "maritime": "AJP-3.1",
    "land":     "AJP-3.2",
    "air":      "AJP-3.3",
}


# ---------------------------------------------------------------------------
# Implementaciones de cada tool
# ---------------------------------------------------------------------------

def _run_resolve_entity(args: dict[str, Any]) -> list[dict]:
    from sparql_templatesv0 import R1_resolve_entity, R2_resolve_entity_by_fragment
    executor = _get_sparql_executor()
    name = args["name"]
    entity_type = args.get("entity_type")

    # Intento 1: UNION sobre propiedades identificadoras
    q1 = R1_resolve_entity(name=name, entity_class=entity_type)
    r1 = executor.run(q1)
    results = executor.simplify_bindings(r1.bindings)

    # Intento 2: fragmento de IRI si R1 no dio nada
    if not results:
        q2 = R2_resolve_entity_by_fragment(name=name, entity_class=entity_type)
        r2 = executor.run(q2)
        results = executor.simplify_bindings(r2.bindings)

    return results


def _run_describe_ontology_schema(args: dict[str, Any]) -> list[dict] | dict:
    from sparql_templatesv0 import S1_list_classes, S2_list_properties_of_class, S3_list_relations_between_classes
    executor = _get_sparql_executor()
    scope = args["scope"]
    class_name = args.get("class_name")

    if scope == "classes":
        r = executor.run(S1_list_classes())
        return executor.simplify_bindings(r.bindings)
    elif scope == "properties":
        if not class_name:
            raise ValueError("class_name requerido cuando scope == 'properties'")
        r = executor.run(S2_list_properties_of_class(class_name))
        return executor.simplify_bindings(r.bindings)
    elif scope == "relations":
        r = executor.run(S3_list_relations_between_classes())
        return executor.simplify_bindings(r.bindings)
    elif scope == "all":
        classes = executor.simplify_bindings(executor.run(S1_list_classes()).bindings)
        relations = executor.simplify_bindings(executor.run(S3_list_relations_between_classes()).bindings)
        return {"classes": classes, "relations": relations}
    else:
        raise ValueError(f"scope invalido: {scope!r}")


def _run_entity_describe(args: dict[str, Any]) -> list[dict]:
    from sparql_templatesv0 import T1_describe
    executor = _get_sparql_executor()
    q = T1_describe(individual=args["entity_iri"])
    r = executor.run(q)
    return executor.simplify_bindings(r.bindings)


def _run_entity_outgoing(args: dict[str, Any]) -> list[dict]:
    from sparql_templatesv0 import T5_outgoing
    executor = _get_sparql_executor()
    q = T5_outgoing(subject=args["subject_iri"], prop=args["property"])
    r = executor.run(q)
    return executor.simplify_bindings(r.bindings)


def _run_entity_incoming(args: dict[str, Any]) -> list[dict]:
    from sparql_templatesv0 import T6_incoming
    executor = _get_sparql_executor()
    q = T6_incoming(obj=args["object_iri"], prop=args["property"])
    r = executor.run(q)
    return executor.simplify_bindings(r.bindings)


def _run_list_by_class(args: dict[str, Any]) -> list[dict]:
    from sparql_templatesv0 import T3_list_by_class, T4_list_by_class_and_status
    executor = _get_sparql_executor()
    class_name = args["class_name"]
    status = args.get("status")
    if status:
        q = T4_list_by_class_and_status(class_iri=class_name, status_value=status)
    else:
        q = T3_list_by_class(class_iri=class_name)
    r = executor.run(q)
    return executor.simplify_bindings(r.bindings)


def _run_node_uses_model(args: dict[str, Any]) -> list[dict]:
    from sparql_templatesv0 import B3_node_uses_model, PREFIXES, iri
    executor = _get_sparql_executor()
    # Reutilizamos B3 pero filtrando por nodo concreto
    from sparql_templatesv0 import DEFAULT_GRAPH
    node = iri(args["node"])
    q = PREFIXES + f"""
SELECT ?model WHERE {{
  GRAPH {DEFAULT_GRAPH} {{ {node} ex:uses_model ?model . }}
}}
"""
    r = executor.run(q)
    return executor.simplify_bindings(r.bindings)


def _run_drone_generates_data(args: dict[str, Any]) -> list[dict]:
    from sparql_templatesv0 import PREFIXES, iri, DEFAULT_GRAPH
    executor = _get_sparql_executor()
    drone = iri(args["drone"])
    q = PREFIXES + f"""
SELECT ?data ?tipo ?fecha WHERE {{
  GRAPH {DEFAULT_GRAPH} {{
    {drone} ex:generates ?data .
    OPTIONAL {{ ?data ex:tipo ?tipo . }}
    OPTIONAL {{ ?data ex:fecha_hora ?fecha . }}
  }}
}}
"""
    r = executor.run(q)
    return executor.simplify_bindings(r.bindings)


def _run_models_used_by_drone(args: dict[str, Any]) -> list[dict]:
    from sparql_templatesv0 import C2_models_used_by_drone
    executor = _get_sparql_executor()
    q = C2_models_used_by_drone(drone=args["drone"])
    r = executor.run(q)
    return executor.simplify_bindings(r.bindings)


def _run_impact_direct_node(args: dict[str, Any]) -> dict:
    from sparql_templatesv0 import G1_impact_direct_node, C3_drones_affected_if_node_fails, C4_models_affected_if_node_fails
    executor = _get_sparql_executor()
    node = args["node"]
    r = executor.run(G1_impact_direct_node(node=node))
    rows = executor.simplify_bindings(r.bindings)
    return {
        "drones": list({row["drone"] for row in rows if row.get("drone")}),
        "data":   list({row["data"]  for row in rows if row.get("data")}),
        "models": list({row["model"] for row in rows if row.get("model")}),
    }


def _run_impact_reachability(args: dict[str, Any]) -> list[dict]:
    from sparql_templatesv0 import F1_impact_reachability
    executor = _get_sparql_executor()
    q = F1_impact_reachability(seed=args["seed"], max_hops=args["max_hops"])
    r = executor.run(q)
    return executor.simplify_bindings(r.bindings)


def _run_impact_subgraph(args: dict[str, Any]) -> dict:
    from sparql_templatesv0 import F2_impact_subgraph
    executor = _get_sparql_executor()
    q = F2_impact_subgraph(seed=args["seed"], max_hops=args["max_hops"])
    return executor.run_any(q)


def _run_rank_entities(args: dict[str, Any]) -> list[dict]:
    from sparql_templatesv0 import D1_top_nodes_by_data, D2_top_nodes_by_models, D3_most_critical_models
    from sparql_templatesv0 import PREFIXES, DEFAULT_GRAPH
    executor = _get_sparql_executor()
    entity_type = args["entity_type"]
    metric = args["metric"]
    limit = args.get("limit", 10)

    valid_combos = {
        ("node", "data_count"), ("node", "model_count"),
        ("model", "usage_count"), ("drone", "data_count"),
    }
    if (entity_type, metric) not in valid_combos:
        raise ValueError(
            f"Combinacion invalida: ({entity_type}, {metric}). "
            f"Validas: {valid_combos}"
        )

    if entity_type == "node" and metric == "data_count":
        q = D1_top_nodes_by_data(limit=limit)
    elif entity_type == "node" and metric == "model_count":
        q = D2_top_nodes_by_models(limit=limit)
    elif entity_type == "model" and metric == "usage_count":
        q = D3_most_critical_models(limit=limit)
    elif entity_type == "drone" and metric == "data_count":
        q = PREFIXES + f"""
SELECT ?drone (COUNT(DISTINCT ?data) AS ?score) WHERE {{
  GRAPH {DEFAULT_GRAPH} {{ ?drone ex:generates ?data . }}
}}
GROUP BY ?drone
ORDER BY DESC(?score)
LIMIT {limit}
"""
    r = executor.run(q)
    return executor.simplify_bindings(r.bindings)


def _run_retrieve_doctrine(args: dict[str, Any]) -> list[dict]:
    retriever = _get_doctrine_retriever()
    domain = args["domain"]
    query = args["query"]
    top_k = args.get("top_k", 5)

    source_doc = _DOMAIN_TO_SOURCE_DOC.get(domain)
    filter_meta = {"source_doc": source_doc} if source_doc else None

    result = retriever.retrieve(query=query, k=top_k, filter_meta=filter_meta)

    chunks = []
    for doc in result["docs_final"]:
        chunks.append({
            "text":       doc.page_content,
            "source_doc": (doc.metadata or {}).get("source_doc", ""),
            "page":       (doc.metadata or {}).get("page", ""),
            "score":      (doc.metadata or {}).get("score", None),
        })
    return chunks


def _run_raw_sparql(args: dict[str, Any]) -> list[dict]:
    """Ejecuta SPARQL SELECT pre-generado por el Planner."""
    executor = _get_sparql_executor()
    query = args["query"]
    # Validacion sintatica minima antes de mandar a Fuseki
    upper = query.strip().upper()
    if "SELECT" not in upper:
        raise ValueError(f"raw_sparql: la query no es SELECT: {query[:100]}")
    result = executor.run_any(query)
    if result.get("type") == "select":
        from tools.sparql_executor import SparqlExecutor
        return SparqlExecutor.simplify_bindings(result["bindings"])
    raise ValueError(f"raw_sparql: resultado inesperado tipo {result.get('type')!r}")


# ---------------------------------------------------------------------------
# Registro de tools
# ---------------------------------------------------------------------------

_TOOL_DISPATCH: dict[str, Any] = {
    "resolve_entity":           _run_resolve_entity,
    "describe_ontology_schema": _run_describe_ontology_schema,
    "entity_describe":          _run_entity_describe,
    "entity_outgoing":          _run_entity_outgoing,
    "entity_incoming":          _run_entity_incoming,
    "list_by_class":            _run_list_by_class,
    "node_uses_model":          _run_node_uses_model,
    "drone_generates_data":     _run_drone_generates_data,
    "models_used_by_drone":     _run_models_used_by_drone,
    "impact_direct_node":       _run_impact_direct_node,
    "impact_reachability":      _run_impact_reachability,
    "impact_subgraph":          _run_impact_subgraph,
    "rank_entities":            _run_rank_entities,
    "retrieve_doctrine":        _run_retrieve_doctrine,
    "raw_sparql":               _run_raw_sparql,
}


def run_tool(tool_name: str, args: dict[str, Any]) -> Any:
    """Ejecuta un tool por nombre con args ya resueltos.

    Raises:
      ValueError: tool desconocido (error semantico).
      Exception: cualquier error de ejecucion (clasificar con classify_error).
    """
    if tool_name not in _TOOL_DISPATCH:
        raise ValueError(
            f"Tool desconocido: {tool_name!r}. "
            f"Disponibles: {sorted(_TOOL_DISPATCH.keys())}"
        )
    logger.debug("Ejecutando tool=%s args=%s", tool_name, str(args)[:200])
    return _TOOL_DISPATCH[tool_name](args)