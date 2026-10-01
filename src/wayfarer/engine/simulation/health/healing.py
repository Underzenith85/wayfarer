"""Canonical positive HP recovery shared by medical care and trait intervals."""

from wayfarer.engine.character.traits.physiology import NO_PHYSIOLOGY_TRAITS, PhysiologyTraits
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
    physiology: PhysiologyTraits = NO_PHYSIOLOGY_TRAITS,
    unhealing_condition: bool = False,
) -> tuple[Pool, int]:
    """Restore eligible HP without clearing unrelated injury or death facts."""
    if hp.injury is None or not hp.id.startswith("hp:"):
        raise ValidationError("Recovery requires canonical profile HP")
    if hp.injury.dead and amount > 0:
        raise ValidationError("Dead patients require separate revival")
    if amount < 0 or (entitlement is not None and entitlement < 0):
        raise ValidationError("Positive recovery cannot apply injury")
    actor_id = hp.id.removeprefix("hp:")
    unhealing = physiology.parameter("disadvantage:unhealing", "kind")
    if unhealing is not None and kind in {"natural", "bandage", "first-aid", "physician", "drug"}:
        if not (unhealing == "partial" and unhealing_condition):
            return hp, 0
    if unhealing == "total" and kind == "steal-hp":
        return hp, 0
    if kind in {"bandage", "first-aid"}:
        amount = min(amount, max(0, hp.maximum - hp.current - hp.injury.rest_only_injury))
    healed = min(
        amount,
        max(0, hp.maximum - hp.current - blocked_hp(state.illnesses, actor_id, kind)),
        amount if entitlement is None else entitlement,
    )
    status = hp.injury.model_copy(
        update={
            "rest_only_injury": min(
                hp.injury.rest_only_injury, max(0, hp.maximum - hp.current - healed)
            )
        }
    )
    return hp.model_copy(update={"current": hp.current + healed, "injury": status}), healed
