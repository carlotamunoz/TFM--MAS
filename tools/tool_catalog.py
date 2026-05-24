"""
Catálogo de tools disponibles para el Planner (v4).

Filosofía: el catálogo representa CAPACIDADES GENÉRICAS, no consultas
frecuentes ni joins hardcodeados. Cada tool es una primitiva que el
Planner puede combinar para responder cualquier consulta sobre el grafo.

Familias:
  resolution  → nombre/alias → IRI canónico
  schema      → introspección de la ontología (clases, propiedades)
  graph       → traversal, filtrado, agregación sobre el grafo
  impact      → análisis de impacto y propagación N-hop
  doctrine    → recuperación de doctrina militar (RAG)
  data        → resúmenes ejecutivos del escenario
  generated   → raw_sparql generado por el Planner
"""

from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from models import ToolSchema

_ENTITY_TYPES = [
    "Dron", "Nodo", "Servidor", "Modelo", "Datos",
    "C2", "Operador", "Piloto", "Propietario",
    "Organizacion", "Persona",
]

_OBJECT_PROPERTIES = [
    "belongs_to", "controls", "generates", "hosts_model",
    "interacts_with", "is_backed_up", "is_managed_by",
    "is_operated_by", "is_operated_in", "is_owned_by",
    "is_part_of", "is_used_by", "manages", "provides_data_to",
    "provides_visualization", "stores", "uses_model",
]

_DATATYPE_PROPERTIES = [
    "algoritmo", "autonomia_vuelo", "autoridad_legal",
    "capacidad_almacenamiento", "capacidad_procesamiento",
    "capacidades_organizacion", "capacitacion", "carga_util",
    "compatibilidad_sistemas", "direccion_ip", "especializacion",
    "estado_operativo", "fecha_hora", "funcion_c2", "graficas",
    "hiperparametros", "historial", "horas_vuelo_acumuladas",
    "id", "licencia", "metricas", "miembros", "mision",
    "modelo_global", "nombre", "redundancia", "responsable",
    "rol_puesto", "superior_directo", "tamanyo_bytes",
    "tipo", "tipo_organizacion", "ubicacion", "unidad_militar",
    "velocidad_procesamiento",
]

_FILTER_OPERATORS = ["=", "!=", ">", "<", ">=", "<=", "contains"]

_AGGREGATION_FUNCTIONS = ["COUNT", "AVG", "SUM", "MIN", "MAX"]


TOOL_CATALOG: list[ToolSchema] = [

    # ── Resolution ──────────────────────────────────────────────────────────
    ToolSchema(
        name="resolve_entity",
        family="resolution",
        description=(
            "Resuelve un nombre, alias o fragmento a su IRI canónico en la ontología. "
            "Busca en ex:nombre, ex:licencia, ex:unidad_militar, rdfs:label y fragmento del IRI. "
            "USAR SIEMPRE como primer paso cuando la consulta mencione una entidad por nombre "
            "(ej. 'Nodo-5', 'OTAN', 'Operador-3', 'LIC-000002') y un step posterior necesite su IRI."
        ),
        args_schema={
            "name": {
                "type": "string",
                "description": "Nombre, alias o fragmento a resolver.",
            },
            "entity_type": {
                "type": "string",
                "enum": _ENTITY_TYPES,
                "description": "Clase esperada. Opcional pero recomendado para reducir ambigüedad.",
                "nullable": True,
            },
        },
        returns="Lista de {x: IRI, matched_prop, matched_value, type}.",
    ),

    # ── Schema ───────────────────────────────────────────────────────────────
    ToolSchema(
        name="describe_ontology_schema",
        family="schema",
        description=(
            "Introspección de la ontología: clases disponibles, propiedades de una clase, "
            "o relaciones entre clases. "
            "Usar cuando el Planner necesite conocer nombres exactos de propiedades antes "
            "de construir un plan con filter_entities o aggregate_entities."
        ),
        args_schema={
            "scope": {
                "type": "string",
                "enum": ["classes", "properties", "relations", "all"],
                "description": (
                    "'classes': lista de clases con conteo de individuales. "
                    "'properties': propiedades datatype y object de una clase concreta. "
                    "'relations': object properties entre clases (inferido desde ABox). "
                    "'all': classes + relations."
                ),
            },
            "class_name": {
                "type": "string",
                "enum": _ENTITY_TYPES,
                "description": "Requerido si scope == 'properties'.",
                "nullable": True,
            },
        },
        returns=(
            "classes: [{class, numIndividuals}]. "
            "properties: [{prop, prop_type, sample_value}]. "
            "relations: [{domain_class, prop, range_class}]."
        ),
    ),

    # ── Graph ────────────────────────────────────────────────────────────────
    ToolSchema(
        name="entity_describe",
        family="graph",
        description=(
            "Devuelve todos los atributos y relaciones de una entidad concreta. "
            "Usar cuando el usuario pide 'toda la información sobre X' o "
            "para inspeccionar los valores de propiedades de una entidad específica."
        ),
        args_schema={
            "entity": {
                "type": "string",
                "description": "IRI canónico de la entidad (ej. ex:Nodo-5).",
            },
        },
        returns="Lista de {property, value} con todos los pares propiedad-valor.",
    ),

    ToolSchema(
        name="traverse_graph",
        family="graph",
        description=(
            "Traversal del grafo desde una entidad semilla siguiendo una secuencia "
            "ordenada de relaciones. Cubre desde 1 salto (¿qué controla OTAN?) "
            "hasta cadenas multi-hop (dron → datos → nodo → modelo). "
            "Usar 'outgoing' para sujeto→objeto y 'incoming' para objeto→sujeto (inverso). "
            "Con return_mode='count' devuelve el número de entidades alcanzadas."
        ),
        args_schema={
            "start_entity": {
                "type": "string",
                "description": "IRI canónico de la entidad de partida.",
            },
            "relations": {
                "type": "array",
                "description": (
                    "Secuencia ordenada de saltos a seguir. "
                    "Cada elemento especifica la propiedad y dirección del salto. "
                    "Ejemplo 1 salto: [{property: 'controls', direction: 'outgoing'}]. "
                    "Ejemplo multi-hop: [{property: 'generates'}, {property: 'is_used_by', direction: 'incoming'}, {property: 'uses_model'}]."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "property": {
                            "type": "string",
                            "enum": _OBJECT_PROPERTIES,
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["outgoing", "incoming"],
                            "default": "outgoing",
                        },
                    },
                    "required": ["property"],
                },
            },
            "target_class": {
                "type": "string",
                "enum": _ENTITY_TYPES,
                "nullable": True,
                "description": "Filtro opcional sobre la clase de las entidades finales.",
            },
            "return_mode": {
                "type": "string",
                "enum": ["entities", "count"],
                "default": "entities",
                "description": "'entities' devuelve la lista; 'count' devuelve solo el número.",
            },
        },
        returns=(
            "entities: [{entity, type}] lista de entidades alcanzadas. "
            "count: [{count}] número de entidades alcanzadas."
        ),
    ),

    ToolSchema(
        name="filter_entities",
        family="graph",
        description=(
            "Lista entidades de una clase que cumplen condiciones sobre sus propiedades. "
            "Útil para: 'drones en vuelo', 'servidores con más de 2048 MB', "
            "'operadores disponibles especializados en ciberseguridad'. "
            "Usar describe_ontology_schema(scope='properties', class_name=X) primero "
            "si no se conocen los nombres exactos de las propiedades."
        ),
        args_schema={
            "class_name": {
                "type": "string",
                "enum": _ENTITY_TYPES,
                "description": "Clase de entidades a filtrar.",
            },
            "filters": {
                "type": "array",
                "description": (
                    "Condiciones de filtrado sobre propiedades datatype. "
                    "Todas las condiciones se combinan con AND. "
                    "Propiedades disponibles: " + ", ".join(_DATATYPE_PROPERTIES) + "."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "property": {"type": "string"},
                        "operator": {
                            "type": "string",
                            "enum": _FILTER_OPERATORS,
                        },
                        "value": {"type": "string"},
                    },
                    "required": ["property", "operator", "value"],
                },
            },
            "return_mode": {
                "type": "string",
                "enum": ["entities", "count"],
                "default": "entities",
                "description": "'entities' devuelve la lista; 'count' devuelve solo el número.",
            },
        },
        returns=(
            "entities: [{entity}] lista de IRIs que cumplen los filtros. "
            "count: [{count}] número de entidades que cumplen los filtros."
        ),
    ),

    ToolSchema(
        name="aggregate_entities",
        family="graph",
        description=(
            "Calcula estadísticas agregadas sobre una propiedad numérica de una clase. "
            "Útil para: 'autonomía media de los drones', 'capacidad total de almacenamiento', "
            "'velocidad máxima de procesamiento', 'número de pilotos por estado operativo'. "
            "Soporta filtros opcionales para acotar la población antes de agregar. "
            "Con group_by agrupa los resultados por el valor de otra propiedad "
            "(ej. AVG(autonomia_vuelo) GROUP BY tipo)."
        ),
        args_schema={
            "class_name": {
                "type": "string",
                "enum": _ENTITY_TYPES,
                "description": "Clase sobre la que agregar.",
            },
            "property": {
                "type": "string",
                "description": (
                    "Propiedad datatype sobre la que calcular la agregación. "
                    "Propiedades numéricas disponibles: autonomia_vuelo, capacidad_almacenamiento, "
                    "capacidad_procesamiento, horas_vuelo_acumuladas, tamanyo_bytes, velocidad_procesamiento. "
                    "Para COUNT de entidades usar property='*'."
                ),
            },
            "aggregation": {
                "type": "string",
                "enum": _AGGREGATION_FUNCTIONS,
                "description": "Función de agregación a aplicar.",
            },
            "filters": {
                "type": "array",
                "nullable": True,
                "description": "Condiciones opcionales para acotar la población (mismo formato que filter_entities).",
                "items": {
                    "type": "object",
                    "properties": {
                        "property": {"type": "string"},
                        "operator": {"type": "string", "enum": _FILTER_OPERATORS},
                        "value": {"type": "string"},
                    },
                    "required": ["property", "operator", "value"],
                },
            },
            "group_by": {
                "type": "string",
                "nullable": True,
                "description": (
                    "Propiedad datatype por la que agrupar los resultados. "
                    "Ejemplo: group_by='tipo' para obtener AVG(autonomia_vuelo) por tipo de dron. "
                    "Si se omite, devuelve un único valor agregado."
                ),
            },
            "order_by": {
                "type": "string",
                "enum": ["asc", "desc"],
                "default": "desc",
                "description": "Orden del resultado cuando hay group_by.",
                "nullable": True,
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 10,
                "nullable": True,
                "description": "Máximo de filas cuando hay group_by.",
            },
        },
        returns=(
            "Sin group_by: [{value}] con el resultado escalar. "
            "Con group_by: [{group_value, value}] lista ordenada por valor."
        ),
    ),

    # ── Impact ───────────────────────────────────────────────────────────────
    ToolSchema(
        name="impact_reachability",
        family="impact",
        description=(
            "Entidades alcanzables en cascada desde una entidad semilla hasta N saltos. "
            "Usa property paths sobre: generates, is_used_by, uses_model, interacts_with "
            "y sus inversas. Útil para análisis de propagación de fallos o dependencias."
        ),
        args_schema={
            "seed": {
                "type": "string",
                "description": "IRI de la entidad semilla.",
            },
            "max_hops": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 3,
            },
        },
        returns="Lista de {x: IRI} de entidades alcanzables desde seed.",
    ),

    ToolSchema(
        name="impact_subgraph",
        family="impact",
        description=(
            "Devuelve el subgrafo de relaciones operacionales dentro del alcance N saltos "
            "desde una entidad semilla. Útil para análisis estructural o visualización "
            "de dependencias. Devuelve triples CONSTRUCT, más detallado que impact_reachability."
        ),
        args_schema={
            "seed": {
                "type": "string",
                "description": "IRI de la entidad semilla.",
            },
            "max_hops": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 3,
            },
        },
        returns="Grafo Turtle con los triples del subgrafo operacional.",
    ),

    # ── Doctrine ─────────────────────────────────────────────────────────────
    ToolSchema(
        name="retrieve_doctrine",
        family="doctrine",
        description=(
            "Busca chunks relevantes en doctrinas militares OTAN: "
            "AJP-3.1 (marítimo), AJP-3.2 (terrestre), AJP-3.3 (aéreo). "
            "Filtrado automático por dominio operacional del operador. "
            "Incluye expansión de acrónimos militares."
        ),
        args_schema={
            "query": {
                "type": "string",
                "description": "Consulta en lenguaje natural.",
            },
            "domain": {
                "type": "string",
                "enum": ["air", "land", "maritime"],
            },
            "top_k": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 5,
            },
        },
        returns="Lista de {text, source_doc, page, score}.",
    ),

    # ── Data ─────────────────────────────────────────────────────────────────
    ToolSchema(
        name="scenario_summary",
        family="data",
        description=(
            "Resumen ejecutivo del escenario completo: totales por clase "
            "(Dron, Nodo, Servidor, Datos, Modelo, C2, Piloto, Operador, Organizacion) "
            "y desglose por estado_operativo donde aplique. "
            "Usar para responder 'estado general de la misión', "
            "'cuántos drones están activos', 'resumen del sistema'."
        ),
        args_schema={},
        returns=(
            "Diccionario con métricas: total por clase y breakdown por estado_operativo "
            "para Dron, Piloto, Operador, C2, Servidor."
        ),
    ),

    # ── Generated ────────────────────────────────────────────────────────────
    ToolSchema(
        name="raw_sparql",
        family="generated",
        description=(
            "Ejecuta SPARQL SELECT pre-generado por el Planner vía su tool sparql_from_nl. "
            "El Executor lo ejecuta directamente contra Fuseki sin transformación. "
            "Usar solo cuando ningún otro tool cubra la consulta."
        ),
        args_schema={
            "query": {
                "type": "string",
                "description": "SPARQL SELECT completo con prefijos, listo para ejecutar.",
            },
        },
        returns="Lista de bindings SPARQL SELECT.",
    ),
]

TOOL_REGISTRY: dict[str, ToolSchema] = {t.name: t for t in TOOL_CATALOG}


def get_tools_for_category(category: str) -> list[ToolSchema]:
    """Filtra catálogo según categoría del Router.

    doctrine_question → solo doctrine.
    ontology_question → todos los tools excepto raw_sparql
                        (raw_sparql lo genera el Planner internamente,
                         no es un tool que el Planner elige en el plan).
    """
    if category == "doctrine_question":
        return [t for t in TOOL_CATALOG if t.family == "doctrine"]
    if category == "ontology_question":
        return [t for t in TOOL_CATALOG if t.name != "raw_sparql"]
    raise ValueError(f"Categoría no planificable: {category!r}")