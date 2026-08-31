"""Ejecuta el benchmark de concordancia con el adaptador falso (S8-E1).

**Solo admite `fake`.** Medir contra el proveedor real es un gate distinto, con
coste y cuota, y no puede dispararse desde un comando de conveniencia. Pedir otro
adaptador se rechaza en lugar de degradarse en silencio.

El código de salida refleja si la evaluación **se ejecutó**, no si superó el
85 %: con el adaptador falso un porcentaje bajo sigue significando que la
maquinaria de medición funciona.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.ai_client_app.adapters import FakeAiClassificationAdapter
from apps.ai_client_app.evaluation.dataset import InvalidEvaluationDataset, load_dataset
from apps.ai_client_app.evaluation.reporting import report_to_json
from apps.ai_client_app.evaluation.runner import evaluate_dataset
from apps.ai_client_app.evaluation.scoring import CA4_MIN_CONCORDANCE_PCT

ADAPTADOR_FAKE = "fake"
MODELO_FAKE = "fake-deterministic"

AVISO = (
    "Este resultado NO demuestra CA-4: el adaptador falso valida la maquinaria de "
    "medición, no la capacidad del modelo. La concordancia real exige medir el mismo "
    f"dataset contra el proveedor real y alcanzar el {CA4_MIN_CONCORDANCE_PCT} % en "
    "CA-4. La métrica estricta es diagnóstico y no tiene umbral."
)


class Command(BaseCommand):
    help = "Evalúa el dataset sintético de clasificación IA con el adaptador falso."

    def add_arguments(self, parser):
        parser.add_argument(
            "--adapter",
            default=ADAPTADOR_FAKE,
            help="Solo se admite 'fake'. El proveedor real se mide en un gate separado.",
        )
        parser.add_argument(
            "--format",
            default="text",
            choices=["text", "json"],
            help="Formato de salida.",
        )

    def handle(self, *args, **options):
        adapter_name = options["adapter"]
        if adapter_name != ADAPTADOR_FAKE:
            raise CommandError(
                f"Adaptador no admitido: {adapter_name!r}. S8-E1 solo evalúa con "
                "'fake'; medir contra el proveedor real corresponde a un gate propio."
            )

        try:
            dataset = load_dataset()
        except InvalidEvaluationDataset as exc:
            raise CommandError(f"Dataset inválido: {exc}") from exc

        report = evaluate_dataset(
            dataset,
            FakeAiClassificationAdapter(),
            adapter_name=ADAPTADOR_FAKE,
            model_name=MODELO_FAKE,
        )

        if options["format"] == "json":
            self.stdout.write(report_to_json(report))
            return

        self.stdout.write(f"dataset                {report.dataset_version}")
        self.stdout.write(f"adaptador              {report.adapter_name} ({report.model_name})")
        self.stdout.write(f"casos                  {report.total_cases}")
        self.stdout.write(
            f"CA-4 (cat+inst+prio)   {report.ca4_concordant_cases} "
            f"({report.ca4_concordance_pct} %)"
        )
        self.stdout.write(
            f"estricta (+inconsist)  {report.strict_concordant_cases} "
            f"({report.strict_concordance_pct} %)"
        )
        self.stdout.write(f"categoría              {report.category_accuracy_pct} %")
        self.stdout.write(f"institución            {report.institution_accuracy_pct} %")
        self.stdout.write(f"prioridad              {report.priority_accuracy_pct} %")
        self.stdout.write(f"inconsistencia         {report.inconsistency_accuracy_pct} %")
        self.stdout.write(f"errores                {report.errors_count}")
        self.stdout.write("")
        self.stdout.write(self.style.WARNING(AVISO))
