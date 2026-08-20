import pytest

from apps.admin_app.models import AuditLog
from apps.core.enums import ActorType
from apps.core.services import audit

pytestmark = pytest.mark.django_db


def test_log_persiste_la_accion_con_actor_humano(citizen):
    entrada = audit.log(citizen, "report.created", {"report_id": "abc"})

    assert AuditLog.objects.count() == 1
    assert entrada.actor_user == citizen
    assert entrada.actor_type == ActorType.HUMAN
    assert entrada.action == "report.created"
    assert entrada.payload == {"report_id": "abc"}


def test_sin_actor_la_accion_se_atribuye_al_sistema():
    entrada = audit.log(None, "report.auto_closed")

    assert entrada.actor_user is None
    assert entrada.actor_type == ActorType.SYSTEM
    assert entrada.payload == {}


def test_actor_type_explicito_para_decisiones_de_ia():
    entrada = audit.log(None, "ai.classified", {"confianza": 0.9}, actor_type=ActorType.AI)

    assert entrada.actor_type == ActorType.AI
