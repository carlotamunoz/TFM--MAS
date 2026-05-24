"""
SPARQL templates parametrizables para la ontología operacional.

Convención de prefijos en los nombres de funciones:

    T*  : templates BÁSICOS de exploración
          T1: describe individual, T2: tipos de un individual,
          T3: listar por clase, T4: listar por clase y estado,
          T5: traversal directo (outgoing), T6: traversal inverso (incoming)

    C*  : CHAINS — joins multi-hop frecuentes
          C2: modelos usados por un dron (dron→datos→nodo→modelo)
          C3: drones afectados si cae un nodo
          C4: modelos afectados si cae un nodo

    D*  : RANKINGS globales con agregación
          D1: nodos por datos, D2: nodos por modelos, D3: modelos críticos

    F*  : IMPACTO en CASCADA con property paths
          F1: reachability (SELECT), F2: subgraph (CONSTRUCT, corregido)

    G*  : IMPACTO DIRECTO (1 salto)
          G1: reescrito con subqueries separadas (sin OPTIONAL espurio)

    R*  : RESOLUCIÓN de nombres / aliases a IRIs
          R1: resolución UNION sobre propiedades identificadoras
          R2: fallback por fragmento de IRI

    S*  : SCHEMA / introspección de la ontología
          S1: listar clases, S2: propiedades por clase (desde ABox),
          S3: relaciones entre clases (desde ABox), S4: describir clase

    B*  : CONTEOS puntuales (utilities internas, sin tool de catálogo)
          B4: modelos usados por un nodo, B5: datos procesados por un nodo

Reglas:
  - TODAS las funciones devuelven SPARQL listo para ejecutar.
  - TODAS usan la constante PREFIXES (no redefinen prefijos inline).
  - TODAS aceptan IRIs en cualquier forma soportada por iri():
      'ex:Foo', 'http://.../Foo', o 'Foo' suelto.
  - Todas las queries con resultados variables tienen LIMIT configurable.

NOTA DE SEGURIDAD:
  iri() y quote_str() no son a prueba de inyección SPARQL exhaustiva.
  Para inputs externos (ej. sparql_from_nl) validar antes de pasar.
"""

from __future__ import annotations

EX = "http://www.semanticweb.org/carlo/ontologies/2024/10/untitled-ontology-4#"
DEFAULT_GRAPH = "<urn:scenario:static>"

PREFIXES = f"""\
PREFIX ex:   <{EX}>
PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl:  <http://www.w3.org/2002/07/owl#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
"""


def iri(x: str) -> str:
    """Normaliza un identificador a forma SPARQL válida.

    Maneja todos los formatos que pueden llegar desde el Planner o desde
    el output de resolve_entity:

    - 'ex:Foo'                  → 'ex:Foo'      (prefijo correcto)
    - 'http://<EX_NAMESPACE>#Foo' → 'ex:Foo'    (URI completa de nuestro NS → prefijo corto)
    - 'http://otro-namespace/Foo' → '<http://...>' (URI externa)
    - 'ex:http://...'           → 'ex:Foo'      (bug LLM: prefijo pegado a URI → corregido)
    - 'Foo'                     → 'ex:Foo'      (fragmento local)
    """
    # Limpiar prefijo 'ex:' pegado incorrectamente a una URI completa
    if x.startswith("ex:http://") or x.startswith("ex:https://"):
        x = x[3:]  # quitar 'ex:' mal pegado, procesar la URI sola

    # URI completa de nuestro namespace → convertir a prefijo corto
    if x.startswith(EX):
        return "ex:" + x[len(EX):]

    # URI completa externa → envolver en angle brackets
    if x.startswith("http://") or x.startswith("https://"):
        return f"<{x}>"

    # Ya tiene prefijo ex: correcto
    if x.startswith("ex:"):
        return x

    # Fragmento local → prefijo ex:
    return f"ex:{x}"


def quote_str(s: str) -> str:
    """Envuelve un literal como cadena SPARQL escapando comillas dobles."""
    return '"' + s.replace('"', '\\"') + '"'


# ---------------------------------------------------------------------------
# T*  Templates básicos de exploración
# ---------------------------------------------------------------------------

def T1_describe(individual: str, graph: str = DEFAULT_GRAPH, limit: int = 200) -> str:
    """Todos los pares propiedad-valor de un individual."""
    return PREFIXES + f"""
SELECT ?p ?o WHERE {{
  GRAPH {graph} {{
    {iri(individual)} ?p ?o .
  }}
}} LIMIT {limit}
"""


def T2_types(individual: str, graph: str = DEFAULT_GRAPH, limit: int = 50) -> str:
    """Clases a las que pertenece un individual."""
    return PREFIXES + f"""
SELECT ?type WHERE {{
  GRAPH {graph} {{
    {iri(individual)} rdf:type ?type .
    FILTER(?type != owl:NamedIndividual)
  }}
}} LIMIT {limit}
"""


def T3_list_by_class(class_iri: str, graph: str = DEFAULT_GRAPH, limit: int = 200) -> str:
    """Lista todos los individuales de una clase."""
    return PREFIXES + f"""
SELECT ?x WHERE {{
  GRAPH {graph} {{
    ?x rdf:type {iri(class_iri)} .
  }}
}} LIMIT {limit}
"""


def T4_list_by_class_and_status(
    class_iri: str,
    status_value: str,
    graph: str = DEFAULT_GRAPH,
    limit: int = 200,
) -> str:
    """Lista individuales de una clase filtrados por estado_operativo (case-insensitive)."""
    return PREFIXES + f"""
SELECT ?x ?estado WHERE {{
  GRAPH {graph} {{
    ?x rdf:type {iri(class_iri)} ;
       ex:estado_operativo ?estado .
    FILTER(LCASE(STR(?estado)) = LCASE({quote_str(status_value)}))
  }}
}} LIMIT {limit}
"""


def T5_outgoing(
    subject: str,
    prop: str,
    graph: str = DEFAULT_GRAPH,
    limit: int = 200,
) -> str:
    """Traversal directo: sujeto →[prop]→ ?objeto.

    Cubre cualquier relación directa: controls, manages, is_backed_up,
    uses_model, hosts_model, stores, generates, provides_data_to, etc.
    """
    return PREFIXES + f"""
SELECT ?o WHERE {{
  GRAPH {graph} {{
    {iri(subject)} {iri(prop)} ?o .
  }}
}} LIMIT {limit}
"""


def T6_incoming(
    obj: str,
    prop: str,
    graph: str = DEFAULT_GRAPH,
    limit: int = 200,
) -> str:
    """Traversal inverso: ?sujeto →[prop]→ objeto.

    Cubre cualquier relación inversa: quién controla X, quién opera X, etc.
    """
    return PREFIXES + f"""
SELECT ?s WHERE {{
  GRAPH {graph} {{
    ?s {iri(prop)} {iri(obj)} .
  }}
}} LIMIT {limit}
"""


# ---------------------------------------------------------------------------
# C*  Chains — joins multi-hop frecuentes
# ---------------------------------------------------------------------------

def C2_models_used_by_drone(
    drone: str,
    graph: str = DEFAULT_GRAPH,
    limit: int = 200,
) -> str:
    """Modelos que usa un dron via cadena dron→datos→nodo→modelo.

    Join de 3 saltos que no es directo con T5/T6.
    Devuelve los modelos distintos alcanzables desde el dron dado.
    """
    return PREFIXES + f"""
SELECT DISTINCT ?model WHERE {{
  GRAPH {graph} {{
    {iri(drone)} ex:generates ?data .
    ?data ex:is_used_by ?node .
    ?node ex:uses_model ?model .
  }}
}}
ORDER BY ?model
LIMIT {limit}
"""


def C3_drones_affected_if_node_fails(
    node: str,
    graph: str = DEFAULT_GRAPH,
    limit: int = 200,
) -> str:
    """Drones afectados si falla un nodo.

    Un dron se ve afectado si genera datos que ese nodo procesa.
    """
    return PREFIXES + f"""
SELECT DISTINCT ?drone WHERE {{
  GRAPH {graph} {{
    ?data ex:is_used_by {iri(node)} .
    ?drone ex:generates ?data .
  }}
}}
ORDER BY ?drone
LIMIT {limit}
"""


def C4_models_affected_if_node_fails(
    node: str,
    graph: str = DEFAULT_GRAPH,
    limit: int = 200,
) -> str:
    """Modelos afectados si falla un nodo (los que el nodo usaba)."""
    return PREFIXES + f"""
SELECT DISTINCT ?model WHERE {{
  GRAPH {graph} {{
    {iri(node)} ex:uses_model ?model .
  }}
}}
ORDER BY ?model
LIMIT {limit}
"""


def C5_data_processed_by_node(
    node: str,
    graph: str = DEFAULT_GRAPH,
    limit: int = 200,
) -> str:
    """Datos procesados por un nodo (via is_used_by inverso)."""
    return PREFIXES + f"""
SELECT DISTINCT ?data ?tipo ?fecha WHERE {{
  GRAPH {graph} {{
    ?data ex:is_used_by {iri(node)} .
    OPTIONAL {{ ?data ex:tipo ?tipo . }}
    OPTIONAL {{ ?data ex:fecha_hora ?fecha . }}
  }}
}}
ORDER BY ?fecha
LIMIT {limit}
"""


# ---------------------------------------------------------------------------
# D*  Rankings globales
# ---------------------------------------------------------------------------

def D1_top_nodes_by_data(graph: str = DEFAULT_GRAPH, limit: int = 10) -> str:
    """Ranking de nodos por cantidad de datos procesados."""
    return PREFIXES + f"""
SELECT ?node (COUNT(DISTINCT ?data) AS ?numDatos) WHERE {{
  GRAPH {graph} {{ ?data ex:is_used_by ?node . }}
}}
GROUP BY ?node
ORDER BY DESC(?numDatos)
LIMIT {limit}
"""


def D2_top_nodes_by_models(graph: str = DEFAULT_GRAPH, limit: int = 10) -> str:
    """Ranking de nodos por cantidad de modelos que usan."""
    return PREFIXES + f"""
SELECT ?node (COUNT(DISTINCT ?m) AS ?numModelos) WHERE {{
  GRAPH {graph} {{ ?node ex:uses_model ?m . }}
}}
GROUP BY ?node
ORDER BY DESC(?numModelos)
LIMIT {limit}
"""


def D3_most_critical_models(graph: str = DEFAULT_GRAPH, limit: int = 10) -> str:
    """Ranking de modelos por número de nodos que los usan."""
    return PREFIXES + f"""
SELECT ?m (COUNT(DISTINCT ?node) AS ?usedByNodes) WHERE {{
  GRAPH {graph} {{ ?node ex:uses_model ?m . }}
}}
GROUP BY ?m
ORDER BY DESC(?usedByNodes)
LIMIT {limit}
"""


def D4_top_drones_by_data(graph: str = DEFAULT_GRAPH, limit: int = 10) -> str:
    """Ranking de drones por cantidad de datos que generan."""
    return PREFIXES + f"""
SELECT ?drone (COUNT(DISTINCT ?data) AS ?numDatos) WHERE {{
  GRAPH {graph} {{ ?drone ex:generates ?data . }}
}}
GROUP BY ?drone
ORDER BY DESC(?numDatos)
LIMIT {limit}
"""


# ---------------------------------------------------------------------------
# G*  Impacto directo (1 salto) — REESCRITO sin OPTIONAL espurio
# ---------------------------------------------------------------------------

def G1_drones_affected_direct(node: str, graph: str = DEFAULT_GRAPH, limit: int = 200) -> str:
    """Drones afectados directamente si falla el nodo (1 salto).

    Un dron se ve afectado si genera datos que este nodo procesa.
    Query limpia sin OPTIONAL: cada fila tiene exactamente un dron.
    """
    return PREFIXES + f"""
SELECT DISTINCT ?drone WHERE {{
  GRAPH {graph} {{
    ?data ex:is_used_by {iri(node)} .
    ?drone ex:generates ?data .
  }}
}}
ORDER BY ?drone
LIMIT {limit}
"""


def G2_data_affected_direct(node: str, graph: str = DEFAULT_GRAPH, limit: int = 200) -> str:
    """Datos directamente afectados si falla el nodo.

    Son los datos que el nodo procesaba (is_used_by inverso).
    """
    return PREFIXES + f"""
SELECT DISTINCT ?data ?tipo ?tamanyo WHERE {{
  GRAPH {graph} {{
    ?data ex:is_used_by {iri(node)} .
    OPTIONAL {{ ?data ex:tipo ?tipo . }}
    OPTIONAL {{ ?data ex:tamaño_bytes ?tamanyo . }}
  }}
}}
ORDER BY ?data
LIMIT {limit}
"""


def G3_models_affected_direct(node: str, graph: str = DEFAULT_GRAPH, limit: int = 200) -> str:
    """Modelos directamente afectados si falla el nodo (los que el nodo usaba)."""
    return PREFIXES + f"""
SELECT DISTINCT ?model WHERE {{
  GRAPH {graph} {{
    {iri(node)} ex:uses_model ?model .
  }}
}}
ORDER BY ?model
LIMIT {limit}
"""


# ---------------------------------------------------------------------------
# F*  Impacto en cascada (property paths)
# ---------------------------------------------------------------------------

def _impact_path() -> str:
    """Property path operacional (directo + inverso) para F1/F2."""
    return (
        "(ex:generates|^ex:generates"
        "|ex:is_used_by|^ex:is_used_by"
        "|ex:uses_model|^ex:uses_model"
        "|ex:interacts_with|^ex:interacts_with)"
    )


def F1_impact_reachability(
    seed: str,
    max_hops: int = 3,
    graph: str = DEFAULT_GRAPH,
    limit: int = 500,
) -> str:
    """Entidades alcanzables en cascada desde seed a max_hops saltos."""
    return PREFIXES + f"""
SELECT DISTINCT ?x WHERE {{
  GRAPH {graph} {{
    {iri(seed)} {_impact_path()}{{1,{max_hops}}} ?x .
    FILTER(?x != {iri(seed)})
  }}
}}
ORDER BY ?x
LIMIT {limit}
"""


def F2_impact_subgraph(
    seed: str,
    max_hops: int = 3,
    graph: str = DEFAULT_GRAPH,
    limit: int = 1000,
) -> str:
    """Subgrafo de relaciones operacionales dentro del alcance N saltos.

    CORRECCIÓN: el CONSTRUCT ahora filtra sólo triples donde al menos
    uno de los dos extremos (sujeto u objeto) es alcanzable desde seed.
    Así los triples devueltos pertenecen realmente al subgrafo.
    """
    return PREFIXES + f"""
CONSTRUCT {{ ?a ?p ?b . }} WHERE {{
  GRAPH {graph} {{
    # Recoger todos los nodos alcanzables (incluido seed)
    {{
      SELECT DISTINCT ?reached WHERE {{
        {{ BIND({iri(seed)} AS ?reached) }}
        UNION
        {{ {iri(seed)} {_impact_path()}{{1,{max_hops}}} ?reached . }}
      }}
    }}
    # Filtrar triples donde ambos extremos estén en el subgrafo
    ?a ?p ?b .
    FILTER(?p IN (
      ex:generates, ex:is_used_by, ex:uses_model,
      ex:interacts_with, ex:is_backed_up, ex:provides_data_to
    ))
    FILTER EXISTS {{ {{ BIND(?a AS ?reached2) }} UNION {{ BIND(?b AS ?reached2) }}
                    {{ {iri(seed)} {_impact_path()}{{0,{max_hops}}} ?reached2 . }} }}
  }}
}}
LIMIT {limit}
"""


# ---------------------------------------------------------------------------
# R*  Resolución de nombres / aliases a IRIs
# ---------------------------------------------------------------------------

def R1_resolve_entity(
    name: str,
    entity_class: str | None = None,
    graph: str = DEFAULT_GRAPH,
    limit: int = 10,
) -> str:
    """Resuelve un nombre/alias humano a IRI(s) canónico(s).

    Estrategia UNION sobre propiedades identificadoras verificadas:
      - ex:nombre         → Nodo, Operador, Organizacion, Persona
      - ex:licencia       → Piloto  (ej. "LIC-000000")
      - ex:unidad_militar → Propietario (ej. "Batallón-1")
      - rdfs:label        → estándar OWL (previsión futura)
      - skos:prefLabel    → ídem
      - IRI fragment      → fallback para DRON-000, C2-03, Nodo-7, etc.

    Todas las comparaciones son case-insensitive con CONTAINS.
    """
    type_filter = f"?x rdf:type {iri(entity_class)} ." if entity_class else ""
    q = quote_str(name)
    return PREFIXES + f"""
SELECT DISTINCT ?x ?matched_prop ?matched_value ?type WHERE {{
  GRAPH {graph} {{
    {{
      ?x ex:nombre ?matched_value .
      BIND("ex:nombre" AS ?matched_prop)
      FILTER(CONTAINS(LCASE(STR(?matched_value)), LCASE({q})))
    }} UNION {{
      ?x ex:licencia ?matched_value .
      BIND("ex:licencia" AS ?matched_prop)
      FILTER(CONTAINS(LCASE(STR(?matched_value)), LCASE({q})))
    }} UNION {{
      ?x ex:unidad_militar ?matched_value .
      BIND("ex:unidad_militar" AS ?matched_prop)
      FILTER(CONTAINS(LCASE(STR(?matched_value)), LCASE({q})))
    }} UNION {{
      ?x rdfs:label ?matched_value .
      BIND("rdfs:label" AS ?matched_prop)
      FILTER(CONTAINS(LCASE(STR(?matched_value)), LCASE({q})))
    }} UNION {{
      ?x skos:prefLabel ?matched_value .
      BIND("skos:prefLabel" AS ?matched_prop)
      FILTER(CONTAINS(LCASE(STR(?matched_value)), LCASE({q})))
    }} UNION {{
      BIND(STR(?x) AS ?matched_value)
      BIND("iri_fragment" AS ?matched_prop)
      FILTER(CONTAINS(LCASE(STR(?x)), LCASE({q})))
    }}
    OPTIONAL {{ ?x rdf:type ?type . FILTER(?type != owl:NamedIndividual) }}
    {type_filter}
  }}
}}
LIMIT {limit}
"""


def R2_resolve_entity_by_fragment(
    name: str,
    entity_class: str | None = None,
    graph: str = DEFAULT_GRAPH,
    limit: int = 10,
) -> str:
    """Fallback: busca únicamente por fragmento de IRI (case-insensitive)."""
    type_filter = f"?x rdf:type {iri(entity_class)} ." if entity_class else ""
    return PREFIXES + f"""
SELECT DISTINCT ?x ?type WHERE {{
  GRAPH {graph} {{
    ?x ?p ?o .
    OPTIONAL {{ ?x rdf:type ?type . FILTER(?type != owl:NamedIndividual) }}
    {type_filter}
    FILTER(CONTAINS(LCASE(STR(?x)), LCASE({quote_str(name)})))
  }}
}}
LIMIT {limit}
"""


# ---------------------------------------------------------------------------
# S*  Schema / introspección de la ontología
# ---------------------------------------------------------------------------

def S1_list_classes(graph: str = DEFAULT_GRAPH, limit: int = 200) -> str:
    """Lista clases del dominio con al menos un individual en el grafo.

    CORRECCIÓN: en lugar de buscar owl:Class en el TBox (donde las clases
    no tienen rdfs:label), extrae las clases desde el ABox (individuales
    reales), filtrando clases del vocabulario OWL/RDF.
    """
    return PREFIXES + f"""
SELECT DISTINCT ?class (COUNT(?x) AS ?numIndividuals) WHERE {{
  GRAPH {graph} {{
    ?x rdf:type ?class .
    FILTER(?class != owl:NamedIndividual)
    FILTER(STRSTARTS(STR(?class), "{EX}"))
  }}
}}
GROUP BY ?class
ORDER BY DESC(?numIndividuals)
LIMIT {limit}
"""


def S2_list_properties_of_class(
    class_iri: str,
    graph: str = DEFAULT_GRAPH,
    limit: int = 200,
) -> str:
    """Propiedades usadas por individuales de una clase (desde ABox).

    CORRECCIÓN: la ontología no declara rdfs:domain en las propiedades,
    así que inferimos las propiedades desde los triples reales de los
    individuales de esa clase. Distingue object properties (valor=IRI)
    de datatype properties (valor=literal).
    """
    return PREFIXES + f"""
SELECT DISTINCT ?prop
  (IF(ISIRI(?sample_val), "ObjectProperty", "DatatypeProperty") AS ?prop_type)
  (SAMPLE(?sample_val) AS ?sample_value)
WHERE {{
  GRAPH {graph} {{
    ?x rdf:type {iri(class_iri)} .
    ?x ?prop ?sample_val .
    FILTER(?prop != rdf:type)
    FILTER(STRSTARTS(STR(?prop), "{EX}"))
  }}
}}
GROUP BY ?prop
ORDER BY ?prop
LIMIT {limit}
"""


def S3_list_relations_between_classes(
    graph: str = DEFAULT_GRAPH,
    limit: int = 200,
) -> str:
    """Relaciones (object properties) entre clases, inferidas desde el ABox.

    CORRECCIÓN: no usa rdfs:domain/rdfs:range (no declarados), sino que
    infiere domain y range desde los triples reales del grafo.
    """
    return PREFIXES + f"""
SELECT DISTINCT ?domain_class ?prop ?range_class WHERE {{
  GRAPH {graph} {{
    ?s ?prop ?o .
    ?s rdf:type ?domain_class .
    ?o rdf:type ?range_class .
    FILTER(?prop != rdf:type)
    FILTER(?domain_class != owl:NamedIndividual)
    FILTER(?range_class  != owl:NamedIndividual)
    FILTER(STRSTARTS(STR(?prop),         "{EX}"))
    FILTER(STRSTARTS(STR(?domain_class), "{EX}"))
    FILTER(STRSTARTS(STR(?range_class),  "{EX}"))
    FILTER(ISIRI(?o))
  }}
}}
ORDER BY ?domain_class ?prop
LIMIT {limit}
"""


def S4_describe_class(class_iri: str, graph: str = DEFAULT_GRAPH, limit: int = 5) -> str:
    """Muestra un ejemplo de individual de la clase con todos sus atributos.

    Útil para entender la estructura de una clase concreta desde datos reales.
    Devuelve como máximo `limit` individuales de ejemplo con sus propiedades.
    """
    return PREFIXES + f"""
SELECT ?x ?p ?o WHERE {{
  GRAPH {graph} {{
    ?x rdf:type {iri(class_iri)} .
    ?x ?p ?o .
    FILTER(?p != rdf:type)
  }}
}}
ORDER BY ?x ?p
LIMIT {limit * 20}
"""


# ---------------------------------------------------------------------------
# B*  Conteos puntuales (utilities internas, sin tool de catálogo)
# ---------------------------------------------------------------------------

def B4_count_models_used_by_node(node: str, graph: str = DEFAULT_GRAPH) -> str:
    """Cuenta modelos distintos que usa un nodo."""
    return PREFIXES + f"""
SELECT (COUNT(DISTINCT ?m) AS ?numModelos) WHERE {{
  GRAPH {graph} {{ {iri(node)} ex:uses_model ?m . }}
}}
"""


def B5_count_data_processed_by_node(node: str, graph: str = DEFAULT_GRAPH) -> str:
    """Cuenta datos distintos procesados por un nodo."""
    return PREFIXES + f"""
SELECT (COUNT(DISTINCT ?data) AS ?numDatos) WHERE {{
  GRAPH {graph} {{ ?data ex:is_used_by {iri(node)} . }}
}}
"""


def B6_count_related(
    entity: str,
    prop: str,
    direction: str = "outgoing",
    graph: str = DEFAULT_GRAPH,
) -> str:
    """Conteo genérico de entidades relacionadas por una propiedad.

    direction='outgoing': cuenta ?o donde entity →[prop]→ ?o
    direction='incoming': cuenta ?s donde ?s →[prop]→ entity
    """
    if direction == "outgoing":
        pattern = f"{iri(entity)} {iri(prop)} ?related ."
    else:
        pattern = f"?related {iri(prop)} {iri(entity)} ."
    return PREFIXES + f"""
SELECT (COUNT(DISTINCT ?related) AS ?count) WHERE {{
  GRAPH {graph} {{
    {pattern}
  }}
}}
"""


# ---------------------------------------------------------------------------
# N*  Nuevas capacidades genéricas (v4)
# ---------------------------------------------------------------------------

def N1_traverse_graph(
    start_entity: str,
    relations: list[dict],
    target_class: str | None = None,
    return_mode: str = "entities",
    graph: str = DEFAULT_GRAPH,
    limit: int = 200,
) -> str:
    """Traversal multi-hop siguiendo una secuencia explícita de relaciones.

    Cada elemento de `relations` es {property: str, direction: 'outgoing'|'incoming'}.
    Construye una cadena de JOINs SPARQL donde cada salto filtra la variable
    intermedia producida por el salto anterior.

    Ejemplos:
      1 salto outgoing  : [{property: 'controls'}]
        → SELECT ?hop1 WHERE { seed ex:controls ?hop1 }
      1 salto incoming  : [{property: 'is_part_of', direction: 'incoming'}]
        → SELECT ?hop1 WHERE { ?hop1 ex:is_part_of seed }
      3 saltos mixtos   : [{property: 'generates'}, {property: 'is_used_by', direction: 'incoming'}, {property: 'uses_model'}]
        → SELECT ?hop3 WHERE {
            seed ex:generates ?hop1 .
            ?hop2 ex:is_used_by ?hop1 .    ← incoming: objeto es ?hop1
            ?hop2 ex:uses_model ?hop3 .
          }
    """
    if not relations:
        raise ValueError("N1_traverse_graph: relations no puede estar vacío")

    lines: list[str] = []
    prev_var = iri(start_entity)

    for i, rel in enumerate(relations, start=1):
        prop      = iri(rel["property"])
        direction = rel.get("direction", "outgoing")
        curr_var  = f"?hop{i}"

        if direction == "outgoing":
            lines.append(f"    {prev_var} {prop} {curr_var} .")
        else:
            lines.append(f"    {curr_var} {prop} {prev_var} .")

        prev_var = curr_var

    final_var = f"?hop{len(relations)}"
    pattern   = "\n".join(lines)

    type_filter = ""
    if target_class:
        type_filter = f"\n    {final_var} rdf:type {iri(target_class)} ."

    if return_mode == "count":
        return PREFIXES + f"""
SELECT (COUNT(DISTINCT {final_var}) AS ?count) WHERE {{
  GRAPH {graph} {{
{pattern}{type_filter}
  }}
}}
"""
    else:
        return PREFIXES + f"""
SELECT DISTINCT {final_var} ?type WHERE {{
  GRAPH {graph} {{
{pattern}{type_filter}
    OPTIONAL {{ {final_var} rdf:type ?type . FILTER(?type != owl:NamedIndividual) }}
  }}
}}
ORDER BY {final_var}
LIMIT {limit}
"""


def N2_filter_entities(
    class_iri: str,
    filters: list[dict],
    return_mode: str = "entities",
    graph: str = DEFAULT_GRAPH,
    limit: int = 200,
) -> str:
    """Lista entidades de una clase que cumplen condiciones sobre propiedades datatype.

    Cada filtro: {property: str, operator: '='|'!='|'>'|'<'|'>='|'<='|'contains', value: str}.
    Todos los filtros se combinan con AND.

    El operador 'contains' usa CONTAINS(LCASE(STR(?val)), LCASE("value")).
    Los operadores numéricos (>, <, >=, <=) intentan castear el valor a xsd:decimal.
    """
    _OP_MAP = {"=": "=", "!=": "!=", ">": ">", "<": "<", ">=": ">=", "<=": "<="}

    filter_clauses: list[str] = []
    optional_binds: list[str] = []

    for idx, f in enumerate(filters):
        prop     = f["property"]
        operator = f["operator"]
        value    = f["value"]
        var      = f"?fval{idx}"

        optional_binds.append(f"    OPTIONAL {{ ?x ex:{prop} {var} . }}")

        if operator == "contains":
            filter_clauses.append(
                f"    FILTER(BOUND({var}) && CONTAINS(LCASE(STR({var})), LCASE({quote_str(value)})))"
            )
        elif operator in ("=", "!="):
            filter_clauses.append(
                f"    FILTER(BOUND({var}) && STR({var}) {_OP_MAP[operator]} {quote_str(value)})"
            )
        else:
            # Comparación numérica: cast a decimal
            filter_clauses.append(
                f"    FILTER(BOUND({var}) && xsd:decimal(STR({var})) {_OP_MAP[operator]} {value})"
            )

    binds_str   = "\n".join(optional_binds)
    filters_str = "\n".join(filter_clauses)

    if return_mode == "count":
        return PREFIXES + f"""
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT (COUNT(DISTINCT ?x) AS ?count) WHERE {{
  GRAPH {graph} {{
    ?x rdf:type {iri(class_iri)} .
{binds_str}
{filters_str}
  }}
}}
"""
    else:
        return PREFIXES + f"""
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT DISTINCT ?x WHERE {{
  GRAPH {graph} {{
    ?x rdf:type {iri(class_iri)} .
{binds_str}
{filters_str}
  }}
}}
ORDER BY ?x
LIMIT {limit}
"""


def N3_aggregate_entities(
    class_iri: str,
    prop: str,
    aggregation: str,
    filters: list[dict] | None = None,
    group_by: str | None = None,
    order_by: str = "desc",
    limit: int = 10,
    graph: str = DEFAULT_GRAPH,
) -> str:
    """Calcula estadísticas agregadas sobre una propiedad de una clase.

    Si prop == '*': COUNT(*) de entidades (útil para conteos simples).
    Si group_by está presente: GROUP BY ?group_val ORDER BY asc|desc.
    Los filtros (mismo formato que N2) se aplican antes de agregar.
    """
    aggregation = aggregation.upper()
    if aggregation not in ("COUNT", "AVG", "SUM", "MIN", "MAX"):
        raise ValueError(f"Agregación desconocida: {aggregation!r}")

    # Expresión de agregación
    if prop == "*":
        agg_expr = f"(COUNT(DISTINCT ?x) AS ?value)"
        prop_bind = ""
    else:
        agg_expr = f"({aggregation}(xsd:decimal(STR(?agg_val))) AS ?value)"
        prop_bind = f"\n    OPTIONAL {{ ?x ex:{prop} ?agg_val . }}"

    # Filtros opcionales
    filter_parts: list[str] = []
    if filters:
        for idx, f in enumerate(filters):
            fprop    = f["property"]
            operator = f["operator"]
            value    = f["value"]
            fvar     = f"?ffval{idx}"
            filter_parts.append(f"    OPTIONAL {{ ?x ex:{fprop} {fvar} . }}")
            if operator == "contains":
                filter_parts.append(
                    f"    FILTER(BOUND({fvar}) && CONTAINS(LCASE(STR({fvar})), LCASE({quote_str(value)})))"
                )
            elif operator in ("=", "!="):
                op_sym = "=" if operator == "=" else "!="
                filter_parts.append(
                    f"    FILTER(BOUND({fvar}) && STR({fvar}) {op_sym} {quote_str(value)})"
                )
            else:
                filter_parts.append(
                    f"    FILTER(BOUND({fvar}) && xsd:decimal(STR({fvar})) {operator} {value})"
                )
    filters_str = "\n".join(filter_parts)

    # GROUP BY
    if group_by:
        order_dir  = "DESC" if order_by == "desc" else "ASC"
        group_bind = f"\n    OPTIONAL {{ ?x ex:{group_by} ?group_val . }}"
        return PREFIXES + f"""
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT ?group_val {agg_expr} WHERE {{
  GRAPH {graph} {{
    ?x rdf:type {iri(class_iri)} .{group_bind}{prop_bind}
{filters_str}
  }}
}}
GROUP BY ?group_val
ORDER BY {order_dir}(?value)
LIMIT {limit}
"""
    else:
        return PREFIXES + f"""
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
SELECT {agg_expr} WHERE {{
  GRAPH {graph} {{
    ?x rdf:type {iri(class_iri)} .{prop_bind}
{filters_str}
  }}
}}
"""


def N4_scenario_summary(graph: str = DEFAULT_GRAPH) -> str:
    """Resumen ejecutivo: totales por clase + breakdown por estado_operativo.

    Devuelve una sola query con UNION que recoge:
      - Total de individuales por clase (todas las clases)
      - Para clases con estado_operativo: conteo por estado
    """
    classes_with_status = ["Dron", "Piloto", "Operador", "C2", "Servidor"]
    all_classes = [
        "Dron", "Nodo", "Servidor", "Modelo", "Datos",
        "C2", "Operador", "Piloto", "Propietario", "Organizacion", "Persona",
    ]

    union_parts = []

    # Totales por clase
    for cls in all_classes:
        union_parts.append(f"""\
  {{
    SELECT ({quote_str(cls)} AS ?class) "total" AS ?breakdown
           (COUNT(DISTINCT ?x) AS ?count)
    WHERE {{
      GRAPH {graph} {{ ?x rdf:type ex:{cls} . }}
    }}
  }}""")

    # Breakdown por estado_operativo
    for cls in classes_with_status:
        union_parts.append(f"""\
  {{
    SELECT ({quote_str(cls)} AS ?class) ?breakdown
           (COUNT(DISTINCT ?x) AS ?count)
    WHERE {{
      GRAPH {graph} {{
        ?x rdf:type ex:{cls} ;
           ex:estado_operativo ?breakdown .
      }}
    }}
    GROUP BY ?breakdown
  }}""")

    union_str = "\nUNION\n".join(union_parts)

    return PREFIXES + f"""
SELECT ?class ?breakdown ?count WHERE {{
{union_str}
}}
ORDER BY ?class ?breakdown
"""