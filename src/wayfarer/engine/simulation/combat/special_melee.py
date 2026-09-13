"""Integration helpers for Campaigns B400-B406 special melee rules."""

from typing import Literal

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.tables.special_melee import DamageType, chink_penalty, size_reach
from wayfarer.engine.rules.types.location import HitLocation
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog
from wayfarer.engine.simulation.equipment.catalog import MeleeMode, RangedMode, WeaponMode
from wayfarer.engine.simulation.health.hit_locations import attack_penalty, part
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def actor_size_modifier(runtime: RulesContext, state: PlayState, actor_id: str) -> int:
    """Read SM from the approved build; a command can never supply or override it."""
    return approved_size_modifier(build(runtime, state, actor_id))


def approved_size_modifier(compiled: ValidatedBuild) -> int:
    """Extract the compiler-validated Size Modifier purchase."""
    return next(
        (p.amount for p in compiled.purchases if p.definition_id == "trait:size-modifier"),
        0,
    )


def actor_reaches(
    runtime: RulesContext, state: PlayState, actor_id: str, reaches: tuple[int, ...]
) -> tuple[int, ...]:
    return size_reach(reaches, actor_size_modifier(runtime, state, actor_id))


def targeted_attack_penalty(
    location: HitLocation | None,
    *,
    armor_chink: bool,
    damage_type: DamageType,
    tight_beam: bool,
    shield_side: str | None,
) -> int:
    """Select either the complete chink penalty or the ordinary anatomy penalty."""
    if armor_chink:
        return chink_penalty(location, damage_type, tight_beam=tight_beam)
    if location is not None:
        return attack_penalty(location, shield_side=shield_side)
    return 0


def validate_special_attack(
    runtime: RulesContext,
    state: PlayState,
    mode: WeaponMode,
    *,
    attacker_id: str,
    defender_id: str,
    hit_location: HitLocation | None,
    armor_chink: bool,
    strike_strength: int | None,
    subdual_mode: Literal["flat", "blunt-end"] | None,
    target_item_id: str | None,
) -> None:
    """Validate special-strike declarations against approved builds and equipment."""
    if strike_strength is not None:
        compiled = build(runtime, state, attacker_id)
        assert compiled.statistics is not None
        if (
            isinstance(mode, RangedMode)
            or mode.damage.basis == "fixed"
            or strike_strength >= compiled.statistics.st
        ):
            raise ValidationError("Pulled melee strength must be below the attacker's ST")
    if subdual_mode == "flat" and not (
        isinstance(mode, MeleeMode)
        and mode.damage.basis == "swing"
        and mode.damage.damage_type == "cut"
    ):
        raise ValidationError("Flat-side subdual requires a swinging cutting mode")
    if subdual_mode == "blunt-end" and not (
        isinstance(mode, MeleeMode)
        and mode.damage.basis == "thrust"
        and mode.damage.damage_type == "imp"
    ):
        raise ValidationError("Blunt-end subdual requires a thrusting impaling mode")
    if armor_chink:
        if subdual_mode is not None or target_item_id is not None:
            raise ValidationError("Armor chinks require an armored body target")
        chink_penalty(hit_location, mode.damage.damage_type, tight_beam=mode.damage.tight_beam)
        require_armor_chink(
            runtime,
            state,
            defender_id,
            hit_location,
        )


def require_armor_chink(
    runtime: RulesContext,
    state: PlayState,
    actor_id: str,
    location: HitLocation | None,
) -> None:
    """Require equipped, functioning armor over the declared body location."""
    if location == "random":
        raise ValidationError("Armor chinks require a declared hit location")
    selected = location or "torso"
    grouped = {
        "arm": "arms",
        "hand": "hands",
        "leg": "legs",
        "foot": "feet",
        "eye": "eyes",
    }.get(part(selected))
    entries = {entry.definition_id: entry for entry in catalog(runtime).entries}
    armored = any(
        item.owner_id == actor_id
        and item.equipped
        and (item.condition is None or not item.condition.disabled)
        and (armor := entries[item.definition_id].armor) is not None
        and (selected in armor.locations or grouped in armor.locations)
        for item in state.resources.items
    )
    if not armored:
        raise ValidationError("Armor-chink targeting requires covering equipped armor")
