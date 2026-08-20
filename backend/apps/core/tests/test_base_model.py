import uuid

import pytest

from apps.admin_app.models import Institution
from apps.core.models import BaseModel

pytestmark = pytest.mark.django_db


def test_base_model_es_abstracto_y_no_tiene_tabla():
    from django.db import connection

    assert BaseModel._meta.abstract is True
    assert BaseModel._meta.db_table not in connection.introspection.table_names()


def test_pk_es_uuid_generada_en_cliente():
    institution = Institution(name="Nueva", slug="nueva")

    assert isinstance(institution.id, uuid.UUID)
    assert institution._meta.get_field("id").editable is False


def test_timestamps_se_pueblan_solos():
    institution = Institution.objects.create(name="Nueva", slug="nueva")

    assert institution.created_at is not None
    assert institution.updated_at is not None

    creado_en = institution.created_at
    institution.name = "Nueva renombrada"
    institution.save()
    institution.refresh_from_db()

    assert institution.created_at == creado_en
    assert institution.updated_at >= creado_en


def test_todas_las_entidades_del_sprint_2_heredan_de_base_model():
    from apps.admin_app.models import AuditLog, Category, InstitutionOperator
    from apps.ai_client_app.models import AiClassification
    from apps.auth_app.models import CitizenProfile
    from apps.notifications_app.models import Notification
    from apps.reports_app.models import (
        Report,
        ReportConfirmation,
        ReportMedia,
        ReportStatusHistory,
        ResolutionEvidence,
    )
    from apps.rewards_app.models import Reward, RewardRedemption, RewardTransaction
    from apps.social_ingestion_app.models import TelegramIngestedMessage, XMockMessage

    entidades = [
        CitizenProfile,
        Institution,
        Category,
        InstitutionOperator,
        AuditLog,
        Report,
        ReportMedia,
        ReportStatusHistory,
        ResolutionEvidence,
        ReportConfirmation,
        AiClassification,
        RewardTransaction,
        Reward,
        RewardRedemption,
        Notification,
        TelegramIngestedMessage,
        XMockMessage,
    ]

    # 17 tablas nuevas; `users` conserva su propia estructura del Sprint 1.
    assert len(entidades) == 17
    for modelo in entidades:
        assert issubclass(modelo, BaseModel), modelo.__name__
        assert modelo._meta.get_field("id").primary_key
