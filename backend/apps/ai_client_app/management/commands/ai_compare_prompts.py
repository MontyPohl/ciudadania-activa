"""Compara prompts con una planilla local sin persistir clasificaciones."""

import csv
from dataclasses import asdict
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.admin_app.models import Category, Institution
from apps.ai_client_app.prompt_trial import (
    build_trial_adapter,
    build_trial_request,
    load_photo,
    normalize_catalog_label,
    normalize_priority,
    run_trial,
)
from apps.ai_client_app.prompts import PROMPT_VERSIONS

COLUMNS = (
    "id",
    "foto",
    "descripcion",
    "categoria_elegida",
    "categoria_esperada",
    "institucion_esperada",
    "prioridad_esperada",
    "por_que",
    "tipo_caso",
)


class Command(BaseCommand):
    help = "Compara prompts de OpenAI con un CSV UTF-8 y fotos locales."

    def add_arguments(self, parser):
        parser.add_argument("--cases", required=True)
        parser.add_argument("--photos", required=True)
        parser.add_argument(
            "--prompts", nargs="+", choices=tuple(PROMPT_VERSIONS), default=["v1", "v2"]
        )
        parser.add_argument("--out")

    def _load_cases(self, options):
        cases = []
        with Path(options["cases"]).open(encoding="utf-8-sig", newline="") as source:
            header = source.readline()
            delimiter = ";" if header.count(";") > header.count(",") else ","
            source.seek(0)
            reader = csv.DictReader(source, delimiter=delimiter)
            if (
                reader.fieldnames is None
                or len(reader.fieldnames) != len(COLUMNS)
                or set(reader.fieldnames) != set(COLUMNS)
            ):
                raise CommandError(
                    "El CSV debe tener los encabezados exactos: " + ", ".join(COLUMNS)
                )
            for line, row in enumerate(reader, start=2):
                if not any(value and str(value).strip() for value in row.values()):
                    continue
                try:
                    if None in row or any(row[column] is None for column in COLUMNS):
                        raise ValueError("Cantidad de columnas inválida.")
                    if not row["id"].strip() or not row["descripcion"].strip():
                        raise ValueError("El id y la descripción son obligatorios.")
                    expected = (
                        normalize_catalog_label(row["categoria_esperada"], Category),
                        normalize_catalog_label(row["institucion_esperada"], Institution),
                        normalize_priority(row["prioridad_esperada"]),
                    )
                    image = None
                    if row["foto"].strip():
                        folder = Path(options["photos"]).resolve()
                        photo = (folder / row["foto"].strip()).resolve()
                        if not photo.is_relative_to(folder):
                            raise ValueError("La foto debe estar dentro de la carpeta indicada.")
                        image = load_photo(photo)
                    request = build_trial_request(
                        row["descripcion"], row["categoria_elegida"], image
                    )
                except (OSError, ValueError) as exc:
                    self.stderr.write(f"Fila {line} omitida: {exc}")
                    continue
                cases.append((row, request, expected))
        return cases

    def handle(self, *args, **options):
        try:
            cases = self._load_cases(options)
        except (OSError, UnicodeError, csv.Error) as exc:
            raise CommandError("No se pudo leer el CSV de casos.") from exc
        versions = list(dict.fromkeys(options["prompts"]))
        adapters = {version: build_trial_adapter(version) for version in versions} if cases else {}
        self.stdout.write(f"Llamadas al proveedor: {len(cases) * len(versions)}")
        results = []
        # Abrir antes de llamar permite detectar una ruta de salida inválida sin gastar cuota.
        output = None
        if options["out"]:
            try:
                destination = Path(options["out"]).resolve()
                inputs = [Path(options["cases"]).resolve()]
                inputs.extend(
                    (Path(options["photos"]) / row["foto"].strip()).resolve()
                    for row, _, _ in cases
                    if row["foto"].strip()
                )
                if any(
                    destination == source or (destination.exists() and destination.samefile(source))
                    for source in inputs
                ):
                    raise CommandError(
                        "La salida no puede sobrescribir el CSV ni las fotos de entrada."
                    )
                output = destination.open("w", encoding="utf-8", newline="")
            except OSError as exc:
                raise CommandError("No se pudo abrir el CSV de resultados.") from exc
        try:
            for version in versions:
                counts = dict(
                    category=0, institution=0, priority=0, all=0, errors=0, auto=0, wrong=0
                )
                tokens = 0
                missing_tokens = 0
                for row, request, expected in cases:
                    result = run_trial(adapters[version], request)
                    hits = tuple(
                        not result.error and actual == wanted
                        for actual, wanted in zip(
                            (result.category_slug, result.institution_slug, result.priority),
                            expected,
                            strict=True,
                        )
                    )
                    for field, hit in zip(
                        ("category", "institution", "priority"), hits, strict=True
                    ):
                        counts[field] += int(hit)
                    counts["all"] += int(all(hits))
                    counts["errors"] += int(result.error is not None)
                    counts["auto"] += int(result.auto_apply)
                    counts["wrong"] += int(result.auto_apply and not hits[2])
                    tokens += result.total_tokens or 0
                    missing_tokens += int(result.total_tokens is None)
                    results.append(
                        {
                            **row,
                            "prompt": version,
                            **asdict(result),
                            "categoria_esperada": expected[0],
                            "institucion_esperada": expected[1],
                            "prioridad_esperada": expected[2],
                            "acierto_categoria": hits[0],
                            "acierto_institucion": hits[1],
                            "acierto_prioridad": hits[2],
                            "acierto_total": all(hits),
                        }
                    )
                total = len(cases)
                self.stdout.write(f"Prompt {version}: casos={total}")
                for field, label in (
                    ("category", "categoría"),
                    ("institution", "institución"),
                    ("priority", "prioridad"),
                    ("all", "las tres juntas"),
                ):
                    percent = counts[field] / total * 100 if total else 0
                    self.stdout.write(
                        f"Aciertos de {label}: {counts[field]}/{total} ({percent:.2f}%)"
                    )
                self.stdout.write(f"Errores: {counts['errors']}")
                self.stdout.write(f"Se aplicarían solas: {counts['auto']}")
                self.stdout.write(
                    f"Se aplicarían solas con prioridad equivocada: {counts['wrong']}"
                )
                average = tokens / total if total else 0
                self.stdout.write(f"Tokens totales: {tokens}; promedio por caso: {average:.2f}")
                if missing_tokens:
                    self.stdout.write(f"Casos sin total de tokens informado: {missing_tokens}")
            if output is not None:
                fields = [
                    *COLUMNS,
                    "prompt",
                    "category_slug",
                    "institution_slug",
                    "priority",
                    "confidence",
                    "inconsistency",
                    "justification",
                    "auto_apply",
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                    "error",
                    "acierto_categoria",
                    "acierto_institucion",
                    "acierto_prioridad",
                    "acierto_total",
                ]
                writer = csv.DictWriter(output, fieldnames=fields)
                writer.writeheader()
                writer.writerows(results)
        finally:
            if output is not None:
                output.close()
