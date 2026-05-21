from fastapi import FastAPI, Query
from fastapi.responses import Response
import httpx

FUSEKI_QUERY_URL = "http://localhost:3030/dron/query"

PREFIXES = """\
PREFIX ex:   <http://www.semanticweb.org/carlo/ontologies/2024/10/untitled-ontology-4#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
"""

SCENARIO_GRAPH = "urn:scenario:static"

BASE = "http://www.semanticweb.org/carlo/ontologies/2024/10/untitled-ontology-4#"

app = FastAPI(title="COP Static API", version="1.0")


def ex_uri(local_id: str) -> str:
    """Build a full URI from a local id like 'Nodo-1' or 'DRON-001'."""
    local_id = local_id.strip()
    if local_id.startswith("ex:"):
        local_id = local_id[3:]
    return BASE + local_id


async def sparql_select(query: str):
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(
            FUSEKI_QUERY_URL,
            params={"query": query},
            headers={"Accept": "application/sparql-results+json"},
        )
        r.raise_for_status()
        return r.json()


async def sparql_construct(query: str, accept: str):
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(
            FUSEKI_QUERY_URL,
            params={"query": query},
            headers={"Accept": accept},
        )
        r.raise_for_status()
        return r.text


@app.get("/cop/org/count")
async def org_count():
    q = PREFIXES + "SELECT (COUNT(?o) AS ?numOrganizaciones) WHERE { ?o a ex:Organizacion . }"
    return await sparql_select(q)


@app.get("/cop/node/models/count")
async def node_models_count(node_id: str = Query(..., description="Ej: Nodo-1")):
    node_uri = ex_uri(node_id)
    q = PREFIXES + f"""
SELECT (COUNT(DISTINCT ?m) AS ?numModelos) WHERE {{
  GRAPH <{SCENARIO_GRAPH}> {{ <{node_uri}> ex:uses_model ?m . }}
}}
"""
    return await sparql_select(q)


@app.get("/cop/drone/data")
async def drone_data(drone_id: str = Query(..., description="Ej: DRON-001 o Dron-1")):
    drone_uri = ex_uri(drone_id)
    q = PREFIXES + f"""
SELECT ?data WHERE {{
  GRAPH <{SCENARIO_GRAPH}> {{ <{drone_uri}> ex:generates ?data . }}
}}
ORDER BY ?data
"""
    return await sparql_select(q)


@app.get("/cop/drone/models")
async def drone_models(drone_id: str = Query(..., description="Ej: DRON-001 o Dron-1")):
    drone_uri = ex_uri(drone_id)
    q = PREFIXES + f"""
SELECT DISTINCT ?m WHERE {{
  GRAPH <{SCENARIO_GRAPH}> {{
    <{drone_uri}> ex:generates ?data .
    ?data ex:is_used_by ?node .
    ?node ex:uses_model ?m .
  }}
}}
ORDER BY ?m
"""
    return await sparql_select(q)


@app.get("/graph/drone_chain")
async def graph_drone_chain(
    drone_id: str = Query(..., description="Ej: DRON-001 o Dron-1"),
    format: str = Query("jsonld", description="turtle|jsonld|ntriples"),
):
    drone_uri = ex_uri(drone_id)

    accept_map = {
        "turtle": "text/turtle",
        "jsonld": "application/ld+json",
        "ntriples": "application/n-triples",
    }
    accept = accept_map.get(format.lower(), "application/ld+json")

    q = PREFIXES + f"""
CONSTRUCT {{
  <{drone_uri}> a ex:Dron ;
             rdfs:label ?dl ;
             ex:generates ?data .

  ?data a ex:Datos ;
        rdfs:label ?datal ;
        ex:is_used_by ?node .

  ?node a ex:Nodo ;
        rdfs:label ?nl ;
        ex:uses_model ?model .

  ?model a ex:Modelo ;
         rdfs:label ?ml .
}}
WHERE {{
  GRAPH <{SCENARIO_GRAPH}> {{
    <{drone_uri}> ex:generates ?data .
    ?data ex:is_used_by ?node .
    ?node ex:uses_model ?model .
  }}
  OPTIONAL {{ <{drone_uri}> rdfs:label ?dl }}
  OPTIONAL {{ ?data rdfs:label ?datal }}
  OPTIONAL {{ ?node rdfs:label ?nl }}
  OPTIONAL {{ ?model rdfs:label ?ml }}
}}
"""
    graph_text = await sparql_construct(q, accept)
    return Response(content=graph_text, media_type=accept)

@app.get("/graph/entity")
async def graph_entity(
    id: str = Query(..., description="Ej: Nodo-1, DRON-001, Redes%20Neuronales_Hist-1"),
    hops: int = Query(1, ge=1, le=2, description="1 o 2 saltos (2 es más grande)"),
    format: str = Query("jsonld", description="turtle|jsonld|ntriples"),
):
    """
    Devuelve un subgrafo RDF alrededor de una entidad:
    - hops=1: todo lo que SALE y ENTRA (1 salto)
    - hops=2: añade un salto extra desde los vecinos (más grande)
    """
    entity_uri = ex_uri(id)

    accept_map = {
        "turtle": "text/turtle",
        "jsonld": "application/ld+json",
        "ntriples": "application/n-triples",
    }
    accept = accept_map.get(format.lower(), "application/ld+json")

    if hops == 1:
        q = PREFIXES + f"""
CONSTRUCT {{
  <{entity_uri}> ?p ?o .
  ?s ?p2 <{entity_uri}> .
  <{entity_uri}> rdfs:label ?l .
  ?o rdfs:label ?ol .
  ?s rdfs:label ?sl .
}}
WHERE {{
  {{
    GRAPH <{SCENARIO_GRAPH}> {{ <{entity_uri}> ?p ?o . }}
    OPTIONAL {{ <{entity_uri}> rdfs:label ?l }}
    OPTIONAL {{ ?o rdfs:label ?ol }}
  }}
  UNION
  {{
    GRAPH <{SCENARIO_GRAPH}> {{ ?s ?p2 <{entity_uri}> . }}
    OPTIONAL {{ ?s rdfs:label ?sl }}
  }}
}}
"""
    else:
        # hops=2: incluye también triples salientes/entrantes de los vecinos (un paso adicional)
        q = PREFIXES + f"""
CONSTRUCT {{
  <{entity_uri}> ?p ?o .
  ?s ?p2 <{entity_uri}> .
  ?o ?p3 ?o2 .
  ?s2 ?p4 ?s .

  <{entity_uri}> rdfs:label ?l .
  ?o rdfs:label ?ol .
  ?s rdfs:label ?sl .
  ?o2 rdfs:label ?o2l .
  ?s2 rdfs:label ?s2l .
}}
WHERE {{
  GRAPH <{SCENARIO_GRAPH}> {{
    OPTIONAL {{ <{entity_uri}> ?p ?o . }}
    OPTIONAL {{ ?s ?p2 <{entity_uri}> . }}
    OPTIONAL {{ ?o ?p3 ?o2 . }}
    OPTIONAL {{ ?s2 ?p4 ?s . }}
  }}
  OPTIONAL {{ <{entity_uri}> rdfs:label ?l }}
  OPTIONAL {{ ?o rdfs:label ?ol }}
  OPTIONAL {{ ?s rdfs:label ?sl }}
  OPTIONAL {{ ?o2 rdfs:label ?o2l }}
  OPTIONAL {{ ?s2 rdfs:label ?s2l }}
}}
"""
    graph_text = await sparql_construct(q, accept)
    return Response(content=graph_text, media_type=accept)


from rdflib import Graph

def short_id(uri: str) -> str:
    """Convierte una URI larga en un id corto."""
    if "#" in uri:
        return uri.split("#")[-1]
    return uri.rsplit("/", 1)[-1]


@app.get("/graph/entity_view")
async def graph_entity_view(
    id: str = Query(..., description="Ej: Nodo-1, Dron-1, DRON-001"),
    hops: int = Query(1, ge=1, le=2),
):
    # 1) Reutiliza el CONSTRUCT de /graph/entity en turtle (fácil de parsear)
    entity_uri = ex_uri(id)

    q = PREFIXES + f"""
CONSTRUCT {{
  <{entity_uri}> ?p ?o .
  ?s ?p2 <{entity_uri}> .
  ?o ?p3 ?o2 .
  ?s2 ?p4 ?s .
}}
WHERE {{
  GRAPH <{SCENARIO_GRAPH}> {{
    OPTIONAL {{ <{entity_uri}> ?p ?o . }}
    OPTIONAL {{ ?s ?p2 <{entity_uri}> . }}
    { "OPTIONAL { ?o ?p3 ?o2 . } OPTIONAL { ?s2 ?p4 ?s . }" if hops == 2 else "" }
  }}
}}
"""
    ttl = await sparql_construct(q, "text/turtle")

    # 2) Parse RDF -> nodes+edges
    g = Graph()
    g.parse(data=ttl, format="turtle")

    nodes = {}
    edges = []
    edge_id = 0

    for s, p, o in g:
        s_str = str(s)
        p_str = str(p)
        o_str = str(o)

        # Nodo sujeto
        if s_str not in nodes:
            nodes[s_str] = {"id": s_str, "label": short_id(s_str)}
        # Nodo objeto (si es URI; si es literal lo representamos como nodo literal)
        if o_str not in nodes:
            if o_str.startswith("http://") or o_str.startswith("https://") or o_str.startswith("urn:"):
                nodes[o_str] = {"id": o_str, "label": short_id(o_str)}
            else:
                # literal
                lit_id = f"literal:{o_str}"
                nodes[lit_id] = {"id": lit_id, "label": o_str}
                o_str = lit_id

        edges.append({
            "id": f"e{edge_id}",
            "source": s_str,
            "target": o_str,
            "label": short_id(p_str),
            "predicate": p_str
        })
        edge_id += 1

    # 3) Marca el nodo central
    center = entity_uri
    if center in nodes:
        nodes[center]["center"] = True

    return {"center": center, "nodes": list(nodes.values()), "edges": edges}



@app.get("/ping")
async def ping():
    return {"ok": True}

@app.get("/cop/node/impact")
async def node_impact(node_id: str = Query(..., description="Ej: Nodo-1")):
    node_uri = ex_uri(node_id)

    q = PREFIXES + f"""
SELECT DISTINCT ?drone ?data ?model WHERE {{
  GRAPH <{SCENARIO_GRAPH}> {{
    ?data ex:is_used_by <{node_uri}> .
    ?drone ex:generates ?data .
    <{node_uri}> ex:uses_model ?model .
  }}
}}
"""
    result = await sparql_select(q)

    drones = set()
    data_items = set()
    models = set()

    for binding in result["results"]["bindings"]:
        if "drone" in binding:
            drones.add(binding["drone"]["value"])
        if "data" in binding:
            data_items.add(binding["data"]["value"])
        if "model" in binding:
            models.add(binding["model"]["value"])

    return {
        "node": node_id,
        "affected_drones": sorted(list(drones)),
        "affected_data": sorted(list(data_items)),
        "affected_models": sorted(list(models))
    }


@app.get("/cop/impact_view")
async def impact_view(
    entity_id: str = Query(..., description="Ej: Nodo-1, Dron-1, Modelo-1"),
    depth: int = Query(2, ge=1, le=6, description="Profundidad de propagación (1..6)"),
):
    """
    Devuelve subgrafo de impacto propagado hasta 'depth' saltos,
    siguiendo predicados operacionales (y sus inversas).
    """
    center_uri = ex_uri(entity_id)

    # Predicados por los que se propaga impacto (añade/quita según tu doctrina)
    # Incluimos inversas con ^ para poder propagarnos en ambos sentidos.
    impact_path = (
        "(ex:generates|^ex:generates|"
        "ex:is_used_by|^ex:is_used_by|"
        "ex:uses_model|^ex:uses_model|"
        "ex:hosts_model|^ex:hosts_model|"
        "ex:stores|^ex:stores|"
        "ex:controls|^ex:controls|"
        "ex:manages|^ex:manages|"
        "ex:is_managed_by|^ex:is_managed_by|"
        "ex:is_backed_up|^ex:is_backed_up|"
        "ex:interacts_with|^ex:interacts_with"
        f"){{1,{depth}}}"
    )

    # 1) CONSTRUCT: nodos alcanzables + aristas operacionales entre ellos
    q = PREFIXES + f"""
CONSTRUCT {{
  <{center_uri}> ?p ?o .
  ?s ?p <{center_uri}> .

  ?a ?p2 ?b .
}}
WHERE {{
  # a) Encuentra el conjunto de nodos alcanzables en <= depth saltos
  {{
    SELECT DISTINCT ?v WHERE {{
      GRAPH <{SCENARIO_GRAPH}> {{
        <{center_uri}> {impact_path} ?v .
      }}
    }}
  }}

  # b) Construye aristas operacionales entre nodos alcanzables (incluye el centro)
  GRAPH <{SCENARIO_GRAPH}> {{
    {{
      # edges entre alcanzables
      ?a ?p2 ?b .
      FILTER(?a = <{center_uri}> || ?a = ?v || EXISTS {{
        <{center_uri}> {impact_path} ?a .
      }})
      FILTER(?b = <{center_uri}> || ?b = ?v || EXISTS {{
        <{center_uri}> {impact_path} ?b .
      }})

      # filtra para quedarnos solo con predicados “operacionales”
      FILTER(?p2 IN (
        ex:generates, ex:is_used_by, ex:uses_model, ex:hosts_model, ex:stores,
        ex:controls, ex:manages, ex:is_managed_by, ex:is_backed_up, ex:interacts_with
      ))
    }}
    UNION
    {{
      <{center_uri}> ?p ?o .
    }}
    UNION
    {{
      ?s ?p <{center_uri}> .
    }}
  }}
}}
"""

    ttl = await sparql_construct(q, "text/turtle")

    # 2) Reutiliza tu conversión RDF -> nodes+edges (rdflib)
    g = Graph()
    g.parse(data=ttl, format="turtle")

    nodes = {}
    edges = []
    edge_id = 0

    for s, p, o in g:
        s_str = str(s)
        p_str = str(p)
        o_str = str(o)

        if s_str not in nodes:
            nodes[s_str] = {"id": s_str, "label": short_id(s_str)}
        # objeto
        if o_str.startswith("http://") or o_str.startswith("https://") or o_str.startswith("urn:"):
            if o_str not in nodes:
                nodes[o_str] = {"id": o_str, "label": short_id(o_str)}
            target_id = o_str
        else:
            lit_id = f"literal:{o_str}"
            if lit_id not in nodes:
                nodes[lit_id] = {"id": lit_id, "label": o_str}
            target_id = lit_id

        edges.append({
            "id": f"e{edge_id}",
            "source": s_str,
            "target": target_id,
            "label": short_id(p_str),
            "predicate": p_str
        })
        edge_id += 1

    if center_uri in nodes:
        nodes[center_uri]["center"] = True

    return {
        "center": center_uri,
        "depth": depth,
        "nodes": list(nodes.values()),
        "edges": edges
    }


@app.get("/cop/impact_pro")
async def impact_pro(
    entity_id: str = Query(..., description="Ej: Nodo-1, Dron-1, Modelo-1"),
    depth: int = Query(2, ge=1, le=6, description="Profundidad 1..6"),
    top_k: int = Query(10, ge=1, le=50, description="Top críticos por grado"),
):
    center_uri = ex_uri(entity_id)

    # Predicados por los que se propaga impacto (bidireccional con ^)
    impact_path = (
        "(ex:generates|^ex:generates|"
        "ex:is_used_by|^ex:is_used_by|"
        "ex:uses_model|^ex:uses_model|"
        "ex:hosts_model|^ex:hosts_model|"
        "ex:stores|^ex:stores|"
        "ex:controls|^ex:controls|"
        "ex:manages|^ex:manages|"
        "ex:is_managed_by|^ex:is_managed_by|"
        "ex:is_backed_up|^ex:is_backed_up|"
        "ex:interacts_with|^ex:interacts_with"
        f"){{1,{depth}}}"
    )

    # CONSTRUCT:
    # - aristas operacionales dentro del subgrafo (desde el named graph del escenario)
    # - y metadatos (rdf:type, rdfs:label) desde cualquier grafo (no forzamos GRAPH)
    q = PREFIXES + f"""
CONSTRUCT {{
  ?a ?p ?b .
  ?x a ?t .
  ?x rdfs:label ?l .
}}
WHERE {{
  # 1) conjunto de alcanzables en <=depth saltos (sobre el grafo del escenario)
  {{
    SELECT DISTINCT ?x WHERE {{
      GRAPH <{SCENARIO_GRAPH}> {{
        <{center_uri}> {impact_path} ?x .
      }}
    }}
  }}

  # 2) Aristas operacionales dentro del conjunto (incluye el centro)
  GRAPH <{SCENARIO_GRAPH}> {{
    ?a ?p ?b .
    FILTER(?p IN (
      ex:generates, ex:is_used_by, ex:uses_model, ex:hosts_model, ex:stores,
      ex:controls, ex:manages, ex:is_managed_by, ex:is_backed_up, ex:interacts_with
    ))
    FILTER(
      (?a = <{center_uri}> || EXISTS {{ <{center_uri}> {impact_path} ?a }}) &&
      (?b = <{center_uri}> || EXISTS {{ <{center_uri}> {impact_path} ?b }})
    )
  }}

  # 3) Tipos/labels para el centro y alcanzables (pueden estar fuera del named graph)
  OPTIONAL {{
    VALUES ?x {{ <{center_uri}> }}
    ?x a ?t .
  }}
  OPTIONAL {{
    ?x a ?t .
  }}
  OPTIONAL {{
    VALUES ?x {{ <{center_uri}> }}
    ?x rdfs:label ?l .
  }}
  OPTIONAL {{
    ?x rdfs:label ?l .
  }}
}}
"""

    ttl = await sparql_construct(q, "text/turtle")

    g = Graph()
    g.parse(data=ttl, format="turtle")

    # Construye nodes/edges, capturando types y label si vienen
    nodes = {}  # uri -> node dict
    edges = []
    edge_id = 0

    # Guardamos types y labels detectados
    types_map = {}   # uri -> set(type_uri)
    labels_map = {}  # uri -> label str

    RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
    RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"

    # Primero extrae tipos/labels
    for s, p, o in g:
        s_str, p_str, o_str = str(s), str(p), str(o)
        if p_str == RDF_TYPE and (o_str.startswith("http://") or o_str.startswith("https://") or o_str.startswith("urn:")):
            types_map.setdefault(s_str, set()).add(o_str)
        if p_str == RDFS_LABEL and not (o_str.startswith("http://") or o_str.startswith("https://") or o_str.startswith("urn:")):
            # literal
            labels_map.setdefault(s_str, o_str)

    # Luego extrae aristas (ignoramos rdf:type y rdfs:label como edges de visualización)
    for s, p, o in g:
        s_str, p_str, o_str = str(s), str(p), str(o)

        if p_str in (RDF_TYPE, RDFS_LABEL):
            continue

        # nodos sujeto
        if s_str not in nodes:
            nodes[s_str] = {"id": s_str, "label": labels_map.get(s_str, short_id(s_str))}
        # objeto (URI o literal)
        if o_str.startswith(("http://", "https://", "urn:")):
            if o_str not in nodes:
                nodes[o_str] = {"id": o_str, "label": labels_map.get(o_str, short_id(o_str))}
            target_id = o_str
        else:
            lit_id = f"literal:{o_str}"
            if lit_id not in nodes:
                nodes[lit_id] = {"id": lit_id, "label": o_str, "literal": True}
            target_id = lit_id

        edges.append({
            "id": f"e{edge_id}",
            "source": s_str,
            "target": target_id,
            "label": short_id(p_str),
            "predicate": p_str
        })
        edge_id += 1

    # Enriquecer nodes con types
    for uri, node in nodes.items():
        if uri.startswith("literal:"):
            continue
        tset = types_map.get(uri, set())
        clean_types = []
        for t in tset:
            ts = short_id(t)
            if ts in ("NamedIndividual",):
                continue
            clean_types.append(ts)
        node["types"] = sorted(clean_types)

    # Marcar centro
    if center_uri in nodes:
        nodes[center_uri]["center"] = True

    # --- SUMMARY PRO ---

    # Conteo por tipo (solo recursos URI, no literales)
    counts_by_type = {}
    for uri, tset in types_map.items():
        # cuenta el nodo si está en el subgrafo visualizado
        if uri not in nodes:
            continue
        for t in tset:
            t_short = short_id(t)
            if t_short in ("NamedIndividual",):
                continue
            counts_by_type[t_short] = counts_by_type.get(t_short, 0) + 1

    # Centralidad simple: grado (undirected) dentro del subgrafo
    degree = {uri: 0 for uri in nodes.keys() if not uri.startswith("literal:")}
    for e in edges:
        s = e["source"]
        t = e["target"]
        if not s.startswith("literal:") and s in degree:
            degree[s] += 1
        if not t.startswith("literal:") and t in degree:
            degree[t] += 1

    # Top críticos
    critical = sorted(degree.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    critical_nodes = []
    for uri, deg in critical:
        n = nodes.get(uri, {"id": uri, "label": short_id(uri)})
        critical_nodes.append({
            "id": uri,
            "label": n.get("label", short_id(uri)),
            "degree": deg,
            "types": n.get("types", [])
        })
    key_types = ["Dron", "Nodo", "Datos", "Modelo", "Organizacion", "C2"]
    impact_counts = {k: counts_by_type.get(k, 0) for k in key_types}
    return {
        "center": center_uri,
        "depth": depth,
        "nodes": list(nodes.values()),
        "edges": edges,
        "summary": {
            "counts_by_type": counts_by_type,
            "critical_nodes": critical_nodes,
            "impact_counts": impact_counts,
        }
    }