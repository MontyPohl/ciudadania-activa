"""Administración de la recompensa simulada de cada institución."""

from django.db import IntegrityError, transaction

from apps.core.services import audit
from apps.rewards_app.constants import MVP_CREDIT_COST
from apps.rewards_app.models import Reward


class RewardConflict(Exception):
    code = "REWARD_ALREADY_EXISTS"

    def __init__(self, reward_id):
        self.reward_id = reward_id
        super().__init__(self.code)


def create_reward(*, operator, institution, name, description, active=True) -> Reward:
    """Crea una sola recompensa por institución con el costo fijo del MVP."""
    with transaction.atomic():
        try:
            with transaction.atomic():
                reward = Reward.objects.create(
                    institution=institution,
                    name=name,
                    description=description,
                    active=active,
                    credit_cost=MVP_CREDIT_COST,
                    simulated=True,
                )
        except IntegrityError as exc:
            constraint = getattr(getattr(exc.__cause__, "diag", None), "constraint_name", None)
            if constraint != "reward_una_por_institucion":
                raise
            existing = Reward.objects.get(institution=institution)
            raise RewardConflict(existing.pk) from exc
        audit.log(
            operator,
            "reward.created",
            {
                "reward_id": str(reward.pk),
                "institution_id": str(institution.pk),
                "name": reward.name,
                "active": reward.active,
            },
        )
        return reward


def update_reward(*, operator, reward, changes: dict) -> Reward:
    """Actualiza solo los campos editables y audita sus valores anteriores y nuevos."""
    with transaction.atomic():
        reward = Reward.objects.select_for_update().get(pk=reward.pk)
        changed = {}
        for field in ("name", "description", "active"):
            if field in changes and getattr(reward, field) != changes[field]:
                changed[field] = [getattr(reward, field), changes[field]]
                setattr(reward, field, changes[field])
        if not changed:
            return reward
        reward.save(update_fields=[*changed, "updated_at"])
        audit.log(
            operator,
            "reward.updated",
            {
                "reward_id": str(reward.pk),
                "institution_id": str(reward.institution_id),
                "changes": changed,
            },
        )
        return reward
