"""
Planner ReWOO.

Agente Pydantic AI con una sola tool: sparql_from_nl.
El Planner invoca esta tool cuando ningun tool del catalogo del Executor
cubre la informacion necesaria. El resultado (SPARQL generado) entra al
plan como un step con tool="raw_sparql".

Llamadas LLM por invocacion:
  - 1 base (razonamiento + construccion del plan)
  - +1 por cada invocacion de sparql_from_nl (si la query lo requiere)
  Total: 1..N, acotado por la complejidad de la query.

El Executor es 100% determinista: recibe el ExecutionPlan con SPARQL
ya generado y lo ejecuta sin llamadas LLM adicionales.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from pydantic import ValidationError
from pydantic_ai import Agent, RunContext

from models import (
    ConversationTurn,
    Domain,
    ExecutionPlan,
    PlannerInput,
    ToolSchema,
    RetrieverOutput
)
from prompts.planner_prompt import PLANNER_SYSTEM_PROMPT
from prompts.retriever_prompt import build_retriever_context_block
from models import RetrieverOutput
from prompts.nl_to_sparql_prompt import NL_TO_SPARQL_SYSTEM_PROMPT
from tools.tool_catalog import get_tools_for_category

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------

PLANNER_MODEL    = os.getenv("PLANNER_MODEL",    "openai:gpt-4.1")
NL_SPARQL_MODEL  = os.getenv("NL_SPARQL_MODEL",  "openai:gpt-4.1")
PLANNER_HISTORY_TURNS = int(os.getenv("PLANNER_HISTORY_TURNS", "5"))

# ---------------------------------------------------------------------------
# Sub-agente NL->SPARQL (usado como tool del Planner)
# ---------------------------------------------------------------------------

_nl_to_sparql_agent: Agent[None, str] = Agent(
    model=NL_SPARQL_MODEL,
    output_type=str,
    system_prompt=NL_TO_SPARQL_SYSTEM_PROMPT,
)

_SPARQL_PREFIXES = (
    "PREFIX ex:   <http://www.semanticweb.org/carlo/ontologies/2024/10/untitled-ontology-4#>\n"
    "PREFIX rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#>\n"
    "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
    "PREFIX owl:  <http://www.w3.org/2002/07/owl#>\n"
)

def _validate_sparql_basic(sparql: str) -> str | None:
    """Validacion sintatica ligera. Devuelve mensaje de error o None si OK."""
    s = sparql.strip().upper()
    if not s.startswith("PREFIX") and not s.startswith("SELECT"):
        return "La query no empieza por PREFIX o SELECT"
    if "SELECT" not in s:
        return "No es una query SELECT"
    if "WHERE" not in s:
        return "Falta clausula WHERE"
    if s.count("{") != s.count("}"):
        return "Llaves desbalanceadas"
    forbidden = ["INSERT", "DELETE", "DROP", "UPDATE", "CLEAR", "LOAD"]
    for kw in forbidden:
        if kw in s:
            return f"Operacion no permitida: {kw}"
    return None

# ---------------------------------------------------------------------------
# Agente Planner
# ---------------------------------------------------------------------------

planner_agent: Agent[None, ExecutionPlan] = Agent(
    model=PLANNER_MODEL,
    output_type=ExecutionPlan,
    system_prompt=PLANNER_SYSTEM_PROMPT,
)


@planner_agent.tool_plain
async def sparql_from_nl(instruction: str) -> str:
    """Genera SPARQL SELECT desde una instruccion en lenguaje natural.

    Invocala cuando ningun tool especifico del catalogo cubra la informacion
    que necesitas. El SPARQL generado entrara al plan como un step con
    tool='raw_sparql' y args={'query': '<sparql generado>'}.

    Args:
        instruction: descripcion precisa en NL de que datos necesitas.
                     Puedes incluir IRIs ya resueltos: 'ex:Nodo-5'.
                     Incluye condiciones, filtros y campos a devolver.

    Returns:
        SPARQL SELECT valido listo para incluir en el plan como raw_sparql.
    """
    logger.debug("sparql_from_nl invocada con: %s", instruction[:200])

    last_err: Exception | None = None
    for attempt in range(2):
        try:
            run = await _nl_to_sparql_agent.run(instruction)
            sparql = run.output.strip()

            # Limpiar posibles bloques markdown que el LLM anade a veces
            sparql = re.sub(r"^```(?:sparql)?\s*", "", sparql, flags=re.I)
            sparql = re.sub(r"\s*```$", "", sparql)
            sparql = sparql.strip()

            # Asegurar prefijos presentes
            if not sparql.upper().startswith("PREFIX"):
                sparql = _SPARQL_PREFIXES + sparql

            err = _validate_sparql_basic(sparql)
            if err:
                raise ValueError(f"SPARQL invalido (intento {attempt+1}): {err}\nQuery: {sparql[:300]}")

            logger.debug("sparql_from_nl OK en intento %d", attempt + 1)
            return sparql

        except Exception as exc:
            last_err = exc
            logger.warning("sparql_from_nl intento %d fallo: %s", attempt + 1, exc)

    raise RuntimeError(
        f"sparql_from_nl fallo tras 2 intentos. Ultimo error: {last_err}"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_tool_catalog(tools: list[ToolSchema]) -> str:
    blocks = []
    for t in tools:
        blocks.append(json.dumps({
            "name": t.name,
            "family": t.family,
            "description": t.description,
            "args_schema": t.args_schema,
            "returns": t.returns,
        }, indent=2, ensure_ascii=False))
    return "\n\n".join(blocks)


def _format_history(history: list[ConversationTurn], n: int) -> str:
    if not history:
        return "(sin historial previo)"
    lines = []
    for i, turn in enumerate(history[-n:], start=1):
        lines += [f"[Turn {i}]",
                  f"  Usuario: {turn.user_input}",
                  f"  Asistente: {turn.assistant_response}"]
    return "\n".join(lines)


def _build_user_message(input_data: PlannerInput) -> str:
    return (
        f"# Dominio del operador\n{input_data.domain.value}\n\n"
        f"# Categoria de la consulta\n{input_data.category}\n\n"
        f"# Catalogo de tools disponibles\n"
        f"{_format_tool_catalog(input_data.available_tools)}\n\n"
        f"# Historial reciente\n"
        f"{_format_history(input_data.conversation_history, PLANNER_HISTORY_TURNS)}\n\n"
        f"# Consulta del usuario\n{input_data.user_query}\n\n"
        f"RECUERDA: si necesitas SPARQL ad-hoc, invoca la tool sparql_from_nl. "
        f"El resultado entrara al plan como un step con tool='raw_sparql'."
    )


def _enforce_plan_metadata(plan: ExecutionPlan, input_data: PlannerInput) -> ExecutionPlan:
    """Sobrescribe campos que vienen del input, no del LLM."""
    return plan.model_copy(update={
        "original_query": input_data.user_query,
        "domain": input_data.domain,
        "category": input_data.category,
    })


# ---------------------------------------------------------------------------
# API publica: plan normal
# ---------------------------------------------------------------------------

async def plan(input_data: PlannerInput, retriever_output: "RetrieverOutput | None" = None) -> ExecutionPlan:    
    user_msg = _build_user_message(input_data)
    last_exc: Exception | None = None

    for attempt in range(2):
        try:
            run = await planner_agent.run(user_msg)
            plan_obj = _enforce_plan_metadata(run.output, input_data)
            ExecutionPlan.model_validate(plan_obj.model_dump())
            logger.info(
                "Planner OK: plan_id=%s steps=%d",
                plan_obj.plan_id[:8], len(plan_obj.steps),
            )
            return plan_obj
        except (ValidationError, Exception) as exc:
            last_exc = exc
            logger.warning("Planner intento %d/%d fallo: %s", attempt + 1, 2, exc)

    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# API publica: re-planning en modo reparacion
# ---------------------------------------------------------------------------

from models import ReplanRequest

# El re-planner es el mismo agente pero con prompt de reparacion.
# Lo instanciamos aqui para no duplicar la tool sparql_from_nl.
from prompts.replanner_prompt import REPLANNER_SYSTEM_PROMPT

replanner_agent: Agent[None, ExecutionPlan] = Agent(
    model=PLANNER_MODEL,
    output_type=ExecutionPlan,
    system_prompt=REPLANNER_SYSTEM_PROMPT,
)

# La tool sparql_from_nl debe estar disponible tambien en el replanner
@replanner_agent.tool_plain
async def sparql_from_nl_replan(instruction: str) -> str:
    """Misma tool que sparql_from_nl pero registrada en el replanner."""
    return await sparql_from_nl(instruction)


async def replan(request: ReplanRequest) -> ExecutionPlan:
    """Re-planifica tras un error semantico.

    Construye un mensaje que incluye:
    - Plan original serializado.
    - Resultados parciales disponibles.
    - Step fallido y mensaje de error.
    """
    partial_summary = {
        sid: {
            "status": r.status.value,
            "output_preview": str(r.output)[:300] if r.output else None,
        }
        for sid, r in request.partial_results.items()
    }

    user_msg = (
        f"# Consulta original del usuario\n{request.original_plan.original_query}\n\n"
        f"# Dominio\n{request.original_plan.domain.value}\n\n"
        f"# Plan original\n"
        f"{json.dumps(request.original_plan.model_dump(), indent=2, ensure_ascii=False)}\n\n"
        f"# Resultados parciales disponibles (reutilizables en el nuevo plan)\n"
        f"{json.dumps(partial_summary, indent=2, ensure_ascii=False)}\n\n"
        f"# Step que fallo\n"
        f"{json.dumps(request.failed_step.model_dump(), indent=2, ensure_ascii=False)}\n\n"
        f"# Error semantico\n{request.error_msg}\n\n"
        f"# Intento de re-planificacion\n{request.replan_attempt}/2\n\n"
        f"Emite un ExecutionPlan corregido. Reutiliza los resultados de "
        f"partial_results referenciandolos como {{{{E1}}}}, {{{{E2}}}}, etc. "
        f"No repitas steps ya ejecutados con exito."
    )

    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            run = await replanner_agent.run(user_msg)
            plan_obj = run.output.model_copy(update={
                "original_query": request.original_plan.original_query,
                "domain": request.original_plan.domain,
                "category": request.original_plan.category,
            })
            ExecutionPlan.model_validate(plan_obj.model_dump())
            logger.info(
                "Re-planner OK (intento %d): plan_id=%s steps=%d",
                request.replan_attempt, plan_obj.plan_id[:8], len(plan_obj.steps),
            )
            return plan_obj
        except (ValidationError, Exception) as exc:
            last_exc = exc
            logger.warning("Re-planner intento %d fallo: %s", attempt + 1, exc)

    assert last_exc is not None
    raise last_exc


def plan_sync(
    user_query: str,
    domain: Domain,
    category: str,
    conversation_history: list[ConversationTurn] | None = None,
) -> ExecutionPlan:
    """Helper sincrono para tests / CLI."""
    import asyncio
    if category not in {"doctrine_only", "ontology_only"}:
        raise ValueError(f"Categoria no planificable: {category!r}")
    available_tools = get_tools_for_category(category)
    input_data = PlannerInput(
        user_query=user_query,
        domain=domain,
        category=category,  # type: ignore[arg-type]
        available_tools=available_tools,
        conversation_history=conversation_history or [],
    )
    return asyncio.run(plan(input_data))