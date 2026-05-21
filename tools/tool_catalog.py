"""
Catalogo de tools disponibles para el Planner (v3).

Cambios respecto a v2:
  - Eliminado sparql_from_nl: el Planner lo invoca como @agent.tool propio,
    no como step del plan. El resultado entra al plan como raw_sparql.
  - Anadido raw_sparql (family=generated): SPARQL pre-generado por el Planner,
    el Executor lo ejecuta directamente contra Fuseki.

Familias:
  resolution  -> traduccion nombre->IRI
  schema      -> introspeccion de la ontologia
  navigation  -> traversal simple de propiedades y listados por clase
  data        -> joins multi-hop frecuentes
  impact      -> analisis de impacto y propagacion
  ranking     -> rankings parametrizables
  doctrine    -> recuperacion de doctrina militar (RAG)
  generated   -> raw_sparql generado por el Planner
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


TOOL_CATALOG: list[ToolSchema] = [

    # --- Resolution ---
    ToolSchema(
        name="resolve_entity",
        family="resolution",
        description=(
            "Resuelve un nombre, alias o fragmento a su IRI canonico en la ontologia. "
            "Busca en ex:nombre, ex:licencia, ex:unidad_militar, rdfs:label y fragmento del IRI. "
            "USALO SIEMPRE como primer paso cuando la consulta mencione una entidad por nombre "
            "(ej. 'Nodo-5', 'OTAN', 'Operador-3', 'LIC-000002') y un tool posterior necesite un IRI."
        ),
        args_schema={
            "name": {"type": "string", "description": "Nombre, alias o fragmento a resolver."},
            "entity_type": {
                "type": "string",
                "enum": _ENTITY_TYPES,
                "description": "Clase esperada. Opcional pero recomendado.",
                "nullable": True,
            },
        },
        returns="Lista de {x: IRI, matched_prop, matched_value, type}.",
    ),

    # --- Schema ---
    ToolSchema(
        name="describe_ontology_schema",
        family="schema",
        description=(
            "Devuelve la estructura del esquema: clases, propiedades por clase, "
            "o relaciones entre clases. Para preguntas sobre que informacion existe "
            "o que propiedades tiene una clase."
        ),
        args_schema={
            "scope": {"type": "string", "enum": ["classes", "properties", "relations", "all"]},
            "class_name": {
                "type": "string",
                "enum": _ENTITY_TYPES,
                "description": "Solo si scope == 'properties'.",
                "nullable": True,
            },
        },
        returns="Estructura jerarquica de clases/propiedades/relaciones.",
    ),

    # --- Navigation ---
    ToolSchema(
        name="entity_describe",
        family="navigation",
        description=(
            "Devuelve todos los atributos y relaciones de una entidad concreta. "
            "Util cuando el usuario pide 'toda la informacion sobre X'."
        ),
        args_schema={
            "entity_iri": {"type": "string", "description": "IRI de la entidad (ex:...)."},
        },
        returns="Lista de {p: propiedad, o: valor}.",
    ),

    ToolSchema(
        name="entity_outgoing",
        family="navigation",
        description=(
            "Devuelve los objetos conectados a una entidad por una propiedad concreta "
            "(traversal directo: sujeto -> objeto). "
            "Ejemplos: nodos que controla OTAN, modelo que usa Nodo-5, "
            "backup de Nodo-1, piloto de un dron."
        ),
        args_schema={
            "subject_iri": {"type": "string", "description": "IRI del sujeto."},
            "property": {
                "type": "string",
                "enum": _OBJECT_PROPERTIES,
                "description": "Nombre de la propiedad (sin prefijo).",
            },
        },
        returns="Lista de IRIs objeto conectados al sujeto por la propiedad.",
    ),

    ToolSchema(
        name="entity_incoming",
        family="navigation",
        description=(
            "Devuelve los sujetos que apuntan a una entidad por una propiedad concreta "
            "(traversal inverso). "
            "Ejemplos: que org controla Nodo-5, que C2 gestiona Nodo-3, "
            "que drones opera un piloto."
        ),
        args_schema={
            "object_iri": {"type": "string", "description": "IRI del objeto destino."},
            "property": {
                "type": "string",
                "enum": _OBJECT_PROPERTIES,
                "description": "Nombre de la propiedad (sin prefijo).",
            },
        },
        returns="Lista de IRIs sujeto que apuntan al objeto por la propiedad.",
    ),

    ToolSchema(
        name="list_by_class",
        family="navigation",
        description=(
            "Lista todas las instancias de una clase. Opcionalmente filtra por estado_operativo. "
            "Para: 'drones en vuelo', 'operadores disponibles', 'todos los nodos', 'C2 activos'."
        ),
        args_schema={
            "class_name": {"type": "string", "enum": _ENTITY_TYPES},
            "status": {
                "type": "string",
                "description": (
                    "Valor de estado_operativo. Opcional. "
                    "Dron: 'En vuelo'|'En mantenimiento'|'En espera'. "
                    "Operador/Piloto: 'Disponible'|'En mision'|'En descanso'. "
                    "C2: 'Activo'|'Mantenimiento'|'Inactivo'."
                ),
                "nullable": True,
            },
        },
        returns="Lista de {x: IRI, estado: estado_operativo si aplica}.",
    ),

    # --- Data ---
    ToolSchema(
        name="node_uses_model",
        family="data",
        description="Devuelve los modelos usados por un nodo concreto.",
        args_schema={
            "node": {"type": "string", "description": "IRI del nodo (ex:...)."},
        },
        returns="Lista de {model: IRI del modelo}.",
    ),

    ToolSchema(
        name="drone_generates_data",
        family="data",
        description="Devuelve los datos generados por un dron concreto.",
        args_schema={
            "drone": {"type": "string", "description": "IRI del dron (ex:...)."},
        },
        returns="Lista de {data: IRI, tipo, fecha_hora}.",
    ),

    ToolSchema(
        name="models_used_by_drone",
        family="data",
        description=(
            "Modelos que usa un dron via cadena dron->datos->nodo->modelo. "
            "Encapsula un join multi-hop de 3 saltos."
        ),
        args_schema={
            "drone": {"type": "string", "description": "IRI del dron (ex:...)."},
        },
        returns="Lista de {model: IRI del modelo}.",
    ),

    # --- Impact ---
    ToolSchema(
        name="impact_direct_node",
        family="impact",
        description=(
            "Impacto directo (1 salto) si falla un nodo: drones, datos y modelos afectados. "
            "Para cascada usar impact_reachability."
        ),
        args_schema={
            "node": {"type": "string", "description": "IRI del nodo (ex:...)."},
        },
        returns="Objeto {drones: [...], data: [...], models: [...]}.",
    ),

    ToolSchema(
        name="impact_reachability",
        family="impact",
        description=(
            "Entidades alcanzables en cascada desde una entidad semilla a N saltos. "
            "Usa property paths sobre: generates, is_used_by, uses_model, interacts_with "
            "y sus inversas."
        ),
        args_schema={
            "seed": {"type": "string", "description": "IRI de la entidad origen."},
            "max_hops": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        returns="Lista de {x: IRI alcanzable}.",
    ),

    ToolSchema(
        name="impact_subgraph",
        family="impact",
        description=(
            "Subgrafo SPARQL CONSTRUCT de relaciones operacionales dentro del alcance "
            "N saltos. Para analisis estructural o visualizacion."
        ),
        args_schema={
            "seed": {"type": "string", "description": "IRI de la entidad origen."},
            "max_hops": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        returns="Grafo Turtle con las relaciones del subgrafo.",
    ),

    # --- Ranking ---
    ToolSchema(
        name="rank_entities",
        family="ranking",
        description=(
            "Ranking de entidades segun una metrica. Combinaciones validas: "
            "(node, data_count), (node, model_count), (model, usage_count), "
            "(drone, data_count)."
        ),
        args_schema={
            "entity_type": {"type": "string", "enum": ["node", "model", "drone"]},
            "metric": {"type": "string", "enum": ["data_count", "model_count", "usage_count"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
        },
        returns="Lista ordenada de {entity: IRI, score: int}.",
    ),

    # --- Doctrine ---
    ToolSchema(
        name="retrieve_doctrine",
        family="doctrine",
        description=(
            "Busca chunks relevantes en doctrinas militares: "
            "AJP-3.1 (maritime), AJP-3.2 (land), AJP-3.3 (air). "
            "Filtrado automatico por dominio del operador. "
            "Incluye expansion de acronimos militares."
        ),
        args_schema={
            "query": {"type": "string", "description": "Consulta en lenguaje natural."},
            "domain": {"type": "string", "enum": ["air", "land", "maritime"]},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
        },
        returns="Lista de {text, source_doc, page, score}.",
    ),

    # --- Generated (SPARQL pre-generado por el Planner) ---
    ToolSchema(
        name="raw_sparql",
        family="generated",
        description=(
            "Ejecuta SPARQL SELECT pre-generado por el Planner via su tool sparql_from_nl. "
            "El Executor lo ejecuta directamente contra Fuseki sin transformacion."
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
    """Filtra catalogo segun categoria del Router.

    doctrine_question -> solo doctrine.
    ontology_question -> todos los tools excepto raw_sparql
                         (raw_sparql lo genera el Planner internamente,
                          no es un tool que el Planner elige en el plan).
    """
    if category == "doctrine_question":
        return [t for t in TOOL_CATALOG if t.family == "doctrine"]
    if category == "ontology_question":
        return [t for t in TOOL_CATALOG if t.name != "raw_sparql"]
    raise ValueError(f"Categoria no planificable: {category!r}")