"""Servicio de auditoría transversal (§30 del plan).

El Sprint 2 entrega únicamente la infraestructura mínima: una función de
escritura disponible desde el inicio del proyecto. Las acciones concretas que
deben auditarse se conectan en sus sprints respectivos (S4 en adelante); aquí no
se registra ningún `signal` global ni se instrumenta ningún flujo.
"""

from __future__ import annotations

from typing import Any

from apps.core.enums import ActorType


def log(
    actor,
    action: str,
    payload: dict[str, Any] | None = None,
    *,
    actor_type: str | None = None,
):
    """Registra una acción en `audit_logs` y devuelve la fila creada.

    `actor` es una instancia de `AUTH_USER_MODEL` o `None` cuando la acción no
    tiene autor humano. Si no se indica `actor_type` se deduce: con actor,
    `human`; sin actor, `system`. Las decisiones de IA deben pasar
    `actor_type=ActorType.AI` de forma explícita (RF-24).

    La importación del modelo es diferida a propósito: `core` no puede depender
    de `admin_app` en tiempo de importación sin invertir el grafo de
    dependencias del §9 del plan.
    """
    from apps.admin_app.models import AuditLog

    if actor_type is None:
        actor_type = ActorType.HUMAN if actor is not None else ActorType.SYSTEM

    return AuditLog.objects.create(
        actor_user=actor,
        actor_type=actor_type,
        action=action,
        payload=payload or {},
    )
