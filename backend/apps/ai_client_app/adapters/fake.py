"""Adaptador falso y determinista (S8-B).

Implementación por defecto en pruebas y CI. Existe para ejercitar la
infraestructura —parser, mapeo, política— sin red, sin cuota y sin credenciales,
no para imitar el criterio de un modelo (ADR-0008, §3 y §4).

Determinismo estricto: sin `random`, sin reloj, sin UUID, sin entorno, sin
`settings` y sin base de datos. La misma petición produce la misma cadena byte a
byte, porque una prueba de concordancia sobre una salida inestable no mide nada.
"""

from __future__ import annotations

import json

from apps.core.enums import ReportPriority

from ..contracts import ClassificationRequest

# Valores fijos y documentados. La confianza está por encima del umbral de 0.75
# para que el camino elegible sea el que se ejercita por defecto; las pruebas que
# necesitan el camino de revisión humana construyen su propia respuesta.
FAKE_CONFIDENCE = 0.95
FAKE_JUSTIFICATION = "Clasificación simulada determinista para pruebas offline."


class FakeAiClassificationAdapter:
    """Devuelve una clasificación válida derivada solo de la petición."""

    def classify(self, request: ClassificationRequest) -> str:
        payload = {
            "category_slug": _pick(request.current_category_slug, request.allowed_category_slugs),
            "institution_slug": _pick(
                request.current_institution_slug, request.allowed_institution_slugs
            ),
            "priority": _pick_priority(request.allowed_priorities),
            "confidence": FAKE_CONFIDENCE,
            "inconsistency": False,
            "justification": FAKE_JUSTIFICATION,
        }
        # `sort_keys` y separadores explícitos fijan la representación: sin ellos
        # el orden dependería del dict y la estabilidad byte a byte sería una
        # coincidencia, no una garantía.
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _pick(current: str | None, allowed: tuple[str, ...]) -> str | None:
    """Conserva el valor actual si es admisible; si no, el menor permitido.

    Confirmar lo que eligió el ciudadano cuando es válido reproduce el caso
    frecuente —la categoría era correcta— y hace que `derive_has_changes`
    devuelva `False`, que es el escenario que conviene tener cubierto por
    defecto. Cuando el valor actual no está permitido se toma el menor del
    conjunto ordenado: arbitrario pero estable, que es todo lo que se le pide.
    """
    if current is not None and current in allowed:
        return current
    return min(allowed) if allowed else None


def _pick_priority(allowed: tuple[str, ...]) -> str:
    if ReportPriority.MEDIA in allowed:
        return str(ReportPriority.MEDIA)
    return min(allowed) if allowed else str(ReportPriority.MEDIA)
