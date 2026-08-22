"""Preferencia de visibilidad en el ranking (RF-07, ADR-0011).

`PATCH /api/v1/profile/ranking-visibility/` y el servicio de dominio que lo respalda.

El Sprint 4 solo guarda la **preferencia**: la resolución del nombre visible llega con
el ranking del Sprint 11. Aquí se comprueba que las cuatro modalidades se guardan y se
recuperan, que el alias es obligatorio solo en la modalidad `ALIAS`, que **no** es único
a nivel global y que abandonar `ALIAS` no lo borra.
"""

import pytest
from rest_framework_simplejwt.tokens import RefreshToken

from apps.auth_app.enums import RankingVisibility, VerificationStatus
from apps.auth_app.models import CitizenProfile
from apps.auth_app.services import identity
from apps.auth_app.services import profile as profile_service

pytestmark = pytest.mark.django_db

URL = "/api/v1/profile/ranking-visibility/"


@pytest.fixture
def api():
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def como(api):
    def autenticar(user):
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
        return api

    return autenticar


@pytest.fixture
def perfil_factory(document_hmac_secret):
    """Perfil ya enviado, que es la precondición para tener preferencia de ranking."""

    def make(user, cedula="1234567", **extra):
        return CitizenProfile.objects.create(
            user=user,
            document_hmac=identity.compute_document_hmac(cedula),
            verification_selfie_url=f"verifications/{user.pk}/selfie.jpg",
            **extra,
        )

    return make


# --------------------------------------------------------------------------- #
# Las cuatro modalidades
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("modalidad", ["FULL_NAME", "FIRST_INITIAL", "PRIVATE"])
def test_las_modalidades_sin_alias_se_guardan_y_se_recuperan(
    como, citizen, perfil_factory, modalidad
):
    perfil = perfil_factory(citizen)

    respuesta = como(citizen).patch(URL, {"ranking_visibility": modalidad}, format="json")

    assert respuesta.status_code == 200
    assert respuesta.json() == {"ranking_visibility": modalidad, "ranking_alias": ""}
    perfil.refresh_from_db()
    assert perfil.ranking_visibility == modalidad


def test_la_modalidad_alias_se_guarda_con_su_alias(como, citizen, perfil_factory):
    perfil = perfil_factory(citizen)

    respuesta = como(citizen).patch(
        URL, {"ranking_visibility": "ALIAS", "ranking_alias": "Vecina del barrio"}, format="json"
    )

    assert respuesta.status_code == 200
    assert respuesta.json() == {
        "ranking_visibility": "ALIAS",
        "ranking_alias": "Vecina del barrio",
    }
    perfil.refresh_from_db()
    assert perfil.ranking_visibility == RankingVisibility.ALIAS
    assert perfil.ranking_alias == "Vecina del barrio"


def test_el_valor_por_defecto_es_nombre_completo(citizen, perfil_factory):
    assert perfil_factory(citizen).ranking_visibility == RankingVisibility.FULL_NAME


# --------------------------------------------------------------------------- #
# Reglas del alias
# --------------------------------------------------------------------------- #


def test_alias_obligatorio_en_la_modalidad_alias(como, citizen, perfil_factory):
    perfil_factory(citizen)

    respuesta = como(citizen).patch(URL, {"ranking_visibility": "ALIAS"}, format="json")

    assert respuesta.status_code == 400


@pytest.mark.parametrize("alias", ["", "   ", "\t"])
def test_un_alias_vacio_o_en_blanco_no_sirve_para_la_modalidad_alias(
    como, citizen, perfil_factory, alias
):
    perfil_factory(citizen)

    respuesta = como(citizen).patch(
        URL, {"ranking_visibility": "ALIAS", "ranking_alias": alias}, format="json"
    )

    assert respuesta.status_code == 400


def test_el_alias_se_recorta(como, citizen, perfil_factory):
    perfil = perfil_factory(citizen)

    como(citizen).patch(
        URL, {"ranking_visibility": "ALIAS", "ranking_alias": "  Vecina  "}, format="json"
    )

    perfil.refresh_from_db()
    assert perfil.ranking_alias == "Vecina"


def test_un_alias_de_60_caracteres_se_acepta(como, citizen, perfil_factory):
    perfil_factory(citizen)
    alias = "a" * 60

    respuesta = como(citizen).patch(
        URL, {"ranking_visibility": "ALIAS", "ranking_alias": alias}, format="json"
    )

    assert respuesta.status_code == 200
    assert respuesta.json()["ranking_alias"] == alias


def test_un_alias_de_mas_de_60_caracteres_se_rechaza(como, citizen, perfil_factory):
    perfil_factory(citizen)

    respuesta = como(citizen).patch(
        URL, {"ranking_visibility": "ALIAS", "ranking_alias": "a" * 61}, format="json"
    )

    assert respuesta.status_code == 400


def test_dos_ciudadanos_pueden_elegir_el_mismo_alias(como, citizen, other_citizen, perfil_factory):
    """El alias **no** es único a nivel global (ADR-0011)."""
    perfil_factory(citizen, cedula="1111111")
    perfil_factory(other_citizen, cedula="2222222")
    cuerpo = {"ranking_visibility": "ALIAS", "ranking_alias": "Vecino"}

    assert como(citizen).patch(URL, cuerpo, format="json").status_code == 200
    assert como(other_citizen).patch(URL, cuerpo, format="json").status_code == 200

    assert CitizenProfile.objects.filter(ranking_alias="Vecino").count() == 2


def test_al_abandonar_la_modalidad_alias_el_alias_se_conserva(como, citizen, perfil_factory):
    """Quien vuelva a elegir `ALIAS` no tiene que reescribirlo."""
    perfil = perfil_factory(citizen)
    cliente = como(citizen)
    cliente.patch(URL, {"ranking_visibility": "ALIAS", "ranking_alias": "Vecina"}, format="json")

    respuesta = cliente.patch(URL, {"ranking_visibility": "PRIVATE"}, format="json")

    assert respuesta.status_code == 200
    assert respuesta.json()["ranking_alias"] == "Vecina"
    perfil.refresh_from_db()
    assert perfil.ranking_visibility == RankingVisibility.PRIVATE
    assert perfil.ranking_alias == "Vecina"


def test_se_puede_volver_a_alias_sin_reescribirlo_en_la_base(como, citizen, perfil_factory):
    perfil = perfil_factory(citizen)
    cliente = como(citizen)
    cliente.patch(URL, {"ranking_visibility": "ALIAS", "ranking_alias": "Vecina"}, format="json")
    cliente.patch(URL, {"ranking_visibility": "PRIVATE"}, format="json")

    # El alias sigue en la fila, aunque la petición deba volver a declararlo.
    perfil.refresh_from_db()
    assert perfil.ranking_alias == "Vecina"

    cliente.patch(URL, {"ranking_visibility": "ALIAS", "ranking_alias": "Vecina"}, format="json")
    perfil.refresh_from_db()
    assert perfil.ranking_visibility == RankingVisibility.ALIAS


def test_el_alias_puede_actualizarse_fuera_de_la_modalidad_alias(como, citizen, perfil_factory):
    """Semántica de PATCH: si el campo llega, se aplica; si no llega, no se toca."""
    perfil = perfil_factory(citizen)

    como(citizen).patch(
        URL, {"ranking_visibility": "PRIVATE", "ranking_alias": "Reservado"}, format="json"
    )

    perfil.refresh_from_db()
    assert perfil.ranking_alias == "Reservado"


# --------------------------------------------------------------------------- #
# Entradas inválidas y ausencia de perfil
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "payload", [{}, {"ranking_visibility": "OTRA_COSA"}, {"ranking_alias": "x"}]
)
def test_un_payload_invalido_responde_400(como, citizen, perfil_factory, payload):
    perfil_factory(citizen)

    assert como(citizen).patch(URL, payload, format="json").status_code == 400


def test_sin_perfil_responde_404(como, citizen):
    respuesta = como(citizen).patch(URL, {"ranking_visibility": "PRIVATE"}, format="json")

    assert respuesta.status_code == 404
    assert not CitizenProfile.objects.filter(user=citizen).exists()


def test_el_404_se_evalua_despues_de_validar_el_payload(como, citizen):
    """Un payload inválido sin perfil sigue siendo 400: el orden es predecible."""
    respuesta = como(citizen).patch(URL, {"ranking_visibility": "NADA"}, format="json")

    assert respuesta.status_code == 400


# --------------------------------------------------------------------------- #
# Permisos
# --------------------------------------------------------------------------- #


def test_anonimo_responde_401(api):
    assert api.patch(URL, {"ranking_visibility": "PRIVATE"}, format="json").status_code == 401


@pytest.mark.parametrize("rol", ["operator", "admin"])
def test_ni_operador_ni_admin_configuran_visibilidad(como, user_factory, rol):
    user = user_factory(email=f"{rol}-ranking@example.com", role=rol)

    respuesta = como(user).patch(URL, {"ranking_visibility": "PRIVATE"}, format="json")

    assert respuesta.status_code == 403


def test_un_ciudadano_verificado_tambien_puede_configurarla(como, user_factory, perfil_factory):
    verificado = user_factory(
        email="verificado-ranking@example.com", verification_status=VerificationStatus.APROBADO
    )
    perfil_factory(verificado)

    respuesta = como(verificado).patch(URL, {"ranking_visibility": "PRIVATE"}, format="json")

    assert respuesta.status_code == 200


# --------------------------------------------------------------------------- #
# Efectos que NO debe tener
# --------------------------------------------------------------------------- #


def test_no_toca_el_estado_de_verificacion(como, citizen, perfil_factory):
    perfil = perfil_factory(citizen, verified_at=None)

    como(citizen).patch(URL, {"ranking_visibility": "PRIVATE"}, format="json")

    citizen.refresh_from_db()
    perfil.refresh_from_db()
    assert citizen.verification_status == VerificationStatus.PENDIENTE
    assert perfil.verified_at is None
    assert perfil.rejection_reason == ""


def test_no_expone_datos_sensibles(como, citizen, perfil_factory):
    perfil = perfil_factory(citizen)

    respuesta = como(citizen).patch(URL, {"ranking_visibility": "PRIVATE"}, format="json")
    cuerpo = respuesta.content.decode()

    assert perfil.document_hmac not in cuerpo
    assert perfil.verification_selfie_url not in cuerpo
    assert "verifications/" not in cuerpo
    assert set(respuesta.json()) == {"ranking_visibility", "ranking_alias"}


def test_no_se_audita(como, citizen, perfil_factory):
    """§30 del plan no incluye la preferencia de ranking entre las acciones auditadas."""
    from apps.admin_app.models import AuditLog

    perfil_factory(citizen)

    como(citizen).patch(URL, {"ranking_visibility": "PRIVATE"}, format="json")

    assert not AuditLog.objects.exists()


# --------------------------------------------------------------------------- #
# Servicio, invocado directamente
# --------------------------------------------------------------------------- #


def test_el_servicio_exige_perfil(citizen):
    with pytest.raises(profile_service.ProfileNotFoundError):
        profile_service.update_ranking_visibility(citizen, RankingVisibility.PRIVATE)


def test_el_servicio_exige_rol_ciudadano(operator_user):
    with pytest.raises(profile_service.ProfileNotFoundError):
        profile_service.update_ranking_visibility(operator_user, RankingVisibility.PRIVATE)


def test_el_servicio_rechaza_una_modalidad_inexistente(citizen, perfil_factory):
    perfil_factory(citizen)

    with pytest.raises(profile_service.InvalidRankingPreferenceError):
        profile_service.update_ranking_visibility(citizen, "NO_EXISTE")


def test_el_servicio_reexige_el_alias_aunque_la_vista_ya_lo_valide(citizen, perfil_factory):
    """Precondición de dominio: no se delega en el serializador."""
    perfil_factory(citizen)

    with pytest.raises(profile_service.InvalidRankingPreferenceError):
        profile_service.update_ranking_visibility(citizen, RankingVisibility.ALIAS)


def test_el_servicio_rechaza_un_alias_demasiado_largo(citizen, perfil_factory):
    perfil_factory(citizen)

    with pytest.raises(profile_service.InvalidRankingPreferenceError):
        profile_service.update_ranking_visibility(
            citizen, RankingVisibility.ALIAS, ranking_alias="a" * 61
        )


def test_el_servicio_conserva_el_alias_cuando_no_se_indica(citizen, perfil_factory):
    perfil_factory(citizen, ranking_alias="Vecina", ranking_visibility=RankingVisibility.ALIAS)

    perfil = profile_service.update_ranking_visibility(citizen, RankingVisibility.PRIVATE)

    assert perfil.ranking_alias == "Vecina"


def test_la_vista_traduce_una_preferencia_invalida_del_servicio_a_400(
    como, citizen, perfil_factory, monkeypatch
):
    """El serializador ya filtra estos casos; la traducción debe existir igualmente."""

    def rechazar(**kwargs):
        raise profile_service.InvalidRankingPreferenceError("Preferencia incoherente.")

    perfil_factory(citizen)
    monkeypatch.setattr(profile_service, "update_ranking_visibility", rechazar)

    respuesta = como(citizen).patch(URL, {"ranking_visibility": "PRIVATE"}, format="json")

    assert respuesta.status_code == 400


# --------------------------------------------------------------------------- #
# GET: recuperar la preferencia persistida
# --------------------------------------------------------------------------- #
#
# El criterio de aceptación del Sprint 4 pide que las cuatro modalidades «se guarden y
# se recuperen correctamente». Guardar sin poder leer de vuelta no lo cumple: al
# recargar, la persona no sabría qué eligió.

CAMPOS_PREFERENCIA = {"ranking_visibility", "ranking_alias"}


@pytest.mark.parametrize(
    ("modalidad", "alias"),
    [
        ("FULL_NAME", ""),
        ("FIRST_INITIAL", ""),
        ("ALIAS", "Vecina del barrio"),
        ("PRIVATE", ""),
    ],
)
def test_las_cuatro_modalidades_se_recuperan(como, citizen, perfil_factory, modalidad, alias):
    perfil_factory(citizen, ranking_visibility=modalidad, ranking_alias=alias)

    respuesta = como(citizen).get(URL)

    assert respuesta.status_code == 200
    assert respuesta.json() == {"ranking_visibility": modalidad, "ranking_alias": alias}


def test_lo_guardado_es_exactamente_lo_que_se_recupera(como, citizen, perfil_factory):
    """Ida y vuelta completa: PATCH y después GET, sin tocar la base en medio."""
    perfil_factory(citizen)
    cliente = como(citizen)
    guardado = cliente.patch(
        URL, {"ranking_visibility": "ALIAS", "ranking_alias": "  Vecina  "}, format="json"
    ).json()

    assert cliente.get(URL).json() == guardado
    assert guardado == {"ranking_visibility": "ALIAS", "ranking_alias": "Vecina"}


def test_el_alias_conservado_al_salir_de_alias_sigue_visible_en_la_configuracion(
    como, citizen, perfil_factory
):
    perfil_factory(citizen)
    cliente = como(citizen)
    cliente.patch(URL, {"ranking_visibility": "ALIAS", "ranking_alias": "Vecina"}, format="json")
    cliente.patch(URL, {"ranking_visibility": "PRIVATE"}, format="json")

    cuerpo = cliente.get(URL).json()

    assert cuerpo == {"ranking_visibility": "PRIVATE", "ranking_alias": "Vecina"}


def test_el_get_devuelve_el_conjunto_exacto_de_campos(como, citizen, perfil_factory):
    perfil_factory(citizen)

    assert set(como(citizen).get(URL).json()) == CAMPOS_PREFERENCIA


def test_el_get_no_expone_datos_sensibles(como, citizen, perfil_factory):
    perfil = perfil_factory(
        citizen,
        ranking_visibility="ALIAS",
        ranking_alias="Vecina",
        rejection_reason="Motivo previo.",
    )

    cuerpo = como(citizen).get(URL).content.decode()

    assert perfil.document_hmac not in cuerpo
    assert perfil.verification_selfie_url not in cuerpo
    assert "verifications/" not in cuerpo
    assert "Motivo previo." not in cuerpo
    assert "rejection_reason" not in cuerpo
    assert citizen.email not in cuerpo


def test_el_get_sin_perfil_responde_404(como, citizen):
    respuesta = como(citizen).get(URL)

    assert respuesta.status_code == 404
    assert not CitizenProfile.objects.filter(user=citizen).exists()


def test_el_get_anonimo_responde_401(api):
    assert api.get(URL).status_code == 401


@pytest.mark.parametrize("rol", ["operator", "admin"])
def test_ni_operador_ni_admin_consultan_la_preferencia(como, user_factory, rol):
    user = user_factory(email=f"{rol}-get-ranking@example.com", role=rol)

    assert como(user).get(URL).status_code == 403


def test_el_get_no_escribe_nada(como, citizen, perfil_factory):
    """Una lectura no puede mover `updated_at` ni ninguna otra columna."""
    perfil = perfil_factory(citizen, ranking_visibility="ALIAS", ranking_alias="Vecina")
    antes = CitizenProfile.objects.filter(pk=perfil.pk).values().first()

    como(citizen).get(URL)

    assert CitizenProfile.objects.filter(pk=perfil.pk).values().first() == antes


def test_el_get_no_toca_el_estado_de_verificacion(como, citizen, perfil_factory):
    perfil_factory(citizen)

    como(citizen).get(URL)

    citizen.refresh_from_db()
    assert citizen.verification_status == VerificationStatus.PENDIENTE


def test_el_get_no_se_audita(como, citizen, perfil_factory):
    from apps.admin_app.models import AuditLog

    perfil_factory(citizen)

    como(citizen).get(URL)

    assert not AuditLog.objects.exists()


def test_el_get_no_tiene_throttle_propio(como, citizen, perfil_factory):
    """El único endpoint con límite del proyecto es el envío de verificación."""
    perfil_factory(citizen)
    cliente = como(citizen)

    for _ in range(8):
        assert cliente.get(URL).status_code == 200


def test_el_servicio_de_lectura_exige_perfil(citizen):
    with pytest.raises(profile_service.ProfileNotFoundError):
        profile_service.get_ranking_preferences(citizen)


def test_el_servicio_de_lectura_exige_rol_ciudadano(operator_user):
    with pytest.raises(profile_service.ProfileNotFoundError):
        profile_service.get_ranking_preferences(operator_user)


def test_el_servicio_de_lectura_devuelve_el_perfil_propio(citizen, other_citizen, perfil_factory):
    """No hay parámetro por el que pedir el perfil de otra persona."""
    mio = perfil_factory(citizen, cedula="1111111")
    perfil_factory(other_citizen, cedula="2222222")

    assert profile_service.get_ranking_preferences(citizen).pk == mio.pk
