"""Sanitización del mensaje de error que entra en la evidencia (S8-E2-R1).

La corrida canónica S8-E2 guardó únicamente el nombre de la clase de la excepción
y por eso su diagnóstico resultó irrecuperable: el adaptador ya distinguía «el
proveedor respondió 429» de «el proveedor no respondió a tiempo», pero esa
distinción se perdía al serializar. Los 22 fallos transitorios quedaron bajo una
sola etiqueta.

Guardar `str(exc)` sin más tampoco sirve: un mensaje del proveedor puede arrastrar
una URL con la clave en el `query string`, el eco de una cabecera de autorización
o una cadena de conexión. Los dos objetivos —conservar el dato diagnóstico y no
filtrar credenciales— están en tensión, así que la redacción es por patrones
explícitos y enumerados, nunca un borrado general: convertir todo en texto
genérico reproduciría exactamente el problema que este módulo existe para evitar.
"""

from __future__ import annotations

import re

REDACTED = "[REDACTADO]"

# Un mensaje de error diagnóstico cabe de sobra aquí. El límite existe para que un
# proveedor que devuelva un cuerpo entero no acabe volcado en la evidencia.
MAX_MESSAGE_LENGTH = 300

# Umbral de la regla 6. Por debajo de 24 caracteres empezaría a alcanzar a
# identificadores legítimos del propio proyecto (`s8-classification-v1` mide 20) y
# la evidencia perdería información diagnóstica sin ganar seguridad. Una clave de
# Google (`AIza…`, 39) y cualquier token OAuth quedan muy por encima.
_LONGITUD_TOKEN_OPACO = 24

_ESQUEMAS_DE_CONEXION = "postgres|postgresql|postgis|redis|rediss|mongodb|mysql|amqp|amqps"

# El orden importa: las reglas más específicas van antes que las genéricas, para
# que una cadena de conexión no se degrade a «URL con query» y pierda la redacción
# completa que le corresponde.
_PATRONES: tuple[tuple[re.Pattern[str], str], ...] = (
    # 1. Cadenas de conexión completas: el host tampoco aporta nada diagnóstico.
    (re.compile(rf"(?i)\b(?:{_ESQUEMAS_DE_CONEXION})://\S+"), REDACTED),
    # 2. Credenciales embebidas en el `userinfo` de una URL.
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^/\s@]+@"), rf"\1{REDACTED}@"),
    # 3. `query string` de cualquier URL: es donde viaja `?key=…` en las APIs de
    #    Google. Se conservan esquema, host y ruta, que sí son diagnósticos.
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.\-]*://[^\s?]*)\?\S*"), rf"\1?{REDACTED}"),
    # 4. `Bearer <token>`. Va **antes** de la regla 5 a propósito: en
    #    `Authorization: Bearer abc123`, la regla de pares clave/valor consumiría
    #    solo la palabra `Bearer` como valor y dejaría el token suelto detrás.
    (re.compile(r"(?i)\bbearer\s+\S+"), f"Bearer {REDACTED}"),
    # 5. Pares clave/valor sensibles: se conserva la clave, se borra el valor.
    (
        re.compile(
            r"(?i)\b(api[_-]?key|authorization|token|secret|password|passwd|pwd|cookie"
            r"|credentials?)\b\s*[:=]\s*\S+"
        ),
        rf"\1={REDACTED}",
    ),
    # 6. Token opaco suelto: cadena larga que mezcla letras y dígitos. Es la red de
    #    seguridad para un secreto que aparezca sin etiqueta que lo anuncie.
    (
        re.compile(
            rf"\b(?=[A-Za-z0-9_\-]*[A-Za-z])(?=[A-Za-z0-9_\-]*\d)"
            rf"[A-Za-z0-9_\-]{{{_LONGITUD_TOKEN_OPACO},}}\b"
        ),
        REDACTED,
    ),
)


def sanitize_error_message(exc: BaseException) -> str:
    """Mensaje del error, apto para quedar archivado como evidencia.

    Se trabaja sobre `str(exc)` y no sobre el traceback: la cadena de llamadas
    expondría rutas locales y haría la evidencia irreproducible entre máquinas.
    """
    return sanitize_text(str(exc))


def sanitize_text(texto: str) -> str:
    """Aplica las reglas de redacción y acota la longitud.

    Se expone aparte de `sanitize_error_message` para poder probar las reglas
    directamente sobre cadenas, sin fabricar una excepción por cada caso.
    """
    limpio = texto.strip()
    for patron, reemplazo in _PATRONES:
        limpio = patron.sub(reemplazo, limpio)

    # El recorte va al final: si fuese antes, podría partir un secreto por la mitad
    # y dejar el primer trozo intacto en la evidencia.
    if len(limpio) > MAX_MESSAGE_LENGTH:
        limpio = limpio[:MAX_MESSAGE_LENGTH] + "…"
    return limpio
