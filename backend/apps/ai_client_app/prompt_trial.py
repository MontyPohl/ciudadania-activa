"""Pruebas manuales de prompts sin persistencia ni auditoría."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import CommandError

from apps.admin_app.models import Category, Institution
from apps.core.enums import ReportPriority
from apps.reports_app.services.photos import MAX_REPORT_PHOTO_BYTES, prepare_report_photo

from .adapters.openai import OpenAiClassificationAdapter, build_client
from .contracts import ClassificationRequest
from .parser import ClassificationParseError, parse_classification_response
from .policy import resolve_classification
from .prompts import load_prompt_template, prompt_version_id


def build_trial_adapter(version):
    """Construye OpenAI sin depender del interruptor de reportes reales."""
    prompt_version_id(version)
    try:
        load_prompt_template(version)
    except OSError as exc:
        raise CommandError("No se pudo leer la plantilla del prompt seleccionado.") from exc
    if not settings.OPENAI_API_KEY or not settings.OPENAI_MODEL:
        raise CommandError("Configurá OPENAI_API_KEY y OPENAI_MODEL para probar prompts.")
    return OpenAiClassificationAdapter(
        client=build_client(
            api_key=settings.OPENAI_API_KEY,
            timeout_seconds=settings.AI_REQUEST_TIMEOUT_SECONDS,
        ),
        model=settings.OPENAI_MODEL,
        reasoning_effort=settings.OPENAI_REASONING_EFFORT,
        prompt_version=version,
    )


def load_photo(path):
    """Devuelve los mismos bytes normalizados que recibe producción."""
    path = Path(path)
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}.get(
        path.suffix.lower(), ""
    )
    with path.open("rb") as source:
        content = source.read(MAX_REPORT_PHOTO_BYTES + 1)
    prepared = prepare_report_photo(SimpleUploadedFile(path.name, content, content_type=mime))
    return prepared.content, prepared.content_type


def _normalize(label):
    return "".join(
        char
        for char in unicodedata.normalize("NFD", label.strip().casefold())
        if not unicodedata.combining(char)
    )


def normalize_catalog_label(label, model):
    """Acepta slug o nombre visible de una entrada activa, sin tildes ni caja."""
    entries = list(model.objects.filter(is_active=True).order_by("slug"))
    normalized = _normalize(label)
    for entry in entries:
        if _normalize(entry.slug) == normalized:
            return entry.slug
    matches = [entry.slug for entry in entries if _normalize(entry.name) == normalized]
    if len(matches) != 1:
        raise ValueError(f"Etiqueta de {model._meta.verbose_name} desconocida o ambigua.")
    return matches[0]


def normalize_priority(label):
    priority = label.strip().upper()
    if priority not in ReportPriority.values:
        raise ValueError("Prioridad desconocida; usá Baja, Media o Alta.")
    return priority


def build_trial_request(description, chosen_category_slug, image=None):
    """Construye la lista blanca con los catálogos activos ordenados por slug."""
    slug = normalize_catalog_label(chosen_category_slug, Category)
    category = Category.objects.select_related("default_institution").get(slug=slug)
    return ClassificationRequest(
        description=description,
        current_category_slug=category.slug,
        current_institution_slug=category.default_institution.slug,
        allowed_category_slugs=tuple(
            Category.objects.filter(is_active=True).order_by("slug").values_list("slug", flat=True)
        ),
        allowed_institution_slugs=tuple(
            Institution.objects.filter(is_active=True)
            .order_by("slug")
            .values_list("slug", flat=True)
        ),
        allowed_priorities=tuple(ReportPriority.values),
        image_bytes=image[0] if image is not None else b"",
        image_mime_type=image[1] if image is not None else None,
    )


@dataclass(frozen=True, slots=True)
class TrialResult:
    """Resultado resuelto, o tipo de fallo sin contenido del proveedor."""

    category_slug: str | None = None
    institution_slug: str | None = None
    priority: str | None = None
    confidence: Decimal | None = None
    inconsistency: bool | None = None
    justification: str = ""
    auto_apply: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    error: str | None = None


def run_trial(adapter, request):
    """Ejecuta una sola llamada y aplica parser y política de producción."""
    try:
        call = adapter.classify_with_metadata(request)
    except Exception as exc:
        return TrialResult(error=type(exc).__name__)
    usage = call.usage
    tokens = {
        field: getattr(usage, field, None)
        for field in ("input_tokens", "output_tokens", "total_tokens")
    }
    try:
        parsed = parse_classification_response(call.text)
    except ClassificationParseError as exc:
        return TrialResult(error=type(exc).__name__, **tokens)
    resolved = resolve_classification(parsed)
    return TrialResult(
        category_slug=resolved.category.slug if resolved.category else None,
        institution_slug=resolved.institution.slug if resolved.institution else None,
        priority=parsed.priority,
        confidence=parsed.confidence,
        inconsistency=resolved.effective_inconsistency,
        justification=parsed.justification,
        auto_apply=resolved.eligible_for_auto_apply,
        **tokens,
    )
