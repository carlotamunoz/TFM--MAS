"""
Modelos Pydantic compartidos por todos los agentes.

Define los contratos de entrada/salida del Classifier (Router), Planner,
Executor y Synthesizer, así como las estructuras intermedias.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Dominios y tipos base
# ---------------------------------------------------------------------------

class Domain(str, Enum):
    AIR      = "air"
    LAND     = "land"
    MARITIME = "maritime"


class ConversationTurn(BaseModel):
    user_input: str
    assistant_response: str
    timestamp: str  # ISO 8601


# ---------------------------------------------------------------------------
# Classifier (Router)
# ---------------------------------------------------------------------------

QueryCategory = Literal[
    "smalltalk",
    "doctrine_question",
    "ontology_question",
    "unclear",
]


class ClassifierInput(BaseModel):
    user_input: str
    conversation_history: list[ConversationTurn] = Field(default_factory=list)


class ClassificationResult(BaseModel):
    """Output del Router.

    Si confidence < umbral configurable (default 0.6), el orquestador
    fuerza category='unclear' y devuelve clarification_question al usuario.
    """
    category: QueryCategory
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    clarification_question: str | None = None

    @model_validator(mode="after")
    def _check_clarification(self) -> ClassificationResult:
        if self.category == "unclear" and not self.clarification_question:
            raise ValueError(
                "clarification_question es obligatoria cuando category == 'unclear'"
            )
        if self.category != "unclear" and self.clarification_question:
            self.clarification_question = None
        return self


class RouterDecision(BaseModel):
    result: ClassificationResult
    fell_back: bool = False
    retries_used: int = 0


# ---------------------------------------------------------------------------
# Planner — Tools y Plan
# ---------------------------------------------------------------------------

class ToolSchema(BaseModel):
    """Schema de un tool tal y como se le presenta al Planner."""
    name: str
    description: str
    args_schema: dict[str, Any]
    returns: str
    family: Literal[
        "resolution",
        "schema",
        "navigation",
        "data",
        "impact",
        "ranking",
        "doctrine",
        "graph",
        "generated",  # raw_sparql: SPARQL pre-generado por el Planner
    ]


class PlannerInput(BaseModel):
    user_query: str
    domain: Domain
    category: Literal["doctrine_question", "ontology_question"]
    available_tools: list[ToolSchema]
    conversation_history: list[ConversationTurn] = Field(default_factory=list)


class StepPlan(BaseModel):
    """Un paso del plan ReWOO.

    Convenciones de referencias entre steps:
      - {{E1}}        → output completo del paso E1
      - {{E1.field}}  → campo concreto del output de E1
      Toda referencia {{Ek}} debe aparecer en depends_on.
    """
    step_id: str = Field(pattern=r"^E\d+$")
    tool: str
    args: dict[str, Any]
    depends_on: list[str] = Field(default_factory=list)
    description: str

    @field_validator("depends_on")
    @classmethod
    def _validate_depends_on_format(cls, v: list[str]) -> list[str]:
        for dep in v:
            if not (dep.startswith("E") and dep[1:].isdigit()):
                raise ValueError(
                    f"depends_on debe contener step_ids tipo 'E1', 'E2'... "
                    f"Recibido: {dep!r}"
                )
        return v


class ExecutionPlan(BaseModel):
    """Output del Planner. Plan ReWOO completo."""
    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    original_query: str
    domain: Domain
    category: Literal["doctrine_question", "ontology_question"]
    steps: list[StepPlan]
    rationale: str
    expected_output_shape: str | None = None

    @model_validator(mode="after")
    def _validate_plan_topology(self) -> ExecutionPlan:
        ids = [s.step_id for s in self.steps]

        # IDs únicos
        if len(ids) != len(set(ids)):
            raise ValueError("step_ids duplicados en el plan")

        id_set = set(ids)

        # depends_on referencia steps existentes
        for step in self.steps:
            for dep in step.depends_on:
                if dep not in id_set:
                    raise ValueError(
                        f"Step {step.step_id} depende de {dep!r} que no existe."
                    )

        # Sin ciclos (DFS)
        graph = {s.step_id: s.depends_on for s in self.steps}
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {sid: WHITE for sid in id_set}

        def dfs(node: str) -> None:
            color[node] = GRAY
            for nxt in graph[node]:
                if color[nxt] == GRAY:
                    raise ValueError(f"Ciclo detectado en el paso {node!r}")
                if color[nxt] == WHITE:
                    dfs(nxt)
            color[node] = BLACK

        for sid in id_set:
            if color[sid] == WHITE:
                dfs(sid)

        # Referencias {{Ek}} en args declaradas en depends_on
        ref_pattern = re.compile(r"\{\{\s*(E\d+)(?:\.[^\s}]+)?\s*\}\}")
        for step in self.steps:
            refs = set(ref_pattern.findall(str(step.args)))
            missing = refs - set(step.depends_on)
            if missing:
                raise ValueError(
                    f"Step {step.step_id} referencia {sorted(missing)} en args "
                    f"pero no están en depends_on."
                )

        return self


# ---------------------------------------------------------------------------
# Executor — Ejecución y resultados
# ---------------------------------------------------------------------------

class StepStatus(str, Enum):
    PENDING   = "pending"
    SUCCESS   = "success"
    EMPTY     = "empty"     # ejecutó OK pero 0 resultados
    RETRYING  = "retrying"  # error transitorio, reintentando
    FAILED    = "failed"    # error semántico o agotó reintentos


class ErrorType(str, Enum):
    TRANSIENT = "transient"  # timeout, conexión, BD ocupada
    SEMANTIC  = "semantic"   # args inválidos, resultado vacío bloqueante


class StepResult(BaseModel):
    step_id:     str
    tool:        str
    status:      StepStatus
    output:      Any | None = None
    error_type:  ErrorType | None = None
    error_msg:   str | None = None
    attempts:    int = 1
    duration_ms: int | None = None


class ExecutionContext(BaseModel):
    """Estado mutable del Executor durante la ejecución de un plan."""
    plan:         ExecutionPlan
    results:      dict[str, StepResult] = Field(default_factory=dict)
    replan_count: int = 0
    max_replans:  int = 2

    model_config = {"arbitrary_types_allowed": True}


class ReplanRequest(BaseModel):
    """Lo que el Executor empaqueta y envía al Re-planner."""
    original_plan:    ExecutionPlan
    partial_results:  dict[str, StepResult]
    failed_step:      StepPlan
    error_type:       Literal["semantic"]
    error_msg:        str
    replan_attempt:   int   # 1 o 2


class ExecutionResult(BaseModel):
    """Output final del Executor hacia el Synthesizer."""
    plan_id:           str
    domain:            Domain
    original_query:    str
    results:           dict[str, StepResult]
    final_status:      Literal["success", "partial", "failed"]
    replan_count:      int
    total_duration_ms: int | None = None
    failure_reason:    str | None = None  # poblado si final_status == "failed"


# ---------------------------------------------------------------------------
# Synthesizer — Patrón de respuesta, Input, Output
# ---------------------------------------------------------------------------

ResponsePattern = Literal[
    "smalltalk",              # respuesta conversacional sin Executor
    "doctrine_only",          # solo retrieve_doctrine en results
    "ontology_only",          # solo ontología, sin doctrina
    "ontology_with_context",  # ontología + doctrina (mixto)
    "schema_only",            # solo describe_ontology_schema
    "failure",                # ExecutionResult.final_status == "failed"
]


class Citation(BaseModel):
    """Cita estructurada para incluir en el output del Synthesizer."""
    source_type: Literal["doctrine", "ontology"]
    # Para doctrina:
    source_doc:  str | None = None   # "AJP-3.2"
    page:        str | None = None
    excerpt:     str | None = None   # fragmento citado (máx ~200 chars)
    # Para ontología:
    iri:         str | None = None   # ex:Nodo-5
    label:       str | None = None   # "Nodo-5"


class EntityReference(BaseModel):
    """Entidad de la ontología referenciada en la respuesta."""
    iri:   str
    label: str | None = None
    type:  str | None = None  # clase RDF: "Nodo", "Dron", etc.


class SynthesizerInput(BaseModel):
    """Input del Synthesizer.

    Para los modos 'smalltalk' y 'failure', execution_result puede venir
    como None o con un objeto mínimo. Para el resto, execution_result es
    el output completo del Executor.
    """
    user_input:            str
    domain:                Domain
    category:              QueryCategory
    execution_result:      ExecutionResult | None = None
    conversation_history:  list[ConversationTurn] = Field(default_factory=list)
    # En modo 'unclear' el orquestador ya respondió al usuario; el
    # Synthesizer no se invoca. Por eso QueryCategory aquí en la práctica
    # solo será smalltalk, doctrine_question u ontology_question.


class SynthesizerOutput(BaseModel):
    """Output estructurado del Synthesizer.

    El frontend puede usar:
      - response_text como cuerpo principal.
      - citations para renderizar referencias.
      - entities_referenced para enlaces / detalles.
      - degraded para marcar visualmente respuestas parciales.
    """
    response_text:       str
    citations:           list[Citation] = Field(default_factory=list)
    entities_referenced: list[EntityReference] = Field(default_factory=list)
    pattern:             ResponsePattern
    degraded:            bool = False
    degradation_reason:  str | None = None