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


#: Celdas que se suman entre instituciones y, por eso, se pueden despejar por diferencia.
ADDITIVE_CELLS = ("confirmations", "redemptions", "credits_issued")


def complement(totals: dict, rows: list[dict], *, cells=ADDITIVE_CELLS) -> None:
    """Supresión complementaria: ninguna celda suprimida se despeja restando.

    Si en una columna queda una sola institución suprimida, `totals − Σ publicadas`
    la revelaría. Se suprime además la celda publicada más chica de esa columna, o el
    total si no queda otra. Las tasas y medianas no son aditivas y no se tocan.
    """
    for cell in cells:
        hidden = [row for row in rows if cell in row["suppressed"]]
        if totals[cell] is None or len(hidden) != 1:
            continue
        shown = [row for row in rows if cell not in row["suppressed"]]
        target = min(shown, key=lambda row: row[cell]) if shown else totals
        target[cell] = None
        target["suppressed"].append(cell)
