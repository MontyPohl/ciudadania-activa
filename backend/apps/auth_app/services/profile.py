"""Preferencias del perfil ciudadano: visibilidad en el ranking (RF-07, ADR-0011).

El Sprint 4 registra la **preferencia** y permite recuperarla: guardarla sin poder
leerla de vuelta no cumpliría el criterio de aceptación del sprint, que pide que las
cuatro modalidades «se guarden y se recuperen correctamente».

La resolución del nombre visible —nombre completo, nombre e inicial, alias o el literal
«Usuario privado»— ocurre en el serializador del ranking del Sprint 11, en el backend y
nunca en el cliente.

El perfil vive en `citizen_profiles`, que nace al enviar la verificación de identidad.
Un ciudadano que todavía no envió la suya no tiene dónde guardar la preferencia y
recibe un 404 controlado. Es coherente —sin verificación no hay puntos y sin puntos no
hay ranking— y no justifica hacer `document_hmac` nullable ni crear perfiles vacíos.
"""

from __future__ import annotations

from apps.auth_app.enums import RankingVisibility, UserRole
from apps.auth_app.models import CitizenProfile

#: Longitud máxima del alias, igual que la columna `ranking_alias varchar(60)`.
ALIAS_MAX_LENGTH = 60


class ProfileNotFoundError(Exception):
    """El ciudadano todavía no tiene perfil. Se traduce a 404."""


class InvalidRankingPreferenceError(Exception):
    """La preferencia recibida no es coherente. Se traduce a 400."""


def get_ranking_preferences(user) -> CitizenProfile:
    """Perfil ciudadano de `user`, o `ProfileNotFoundError` si no tiene.

    Concentra en un solo sitio la regla de «quién tiene perfil»: la lectura y la
    escritura de la preferencia comparten exactamente el mismo criterio, de modo que no
    puede darse el caso de que una responda 404 y la otra no.

    Es **solo lectura**: no crea el perfil, no toca `updated_at` y no escribe nada. El
    sujeto es siempre el usuario autenticado; no hay ningún parámetro por el que pedir
    el perfil de otra persona.
    """
    if getattr(user, "role", None) != UserRole.CITIZEN:
        raise ProfileNotFoundError("No hay un perfil ciudadano para esta cuenta.")

    perfil = CitizenProfile.objects.filter(user=user).first()
    if perfil is None:
        raise ProfileNotFoundError("No hay un perfil ciudadano para esta cuenta.")

    return perfil


def update_ranking_visibility(
    user, ranking_visibility: str, ranking_alias: str | None = None
) -> CitizenProfile:
    """Actualiza la preferencia de visibilidad y, si corresponde, el alias.

    `ranking_alias` es opcional y sigue la semántica de un PATCH: si no llega, el alias
    actual **no se toca**. Esa es justamente la regla que hace que abandonar el modo
    `ALIAS` no borre el alias: quien vuelva a elegirlo no tiene que reescribirlo.

    El alias **no es único a nivel global**. Dos personas pueden elegir el mismo, y la
    columna no declara `unique`. Imponer unicidad convertiría el ranking en un
    directorio de nombres reservados y filtraría qué alias ya están tomados.

    No toca `verification_status` ni ningún otro campo del usuario.
    """
    perfil = get_ranking_preferences(user)

    if ranking_visibility not in RankingVisibility.values:
        raise InvalidRankingPreferenceError("La modalidad de visibilidad no es válida.")

    alias = perfil.ranking_alias if ranking_alias is None else ranking_alias.strip()

    # Precondición del dominio, no solo del serializador: elegir `ALIAS` sin alias
    # dejaría al ciudadano sin nombre visible en el ranking del Sprint 11.
    if ranking_visibility == RankingVisibility.ALIAS and not alias:
        raise InvalidRankingPreferenceError("El alias es obligatorio en la modalidad Alias.")

    if len(alias) > ALIAS_MAX_LENGTH:
        raise InvalidRankingPreferenceError(
            f"El alias no puede superar los {ALIAS_MAX_LENGTH} caracteres."
        )

    perfil.ranking_visibility = ranking_visibility
    perfil.ranking_alias = alias
    perfil.save(update_fields=["ranking_visibility", "ranking_alias", "updated_at"])

    return perfil
