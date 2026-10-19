from apps.auth_app.enums import RankingVisibility
from apps.auth_app.models import CitizenProfile


def citizen_short_name(user) -> str:
    """Muestra solo el primer nombre y la inicial del último al operador."""
    words = user.full_name.split()
    if not words:
        return "Ciudadano"
    if len(words) == 1:
        return words[0]
    return f"{words[0]} {words[-1][0]}."


def ranking_display_name(user) -> tuple[str, bool]:
    """Resuelve el nombre público respetando la preferencia del ciudadano."""
    private = ("Usuario privado", True)
    try:
        profile = getattr(user, "citizen_profile", None)
    except CitizenProfile.DoesNotExist:
        profile = None
    if profile is None:
        return private

    visibility = profile.ranking_visibility
    if visibility == RankingVisibility.ALIAS:
        alias = profile.ranking_alias.strip()
        return (alias, False) if alias else private
    if visibility in (RankingVisibility.FULL_NAME, RankingVisibility.FIRST_INITIAL):
        name = user.full_name.strip()
        if not name:
            return private
        if visibility == RankingVisibility.FULL_NAME:
            return name, False
        return citizen_short_name(user), False
    return private
