"""
session.py

Gestión de sesiones en memoria. Las sesiones se pierden al reiniciar.
El dominio se fija en /login y es inmutable durante la sesión.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict

from models import ConversationTurn, Domain


class Session:
    def __init__(self, operator_id: str, domain: Domain) -> None:
        self.session_id: str = str(uuid.uuid4())
        self.operator_id: str = operator_id
        self.domain: Domain = domain
        self.created_at: str = datetime.now(timezone.utc).isoformat()
        self.history: list[ConversationTurn] = []

    def add_turn(self, user_input: str, assistant_response: str) -> None:
        self.history.append(ConversationTurn(
            user_input=user_input,
            assistant_response=assistant_response,
            timestamp=datetime.now(timezone.utc).isoformat(),
        ))

    def last_n_turns(self, n: int) -> list[ConversationTurn]:
        return self.history[-n:] if self.history else []


class SessionStore:
    def __init__(self) -> None:
        self._sessions: Dict[str, Session] = {}

    def create(self, operator_id: str, domain: Domain) -> Session:
        session = Session(operator_id=operator_id, domain=domain)
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


# Singleton global
session_store = SessionStore()