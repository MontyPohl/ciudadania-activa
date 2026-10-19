from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def with_ssl_cert_reqs(url: str, cert_reqs: str) -> str:
    """Añade ``ssl_cert_reqs`` a una URL ``rediss://`` que no lo declara.

    Heroku Redis expone TLS con un certificado autofirmado y, al rotar credenciales,
    reescribe ``REDIS_URL`` sin el parámetro: redis-py y kombu exigen entonces
    verificar el certificado y worker y beat no llegan a conectar. Un valor explícito
    en la URL siempre gana; las URL sin TLS se devuelven intactas.
    """
    parts = urlsplit(url)
    if parts.scheme != "rediss":
        return url
    query = parse_qsl(parts.query, keep_blank_values=True)
    if any(key == "ssl_cert_reqs" for key, _ in query):
        return url
    query.append(("ssl_cert_reqs", cert_reqs))
    return urlunsplit(parts._replace(query=urlencode(query)))
