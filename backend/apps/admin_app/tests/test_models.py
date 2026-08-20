import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.admin_app.models import AuditLog, Category, Institution, InstitutionOperator
from apps.core.enums import ActorType, ReportPriority

pytestmark = pytest.mark.django_db


def test_institucion_valida():
    institution = Institution.objects.create(name="Nueva entidad", slug="nueva-entidad")

    assert institution.is_active is True
    assert institution.sla_hours is None
    assert institution.contact_email == ""
    assert str(institution) == "Nueva entidad"


def test_nombre_y_slug_de_institucion_son_unicos(institution):
    with pytest.raises(IntegrityError), transaction.atomic():
        Institution.objects.create(name=institution.name, slug="otro-slug")

    with pytest.raises(IntegrityError), transaction.atomic():
        Institution.objects.create(name="Otro nombre", slug=institution.slug)


def test_sla_hours_debe_ser_positivo():
    with pytest.raises(IntegrityError), transaction.atomic():
        Institution.objects.create(name="Con SLA cero", slug="sla-cero", sla_hours=0)


def test_sla_hours_admite_null_y_valores_positivos():
    Institution.objects.create(name="Con SLA", slug="con-sla", sla_hours=72)
    Institution.objects.create(name="Sin SLA", slug="sin-sla")

    assert Institution.objects.filter(sla_hours__isnull=True).exists()


def test_categoria_valida(category, institution):
    assert category.default_institution == institution
    assert category.default_priority == ReportPriority.MEDIA
    assert category.is_active is True
    assert str(category) == "Baches"


def test_nombre_y_slug_de_categoria_son_unicos(category, institution):
    with pytest.raises(IntegrityError), transaction.atomic():
        Category.objects.create(
            name=category.name, slug="otro-slug", default_institution=institution
        )

    with pytest.raises(IntegrityError), transaction.atomic():
        Category.objects.create(
            name="Otro nombre", slug=category.slug, default_institution=institution
        )


def test_no_se_puede_borrar_una_institucion_con_categorias(institution):
    with pytest.raises(ProtectedError):
        institution.delete()


def test_un_operador_no_se_vincula_dos_veces_a_la_misma_institucion(citizen, institution):
    InstitutionOperator.objects.create(user=citizen, institution=institution)

    with pytest.raises(IntegrityError), transaction.atomic():
        InstitutionOperator.objects.create(user=citizen, institution=institution)


def test_el_operador_puede_vincularse_a_dos_instituciones(citizen, institution):
    otra = Institution.objects.get(slug="ande")
    InstitutionOperator.objects.create(user=citizen, institution=institution)
    InstitutionOperator.objects.create(user=citizen, institution=otra)

    assert InstitutionOperator.objects.filter(user=citizen).count() == 2


def test_borrar_el_usuario_arrastra_su_vinculo_de_operador(citizen, institution):
    vinculo = InstitutionOperator.objects.create(user=citizen, institution=institution)
    assert vinculo.active is True

    citizen.delete()

    assert InstitutionOperator.objects.count() == 0


def test_no_se_puede_borrar_una_institucion_con_operadores(citizen):
    essap = Institution.objects.get(slug="essap")
    Category.objects.filter(default_institution=essap).delete()
    InstitutionOperator.objects.create(user=citizen, institution=essap)

    with pytest.raises(ProtectedError):
        essap.delete()


def test_la_auditoria_sobrevive_al_borrado_de_su_actor(citizen):
    entrada = AuditLog.objects.create(
        actor_user=citizen, actor_type=ActorType.HUMAN, action="catalog.updated"
    )
    citizen.delete()
    entrada.refresh_from_db()

    assert entrada.actor_user is None
    assert str(entrada) == "human:catalog.updated"
