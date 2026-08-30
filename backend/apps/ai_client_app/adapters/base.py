"""Interfaz del proveedor de clasificación (S8-B).

Deliberadamente diminuta: recibe el contrato permitido y devuelve texto crudo.

No parsea, no persiste, no toca el ORM y no conoce Celery. Devolver `str` y no
una estructura ya validada es intencional: el parser es la frontera de seguridad
(D13), y un adaptador que devolviese objetos de dominio la esquivaría.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..contracts import ClassificationRequest


@runtime_checkable
class AiClassificationAdapter(Protocol):
    """Contrato que implementan por igual el proveedor real y el falso.

    Que ambos compartan interfaz es lo que hace que las pruebas ejerciten el
    mismo camino que producción (ADR-0008, §3).
    """

    def classify(self, request: ClassificationRequest) -> str:
        """Devuelve la respuesta cruda del proveedor como texto JSON."""
        ...
