from config.redis_url import with_ssl_cert_reqs


def test_redis_sin_tls_queda_intacta():
    url = "redis://localhost:6379/0"
    assert with_ssl_cert_reqs(url, "none") == url


def test_rediss_sin_parametro_lo_recibe_y_conserva_credenciales():
    url = "rediss://:p%40ss@host.example:6380/0?health_check_interval=10"
    assert with_ssl_cert_reqs(url, "none") == (
        "rediss://:p%40ss@host.example:6380/0?health_check_interval=10&ssl_cert_reqs=none"
    )


def test_rediss_con_valor_explicito_no_se_pisa():
    url = "rediss://:secreto@host.example:6380/0?ssl_cert_reqs=required"
    assert with_ssl_cert_reqs(url, "none") == url
