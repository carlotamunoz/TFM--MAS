"""
api.py

FastAPI application con dos endpoints:

  POST /login  → crea sesión, fija dominio del operador
  POST /query  → ejecuta el pipeline completo y devuelve respuesta

La sesión se identifica por session_id en el header X-Session-ID.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel

from models import Domain, SynthesizerOutput
from orchestration.orchestrator import run_query
from session import session_store

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("SA-MultiAgent API arrancando...")
    yield
    logger.info("SA-MultiAgent API cerrando...")


app = FastAPI(
    title="SA-MultiAgent API",
    description="Sistema de inteligencia operacional multi-agente.",
    version="2.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Schemas de request / response
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    operator_id: str
    domain: Domain


class LoginResponse(BaseModel):
    session_id: str
    operator_id: str
    domain: str
    message: str


class QueryRequest(BaseModel):
    query: str


class QueryResponse(BaseModel):
    session_id: str
    response: SynthesizerOutput
    clarification_needed: bool = False
    clarification_question: str | None = None
    pipeline_trace: dict | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post(
    "/login",
    response_model=LoginResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear sesión y fijar dominio operacional",
)
async def login(body: LoginRequest) -> LoginResponse:
    """Crea una nueva sesión. El dominio es inmutable durante la sesión."""
    session = session_store.create(
        operator_id=body.operator_id,
        domain=body.domain,
    )
    logger.info(
        "Login: operator=%s domain=%s session=%s",
        body.operator_id, body.domain.value, session.session_id[:8],
    )
    return LoginResponse(
        session_id=session.session_id,
        operator_id=session.operator_id,
        domain=session.domain.value,
        message=f"Sesión creada para operador {body.operator_id} en dominio {body.domain.value}.",
    )


@app.post(
    "/query",
    response_model=QueryResponse,
    summary="Ejecutar una consulta operacional",
)
async def query(
    body: QueryRequest,
    x_session_id: str = Header(..., alias="X-Session-ID"),
) -> QueryResponse:
    """Ejecuta el pipeline completo: Classifier → Planner → Executor → Synthesizer."""

    session = session_store.get(x_session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Sesión no encontrada: {x_session_id}. Llama primero a /login.",
        )

    if not body.query.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La consulta no puede estar vacía.",
        )

    logger.info(
        "Query: session=%s query='%s'",
        x_session_id[:8], body.query[:80],
    )

    result = await run_query(user_input=body.query, session=session)

    return QueryResponse(
        session_id=result.session_id,
        response=result.output,
        clarification_needed=result.clarification_needed,
        clarification_question=result.clarification_question,
        pipeline_trace=result.pipeline_trace,
    )


@app.delete(
    "/session",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cerrar sesión activa",
)
async def logout(
    x_session_id: str = Header(..., alias="X-Session-ID"),
) -> None:
    """Elimina la sesión activa."""
    session_store.delete(x_session_id)
    logger.info("Logout: session=%s", x_session_id[:8])


@app.get("/health", summary="Health check")
async def health() -> dict:
    return {"status": "ok", "version": "2.0.0"}