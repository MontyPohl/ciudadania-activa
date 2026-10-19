"""Compatibilidad de v1 y selección explícita de prompts."""

import json
from pathlib import Path

import pytest

from apps.ai_client_app.prompts import (
    PROMPT_VERSION,
    load_prompt_template,
    prompt_version_id,
    render_classification_prompt,
)

from .test_openai_adapter import build_request


@pytest.mark.parametrize("function", [load_prompt_template, prompt_version_id])
def test_version_desconocida(function):
    with pytest.raises(ValueError):
        function("v3")


def test_render_desconocido():
    with pytest.raises(ValueError):
        render_classification_prompt(build_request(), "v3")


def test_sin_version_conserva_el_texto_de_v1():
    request = build_request()
    path = Path(__file__).resolve().parents[1] / "prompts" / "classification_v1.txt"
    template = path.read_text(encoding="utf-8")
    replacements = {
        "allowed_category_slugs": json.dumps(list(request.allowed_category_slugs)),
        "allowed_institution_slugs": json.dumps(list(request.allowed_institution_slugs)),
        "allowed_priorities": json.dumps(list(request.allowed_priorities)),
        "current_category_slug": request.current_category_slug,
        "current_institution_slug": request.current_institution_slug,
        "image": "no disponible",
        "description": "(se envía por separado como contenido no confiable del usuario)",
    }
    for key, value in replacements.items():
        template = template.replace("{" + key + "}", value)
    assert load_prompt_template() == path.read_text(encoding="utf-8")
    assert render_classification_prompt(request) == template
    assert prompt_version_id("v1") == PROMPT_VERSION == "s8-classification-v1"
    assert prompt_version_id("v2") == "classification-v2"
