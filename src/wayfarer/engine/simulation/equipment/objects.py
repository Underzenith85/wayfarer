"""Basic Set object damage using authoritative inventory and receipts.

Campaigns fourth printing B380, B483-485. Opt-in profiles only; no implicit
migration. Sentient machines are routed to actor injury by the combat adapter.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field

from wayfarer.engine.rules.checks import (
    NO_RANDOM,
    CheckTrace,
    Outcome,
    RandomSource,
    draw_dice,
    evaluate_success,
)
from wayfarer.engine.rules.types.hazard import require_hazards_settled
from wayfarer.engine.rules.types.object import (
    ObjectCondition,
    ObjectProfile,
    ObjectResult,
    residual_definition,
)
from wayfarer.engine.rules.types.recovery import require_settled
from wayfarer.engine.simulation.resources import (
    Command,
    Item,
    Receipt,
    ResourceEvent,
    ResourceState,
)
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id

if TYPE_CHECKING:
    from wayfarer.engine.simulation.resource_engine import ResourceEngine


class DamageObject(Command):
    kind: Literal["damage_object"] = "damage_object"
    item_id: Id
    basic_damage: int = Field(ge=0)
    damage_type: Literal["cr", "cut", "imp", "pi-", "pi", "pi+", "pi++", "burn"]
    armor_divisor: Decimal = Field(default=Decimal(1), gt=0, allow_inf_nan=False)
    explosive: bool = Field(default=False, exclude_if=lambda v: not v)


class StressObject(Command):
    kind: Literal["stress_object"] = "stress_object"
    item_id: Id


class BurnObject(Command):
    kind: Literal["burn_object"] = "burn_object"
    item_id: Id


class SetObjectEffectiveness(Command):
    kind: Literal["set_object_effectiveness"] = "set_object_effectiveness"
    item_id: Id
    definition_id: str | None = None


ObjectCommand = Annotated[
    DamageObject | StressObject | BurnObject | SetObjectEffectiveness,
    Field(discriminator="kind"),
]


def object_hp(
    weight_millipounds: int, construction: Literal["unliving", "homogenous", "diffuse"]
) -> int:
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


@dataclass(frozen=True)
class _ObjectEffect:
    condition: ObjectCondition
    injury: int
    dr: int
    checks: tuple[tuple[int, int, int], ...]
    damage_dice: tuple[int, ...]
    ignited: bool
    exploded: bool


def _fragile_result(
    profile: ObjectProfile,
    condition: ObjectCondition,
    injury: int,
    hp: int,
    attack_is_fire: bool,
    check: Callable[[int], CheckTrace],
) -> tuple[int, bool, bool, bool, bool]:
    disabled, destroyed = condition.disabled, condition.destroyed
    ignited, exploded = condition.burning, False
    major = injury > profile.hp // 2
    combustible = "combustible" in profile.fragility
    flammable = "flammable" in profile.fragility
    combined_auto = combustible and flammable and attack_is_fire and (major or injury >= 10)
    if combined_auto:
        ignited = True
    elif combustible and attack_is_fire:
        ignited = injury >= 10 or (major and not check(profile.ht).outcome.succeeded)
    if major and flammable and not combined_auto:
        ignited = ignited or not check(profile.ht - (3 if attack_is_fire else 0)).outcome.succeeded
    if major and "explosive" in profile.fragility:
        exploded = check(profile.ht).outcome is Outcome.CRITICAL_FAILURE
    if hp <= -5 * profile.hp:
        disabled = destroyed = True
    elif not destroyed:
        for multiple in range(1, 5):
            if hp <= -multiple * profile.hp < condition.hp:
                death = check(profile.ht)
                if death.outcome.succeeded:
                    continue
                disabled = destroyed = True
                if "brittle" in profile.fragility or (
                    "explosive" in profile.fragility and death.margin <= -3
                ):
                    hp = -10 * profile.hp
                if "explosive" in profile.fragility and death.margin <= -3:
                    exploded = True
                if (
                    flammable
                    and (condition.burning or ignited)
                    and death.outcome is Outcome.CRITICAL_FAILURE
                ):
                    exploded = True
                break
    if exploded:
        hp, disabled, destroyed, ignited = -10 * profile.hp, True, True, False
    return hp, disabled, destroyed, ignited, exploded


def _resolve_object_effect(
    profile: ObjectProfile,
    condition: ObjectCondition,
    command: DamageObject | StressObject | BurnObject,
    state: ResourceState,
    rng: RandomSource,
) -> _ObjectEffect:
    rolls: list[tuple[int, int, int]] = []

    def check(target: int) -> CheckTrace:
        dice = draw_dice(rng)
        rolls.append(dice)
        return evaluate_success(
            target,
            (),
            dice,
            rules_package=profile.profile_id,
            rules_version="1",
            rule_id="object:ht",
        )

    injury, dr = 0, profile.dr
    hp, disabled, destroyed = condition.hp, condition.disabled, condition.destroyed
    stress_at, burn_at = condition.last_stress_at, condition.last_burn_at
    damage_dice: tuple[int, ...] = ()
    ignited, exploded = condition.burning, False
    basic_damage = command.basic_damage if isinstance(command, DamageObject) else 0
    damage_type = command.damage_type if isinstance(command, DamageObject) else "burn"
    armor_divisor = command.armor_divisor if isinstance(command, DamageObject) else Decimal(1)
    if isinstance(command, BurnObject):
        if not condition.burning:
            raise ValidationError("Object is not burning")
        if burn_at == state.game_time:
            raise ConflictError("Object already took burning damage this second")
        burn_at = state.game_time
        damage_dice = draw_dice(rng, 1)
        basic_damage = max(0, damage_dice[0] - 1)
    if isinstance(command, (DamageObject, BurnObject)):
        dr = int(Decimal(profile.dr) / armor_divisor)
        if profile.dr == 0 and armor_divisor < 1:
            dr = 1
        penetrating = max(0, basic_damage - dr)
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
            damage_type, Fraction(3, 2) if damage_type == "cut" else Fraction(1)
        )
        injury = max(1, int(penetrating * multiplier)) if penetrating else 0
        if profile.construction == "diffuse":
            injury = min(injury, 1 if damage_type in ratios else 2)
        hp -= injury
        attack_is_fire = damage_type == "burn" or (
            isinstance(command, DamageObject) and command.explosive
        )
        hp, disabled, destroyed, ignited, exploded = _fragile_result(
            profile, condition, injury, hp, attack_is_fire, check
        )
    else:
        if disabled:
            raise ValidationError("Disabled objects cannot be used under stress")
        if hp <= 0:
            if stress_at == state.game_time:
                raise ConflictError("Object already checked under stress this second")
            stress_at = state.game_time
            if not check(profile.ht).outcome.succeeded:
                disabled = True
    residual_roll = condition.residual_roll
    if (
        disabled
        and not condition.disabled
        and not destroyed
        and (profile.residual_definitions or profile.broken_weapon_outcomes)
    ):
        residual_roll = rng.randbelow(6) + 1
    updated = condition.model_copy(
        update={
            "hp": hp,
            "disabled": disabled,
            "destroyed": destroyed,
            "last_stress_at": stress_at,
            "last_burn_at": burn_at,
            "residual_roll": residual_roll,
            "shock": min(4, injury)
            if injury and not profile.high_pain_threshold
            else condition.shock,
            "shock_until": state.game_time + 1 if injury else condition.shock_until,
            "burning": ignited,
        }
    )
    return _ObjectEffect(
        updated,
        injury,
        dr,
        tuple(rolls),
        damage_dice,
        ignited and not condition.burning,
        exploded,
    )


def apply_object(
    engine: ResourceEngine,
    state: ResourceState,
    command: ObjectCommand,
    *,
    system: bool = False,
    shield: bool = False,
    rng: RandomSource = NO_RANDOM,
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
    digest = hashlib.sha256(
        (command.model_dump_json() + (":shield" if shield else "")).encode()
    ).hexdigest()
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
    if profile.sentient:
        raise ValidationError("Sentient machines use their actor injury authority")
    if condition.destroyed and not (shield and isinstance(command, DamageObject)):
        raise ValidationError("Object is already destroyed")
    if isinstance(command, SetObjectEffectiveness):
        if condition.hp * 3 >= profile.hp:
            raise ValidationError("Reduced effectiveness requires less than one-third HP")
        if (
            command.definition_id is not None
            and command.definition_id not in profile.reduced_effectiveness_definitions
        ):
            raise ValidationError("GM selection is not a pinned reduced-effectiveness mode")
        updated_condition = condition.model_copy(
            update={"reduced_definition_id": command.definition_id}
        )
        result = ObjectResult(
            command_id=command.id,
            item_id=item.id,
            condition=updated_condition,
        )
        updated_item = item.model_copy(update={"condition": updated_condition})
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
    effect = _resolve_object_effect(profile, condition, command, state, rng)
    updated_condition = effect.condition
    usable = residual_definition(profile, effect.condition) is not None
    result = ObjectResult(
        command_id=command.id,
        item_id=item.id,
        injury=effect.injury,
        effective_dr=effect.dr,
        checks=effect.checks,
        damage_dice=effect.damage_dice,
        ignited=effect.ignited,
        exploded=effect.exploded,
        explosion_dice=(6 * profile.hp + 9) // 10 if effect.exploded else 0,
        condition=updated_condition,
    )
    updated_item = item.model_copy(
        update={
            "condition": updated_condition,
            "ready": item.ready and (not effect.condition.disabled or usable),
            "equipped": item.equipped and not (shield and effect.condition.hp <= -10 * profile.hp),
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
