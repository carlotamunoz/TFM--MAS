"""
Catálogo de tools v2 para el Planner.

Cambios respecto a v1
─────────────────────
v1 tenía ~15 tools, la mayoría solapadas o hardcodeadas para pares concretos
de clases (node_uses_model, drone_generates_data, models_used_by_drone…).
El Planner no podía combinarlas correctamente y elegía herramientas basándose
en nombres específicos en lugar de razonar sobre el grafo.

v2 expone 8 tools GENÉRICOS que cubren el 100% de los casos de uso:

  resolve_entity        → nombre/alias → IRI canónico          (siempre primer paso)
  entity_describe       → todos los atributos y relaciones       (inspección completa)
  entity_relations      → traversal por UNA propiedad, cualquier dirección
  list_by_class         → listado de instancias con filtros
  entity_path           → cadena multi-hop fija (joins encadenados)
  entity_neighborhood   → vecindad N saltos libre (impacto en cascada)
  rank_by_count         → ranking por conteo de relaciones
  retrieve_doctrine     → búsqueda RAG en doctrina militar
  schema_relations      → mapa clase→propiedad→clase (para razonamiento del Planner)

MAPA DE RELACIONES DE LA ONTOLOGÍA
────────────────────────────────────
  C2           --[is_owned_by]-->          Persona
  C2           --[manages]-->              Nodo
  Datos        --[is_used_by]-->           Nodo
  Dron         --[generates]-->            Datos
  Dron         --[is_operated_by]-->       Piloto
  Dron         --[provides_data_to]-->     Servidor
  Dron         --[uses_model]-->           Modelo
  Modelo       --[provides_visualization]--> C2
  Nodo         --[interacts_with]-->       Nodo
  Nodo         --[is_backed_up]-->         Nodo
  Nodo         --[is_managed_by]-->        C2
  Nodo         --[is_part_of]-->           Organizacion
  Nodo         --[uses_model]-->           Modelo
  Organizacion --[controls]-->             Nodo
  Organizacion --[interacts_with]-->       Organizacion
  Piloto       --[belongs_to]-->           Organizacion
  Servidor     --[hosts_model]-->          Modelo
  Servidor     --[is_owned_by]-->          Propietario
  Servidor     --[stores]-->               Datos
"""

from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from models import ToolSchema


# ── Enumeraciones compartidas ──────────────────────────────────────────────

_ENTITY_CLASSES = [
    "Dron", "Nodo", "Servidor", "Modelo", "Datos",
    "C2", "Operador", "Piloto", "Propietario", "Organizacion", "Persona",
]

# Solo propiedades que tienen datos reales en la ontología.
# Se incluyen aquí para que el Planner sepa exactamente qué puede usar.
_OBJECT_PROPERTIES = [
    "belongs_to",           # Piloto → Organizacion
    "controls",             # Organizacion → Nodo
    "generates",            # Dron → Datos
    "hosts_model",          # Servidor → Modelo
    "interacts_with",       # Nodo ↔ Nodo, Organizacion ↔ Organizacion
    "is_backed_up",         # Nodo → Nodo (nodo de respaldo)
    "is_managed_by",        # Nodo → C2
    "is_operated_by",       # Dron → Piloto
    "is_owned_by",          # C2 → Persona, Servidor → Propietario
    "is_part_of",           # Nodo → Organizacion
    "is_used_by",           # Datos → Nodo
    "manages",              # C2 → Nodo  (inversa de is_managed_by)
    "provides_data_to",     # Dron → Servidor
    "provides_visualization", # Modelo → C2
    "stores",               # Servidor → Datos
    "uses_model",           # Nodo → Modelo, Dron → Modelo
]

_DRONE_STATUSES   = ["En vuelo", "En mantenimiento", "En espera"]
_PERSON_STATUSES  = ["Disponible", "En misión", "En descanso"]
_SYSTEM_STATUSES  = ["Activo", "Mantenimiento", "Inactivo"]


# ── Catálogo ───────────────────────────────────────────────────────────────

TOOL_CATALOG: list[ToolSchema] = [

    # ── 1. resolve_entity ────────────────────────────────────────────────
    ToolSchema(
        name="resolve_entity",
        family="resolution",
        description=(
            "Resuelve un nombre, alias, IP o fragmento a su IRI canónico. "
            "Busca en: ex:nombre, ex:licencia, ex:unidad_militar, ex:direccion_ip "
            "y fragmento del IRI. "
            "ÚSALO SIEMPRE como PRIMER PASO cuando la consulta mencione una entidad "
            "por nombre (ej. 'Nodo-5', 'OTAN', 'LIC-000002', '192.168.1.3', 'Batallón-1'). "
            "Los tools siguientes necesitan el IRI canónico, no el nombre."
        ),
        args_schema={
            "name": {
                "type": "string",
                "description": "Texto a buscar. Ej: 'DRON-000', 'OTAN', 'LIC-000001', '192.168.1.3'.",
            },
            "entity_class": {
                "type": "string",
                "enum": _ENTITY_CLASSES,
                "description": "Restringe la búsqueda a una clase. Opcional pero recomendado.",
                "nullable": True,
            },
        },
        returns="Lista de {x: IRI, matched_prop, matched_value, type}.",
    ),

    # ── 2. entity_describe ───────────────────────────────────────────────
    ToolSchema(
        name="entity_describe",
        family="navigation",
        description=(
            "Devuelve TODOS los atributos y relaciones de una entidad concreta. "
            "Úsalo para 'dame toda la información sobre X' o cuando no sabes "
            "qué propiedades tiene la entidad. "
            "Retorna pares (predicado, valor) incluyendo data properties (nombre, "
            "estado, ubicacion…) y object properties (is_operated_by, uses_model…)."
        ),
        args_schema={
            "entity_iri": {
                "type": "string",
                "description": "IRI de la entidad. Ej: 'ex:DRON-000', 'ex:Nodo-3'.",
            },
        },
        returns="Lista de {predicate, value}.",
    ),

    # ── 3. entity_relations ──────────────────────────────────────────────
    ToolSchema(
        name="entity_relations",
        family="navigation",
        description=(
            "Traversal por UNA propiedad en la dirección indicada. "
            "Este es el tool principal para preguntas de relación entre entidades. "
            "\n"
            "direction='outgoing': (entity) --[prop]--> ?related\n"
            "direction='incoming': ?related  --[prop]--> (entity)\n"
            "\n"
            "EJEMPLOS DE USO:\n"
            "  '¿Quién pilota DRON-001?'     → prop=is_operated_by, dir=outgoing\n"
            "  '¿Qué drones opera LIC-0001?' → prop=is_operated_by, dir=incoming\n"
            "  '¿Qué C2 gestiona Nodo-3?'   → prop=is_managed_by,  dir=outgoing\n"
            "  '¿Qué nodos gestiona C2-00?' → prop=manages,        dir=outgoing\n"
            "  '¿Qué org controla Nodo-2?'  → prop=controls,       dir=incoming\n"
            "  '¿Backup de Nodo-1?'         → prop=is_backed_up,   dir=outgoing\n"
            "  '¿Qué nodos usan Modelo-X?'  → prop=uses_model,     dir=incoming\n"
            "  '¿Qué datos genera DRON-0?'  → prop=generates,      dir=outgoing\n"
            "  '¿Qué servidor tiene Dato-X?'→ prop=stores,         dir=incoming\n"
            "\n"
            f"Propiedades disponibles: {', '.join(_OBJECT_PROPERTIES)}"
        ),
        args_schema={
            "entity_iri": {
                "type": "string",
                "description": "IRI de la entidad de partida (obtenido con resolve_entity).",
            },
            "prop": {
                "type": "string",
                "enum": _OBJECT_PROPERTIES,
                "description": "Propiedad a recorrer (sin prefijo ex:).",
            },
            "direction": {
                "type": "string",
                "enum": ["outgoing", "incoming"],
                "description": (
                    "'outgoing': entity --[prop]--> resultado. "
                    "'incoming': resultado --[prop]--> entity (traversal inverso)."
                ),
                "default": "outgoing",
            },
        },
        returns="Lista de {related: IRI de la entidad relacionada}.",
    ),

    # ── 4. list_by_class ─────────────────────────────────────────────────
    ToolSchema(
        name="list_by_class",
        family="navigation",
        description=(
            "Lista todas las instancias de una clase, opcionalmente filtradas "
            "por estado operativo. Incluye propiedades clave de cada instancia "
            "(nombre, estado, tipo, ubicacion, ip, licencia). "
            "\n"
            "ESTADOS VÁLIDOS por clase:\n"
            f"  Dron/Servidor/C2: {_SYSTEM_STATUSES} | {_DRONE_STATUSES}\n"
            f"  Piloto/Operador:  {_PERSON_STATUSES}\n"
            "\n"
            "Ejemplos: 'drones en vuelo', 'pilotos disponibles', "
            "'servidores activos', 'todos los nodos'."
        ),
        args_schema={
            "class_iri": {
                "type": "string",
                "enum": _ENTITY_CLASSES,
                "description": "Clase a listar.",
            },
            "status": {
                "type": "string",
                "description": (
                    "Filtro de estado_operativo (case-insensitive). Opcional. "
                    "Dron: 'En vuelo'|'En mantenimiento'|'En espera'. "
                    "Piloto/Operador: 'Disponible'|'En misión'|'En descanso'. "
                    "Servidor/C2: 'Activo'|'Mantenimiento'|'Inactivo'."
                ),
                "nullable": True,
            },
            "extra_props": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Propiedades adicionales a incluir en el resultado. Opcional. "
                    "Ej: ['autonomia_vuelo','carga_util'] para drones, "
                    "['capacidad_almacenamiento','velocidad_procesamiento'] para servidores."
                ),
                "nullable": True,
            },
        },
        returns=(
            "Lista de {x: IRI, nombre, estado, tipo, ubicacion, ip, licencia} "
            "+ columnas de extra_props si se especifican."
        ),
    ),

    # ── 5. entity_path ───────────────────────────────────────────────────
    ToolSchema(
        name="entity_path",
        family="data",
        description=(
            "Traversal multi-hop siguiendo una cadena FIJA de propiedades. "
            "Construye joins encadenados: A --[p1]--> B --[p2]--> C …\n"
            "\n"
            "Úsalo para preguntas que implican una ruta conocida en el grafo:\n"
            "  '¿Qué nodo procesa los datos de DRON-000?'\n"
            "      → props=['generates', 'is_used_by']\n"
            "  '¿Qué modelos están en la cadena de DRON-000?'\n"
            "      → props=['generates', 'is_used_by', 'uses_model']\n"
            "  '¿Qué C2 visualiza el modelo del servidor 192.168.1.3?'\n"
            "      → props=['hosts_model', 'provides_visualization']\n"
            "  '¿Qué piloto está detrás del dato X?'\n"
            "      Primero: entity_relations(dato, is_used_by) → nodo\n"
            "      NO usar entity_path aquí porque el paso dato→dron es inverso.\n"
            "      Para traversals con inversas, usar entity_neighborhood.\n"
            "\n"
            "NOTA: todas las propiedades de la cadena son OUTGOING."
        ),
        args_schema={
            "start_iri": {
                "type": "string",
                "description": "IRI de la entidad de inicio.",
            },
            "props": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": _OBJECT_PROPERTIES,
                },
                "description": (
                    "Lista ordenada de propiedades a recorrer. Mínimo 1. "
                    "Ej: ['generates', 'is_used_by', 'uses_model']."
                ),
                "minItems": 1,
            },
        },
        returns="Lista de {hop1, hop2, …, hopN} con las entidades de cada paso.",
    ),

    # ── 6. entity_neighborhood ───────────────────────────────────────────
    ToolSchema(
        name="entity_neighborhood",
        family="impact",
        description=(
            "Devuelve TODAS las entidades alcanzables desde seed en N saltos, "
            "recorriendo cualquier relación operacional en ambas direcciones. "
            "Ideal para análisis de impacto en cascada.\n"
            "\n"
            "Relaciones que recorre (en ambas direcciones):\n"
            "  generates, is_used_by, uses_model, interacts_with,\n"
            "  is_managed_by, manages, provides_data_to, hosts_model, stores\n"
            "\n"
            "Casos de uso:\n"
            "  '¿Qué entidades se ven afectadas si falla Nodo-3?'\n"
            "  '¿Qué está conectado a DRON-000 en toda la red?'\n"
            "  'Dame el subgrafo alrededor del Servidor 192.168.1.4'\n"
            "\n"
            "NOTA TÉCNICA: max_hops > 1 requiere Apache Fuseki (SPARQL 1.1 completo). "
            "Para compatibilidad máxima usar max_hops=1."
        ),
        args_schema={
            "seed_iri": {
                "type": "string",
                "description": "IRI de la entidad central del análisis.",
            },
            "max_hops": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 2,
                "description": (
                    "Profundidad máxima. "
                    "1 = vecinos directos (compatible con todos los motores). "
                    "2-3 = cascada operacional típica. "
                    ">3 puede ser lento con grafos grandes."
                ),
            },
        },
        returns="Lista de {entity: IRI, type: clase de la entidad}.",
    ),

    # ── 7. rank_by_count ─────────────────────────────────────────────────
    ToolSchema(
        name="rank_by_count",
        family="ranking",
        description=(
            "Ranking de instancias de una clase por número de relaciones. "
            "Combina clase + propiedad + dirección para cualquier ranking.\n"
            "\n"
            "EJEMPLOS:\n"
            "  'Top nodos por datos que procesan'\n"
            "      → class=Nodo, prop=is_used_by, direction=incoming\n"
            "  'Modelos más usados (por cuántos nodos)'\n"
            "      → class=Modelo, prop=uses_model, direction=incoming\n"
            "  'Drones que más datos generan'\n"
            "      → class=Dron, prop=generates, direction=outgoing\n"
            "  'Servidores con más modelos'\n"
            "      → class=Servidor, prop=hosts_model, direction=outgoing\n"
            "  'Organizaciones que controlan más nodos'\n"
            "      → class=Organizacion, prop=controls, direction=outgoing"
        ),
        args_schema={
            "class_iri": {
                "type": "string",
                "enum": _ENTITY_CLASSES,
                "description": "Clase de las entidades a rankear.",
            },
            "prop": {
                "type": "string",
                "enum": _OBJECT_PROPERTIES,
                "description": "Propiedad a contar.",
            },
            "direction": {
                "type": "string",
                "enum": ["outgoing", "incoming"],
                "description": (
                    "'incoming': contar cuántas entidades apuntan a ?x vía prop. "
                    "'outgoing': contar cuántas entidades apunta ?x vía prop."
                ),
                "default": "incoming",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "default": 10,
                "description": "Número máximo de resultados (top-N).",
            },
        },
        returns="Lista ordenada de {entity: IRI, count: int}.",
    ),

    # ── 8. retrieve_doctrine ─────────────────────────────────────────────
    ToolSchema(
        name="retrieve_doctrine",
        family="doctrine",
        description=(
            "Busca chunks relevantes en doctrinas militares (RAG sobre ChromaDB). "
            "Documentos indexados: AJP-3.1 (marítimo), AJP-3.2 (terrestre), "
            "AJP-3.3 (aéreo). "
            "El dominio se filtra automáticamente según el operador de sesión. "
            "Incluye expansión de acrónimos militares. "
            "Úsalo para preguntas doctrinales: reglas de enfrentamiento, "
            "procedimientos operacionales, definiciones tácticas."
        ),
        args_schema={
            "query": {
                "type": "string",
                "description": "Consulta en lenguaje natural.",
            },
            "domain": {
                "type": "string",
                "enum": ["air", "land", "maritime"],
                "description": "Dominio operacional a consultar.",
            },
            "top_k": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 5,
                "description": "Número de chunks a recuperar.",
            },
        },
        returns="Lista de {text, source_doc, page, score}.",
    ),

    # ── 9. schema_relations ──────────────────────────────────────────────
    ToolSchema(
        name="schema_relations",
        family="schema",
        description=(
            "Devuelve el mapa completo de relaciones entre clases de la ontología "
            "(clase_origen → propiedad → clase_destino). "
            "Úsalo cuando el Planner necesite razonar sobre la estructura del grafo "
            "antes de elegir qué propiedad usar en entity_relations o entity_path. "
            "Por ejemplo: '¿cómo se conecta un Dron con una Organización?' "
            "→ schema_relations() revela que Dron→is_operated_by→Piloto→belongs_to→Organizacion."
        ),
        args_schema={},
        returns="Lista de {domain_class, property, range_class}.",
    ),

]

TOOL_REGISTRY: dict[str, ToolSchema] = {t.name: t for t in TOOL_CATALOG}


def get_tools_for_category(category: str) -> list[ToolSchema]:
    """Filtra el catálogo según la categoría del clasificador.

    doctrine_question  → solo retrieve_doctrine
    ontology_question  → todos excepto retrieve_doctrine
    """
    if category == "doctrine_question":
        return [t for t in TOOL_CATALOG if t.family == "doctrine"]
    if category == "ontology_question":
        return [t for t in TOOL_CATALOG if t.family != "doctrine"]
    raise ValueError(f"Categoría no planificable: {category!r}")