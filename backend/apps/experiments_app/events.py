from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from . import runtime

logger = logging.getLogger(__name__)


def record_event(
    *,
    run_id: str,
    scenario_id: str,
    stream: str,
    event: dict[str, Any],
) -> None:
    if not runtime.enabled():
        return
    payload = {
        "schema_version": "s11-technical-event-v1",
        "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "run_id": run_id,
        "scenario_id": scenario_id,
        **event,
    }
    try:
        runtime.evidence_store().append_jsonl(
            run_id=run_id,
            scenario_id=scenario_id,
            stream=stream,
            payload=payload,
        )
    except Exception:
        # La instrumentación nunca altera la regla funcional ni transforma un éxito
        # de producto en fallo. El harness detecta la ausencia como run inválido.
        logger.exception("No se pudo persistir un evento técnico S11 redactado.")
