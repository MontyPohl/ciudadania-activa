"""Regenera sesiones reales del reloj local, fuera de los artefactos congelados."""

import json
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from apps.admin_app.models import InstitutionOperator
from apps.core.final_dataset.files import read_manifest, write_json
from apps.core.final_dataset.isolation import DatasetError, guard_environment


class Command(BaseCommand):
    help = (
        "Emite JWT de actores sintéticos S15 para demo/carga local en un archivo privado separado."
    )

    def add_arguments(self, parser):
        parser.add_argument("manifest", type=Path)
        parser.add_argument("--artifact-root", required=True, type=Path)
        parser.add_argument("--output", required=True, type=Path)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        try:
            root = guard_environment(settings, options["artifact_root"])
            path, output = options["manifest"].resolve(), options["output"].resolve()
            if (
                root not in path.parents
                or root not in output.parents
                or path.parent in output.parents
            ):
                raise DatasetError(
                    "Sesiones deben estar dentro de artifact-root y fuera del dataset congelado."
                )
            if options["output"].is_symlink() or any(
                p.is_symlink() for p in options["output"].parents
            ):
                raise DatasetError("El archivo de actores no admite symlinks.")
            manifest = read_manifest(path)
            if options["dry_run"]:
                self.stdout.write(json.dumps({"state": "dry-run", "effects": []}))
                return
            actors = []
            for label, data in manifest["actors"].items():
                if label.startswith("redemption_"):
                    continue
                user = get_user_model().objects.get(pk=data["id"], is_active=True)
                if not user.email.startswith("s15-") or not user.email.endswith("@example.test"):
                    raise DatasetError("El manifiesto apunta a una cuenta ajena a S15.")
                refresh = RefreshToken.for_user(user)
                actor = {
                    "id": label,
                    "userId": str(user.pk),
                    "role": user.role,
                    "verified": user.verification_status == "APROBADO",
                    "email": user.email,
                    "accessToken": str(refresh.access_token),
                    "refreshToken": str(refresh),
                }
                memberships = list(
                    InstitutionOperator.objects.filter(user=user, active=True).values_list(
                        "institution_id", flat=True
                    )
                )
                if memberships:
                    actor["institutionId"] = str(memberships[0])
                actors.append(actor)
            write_json(
                output,
                {
                    "schemaVersion": 1,
                    "issuedAt": timezone.now().isoformat(),
                    "datasetPlanSha256": manifest["plan_sha256"],
                    "actors": actors,
                },
            )
        except (DatasetError, ValueError, OSError, get_user_model().DoesNotExist) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            json.dumps({"state": "exported", "actors": len(actors), "output": str(output)})
        )
