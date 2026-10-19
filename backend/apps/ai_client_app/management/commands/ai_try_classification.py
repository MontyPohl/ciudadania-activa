"""Prueba una descripción local contra uno o ambos prompts de OpenAI."""

from django.core.management.base import BaseCommand, CommandError

from apps.ai_client_app.prompt_trial import (
    build_trial_adapter,
    build_trial_request,
    load_photo,
    run_trial,
)


class Command(BaseCommand):
    help = "Prueba prompts de OpenAI sin modificar reportes ni auditoría."

    def add_arguments(self, parser):
        parser.add_argument("--description", required=True)
        parser.add_argument("--category", required=True)
        parser.add_argument("--image")
        parser.add_argument("--prompt", choices=("v1", "v2", "both"), default="both")

    def handle(self, *args, **options):
        try:
            image = load_photo(options["image"]) if options["image"] else None
            request = build_trial_request(options["description"], options["category"], image)
        except (OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        versions = ("v1", "v2") if options["prompt"] == "both" else (options["prompt"],)
        adapters = [(version, build_trial_adapter(version)) for version in versions]
        self.stdout.write(f"Llamadas al proveedor: {len(adapters)}")
        for version, adapter in adapters:
            result = run_trial(adapter, request)
            self.stdout.write(f"Prompt {version}")
            if result.error:
                self.stdout.write(f"Error: {result.error}")
            else:
                self.stdout.write(
                    f"Categoría: {result.category_slug}\nInstitución: {result.institution_slug}\n"
                    f"Prioridad: {result.priority}\nConfianza: {result.confidence}\n"
                    f"Inconsistencia: {'sí' if result.inconsistency else 'no'}\n"
                    f"Justificación: {result.justification}"
                )
            self.stdout.write(f"Se aplicaría sola: {'sí' if result.auto_apply else 'no'}")
            self.stdout.write(
                f"Tokens: entrada={result.input_tokens}, salida={result.output_tokens}, "
                f"total={result.total_tokens}"
            )
