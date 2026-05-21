"""
Classifier / Router.

Agente que clasifica el input del usuario en una de cuatro categorías:
smalltalk, doctrine_question, ontology_question, unclear.

Características clave:
  - 1 sola llamada LLM con output estructurado (sin tools, sin bucle).
  - Modelo configurable vía env (default: openai:gpt-4.1-mini).
  - Umbral de confianza configurable (default: 0.6).
  - Historial de conversación: últimas N turns (default: 5).
  - Manejo de errores: 1 reintento; si falla, fallback a `ontology_question`.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from pydantic_ai import Agent

from models import (
    ClassificationResult,
    ClassifierInput,
    ConversationTurn,
    RouterDecision,
)
from prompts.classifier_prompts import CLASSIFIER_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

CLASSIFIER_MODEL = os.getenv("CLASSIFIER_MODEL", "openai:gpt-4.1-mini")
CLASSIFIER_CONFIDENCE_THRESHOLD = float(
    os.getenv("CLASSIFIER_CONFIDENCE_THRESHOLD", "0.6")
)
CLASSIFIER_HISTORY_TURNS = int(os.getenv("CLASSIFIER_HISTORY_TURNS", "5"))


# ---------------------------------------------------------------------------
# Agente Pydantic AI
# ---------------------------------------------------------------------------

classifier_agent: Agent[None, ClassificationResult] = Agent(
    model=CLASSIFIER_MODEL,
    output_type=ClassificationResult,
    system_prompt=CLASSIFIER_SYSTEM_PROMPT,
    # Sin tools: este agente solo clasifica con una llamada estructurada.
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_history(history: list[ConversationTurn], n: int) -> str:
    """Formatea las últimas N turns como texto para el prompt."""
    if not history:
        return "(sin historial previo)"
    recent = history[-n:]
    lines = []
    for i, turn in enumerate(recent, start=1):
        lines.append(f"[Turn {i}]")
        lines.append(f"  Usuario: {turn.user_input}")
        lines.append(f"  Asistente: {turn.assistant_response}")
    return "\n".join(lines)


def _build_user_message(input_data: ClassifierInput) -> str:
    """Compone el mensaje de usuario que ve el LLM."""
    history_block = _format_history(
        input_data.conversation_history, CLASSIFIER_HISTORY_TURNS
    )
    return (
        f"Historial reciente:\n{history_block}\n\n"
        f"Consulta actual del usuario:\n{input_data.user_input}"
    )


def _fallback_result(reason: str) -> ClassificationResult:
    """Construye el resultado de fallback cuando el LLM falla 2 veces."""
    return ClassificationResult(
        category="ontology_question",
        confidence=0.0,
        reasoning=f"fallback: {reason}",
        clarification_question=None,
    )


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

async def classify(input_data: ClassifierInput) -> RouterDecision:
    """Clasifica un input del usuario.

    Comportamiento:
      1. Llama al LLM; si falla, reintenta una vez.
      2. Si tras el reintento sigue fallando, devuelve fallback a
         `ontology_question` (la categoría más completa, no se pierde info).
      3. Si la confianza es < umbral configurado, fuerza categoría `unclear`.
         En ese caso, conserva la `clarification_question` que ya haya
         generado el LLM (si la generó); si no, sintetiza una genérica.
    """
    user_msg = _build_user_message(input_data)

    retries_used = 0
    result: ClassificationResult | None = None
    last_exc: Exception | None = None

    for attempt in range(2):  # intento inicial + 1 reintento
        try:
            run = await classifier_agent.run(user_msg)
            result = run.output
            break
        except Exception as exc:  # noqa: BLE001 — capturamos todo y reintentamos
            last_exc = exc
            retries_used = attempt + 1
            logger.warning(
                "Classifier falló (intento %d/2): %s", attempt + 1, exc
            )

    if result is None:
        logger.error(
            "Classifier falló tras reintento. Aplicando fallback. last_exc=%s",
            last_exc,
        )
        return RouterDecision(
            result=_fallback_result(
                reason=f"LLM error after retry: {type(last_exc).__name__}"
            ),
            fell_back=True,
            retries_used=retries_used,
        )

    # Aplicar umbral de confianza: si está por debajo, forzar 'unclear'.
    if (
        result.category != "unclear"
        and result.confidence < CLASSIFIER_CONFIDENCE_THRESHOLD
    ):
        logger.info(
            "Confianza %.2f < umbral %.2f; forzando 'unclear'.",
            result.confidence,
            CLASSIFIER_CONFIDENCE_THRESHOLD,
        )
        clarification = (
            result.clarification_question
            or "¿Podrías reformular o aportar más contexto? "
               "No tengo claro qué información necesitas."
        )
        result = ClassificationResult(
            category="unclear",
            confidence=result.confidence,
            reasoning=(
                f"forzado a unclear por confianza baja "
                f"({result.confidence:.2f}). Original: {result.reasoning}"
            ),
            clarification_question=clarification,
        )

    return RouterDecision(
        result=result,
        fell_back=False,
        retries_used=retries_used - 1 if retries_used > 0 else 0,
        # retries_used aquí cuenta SOLO reintentos exitosos finales
    )


def classify_sync(input_data: ClassifierInput) -> RouterDecision:
    """Versión síncrona para tests / CLI."""
    import asyncio
    return asyncio.run(classify(input_data))
