"""Resumen de uso y costo estimado de las clasificaciones persistidas."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Q, Sum

from apps.ai_client_app.models import AiClassification


class Command(BaseCommand):
    help = "Resume tokens y costo estimado con los precios indicados, sin modificar datos."

    def add_arguments(self, parser):
        parser.add_argument("--input-usd-per-1m", required=True, help="Precio decimal en USD.")
        parser.add_argument("--output-usd-per-1m", required=True, help="Precio decimal en USD.")
        parser.add_argument("--since", help="Fecha inicial inclusiva (AAAA-MM-DD).")
        parser.add_argument("--model", help="Nombre exacto del modelo.")

    def handle(self, *args, **options):
        precios = []
        for nombre in ("input_usd_per_1m", "output_usd_per_1m"):
            try:
                precio = Decimal(options[nombre])
            except (InvalidOperation, ValueError, TypeError) as exc:
                raise CommandError("Los precios deben ser números decimales finitos.") from exc
            if not precio.is_finite() or precio < 0:
                raise CommandError("Los precios deben ser finitos y no negativos.")
            precios.append(precio)

        clasificaciones = AiClassification.objects.all()
        if options["since"]:
            try:
                desde = date.fromisoformat(options["since"])
            except ValueError as exc:
                raise CommandError("--since debe ser una fecha ISO (AAAA-MM-DD).") from exc
            clasificaciones = clasificaciones.filter(created_at__date__gte=desde)
        if options["model"] is not None:
            clasificaciones = clasificaciones.filter(model_name=options["model"])

        con_uso = (
            Q(input_tokens__isnull=False)
            | Q(output_tokens__isnull=False)
            | Q(total_tokens__isnull=False)
        )
        resumen = clasificaciones.aggregate(
            total=Count("pk"),
            con_uso=Count("pk", filter=con_uso),
            entrada=Sum("input_tokens", filter=con_uso),
            salida=Sum("output_tokens", filter=con_uso),
        )
        cantidad = resumen["con_uso"]
        entrada = resumen["entrada"] or 0
        salida = resumen["salida"] or 0
        costo = (Decimal(entrada) * precios[0] + Decimal(salida) * precios[1]) / Decimal(1_000_000)
        divisor = Decimal(cantidad) if cantidad else Decimal(1)

        def seis_decimales(valor):
            return format(valor.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP), ".6f")

        self.stdout.write(f"Clasificaciones totales: {resumen['total']}")
        self.stdout.write(f"Con uso registrado: {cantidad}")
        self.stdout.write(f"Sin uso registrado: {resumen['total'] - cantidad}")
        self.stdout.write(f"Tokens de entrada: {entrada}")
        self.stdout.write(f"Tokens de salida: {salida}")
        self.stdout.write(
            f"Promedio de tokens de entrada por clasificación con uso: "
            f"{seis_decimales(Decimal(entrada) / divisor)}"
        )
        self.stdout.write(
            f"Promedio de tokens de salida por clasificación con uso: "
            f"{seis_decimales(Decimal(salida) / divisor)}"
        )
        self.stdout.write(f"Costo estimado total (USD): {seis_decimales(costo)}")
        self.stdout.write(
            f"Costo estimado por clasificación con uso (USD): {seis_decimales(costo / divisor)}"
        )
        self.stdout.write(
            "La estimación usa solo contadores informados; si falta alguno, el costo es parcial."
        )
