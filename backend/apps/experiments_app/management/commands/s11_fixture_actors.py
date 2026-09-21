from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from apps.admin_app.models import Category, Institution, InstitutionOperator
from apps.auth_app.enums import UserRole, VerificationStatus
from apps.auth_app.models import CitizenProfile
from apps.experiments_app.canonical import sha256_file
from apps.experiments_app.datasets import dataset_registry
from apps.experiments_app.runtime import require_isolation, valid_scenario_id

_NAMESPACE = uuid.UUID("ff5a50d2-659e-51f0-833a-cd5855bbcad3")


class Command(BaseCommand):
    help = (
        "Crea actores sintéticos idempotentes y emite tokens efímeros por stdout; "
        "el harness debe mantenerlos sólo en memoria."
    )

    def add_arguments(self, parser):
        parser.add_argument("--scenario", required=True)
        parser.add_argument("--dataset", type=Path, required=True)

    def handle(self, *args, **options):
        require_isolation()
        scenario_id = options["scenario"]
        if not valid_scenario_id(scenario_id):
            raise CommandError("scenario_id no pertenece a EX-02…EX-10.")
        dataset_path = options["dataset"].resolve()
        try:
            dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
            reports = sorted(
                (row for row in dataset["reports"] if row["scenario_id"] == scenario_id),
                key=lambda row: row["report_key"],
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise CommandError("El dataset funcional no es recuperable o válido.") from exc
        if dataset.get("dataset_id") != "s11-functional-v1" or dataset.get("synthetic") is not True:
            raise CommandError("Los fixtures EX-02…EX-08 exigen s11-functional-v1 sintético.")
        registered = dataset_registry(dataset.get("seed", 0))["s11-functional-v1"]
        if registered["sha256"] != sha256_file(dataset_path):
            raise CommandError("El dataset funcional no coincide con su generación determinista.")
        if not reports:
            raise CommandError("El dataset no contiene reportes para el escenario solicitado.")

        primary_institution_slug = reports[0]["institution_slug"]
        reports = [row for row in reports if row["institution_slug"] == primary_institution_slug]
        institution_a = Institution.objects.get(slug=primary_institution_slug)
        institution_b = Institution.objects.exclude(pk=institution_a.pk).order_by("slug").first()
        if institution_b is None:
            raise CommandError("EX-08 requiere una segunda institución sintética.")
        dataset_actor_ids = {actor["alias"]: actor["id"] for actor in dataset.get("actors", [])}
        dataset_aliases = {
            "author": "citizen-author",
            "confirmer": "citizen-confirmer",
            "operator_a": "operator-a",
            "operator_b": "operator-b",
            "admin": "admin",
        }
        definitions = {
            "author": (UserRole.CITIZEN, VerificationStatus.APROBADO),
            "confirmer": (UserRole.CITIZEN, VerificationStatus.APROBADO),
            "identity_candidate": (UserRole.CITIZEN, VerificationStatus.PENDIENTE),
            "duplicate_candidate": (UserRole.CITIZEN, VerificationStatus.PENDIENTE),
            "operator_a": (UserRole.OPERATOR, VerificationStatus.PENDIENTE),
            "operator_b": (UserRole.OPERATOR, VerificationStatus.PENDIENTE),
            "admin": (UserRole.ADMIN, VerificationStatus.PENDIENTE),
        }
        actors = {}
        user_model = get_user_model()
        for alias, (role, verification_status) in definitions.items():
            dataset_alias = dataset_aliases.get(alias)
            user_id = (
                uuid.UUID(dataset_actor_ids[dataset_alias])
                if dataset_alias in dataset_actor_ids
                else uuid.uuid5(_NAMESPACE, f"{scenario_id}:{alias}")
            )
            user, _ = user_model.objects.update_or_create(
                id=user_id,
                defaults={
                    "email": f"s11-{scenario_id.lower()}-{alias}@example.invalid",
                    "full_name": f"SYNTHETIC {alias}",
                    "role": role,
                    "verification_status": verification_status,
                    "is_active": True,
                },
            )
            user.set_unusable_password()
            user.save(update_fields=["password"])
            if role == UserRole.CITIZEN and verification_status == VerificationStatus.APROBADO:
                CitizenProfile.objects.update_or_create(
                    user=user,
                    defaults={
                        "document_hmac": hashlib.sha256(
                            f"SYNTHETIC:{scenario_id}:{alias}".encode()
                        ).hexdigest(),
                        "verification_selfie_url": "",
                        "verified_at": timezone.now(),
                    },
                )
            if alias == "operator_a":
                InstitutionOperator.objects.update_or_create(
                    user=user,
                    institution=institution_a,
                    defaults={"active": True},
                )
            elif alias == "operator_b":
                InstitutionOperator.objects.update_or_create(
                    user=user,
                    institution=institution_b,
                    defaults={"active": True},
                )
            refresh = RefreshToken.for_user(user)
            actors[alias] = {
                "id": str(user.id),
                "refresh": str(refresh),
                "access": str(refresh.access_token),
            }

        self.stdout.write(
            json.dumps(
                {
                    "scenario_id": scenario_id,
                    "actors": actors,
                    "institution_a": str(institution_a.id),
                    "institution_b": str(institution_b.id),
                    "dataset_id": dataset["dataset_id"],
                    "report_inputs": [self._report_input(row) for row in reports],
                    "identity_document": f"990{int(scenario_id[-2:]):04d}",
                    "ephemeral_secret": True,
                },
                sort_keys=True,
            )
        )

    @staticmethod
    def _report_input(row):
        category_id = Category.objects.only("id").get(slug=row["category"]["slug"]).id
        return {
            "report_key": row["report_key"],
            "category": str(category_id),
            "description": row["description"],
            "lat": str(row["location"]["latitude"]),
            "lng": str(row["location"]["longitude"]),
            "image": row["image"],
        }
