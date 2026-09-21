from __future__ import annotations

import json

from apps.admin_app.catalogs import CATEGORIES
from apps.ai_client_app.contracts import ClassificationRequest

_CATEGORY_DEFAULTS = {
    slug: (institution_slug, priority) for slug, _name, institution_slug, priority in CATEGORIES
}


class S11SyntheticAiAdapter:
    """Fake offline que conserva la institución/prioridad del dataset funcional."""

    def classify(self, request: ClassificationRequest) -> str:
        institution, priority = _CATEGORY_DEFAULTS[request.current_category_slug]
        if institution not in request.allowed_institution_slugs:
            institution = min(request.allowed_institution_slugs)
        if priority not in request.allowed_priorities:
            priority = min(request.allowed_priorities)
        payload = {
            "category_slug": request.current_category_slug,
            "institution_slug": institution,
            "priority": priority,
            "confidence": 0.95,
            "inconsistency": False,
            "justification": "Clasificación sintética determinista S11; sin proveedor externo.",
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
