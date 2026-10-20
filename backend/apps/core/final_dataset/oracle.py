"""Oráculo declarativo separado del generador y del ledger del producto."""

from datetime import datetime, timedelta

from .recipe import INSTITUTIONS


def expected_ledger(recipe):
    rows = []

    def add(
        case,
        event,
        citizen,
        delta,
        status,
        cycle=None,
        credited=None,
        confirmation=False,
        related=None,
        created=None,
        reversed_at=None,
        voided_at=None,
    ):
        row = {
            "case": case["id"] if case else None,
            "event": event,
            "user": f"C{citizen:02}",
            "impact": 0 if event == "REDEMPTION" else delta,
            "credit": delta,
            "status": status,
            "cycle": cycle,
            "confirmation": confirmation,
            "related": related,
            "institution": case["institution"] if case and cycle else None,
            "credited_at": credited,
            "created_at": created,
            "reversed_at": reversed_at,
            "voided_at": voided_at,
        }
        rows.append(row)
        return row

    for case in recipe.cases():
        at = datetime.fromisoformat(case["at"])
        kind = case["kind"]
        pending = kind == "pending"
        rejected = kind == "rejected"
        reversed_case = case["id"] == "R001" or kind == "revalidated"
        status = (
            "PENDIENTE"
            if pending
            else "ANULADO"
            if rejected
            else "REVERTIDO"
            if reversed_case
            else "ACREDITADO"
        )
        cycle = None if pending or rejected else 1
        credited = None if pending or rejected else at + timedelta(seconds=3)
        reversed_at = (
            recipe.operation + timedelta(seconds=2)
            if case["id"] == "R001"
            else at + timedelta(seconds=4)
            if kind == "revalidated"
            else None
        )
        voided_at = at + timedelta(seconds=3) if rejected else None
        originals = []
        if case["author"] is not None:
            originals.append(
                add(
                    case,
                    "REPORT_CREATED",
                    case["author"],
                    20,
                    status,
                    cycle,
                    credited,
                    created=at,
                    reversed_at=reversed_at,
                    voided_at=voided_at,
                )
            )
        for citizen in case["confirmants"]:
            originals.append(
                add(
                    case,
                    "CONFIRMATION_CREATED",
                    citizen,
                    10,
                    status,
                    cycle,
                    credited,
                    confirmation=True,
                    created=at + timedelta(seconds=2),
                    reversed_at=reversed_at,
                    voided_at=voided_at,
                )
            )
        if not pending and not rejected and case["author"] is not None:
            originals.append(
                add(
                    case,
                    "VALIDATION_BONUS",
                    case["author"],
                    30,
                    status,
                    1,
                    credited,
                    created=credited,
                    reversed_at=reversed_at,
                )
            )
        if kind in {"funding", "closed"}:
            originals.append(
                add(
                    case,
                    "RESOLUTION_BONUS",
                    case["author"],
                    50,
                    status,
                    1,
                    at + timedelta(seconds=6),
                    created=at + timedelta(seconds=6),
                    reversed_at=reversed_at,
                )
            )
        if reversed_case:
            for original in originals:
                add(
                    case,
                    "REVERSAL",
                    int(original["user"][1:]),
                    -original["credit"],
                    "REVERTIDO",
                    1,
                    related=(original["event"], original["user"], original["cycle"]),
                    created=reversed_at,
                    reversed_at=reversed_at,
                )
        if kind == "revalidated":
            add(
                case,
                "VALIDATION_BONUS",
                case["author"],
                30,
                "ACREDITADO",
                2,
                at + timedelta(seconds=5),
                created=at + timedelta(seconds=5),
            )
    for citizen, institution in enumerate(INSTITUTIONS):
        row = add(
            None,
            "REDEMPTION",
            citizen,
            -1000,
            "ACREDITADO",
            credited=recipe.operation,
            created=recipe.operation,
        )
        row["institution"] = institution
    return rows


def expected_history(case):
    """(estado, ciclo, segundo relativo, actor lógico)."""
    kind = case["kind"]
    author = f"C{case['author']:02}" if case["author"] is not None else None
    operator = f"operator-{case['institution']}"
    events = [("CREADO", 0, 0, author)]
    if kind == "pending":
        return events
    if kind != "x_mock":
        events.append(("CLASIFICADO", 0, 1, None))
    if kind == "rejected":
        return [*events, ("RECHAZADO", 0, 3, operator)]
    events.append(("ASIGNADO", 1, 3, operator))
    if kind in {"funding", "closed"}:
        events.extend([("EN_PROCESO", 1, 4, operator), ("RESUELTO", 1, 6, operator)])
    if kind == "revalidated":
        events.extend([("CLASIFICADO", 1, 4, "admin"), ("ASIGNADO", 2, 5, operator)])
    if case["id"] == "R001":
        events.append(("CLASIFICADO", 1, "operation+2", "admin"))
    if kind == "closed":
        events.append(("CERRADO", 1, "operation", None))
    return events
