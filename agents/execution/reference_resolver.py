"""
reference_resolver.py

Resuelve referencias {{Ek}} y {{Ek.field}} en los args de un StepPlan
usando los outputs ya disponibles en el ExecutionContext.

Convencion de outputs por tipo de tool:

  SELECT (bindings):   lista de dicts, ej. [{"x": "ex:Nodo-5", "tipo": "..."}]
  CONSTRUCT (turtle):  dict {"type": "construct", "turtle": "..."}
  doctrine chunks:     lista de dicts {"text": ..., "source_doc": ..., ...}
  resolve_entity:      lista de dicts {"x": IRI, "matched_prop": ..., ...}

Reglas de resolucion:

  {{Ek}}        -> el output completo serializado como string JSON
  {{Ek.field}}  -> campo 'field' del primer elemento si el output es lista,
                   o campo 'field' del dict si es un dict plano.

  Si el campo no existe o el output esta vacio, lanza ReferenceResolutionError.
"""

from __future__ import annotations

import json
import re
from typing import Any

REF_PATTERN = re.compile(r"\{\{\s*(E\d+)(?:\.([^\s}]+))?\s*\}\}")


class ReferenceResolutionError(Exception):
    """Error semantico: referencia no puede resolverse."""


def resolve_args(
    args: dict[str, Any],
    results: dict[str, Any],  # step_id -> StepResult.output
) -> dict[str, Any]:
    """Sustituye todas las referencias {{Ek}} / {{Ek.field}} en args.

    Devuelve un nuevo dict con todos los valores resueltos.
    Lanza ReferenceResolutionError si alguna referencia no puede resolverse.
    """
    resolved: dict[str, Any] = {}
    for key, value in args.items():
        resolved[key] = _resolve_value(value, results)
    return resolved


def _resolve_value(value: Any, results: dict[str, Any]) -> Any:
    """Resuelve recursivamente un valor que puede contener referencias."""
    if isinstance(value, str):
        return _resolve_string(value, results)
    if isinstance(value, dict):
        return {k: _resolve_value(v, results) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_value(v, results) for v in value]
    return value


def _resolve_string(value: str, results: dict[str, Any]) -> Any:
    """Resuelve referencias en un string.

    Si el string ES una sola referencia (ej. "{{E1.x}}"), devuelve el
    valor tipado (string, lista, dict). Si el string CONTIENE referencias
    mezcladas con texto (ej. "nodo {{E1.x}} activo"), sustituye inline
    como string.
    """
    matches = list(REF_PATTERN.finditer(value))
    if not matches:
        return value

    # Caso: string es exactamente una referencia
    if len(matches) == 1 and matches[0].group(0) == value.strip():
        m = matches[0]
        return _extract(m.group(1), m.group(2), results, value)

    # Caso: referencias inline en un string mas largo
    def replacer(m: re.Match) -> str:
        extracted = _extract(m.group(1), m.group(2), results, value)
        if isinstance(extracted, (dict, list)):
            return json.dumps(extracted, ensure_ascii=False)
        return str(extracted)

    return REF_PATTERN.sub(replacer, value)


def _extract(step_id: str, field: str | None, results: dict[str, Any], original: str) -> Any:
    """Extrae el valor para un step_id y campo opcional."""
    if step_id not in results:
        raise ReferenceResolutionError(
            f"Referencia {original!r}: step {step_id!r} no tiene resultado disponible."
        )

    output = results[step_id]

    if output is None:
        raise ReferenceResolutionError(
            f"Referencia {original!r}: step {step_id!r} tiene output None."
        )

    # Sin campo: devolver output completo
    if field is None:
        return output

    # Con campo: buscar en el output
    return _get_field(output, field, step_id, original)


def _get_field(output: Any, field: str, step_id: str, original: str) -> Any:
    """Extrae un campo de un output estructurado."""

    # Output es un dict plano
    if isinstance(output, dict):
        if field in output:
            return output[field]
        raise ReferenceResolutionError(
            f"Referencia {original!r}: campo {field!r} no encontrado en "
            f"output de {step_id}. Claves disponibles: {list(output.keys())}"
        )

    # Output es lista: extraer campo del primer elemento
    if isinstance(output, list):
        if not output:
            raise ReferenceResolutionError(
                f"Referencia {original!r}: output de {step_id} es lista vacia, "
                f"no se puede extraer campo {field!r}."
            )
        first = output[0]
        if isinstance(first, dict) and field in first:
            return first[field]
        raise ReferenceResolutionError(
            f"Referencia {original!r}: campo {field!r} no encontrado en "
            f"primer elemento de {step_id}. "
            f"Claves disponibles: {list(first.keys()) if isinstance(first, dict) else 'no es dict'}"
        )

    # Output es string u otro primitivo
    raise ReferenceResolutionError(
        f"Referencia {original!r}: output de {step_id} es {type(output).__name__}, "
        f"no se puede extraer campo {field!r}."
    )