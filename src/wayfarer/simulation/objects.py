"""Basic Set nonsentient object damage using authoritative inventory and receipts.

Campaigns fourth printing B380, B483-484. Opt-in profiles only; no implicit
migration, sentient machines, diffuse targets, repairs, or special fragile traits.
"""

import hashlib
import secrets
from decimal import Decimal
from fractions import Fraction
from typing import Annotated, Literal

from pydantic import Field

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.checks import RandomSource, draw_dice, evaluate_success
from wayfarer.rules.hazard_types import require_hazards_settled
from wayfarer.rules.object_types import ObjectCondition, ObjectProfile, ObjectResult
from wayfarer.rules.recovery_types import require_settled
from wayfarer.simulation.resources import (
    Command,
    Id,
    Item,
    Receipt,
    ResourceEngine,
    ResourceEvent,
    ResourceState,
)


class DamageObject(Command):
    kind: Literal["damage_object"] = "damage_object"
    item_id: Id
    basic_damage: int = Field(ge=0)
    damage_type: Literal["cr", "cut", "imp", "pi-", "pi", "pi+", "pi++", "burn"]
    armor_divisor: Decimal = Field(default=Decimal(1), gt=0, allow_inf_nan=False)


class StressObject(Command):
    kind: Literal["stress_object"] = "stress_object"
    item_id: Id


ObjectCommand = Annotated[DamageObject | StressObject, Field(discriminator="kind")]


def object_hp(weight_millipounds: int, construction: Literal["unliving", "homogenous"]) -> int:
    """B483: ceil(factor * cube root(pounds)), using exact integer arithmetic."""
    if construction not in ("unliving", "homogenous"):
        raise ValidationError("Unsupported object construction")
    if weight_millipounds <= 0:
        raise ValidationError("Object HP requires positive weight")
    factor = 4 if construction == "unliving" else 8
    target = factor**3 * weight_millipounds
    low, high = 1, 1
    while high**3 * 1000 < target:
        high *= 2
    while low < high:
        middle = (low + high) // 2
        if middle**3 * 1000 < target:
            low = middle + 1
        else:
            high = middle
    return low


def initialize_object(item: Item, profile: ObjectProfile) -> Item:
    """Explicit scenario/migration operation; never reset an existing condition."""
    if item.condition is not None or item.quantity != 1:
        raise ValidationError("Only an uninitialized individual item can be initialized")
    return item.model_copy(update={"condition": ObjectCondition(hp=profile.hp)})


def apply_object(
    engine: ResourceEngine,
    state: ResourceState,
    command: ObjectCommand,
    *,
    system: bool = False,
    rng: RandomSource = secrets,
) -> tuple[ResourceState, ObjectResult]:
    """Trusted resolved damage only; the caller resolves targeting and attack damage.

    This reducer is persisted through the existing resource transaction service.
    The damage amount is never accepted as player authority.
    """
    if not system:
        raise ValidationError("Object damage and stress require engine authority")
    engine.validate(state)
    if command.actor_id not in engine.actors:
        raise ValidationError("Unknown object command actor")
    digest = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
    previous = next((r for r in state.receipts if r.command_id == command.id), None)
    if previous is not None:
        if previous.digest != digest:
            raise ConflictError("Object command ID reused with different payload")
        result = next((r for r in state.object_results if r.command_id == command.id), None)
        if result is None:
            raise ValidationError("Missing object result for receipt")
        return state, result
    if command.expected_revision != state.revision:
        raise ConflictError("Resource revision changed")
    item = next((i for i in state.items if i.id == command.item_id), None)
    if item is None:
        raise ValidationError("Unknown object item")
    actors = frozenset({command.actor_id, item.owner_id})
    require_settled(state.recovery_tasks, actors, state.game_time)
    require_hazards_settled(state.hazards, actors, state.game_time)
    profile = engine.specs[item.definition_id].durability
    condition = item.condition
    if profile is None or condition is None:
        raise ValidationError("Object requires an explicit Basic Set durability profile")
    if condition.destroyed:
        raise ValidationError("Object is already destroyed")
    rolls: list[tuple[int, int, int]] = []

    def passes() -> bool:
        dice = draw_dice(rng)
        rolls.append(dice)
        return evaluate_success(
            profile.ht,
            (),
            dice,
            rules_package=profile.profile_id,
            rules_version="1",
            rule_id="object:ht",
        ).outcome.succeeded

    injury, dr = 0, profile.dr
    hp = condition.hp
    disabled: bool = condition.disabled
    destroyed: bool = False
    stress_at = condition.last_stress_at
    if isinstance(command, DamageObject):
        dr = int(Decimal(profile.dr) / command.armor_divisor)
        if profile.dr == 0 and command.armor_divisor < 1:
            dr = 1
        penetrating = max(0, command.basic_damage - dr)
        ratios = (
            {
                "imp": Fraction(1),
                "pi++": Fraction(1),
                "pi+": Fraction(1, 2),
                "pi": Fraction(1, 3),
                "pi-": Fraction(1, 5),
            }
            if profile.construction == "unliving"
            else {
                "imp": Fraction(1, 2),
                "pi++": Fraction(1, 2),
                "pi+": Fraction(1, 3),
                "pi": Fraction(1, 5),
                "pi-": Fraction(1, 10),
            }
        )
        multiplier = ratios.get(
            command.damage_type, Fraction(3, 2) if command.damage_type == "cut" else Fraction(1)
        )
        injury = max(1, int(penetrating * multiplier)) if penetrating else 0
        hp -= injury
        if hp <= -5 * profile.hp:
            disabled = destroyed = True
        else:
            for multiple in range(1, 5):
                if hp <= -multiple * profile.hp < condition.hp and not passes():
                    disabled = destroyed = True
                    break
    else:
        if disabled:
            raise ValidationError("Disabled objects cannot be used under stress")
        if hp <= 0:
            if stress_at == state.game_time:
                raise ConflictError("Object already checked under stress this second")
            stress_at = state.game_time
            if not passes():
                disabled = True
    updated_condition = ObjectCondition(
        hp=hp, disabled=disabled, destroyed=destroyed, last_stress_at=stress_at
    )
    result = ObjectResult(
        command_id=command.id,
        item_id=item.id,
        injury=injury,
        effective_dr=dr,
        checks=tuple(rolls),
        condition=updated_condition,
    )
    updated_item = item.model_copy(
        update={
            "condition": updated_condition,
            "ready": item.ready and not disabled,
        }
    )
    updated = state.model_copy(
        update={
            "revision": state.revision + 1,
            "object_results": state.object_results + (result,),
            "items": tuple(updated_item if i.id == item.id else i for i in state.items),
            "receipts": state.receipts + (Receipt(command_id=command.id, digest=digest),),
            "events": state.events
            + (
                ResourceEvent(
                    id=f"object:{command.id}",
                    at=state.game_time,
                    kind=command.kind,
                    target_id=item.id,
                ),
            ),
        }
    )
    engine.validate(updated)
    return updated, result
