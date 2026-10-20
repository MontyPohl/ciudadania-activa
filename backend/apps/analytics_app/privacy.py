"""Supresión de celdas públicas según su respaldo, sin alterar el original."""

from copy import deepcopy

from apps.analytics_app.constants import MIN_CELL_SIZE


def suppress(cells: dict, *, counts: dict[str, int], k: int = MIN_CELL_SIZE) -> dict:
    result = deepcopy(cells)
    result["suppressed"] = []
    for path, count in counts.items():
        if count >= k:
            continue
        target = result
        *parents, key = path.split(".")
        for parent in parents:
            target = target[parent]
        target[key] = None
        result["suppressed"].append(path)
    return result
