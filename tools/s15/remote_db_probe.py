"""Mide consultas constantes desde un dyno temporal; nunca consulta datos de personas."""

import argparse
import json
import os
import statistics
import time
from datetime import UTC, datetime


def percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-existing-deployment-readonly", action="store_true")
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--connections", type=int, default=5)
    args = parser.parse_args()
    if (
        not args.confirm_existing_deployment_readonly
        or not 10 <= args.samples <= 60
        or not 1 <= args.connections <= 10
        or os.environ.get("DJANGO_SETTINGS_MODULE") != "config.settings.prod"
        or not os.environ.get("DYNO", "").startswith("run.")
    ):
        parser.error("Se requiere un dyno temporal productivo y confirmación de solo lectura.")
    active_connection = None
    try:
        import django

        django.setup()
        from django.conf import settings
        from django.db import connection, transaction

        active_connection = connection
        database = settings.DATABASES["default"]
        host = database.get("HOST", "")
        if settings.DEBUG or not host.endswith((".supabase.co", ".pooler.supabase.com")):
            raise ValueError("El destino no corresponde al despliegue esperado.")
        options = database.setdefault("OPTIONS", {})
        options["connect_timeout"] = 5
        if options.get("sslmode") not in {"require", "verify-ca", "verify-full"}:
            options["sslmode"] = "require"
        connection.close()
        connection.ensure_connection()
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute("SET LOCAL statement_timeout = '5s'")
                cursor.execute("SELECT current_setting('transaction_read_only')")
                if cursor.fetchone()[0] != "on":
                    raise ValueError("La transacción no confirmó solo lectura.")
                cursor.execute("SELECT current_setting('server_version'), PostGIS_Lib_Version()")
                postgres, postgis = cursor.fetchone()
                # Tres consultas de calentamiento fuera de la población medida.
                for _ in range(3):
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
                samples = []
                for _ in range(args.samples):
                    start = time.perf_counter()
                    cursor.execute("SELECT 1")
                    if cursor.fetchone() != (1,):
                        raise ValueError("Respuesta de consulta inesperada.")
                    samples.append((time.perf_counter() - start) * 1000)
                transaction.set_rollback(True)
        connections = []
        for _ in range(args.connections):
            connection.close()
            start = time.perf_counter()
            connection.ensure_connection()
            connections.append((time.perf_counter() - start) * 1000)
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
                    cursor.execute("SET LOCAL statement_timeout = '5s'")
                    cursor.execute("SELECT current_setting('transaction_read_only')")
                    if cursor.fetchone()[0] != "on":
                        raise ValueError("La transacción reconectada no confirmó solo lectura.")
                    transaction.set_rollback(True)
        receipt = {
            "protocol": "s15-heroku-db-readonly-v1",
            "observedAt": datetime.now(UTC).isoformat(),
            "scope": "one-off Heroku dyno to configured Supabase; constant SQL only",
            "postgres": postgres,
            "postgis": postgis,
            "query": "SELECT 1",
            "warmupExcluded": 3,
            "samplesMs": samples,
            "connectionsMs": connections,
            "queryMedianMs": statistics.median(samples),
            "queryP99Ms": percentile(samples, 0.99),
            "connectionMedianMs": statistics.median(connections),
            "readOnlyConfirmed": True,
            "clientSslMode": options["sslmode"],
            "limitations": [
                "Includes driver, network/pooler and database work; not pure network latency",
                "One-off dyno, not the running web dyno; excludes HTTP, BFF and application work",
                "Small descriptive sample; no sustained capacity or population tail claim",
            ],
        }
        print("S15_DB_PROBE_JSON=" + json.dumps(receipt, sort_keys=True))
        return 0
    except Exception:
        # No traceback: los errores de conexión pueden contener hosts o credenciales.
        print("S15_DB_PROBE_FAILED: conexión/guard de solo lectura no verificado.")
        return 2
    finally:
        try:
            if active_connection is not None:
                active_connection.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
