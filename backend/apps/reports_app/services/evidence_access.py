"""Capacidades efímeras para descargar evidencia sin exponer object keys."""

from django.core import signing
from django.urls import reverse

EVIDENCE_ACCESS_TTL_SECONDS = 300
_SIGNING_SALT = "reports.resolution-evidence.access.v1"


def create_access_url(evidence, request=None):
    token = signing.dumps(
        {"evidence_id": str(evidence.pk)},
        salt=_SIGNING_SALT,
        compress=True,
    )
    path = reverse("resolution-evidence-download", kwargs={"token": token})
    return request.build_absolute_uri(path) if request is not None else path


def evidence_id_from_token(token):
    payload = signing.loads(
        token,
        salt=_SIGNING_SALT,
        max_age=EVIDENCE_ACCESS_TTL_SECONDS,
    )
    return payload["evidence_id"]
