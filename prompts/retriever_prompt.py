"""
retriever_prompt.py

Genera el bloque de contexto doctrinal que se inyecta en el system prompt
del Planner cuando el Retriever ha recuperado chunks relevantes.

El Planner lo recibe como parte de su contexto, NO como historial de usuario,
para que sepa:
  1. Qué términos del dominio corresponden a qué clases de la ontología.
  2. Qué objetivos y procedimientos doctrinales son relevantes para la consulta.
  3. Si debe incluir retrieve_doctrine en el plan o no.
"""

from __future__ import annotations
from models import RetrieverOutput


def build_retriever_context_block(ro: RetrieverOutput) -> str:
    """Construye el bloque de contexto doctrinal para el Planner."""

    if not ro or (not ro.chunks and not ro.relevant_terms):
        return ""

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "CONTEXTO DOCTRINAL (recuperado por el Retriever)",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    # Mapeo de términos
    if ro.relevant_terms:
        lines.append("")
        lines.append("Terminología del operador → vocabulario de la ontología:")
        for term, onto_class in ro.relevant_terms.items():
            lines.append(f"  · \"{term}\" → clase ex:{onto_class}")
        lines.append(
            "Usa estas equivalencias al construir los args de los steps "
            "(entity_type, class_name, target_class)."
        )

    # Flags de fuentes
    lines.append("")
    if ro.needs_graph and ro.needs_doctrine:
        lines.append(
            "FUENTES NECESARIAS: grafo C2 + doctrina. "
            "El plan DEBE incluir tanto steps de ontología como retrieve_doctrine."
        )
    elif ro.needs_graph and not ro.needs_doctrine:
        lines.append(
            "FUENTES NECESARIAS: solo grafo C2. "
            "No es necesario incluir retrieve_doctrine en el plan."
        )
    elif ro.needs_doctrine and not ro.needs_graph:
        lines.append(
            "FUENTES NECESARIAS: solo doctrina. "
            "El plan DEBE incluir retrieve_doctrine y NO necesita steps de ontología."
        )

    # Fragmentos doctrinales más relevantes (máx. 3, truncados)
    if ro.chunks:
        top_chunks = sorted(
            ro.chunks,
            key=lambda x: x.get("score") or 0,
            reverse=True,
        )[:3]
        lines.append("")
        lines.append("Fragmentos doctrinales más relevantes:")
        for i, ch in enumerate(top_chunks, 1):
            src   = ch.get("source_doc", "")
            page  = ch.get("page", "")
            score = ch.get("score")
            text  = ch.get("text", "")
            score_str = f" [score={score:.2f}]" if score else ""
            lines.append(f"  [{i}] {src} p.{page}{score_str}")
            # Primeras 250 chars para no inflar el prompt
            excerpt = text[:250].replace("\n", " ").strip()
            if len(text) > 250:
                excerpt += "..."
            lines.append(f"      {excerpt}")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)