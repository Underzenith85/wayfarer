"""Choosing the weapon mode an attack or parry uses."""

from __future__ import annotations

from wayfarer.engine.rules.skills.mundane.ranged import require_technology
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog, level
from wayfarer.engine.simulation.combat.encounter import Combatant
from wayfarer.engine.simulation.combat.equipment_entry import effective_entry
from wayfarer.engine.simulation.combat.objects.locations import item_hands, unavailable_hand
from wayfarer.engine.simulation.equipment.catalog import (
    MeleeMode,
    RangedMode,
    require_skill_procedure,
)
from wayfarer.engine.simulation.health.hit_locations import disabled
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def launched_mode(
    runtime: RulesContext, state: PlayState, actor_id: str, selected: RangedMode
) -> RangedMode:
    """Apply the launcher's pinned effect, or refuse the throw without it."""
    spec = selected.launcher
    assert spec is not None
    held = next(
        (
            i
            for i in state.resources.items
            if i.owner_id == actor_id
            and i.definition_id == spec.launcher_definition_id
            and i.equipped
            and i.ready
            and (i.condition is None or not i.condition.disabled)
        ),
        None,
    )
    if held is None:
        raise ValidationError("This throw requires its launcher in hand")
    ranges: dict[str, object] = {
        "maximum_range": selected.maximum_range * spec.range_multiplier,
        "damage": selected.damage.model_copy(
            update={"adds": selected.damage.adds + spec.damage_bonus}
        ),
    }
    if selected.half_damage_range is not None:
        ranges["half_damage_range"] = selected.half_damage_range * spec.range_multiplier
    return selected.model_copy(update=ranges)


def mode(
    runtime: RulesContext, state: PlayState, actor_id: str, item_id: str, mode_id: str | None
) -> MeleeMode | RangedMode:
    item = next((i for i in state.resources.items if i.id == item_id), None)
    if item is None or item.owner_id != actor_id or not item.equipped or not item.ready:
        raise ValidationError("Melee requires an owned, equipped, ready weapon")
    entry = next(
        (e for e in catalog(runtime).entries if e.definition_id == item.definition_id), None
    )
    if entry is None:
        raise ValidationError("Weapon is not in the pinned combat catalog")

    entry = effective_entry(runtime, item)
    modes = tuple(m for m in entry.modes if (mode_id is None or m.id == mode_id))
    if len(modes) != 1:
        raise ValidationError("Select exactly one supported weapon mode")
    selected = modes[0]
    if (
        isinstance(selected, RangedMode)
        and selected.smartgun is not None
        and actor_id not in item.authorized_actor_ids
    ):
        raise ValidationError("Smartgun electronic access denies this actor")
    if selected.damage.damage_type == "fat" or (
        selected.damage.armor_divisor != 1
        and catalog(runtime).profile_id != "gurps-basic-set-4e-2004"
    ):
        raise ValidationError("Weapon damage requires unsupported profile mechanics")

    unavailable = disabled(state.resources, actor_id) | frozenset(
        g.location
        for e in state.encounters
        for g in e.grips
        if g.target_id == actor_id and g.location in ("left-arm", "right-arm")
    )
    hands = item_hands(state, actor_id, item_id)
    hp = next(p for p in state.resources.pools if p.id == f"hp:{actor_id}")
    mount = selected.mount if isinstance(selected, RangedMode) else None
    if mount is not None:
        # The mount bears the weapon: the crew, not a grip, is what validates
        # the shot, and everyone it needs must still be serving it (#357).
        crew = item.mount_crew
        if actor_id not in crew:
            raise ValidationError("A mounted weapon is fired by its own crew")
        if len(crew) != mount.crew:
            raise ValidationError("A mounted weapon needs its full crew")
        for member in crew:
            pool = next((p for p in state.resources.pools if p.id == f"hp:{member}"), None)
            if pool is None or pool.injury is None or pool.injury.incapacitated:
                raise ValidationError("A mounted weapon needs every crew member serving it")
    elif hp.injury and hp.injury.anatomy == "human" and len(hands) != selected.hands:
        raise ValidationError("Human weapon mode requires explicit matching hand bindings")
    if (
        mount is None
        and unavailable
        and (len(hands) != selected.hands or any(unavailable_hand(unavailable, h) for h in hands))
    ):
        raise ValidationError(
            "Selected grip uses a crippled hand or requires explicit hand bindings"
        )
    held_others = sum(
        1
        for other in state.resources.items
        if other.owner_id == actor_id
        and other.id != item_id
        and other.equipped
        and other.ready
        and any(
            e.definition_id == other.definition_id and (e.modes or e.shield)
            for e in catalog(runtime).entries
        )
    )
    if mount is None and selected.hands + held_others > 2:
        raise ValidationError("Selected grip exceeds available hands")
    if isinstance(selected, RangedMode) and selected.launcher is not None:
        # B222: the launcher is a second held item that improves the throw. It
        # is not consumed, and without it in hand this mode does not exist.
        selected = launched_mode(runtime, state, actor_id, selected)
    require_skill_procedure(catalog(runtime).profile_id, selected)
    if not isinstance(entry.technology_level, int):
        raise ValidationError("A usable weapon requires a concrete technology level")
    require_technology(
        selected.skill_id,
        runtime.reviewer.compiler.policy.technology_level,
        entry.technology_level,
    )
    level(build(runtime, state, actor_id), selected.skill_id)
    return selected


def mode_reach(selected: MeleeMode | RangedMode) -> int:
    """The reach a combatant holds by declaring this mode: a melee mode's longest, else 1."""
    return max(selected.reach) if isinstance(selected, MeleeMode) else 1


def require_two_weapon_modes(first: MeleeMode | RangedMode, second: MeleeMode | RangedMode) -> None:
    """B365: an All-Out Attack (Double) with two weapons needs a one-handed melee mode in each."""
    if (
        not isinstance(first, MeleeMode)
        or not isinstance(second, MeleeMode)
        or first.hands != 1
        or second.hands != 1
    ):
        raise ValidationError("Two-weapon Double requires one-handed melee modes")


def heavy_parry_weight(
    runtime: RulesContext,
    state: PlayState,
    participant: Combatant,
    incoming_item_id: str | None = None,
    incoming_mode_id: str | None = None,
) -> int | None:
    """B376 incoming weight when the heavy-weapon parry limit applies, else None.

    Only the Basic profile carries the limit; Lite (pp. 24-28) states no weight
    rule. Callers inside the melee dispatch name the attacking item directly;
    otherwise the persisted pending attack supplies it, and a mode that is not
    melee, a spell attack or an unknown item carries no limit.
    """
    equipment = catalog(runtime)
    if equipment.profile_id != "gurps-basic-set-4e-2004":
        return None
    mode_id = incoming_mode_id
    if incoming_item_id is None:
        encounter = next(
            (
                e
                for e in state.encounters
                if e.pending_defense and e.pending_defense.defender_id == participant.actor_id
            ),
            None,
        )
        if encounter is None:
            return None
        pending = encounter.pending_defense
        assert pending is not None
        if pending.spell_cast_id is not None:
            return None
        incoming_item_id, mode_id = pending.weapon_id, pending.mode_id
    item = next((i for i in state.resources.items if i.id == incoming_item_id), None)
    if item is None or not any(
        e.definition_id == item.definition_id and e.modes for e in equipment.entries
    ):
        return None

    entry = effective_entry(runtime, item)
    modes = tuple(m for m in entry.modes if mode_id is None or m.id == mode_id)
    if not modes or not all(isinstance(m, MeleeMode) for m in modes):
        return None
    weight = entry.weight_millipounds
    if not isinstance(weight, int):
        raise ValidationError("Melee weapons require integral millipound weight")
    return weight or None
