"""
orchestrator.py

Conecta los cuatro agentes en el flujo completo:

    Classifier → routing → Planner → Executor → Synthesizer

Casos especiales:
  - smalltalk  : Classifier → Synthesizer (sin Planner ni Executor)
  - unclear    : Classifier devuelve clarification_question al usuario
  - failure    : Executor agota re-plannings → Synthesizer con contexto parcial

El orquestador es stateless: toda la memoria vive en Session (pasada como arg).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from agents.classifier_agent import classify
from agents.planner_agent import plan
from agents.retriever_agent import retrieve as retriever_retrieve
from agents.synthesizer_agent import synthesize
from agents.execution.executor import Executor
from models import (
    ClassifierInput,
    Domain,
    ExecutionResult,
    PlannerInput,
    SynthesizerInput,
    SynthesizerOutput,
)
from session import Session
from tools.tool_catalog import get_tools_for_category

logger = logging.getLogger(__name__)

_executor = Executor()


@dataclass
class OrchestratorResult:
    """Lo que devuelve el orquestador a la capa API."""
    output: SynthesizerOutput
    session_id: str
    clarification_needed: bool = False
    clarification_question: str | None = None
    pipeline_trace: dict | None = None  # ← AÑADIR ESTA LÍNEA


async def run_query(user_input: str, session: Session) -> OrchestratorResult:
    """Ejecuta el flujo completo para una query de usuario.

    Args:
        user_input: lo que el usuario envió.
        session:    sesión activa con domain, operator_id e historial.

    Returns:
        OrchestratorResult con el output final y metadata de sesión.
    """
    logger.info(
        "Orchestrator start: session=%s operator=%s domain=%s",
        session.session_id[:8], session.operator_id, session.domain.value,
    )

    # ------------------------------------------------------------------
    # 1. Classifier / Router
    # ------------------------------------------------------------------
    classifier_input = ClassifierInput(
        user_input=user_input,
        conversation_history=session.last_n_turns(5),
    )
    router_decision = await classify(classifier_input)
    result = router_decision.result

    logger.info(
        "Classifier: category=%s confidence=%.2f fell_back=%s",
        result.category, result.confidence, router_decision.fell_back,
    )

    # ------------------------------------------------------------------
    # 2. Routing por categoría
    # ------------------------------------------------------------------

    # 2a. Unclear → devolver clarification_question al usuario
    if result.category == "unclear":
        logger.info("Routing: unclear → clarification_question")
        session.add_turn(
            user_input=user_input,
            assistant_response=result.clarification_question or "",
        )
        return OrchestratorResult(
            output=SynthesizerOutput(
                response_text=result.clarification_question or "",
                pattern="smalltalk",  # lo más cercano para el frontend
            ),
            session_id=session.session_id,
            clarification_needed=True,
            clarification_question=result.clarification_question,
        )

    # 2b. Smalltalk → Synthesizer directo (sin Planner ni Executor)
    if result.category == "smalltalk":
        logger.info("Routing: smalltalk → Synthesizer directo")
        synth_output = await synthesize(SynthesizerInput(
            user_input=user_input,
            domain=session.domain,
            category="smalltalk",    # ← correcto
            execution_result=None,
            conversation_history=session.last_n_turns(5),
        ))
        session.add_turn(user_input, synth_output.response_text)
        return OrchestratorResult(output=synth_output, session_id=session.session_id)

    # 2c. Ruta al pipeline completo
    category = result.category
    logger.info("Routing: %s → Planner + Executor + Synthesizer", category)
    
    # Inicializar trace para capturar outputs de cada agente

    pipeline_trace = {
        "classifier": {
            "input": {"query": user_input},
            "output": {
                "category": result.category,
                "confidence": getattr(result, "confidence", None),
                "fell_back": getattr(result, "fell_back", False),
            }
        }
    }
    # ------------------------------------------------------------------
    # 2d. Retriever — RAG semántico pre-Planner
    # Activo para: ontology_with_context, doctrine_only siempre.
    # Para ontology_only: solo si la query no contiene IRIs reconocibles.
    # ------------------------------------------------------------------
    import re as _re
    _KNOWN_IRI_PATTERN = r'\b(DRON|Nodo|LIC|Servidor|Modelo|C2)-\w+'
    _has_known_iri = bool(_re.search(_KNOWN_IRI_PATTERN, user_input, _re.IGNORECASE))
    _needs_retriever = (
        category in ("ontology_with_context", "doctrine_only")
        or (category == "ontology_only" and not _has_known_iri)
    )

    retriever_output = None
    if _needs_retriever:
        logger.info(
            "Retriever activado: category=%s domain=%s",
            category, session.domain,
        )
        try:
            retriever_output = await retriever_retrieve(
                query=user_input,
                domain=session.domain,
                category=category,
            )
            logger.info(
                "Retriever OK: chunks=%d terms=%s needs_graph=%s needs_doctrine=%s",
                len(retriever_output.chunks),
                retriever_output.relevant_terms,
                retriever_output.needs_graph,
                retriever_output.needs_doctrine,
            )

            # AÑADIR:
            pipeline_trace["retriever"] = {
                "output": {
                    "chunks": len(retriever_output.chunks),
                    "relevant_terms": retriever_output.relevant_terms,
                    "needs_graph": retriever_output.needs_graph,
                    "needs_doctrine": retriever_output.needs_doctrine,
                }
            }

        except Exception as _ret_err:
            logger.warning(
                "Retriever error (continuando sin contexto): %s", _ret_err
            )
            retriever_output = None

    # ------------------------------------------------------------------
    # 3. Planner
    # ------------------------------------------------------------------
    available_tools = get_tools_for_category(category)
    planner_input = PlannerInput(
        user_query=user_input,
        domain=session.domain,
        category=category,  # type: ignore[arg-type]
        available_tools=available_tools,
        conversation_history=session.last_n_turns(5),
    )

    try:
        execution_plan = await plan(planner_input, retriever_output=retriever_output)
        logger.info(
            "Planner: plan_id=%s steps=%d",
            execution_plan.plan_id[:8], len(execution_plan.steps),
            
        )
        pipeline_trace["planner"] = {
                    "input": {
                        "query": user_input,
                        "domain": session.domain.value,
                        "category": category,
                    },
                    "output": {
                        "plan_id": execution_plan.plan_id,
                        "steps": len(execution_plan.steps),
                        "steps_detail": [
                            {"step_id": s.step_id, "tool": s.tool, "args": s.args}
                            for s in execution_plan.steps
                        ],
                    }
                }
    except Exception as exc:
        logger.error("Planner falló: %s", exc)
        execution_result = _make_failed_result(
            query=user_input,
            domain=session.domain,
            reason=f"El planificador no pudo generar un plan: {exc}",
        )
        return await _synthesize_and_store(
            user_input=user_input,
            domain=session.domain,
            category=category,
            execution_result=execution_result,
            session=session,
            pipeline_trace=pipeline_trace,
        )

    # ------------------------------------------------------------------
    # 4. Executor
    # ------------------------------------------------------------------
    try:
        execution_result = await _executor.run(execution_plan)
        logger.info(
            "Executor: status=%s replans=%d",
            execution_result.final_status, execution_result.replan_count,
        )
        pipeline_trace["executor"] = {
            "output": {
                "status": execution_result.final_status,
                "replans": execution_result.replan_count,
                "results": {k: v for k, v in execution_result.results.items()},
            }
        }

    except Exception as exc:
        logger.error("Executor falló de forma inesperada: %s", exc)
        execution_result = _make_failed_result(
            query=user_input,
            domain=session.domain,
            reason=f"Error inesperado durante la ejecución: {exc}",
        )

    # ------------------------------------------------------------------
    # 5. Synthesizer
    # ------------------------------------------------------------------
    
    return await _synthesize_and_store(
            user_input=user_input,
            domain=session.domain,
            category=category,
            execution_result=execution_result,
            session=session,
            pipeline_trace=pipeline_trace,
        )


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

async def _synthesize_and_store(
    user_input: str,
    domain: Domain,
    category: str,
    execution_result: ExecutionResult,
    session: Session,
    pipeline_trace: dict | None = None,
) -> OrchestratorResult:
    """Invoca el Synthesizer y persiste el turno en el historial."""
    synth_input = SynthesizerInput(
        user_input=user_input,
        domain=domain,
        category=category,
        execution_result=execution_result,
        conversation_history=session.last_n_turns(5),
    )
    synth_output = await synthesize(synth_input)
    session.add_turn(user_input, synth_output.response_text)

    logger.info(
        "Synthesizer: pattern=%s degraded=%s",
        synth_output.pattern, synth_output.degraded,
    )

    # ← AÑADIR AQUÍ:
    if pipeline_trace is None:
        pipeline_trace = {}
    pipeline_trace["synthesizer"] = {
        "output": {
            "pattern": synth_output.pattern,
            "degraded": synth_output.degraded,
            "degradation_reason": synth_output.degradation_reason,
        }
    }

    return OrchestratorResult(output=synth_output, session_id=session.session_id, pipeline_trace=pipeline_trace)

def _make_failed_result(
    query: str,
    domain: Domain,
    reason: str,
) -> ExecutionResult:
    """Construye un ExecutionResult de fallo para casos sin Executor."""
    return ExecutionResult(
        plan_id="no-plan",
        domain=domain,
        original_query=query,
        results={},
        final_status="failed",
        replan_count=0,
        failure_reason=reason,
    )