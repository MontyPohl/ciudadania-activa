"""Perfil de navegador S15: reutiliza el guard local sin mutar os.environ."""

from tools.s15.local_profile import ProfileError, validate_environment


def validate_browser_environment(values):
    if (
        values.get("DJANGO_SETTINGS_MODULE") != "tools.s15.browser_settings"
        or values.get("S15_BROWSER_WORLD") != "ca-s15-browser-local"
    ):
        raise ProfileError("Se requiere el mundo exclusivo de navegador S15.")
    normalized = dict(values)
    normalized["DJANGO_SETTINGS_MODULE"] = "tools.s15.measurement_settings"
    receipt = validate_environment(normalized)
    return {**receipt, "profile": "isolated-browser-v1", "frontend": "http://127.0.0.1:3115"}
