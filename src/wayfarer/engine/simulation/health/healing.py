"""Canonical positive HP recovery shared by medical care and trait intervals."""

from wayfarer.engine.rules.types.hazard import blocked_hp
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.errors import ValidationError


def restore_hp(
    state: ResourceState,
    hp: Pool,
    amount: int,
    *,
    kind: str,
    entitlement: int | None = None,
) -> tuple[Pool, int]:
    """Restore eligible HP without clearing unrelated injury or death facts."""
    if hp.injury is None or not hp.id.startswith("hp:"):
        raise ValidationError("Recovery requires canonical profile HP")
    if hp.injury.dead and amount > 0:
        raise ValidationError("Dead patients require separate revival")
    if amount < 0 or (entitlement is not None and entitlement < 0):
        raise ValidationError("Positive recovery cannot apply injury")
    actor_id = hp.id.removeprefix("hp:")
    healed = min(
        amount,
        max(0, hp.maximum - hp.current - blocked_hp(state.illnesses, actor_id, kind)),
        amount if entitlement is None else entitlement,
    )
    return hp.model_copy(update={"current": hp.current + healed}), healed
