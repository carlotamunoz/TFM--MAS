"""
executor.py

Orquesta la ejecucion de un ExecutionPlan siguiendo la arquitectura ReWOO:
  - Ejecucion secuencial respetando depends_on.
  - 0 llamadas LLM (el Planner ya genero todo, incluido raw_sparql).
  - Reintentos locales con backoff para errores transitorios.
  - Re-planning para errores semanticos (max 2 por turno).
  - Deteccion de EMPTY bloqueante (resultado vacio con dependientes).

Politica de errores:
  TRANSIENT  -> reintento local con backoff exponencial (max TRANSIENT_MAX_RETRIES)
                Si agota reintentos -> trata como SEMANTIC.
  SEMANTIC   -> pausa, empaqueta ReplanRequest, invoca al re-planner.
                Max MAX_REPLANS re-planificaciones por turno.
                Si agota -> ExecutionResult con final_status="failed".
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from models import (
    Domain,
    ErrorType,
    ExecutionContext,
    ExecutionPlan,
    ExecutionResult,
    ReplanRequest,
    StepPlan,
    StepResult,
    StepStatus,
)
from agents.execution.reference_resolver import ReferenceResolutionError, resolve_args
from agents.execution.tool_runner import classify_error, run_tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuracion
# ---------------------------------------------------------------------------

TRANSIENT_MAX_RETRIES   = int(os.getenv("EXECUTOR_TRANSIENT_RETRIES", "3"))
TRANSIENT_BACKOFF_BASE  = float(os.getenv("EXECUTOR_BACKOFF_BASE", "1.0"))  # segundos
MAX_REPLANS             = int(os.getenv("EXECUTOR_MAX_REPLANS", "2"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _execution_order(plan: ExecutionPlan) -> list[StepPlan]:
    """Ordena steps respetando depends_on (topological sort).

    Garantia: el validador de ExecutionPlan ya verifico que no hay ciclos.
    """
    step_map = {s.step_id: s for s in plan.steps}
    visited: set[str] = set()
    order: list[StepPlan] = []

    def visit(step_id: str) -> None:
        if step_id in visited:
            return
        for dep in step_map[step_id].depends_on:
            visit(dep)
        visited.add(step_id)
        order.append(step_map[step_id])

    for step in plan.steps:
        visit(step.step_id)

    return order


def _has_downstream_dependents(step_id: str, plan: ExecutionPlan) -> bool:
    """Devuelve True si algun otro step depende de step_id."""
    return any(step_id in s.depends_on for s in plan.steps)


def _build_execution_result(
    ctx: ExecutionContext,
    final_status: str,
    total_ms: int,
    failure_reason: str | None = None,
) -> ExecutionResult:
    return ExecutionResult(
        plan_id=ctx.plan.plan_id,
        domain=ctx.plan.domain,
        original_query=ctx.plan.original_query,
        results=ctx.results,
        final_status=final_status,  # type: ignore[arg-type]
        replan_count=ctx.replan_count,
        total_duration_ms=total_ms,
        failure_reason=failure_reason,
    )


# ---------------------------------------------------------------------------
# Ejecucion de un step individual (con reintentos transitorios)
# ---------------------------------------------------------------------------

async def _execute_step(
    step: StepPlan,
    ctx: ExecutionContext,
) -> StepResult:
    """Ejecuta un step con reintentos para errores transitorios.

    Devuelve StepResult con status SUCCESS, EMPTY, o FAILED.
    Nunca lanza excepcion: encapsula todos los errores en StepResult.
    """
    t_start = time.monotonic()

    # Resolver referencias en args
    try:
        resolved_args = resolve_args(
            step.args,
            {sid: r.output for sid, r in ctx.results.items()},
        )
    except ReferenceResolutionError as exc:
        # Error semantico: la referencia no pudo resolverse
        return StepResult(
            step_id=step.step_id,
            tool=step.tool,
            status=StepStatus.FAILED,
            error_type=ErrorType.SEMANTIC,
            error_msg=f"ReferenceResolutionError: {exc}",
            duration_ms=int((time.monotonic() - t_start) * 1000),
        )

    # Ejecutar el tool con reintentos transitorios
    attempts = 0
    last_exc: Exception | None = None

    for attempt in range(TRANSIENT_MAX_RETRIES + 1):
        attempts = attempt + 1
        try:
            output = run_tool(step.tool, resolved_args)
            duration_ms = int((time.monotonic() - t_start) * 1000)

            # Detectar resultado vacio
            is_empty = (
                output is None
                or (isinstance(output, (list, dict)) and len(output) == 0)
            )

            if is_empty:
                # EMPTY bloqueante: hay steps que dependen de este
                if _has_downstream_dependents(step.step_id, ctx.plan):
                    logger.warning(
                        "Step %s devolvio EMPTY pero tiene dependientes → SEMANTIC error",
                        step.step_id,
                    )
                    return StepResult(
                        step_id=step.step_id,
                        tool=step.tool,
                        status=StepStatus.EMPTY,
                        output=output,
                        error_type=ErrorType.SEMANTIC,
                        error_msg=(
                            f"Step {step.step_id} ({step.tool}) devolvio resultado vacio "
                            f"y hay steps posteriores que dependen de el."
                        ),
                        attempts=attempts,
                        duration_ms=duration_ms,
                    )
                else:
                    # EMPTY no bloqueante: resultado valido (ej. "no hay drones en vuelo")
                    logger.info("Step %s: EMPTY no bloqueante (sin dependientes)", step.step_id)
                    return StepResult(
                        step_id=step.step_id,
                        tool=step.tool,
                        status=StepStatus.EMPTY,
                        output=output,
                        attempts=attempts,
                        duration_ms=duration_ms,
                    )

            logger.info(
                "Step %s OK: tool=%s output_size=%s attempts=%d",
                step.step_id, step.tool,
                len(output) if isinstance(output, (list, dict)) else "scalar",
                attempts,
            )
            return StepResult(
                step_id=step.step_id,
                tool=step.tool,
                status=StepStatus.SUCCESS,
                output=output,
                attempts=attempts,
                duration_ms=duration_ms,
            )

        except Exception as exc:
            last_exc = exc
            error_type = classify_error(exc)

            if error_type == ErrorType.TRANSIENT and attempt < TRANSIENT_MAX_RETRIES:
                wait = TRANSIENT_BACKOFF_BASE * (2 ** attempt)
                logger.warning(
                    "Step %s: error transitorio (intento %d/%d), reintento en %.1fs: %s",
                    step.step_id, attempt + 1, TRANSIENT_MAX_RETRIES + 1, wait, exc,
                )
                await asyncio.sleep(wait)
                continue

            # Error semantico O agoto reintentos transitorios
            final_type = error_type if error_type == ErrorType.SEMANTIC else ErrorType.SEMANTIC
            logger.error(
                "Step %s FAILED: tool=%s error=%s",
                step.step_id, step.tool, exc,
            )
            return StepResult(
                step_id=step.step_id,
                tool=step.tool,
                status=StepStatus.FAILED,
                error_type=final_type,
                error_msg=str(exc),
                attempts=attempts,
                duration_ms=int((time.monotonic() - t_start) * 1000),
            )

    # No deberia llegar aqui
    return StepResult(
        step_id=step.step_id,
        tool=step.tool,
        status=StepStatus.FAILED,
        error_type=ErrorType.SEMANTIC,
        error_msg=f"Unexpected exit from retry loop. Last: {last_exc}",
        attempts=attempts,
    )


# ---------------------------------------------------------------------------
# Executor principal
# ---------------------------------------------------------------------------

class Executor:
    """Ejecuta un ExecutionPlan de forma determinista (0 llamadas LLM).

    El re-planning se delega al planner_agent si se producen errores
    semanticos, importado de forma lazy para evitar dependencias circulares.
    """

    async def run(self, plan: ExecutionPlan) -> ExecutionResult:
        t_global = time.monotonic()
        ctx = ExecutionContext(plan=plan, max_replans=MAX_REPLANS)

        result = await self._execute_plan(ctx)

        total_ms = int((time.monotonic() - t_global) * 1000)
        result = result.model_copy(update={"total_duration_ms": total_ms})
        logger.info(
            "Executor finalizado: status=%s replans=%d total_ms=%d",
            result.final_status, result.replan_count, total_ms,
        )
        return result

    async def _execute_plan(self, ctx: ExecutionContext) -> ExecutionResult:
        """Ejecuta los steps del plan en orden topologico."""
        ordered_steps = _execution_order(ctx.plan)

        for step in ordered_steps:
            # Saltar steps que ya tienen resultado (de un plan anterior reutilizado)
            if step.step_id in ctx.results:
                logger.debug("Step %s ya tiene resultado, saltando", step.step_id)
                continue

            step_result = await _execute_step(step, ctx)
            ctx.results[step.step_id] = step_result

            # Si el step fallo con error semantico, activar re-planning
            if step_result.status in (StepStatus.FAILED, StepStatus.EMPTY) \
                    and step_result.error_type == ErrorType.SEMANTIC:

                if ctx.replan_count >= ctx.max_replans:
                    logger.error(
                        "Agotados re-plannings (%d/%d). Devolviendo fallo.",
                        ctx.replan_count, ctx.max_replans,
                    )
                    return _build_execution_result(
                        ctx,
                        final_status="failed",
                        total_ms=0,
                        failure_reason=(
                            f"Step {step.step_id} fallo ({step_result.error_msg}) "
                            f"y se agotaron los {ctx.max_replans} re-plannings permitidos."
                        ),
                    )

                # Invocar re-planner
                new_plan = await self._invoke_replanner(ctx, step)
                if new_plan is None:
                    return _build_execution_result(
                        ctx,
                        final_status="failed",
                        total_ms=0,
                        failure_reason=f"Re-planner no pudo reparar el plan tras fallo en {step.step_id}.",
                    )

                # Si el re-planner devuelve plan vacio, la query es irresoluble
                if not new_plan.steps:
                    return _build_execution_result(
                        ctx,
                        final_status="failed",
                        total_ms=0,
                        failure_reason=f"Re-planner determino que la query es irresoluble: {new_plan.rationale}",
                    )

                # Actualizar contexto con el plan reparado y reiniciar el loop
                ctx.plan = new_plan
                ctx.replan_count += 1
                return await self._execute_plan(ctx)  # recursion con nuevo plan

        # Todos los steps completados
        has_failure = any(
            r.status == StepStatus.FAILED for r in ctx.results.values()
        )
        has_empty = any(
            r.status == StepStatus.EMPTY for r in ctx.results.values()
        )

        if has_failure:
            final_status = "partial"
        elif has_empty and not has_failure:
            final_status = "partial"
        else:
            final_status = "success"

        return _build_execution_result(ctx, final_status=final_status, total_ms=0)

    async def _invoke_replanner(
        self,
        ctx: ExecutionContext,
        failed_step: StepPlan,
    ) -> ExecutionPlan | None:
        """Invoca el re-planner y devuelve el plan reparado o None si falla."""
        from agents.planner_agent import replan

        replan_request = ReplanRequest(
            original_plan=ctx.plan,
            partial_results=dict(ctx.results),
            failed_step=failed_step,
            error_type="semantic",
            error_msg=ctx.results[failed_step.step_id].error_msg or "unknown error",
            replan_attempt=ctx.replan_count + 1,
        )

        logger.warning(
            "Invocando re-planner (intento %d/%d) por fallo en step %s",
            ctx.replan_count + 1, ctx.max_replans, failed_step.step_id,
        )

        try:
            return await replan(replan_request)
        except Exception as exc:
            logger.error("Re-planner fallo: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Helper sincrono para tests / CLI
# ---------------------------------------------------------------------------

def execute_sync(plan: ExecutionPlan) -> ExecutionResult:
    return asyncio.run(Executor().run(plan))
