"""Serialización estable del informe de evaluación (S8-E1).

Dos ejecuciones equivalentes deben producir exactamente el mismo texto. Sin eso
no se pueden comparar dos mediciones ni archivar una como evidencia: cualquier
diferencia de formato se confundiría con una diferencia de resultado.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from decimal import Decimal

from .runner import EvaluationReport


def report_to_dict(report: EvaluationReport) -> dict:
    """Diccionario con los `Decimal` ya convertidos a cadena.

    Cadena y no `float`: serializar 85.00 como binario reintroduciría justo la
    imprecisión que `Decimal` evita en el cálculo.
    """
    datos = asdict(report)
    return _normalizar(datos)


def report_to_json(report: EvaluationReport) -> str:
    """JSON determinista: claves ordenadas, separadores fijos y UTF-8."""
    return json.dumps(
        report_to_dict(report),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _normalizar(valor):
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, dict):
        return {k: _normalizar(v) for k, v in valor.items()}
    if isinstance(valor, list | tuple):
        return [_normalizar(v) for v in valor]
    return valor
