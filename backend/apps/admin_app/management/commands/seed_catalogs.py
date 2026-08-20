from django.core.management.base import BaseCommand
from django.db import transaction

from apps.admin_app.catalogs import CATEGORIES, INSTITUTIONS
from apps.admin_app.models import Category, Institution


class Command(BaseCommand):
    help = "Carga los catálogos semilla del MVP (instituciones y categorías). Idempotente."

    @transaction.atomic
    def handle(self, *args, **options):
        institutions = {}
        created_institutions = 0

        for slug, name in INSTITUTIONS:
            institution, created = Institution.objects.update_or_create(
                slug=slug,
                defaults={"name": name},
            )
            institutions[slug] = institution
            created_institutions += int(created)

        created_categories = 0
        for slug, name, institution_slug, priority in CATEGORIES:
            _, created = Category.objects.update_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "default_institution": institutions[institution_slug],
                    "default_priority": priority,
                },
            )
            created_categories += int(created)

        self.stdout.write(
            self.style.SUCCESS(
                f"Instituciones: {len(INSTITUTIONS)} ({created_institutions} nuevas). "
                f"Categorías: {len(CATEGORIES)} ({created_categories} nuevas)."
            )
        )
