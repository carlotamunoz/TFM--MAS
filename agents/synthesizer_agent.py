"""
Synthesizer agent.

Recibe SynthesizerInput (ExecutionResult + contexto) y emite SynthesizerOutput
con la respuesta lista para el usuario.

Características:
  - Detecta el patrón de respuesta automáticamente desde ExecutionResult.results.
    NO depende de ninguna sub-clasificación previa: el Router solo da la
    categoría macro y el Planner ya decidió qué tools usar.
  - 1 sola llamada LLM con output estructurado (sin tools, sin bucle agente).
  - Modelo configurable vía env (default: openai:gpt-4.1-mini).
  - Manejo de errores: 1 reintento; si falla, devuelve respuesta de
    fallback genérica con degraded=true.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from pydantic_ai import Agent

from models import (
    ConversationTurn,
    ExecutionResult,
    ResponsePattern,
    StepResult,
    StepStatus,
    SynthesizerInput,
    SynthesizerOutput,
)
from prompts.synthesizer_prompt import SYNTHESIZER_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

SYNTHESIZER_MODEL = os.getenv("SYNTHESIZER_MODEL", "openai:gpt-4.1-mini")
SYNTHESIZER_HISTORY_TURNS = int(os.getenv("SYNTHESIZER_HISTORY_TURNS", "5"))


# ---------------------------------------------------------------------------
# Detección de patrón de respuesta
# ---------------------------------------------------------------------------

# Tools que indican datos / impacto / navegación (ontología pura)
_ONTOLOGY_DATA_TOOLS = frozenset({
    # Catálogo v4 — tools activos
    "resolve_entity",
    "entity_describe",
    "traverse_graph",
    "filter_entities",
    "aggregate_entities",
    "impact_reachability",
    "impact_subgraph",
    "scenario_summary",
    "raw_sparql",
    # Legacy — por si hay planes antiguos en caché
    "entity_outgoing",
    "entity_incoming",
    "list_by_class",
    "node_uses_model",
    "drone_generates_data",
    "models_used_by_drone",
    "impact_direct_node",
    "rank_entities",
    "count_related",
})

# Tools de doctrina
_DOCTRINE_TOOLS = frozenset({"retrieve_doctrine"})

# Tools de schema/reconocimiento
_SCHEMA_TOOLS = frozenset({"describe_ontology_schema"})


def detect_pattern(
    category: str,
    execution_result,
) -> ResponsePattern:
    """Detecta el patrón de respuesta desde los tools realmente ejecutados.

    La categoría del Classifier orienta pero el patrón final se decide
    desde los resultados reales del Executor.
    """
    # Cortocircuito inmediato
    if category == "smalltalk":
        return "smalltalk"

    if execution_result is None:
        return "smalltalk"

    if execution_result.final_status == "failed":
        return "failure"

    # Recoger tools exitosos
    usable_tools: set[str] = set()
    for step in execution_result.results.values():
        if step.status == "success" and step.output:
            usable_tools.add(step.tool)

    has_doctrine = bool(usable_tools & _DOCTRINE_TOOLS)
    has_schema   = bool(usable_tools & _SCHEMA_TOOLS)
    has_data     = bool(usable_tools & (_ONTOLOGY_DATA_TOOLS - {"resolve_entity"}))

    # Categorías de entrada como hint adicional
    if category == "doctrine_only":
        return "doctrine_only" if has_doctrine else "failure"
    if category == "ontology_with_context":
        if has_data and has_doctrine:
            return "ontology_with_context"
        if has_data:
            return "ontology_only"   # degradado: el retrieve_doctrine no dio resultado
        return "failure"
    if category in ("ontology_only", "ontology_question"):
        if has_doctrine and has_data:
            return "ontology_with_context"
        if has_schema and not has_data:
            return "schema_only"
        if has_data:
            return "ontology_only"
        return "failure"

    # Fallback genérico
    if has_doctrine and has_data:
        return "ontology_with_context"
    if has_doctrine:
        return "doctrine_only"
    if has_schema and not has_data:
        return "schema_only"
    if has_data:
        return "ontology_only"
    return "failure"


def _format_history(history: list[ConversationTurn], n: int) -> str:
    if not history:
        return "(sin historial previo)"
    lines = []
    for i, turn in enumerate(history[-n:], start=1):
        lines += [
            f"[Turn {i}]",
            f"  Usuario: {turn.user_input}",
            f"  Asistente: {turn.assistant_response}",
        ]
    return "\n".join(lines)


def _serialize_step_result(result: StepResult) -> dict[str, Any]:
    """Serializa un StepResult de forma compacta para el LLM.

    Output limitado: si el output es una lista muy larga, se trunca y
    se incluye el conteo total. Esto evita prompts gigantes con poca
    señal adicional.
    """
    serialized: dict[str, Any] = {
        "step_id":   result.step_id,
        "tool":      result.tool,
        "status":    result.status.value,
        "attempts":  result.attempts,
    }
    if result.error_msg:
        serialized["error_msg"] = result.error_msg

    output = result.output
    if output is None:
        serialized["output"] = None
    elif isinstance(output, list):
        total = len(output)
        if total > 30:
            serialized["output_truncated"] = True
            serialized["output_total_count"] = total
            serialized["output_sample"] = output[:30]
        else:
            serialized["output"] = output
    elif isinstance(output, dict):
        serialized["output"] = output
    else:
        # Otros tipos (str, etc.): convertir a string truncado
        s = str(output)
        serialized["output"] = s[:2000] + ("..." if len(s) > 2000 else "")

    return serialized


def _build_user_message(input_data: SynthesizerInput, pattern: ResponsePattern) -> str:
    history_block = _format_history(
        input_data.conversation_history, SYNTHESIZER_HISTORY_TURNS
    )

    base = (
        f"# Patrón detectado\n{pattern}\n\n"
        f"# Dominio del operador\n{input_data.domain.value}\n\n"
        f"# Consulta original del usuario\n{input_data.user_input}\n\n"
        f"# Historial reciente\n{history_block}\n\n"
    )

    if pattern == "smalltalk" or input_data.execution_result is None:
        base += (
            "# Contexto adicional\n"
            "No hay ExecutionResult: esta es una interacción conversacional. "
            "Responde brevemente y con tono profesional.\n"
        )
        return base

    er = input_data.execution_result
    results_serialized = {
        sid: _serialize_step_result(r) for sid, r in er.results.items()
    }

    base += (
        f"# Estado de la ejecución\n"
        f"final_status: {er.final_status}\n"
        f"replan_count: {er.replan_count}\n"
    )
    if er.failure_reason:
        base += f"failure_reason: {er.failure_reason}\n"
    base += "\n"

    base += (
        f"# Resultados de los steps ejecutados\n"
        f"{json.dumps(results_serialized, indent=2, ensure_ascii=False, default=str)}\n\n"
        f"Redacta un SynthesizerOutput coherente con el patrón detectado, "
        f"el dominio del operador, y la información disponible. "
        f"Recuerda: no inventes datos que no estén en los resultados, "
        f"y mantén el tono profesional militar."
    )
    return base



# ---------------------------------------------------------------------------
# Agente Synthesizer
# ---------------------------------------------------------------------------

synthesizer_agent: Agent[None, SynthesizerOutput] = Agent(
    model=SYNTHESIZER_MODEL,
    output_type=SynthesizerOutput,
    system_prompt=SYNTHESIZER_SYSTEM_PROMPT,
)



# ---------------------------------------------------------------------------
# Fallback de emergencia
# ---------------------------------------------------------------------------

def _emergency_fallback(
    input_data: SynthesizerInput,
    pattern: ResponsePattern,
    error: Exception,
) -> SynthesizerOutput:
    """Respuesta cuando el propio Synthesizer LLM falla tras reintento.

    No usa LLM: genera texto estático para no dejar al usuario sin respuesta.
    """
    er = input_data.execution_result
    if pattern == "smalltalk":
        text = (
            "Disculpa, estoy teniendo problemas técnicos para responder en "
            "este momento. Inténtalo de nuevo en unos segundos."
        )
    elif pattern == "failure":
        reason = (er.failure_reason if er else None) or "razón desconocida"
        text = (
            f"No he podido completar tu consulta. Motivo: {reason}. "
            f"¿Puedes reformular o aportar más contexto?"
        )
    else:
        text = (
            "He recibido los resultados de tu consulta pero no he podido "
            "redactar una respuesta legible debido a un fallo interno. "
            "Por favor, vuelve a intentarlo."
        )

    return SynthesizerOutput(
        response_text=text,
        pattern=pattern,
        degraded=True,
        degradation_reason=f"synthesizer LLM failed: {type(error).__name__}",
    )


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

async def synthesize(input_data: SynthesizerInput) -> SynthesizerOutput:
    """Sintetiza una respuesta para el usuario.

    Comportamiento:
      1. Detecta el patrón de respuesta a partir del input.
      2. Construye el user message con todo el contexto necesario.
      3. Llama al LLM una sola vez con output estructurado.
      4. Si falla, reintenta una vez.
      5. Si vuelve a fallar, devuelve fallback estático con degraded=true.
    """
    pattern = detect_pattern(input_data.category, input_data.execution_result)
    logger.info(
        "Synthesizer: pattern=%s domain=%s category=%s",
        pattern, input_data.domain.value, input_data.category,
    )

    user_msg = _build_user_message(input_data, pattern)

    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            run = await synthesizer_agent.run(user_msg)
            output = run.output

            # Asegurar que el campo pattern coincide con lo detectado.
            # Si el LLM lo cambia, lo sobrescribimos: la detección por
            # results es más fiable que la del LLM.
            if output.pattern != pattern:
                logger.debug(
                    "Synthesizer cambió pattern del LLM (%s) al detectado (%s)",
                    output.pattern, pattern,
                )
                output = output.model_copy(update={"pattern": pattern})

            # Si el plan tuvo re-planning, marcar degradado salvo que el
            # LLM ya lo haya hecho con un motivo más específico.
            er = input_data.execution_result
            if er and er.replan_count > 0 and not output.degraded:
                output = output.model_copy(update={
                    "degraded": True,
                    "degradation_reason": (
                        f"Plan reajustado {er.replan_count} vez(es) durante la ejecución."
                    ),
                })

            logger.info(
                "Synthesizer OK: pattern=%s degraded=%s len_text=%d",
                output.pattern, output.degraded, len(output.response_text),
            )
            return output

        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Synthesizer intento %d/2 falló: %s", attempt + 1, exc
            )

    assert last_exc is not None
    logger.error("Synthesizer falló tras reintento. Usando fallback. %s", last_exc)
    return _emergency_fallback(input_data, pattern, last_exc)


def synthesize_sync(input_data: SynthesizerInput) -> SynthesizerOutput:
    """Versión síncrona para tests / CLI."""
    import asyncio
    return asyncio.run(synthesize(input_data))