"""Choosing the weapon mode an attack or parry uses."""

from __future__ import annotations

from decimal import Decimal

from wayfarer.engine.rules.checks import Outcome
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.skills.mundane.ranged import require_technology
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog, level
from wayfarer.engine.simulation.combat.encounter import Combatant
from wayfarer.engine.simulation.combat.equipment_entry import effective_entry
from wayfarer.engine.simulation.combat.objects.locations import item_hands, unavailable_hand
from wayfarer.engine.simulation.equipment.catalog import (
    EquipmentProfile,
    MeleeMode,
    RangedMode,
    require_skill_procedure,
)
from wayfarer.engine.simulation.health.hit_locations import disabled
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def _validate_hand_count(
    runtime: RulesContext,
    state: PlayState,
    actor_id: str,
    selected: MeleeMode | RangedMode,
    hands: tuple[str, ...],
    *,
    human: bool,
    mounted: bool,
) -> None:
    if mounted or not human or len(hands) == selected.hands:
        return
    if not isinstance(selected, RangedMode) or not (
        len(hands) == 1
        and selected.hands == 2
        and selected.one_handed_minimum_st_multiplier is not None
    ):
        raise ValidationError("Human weapon mode requires explicit matching hand bindings")
    compiled = build(runtime, state, actor_id)
    assert compiled.statistics is not None
    assert selected.minimum_st is not None
    required = Decimal(selected.minimum_st) * selected.one_handed_minimum_st_multiplier
    if Decimal(compiled.statistics.st) < required:
        raise ValidationError("One-handed firearm use requires 1.5 times listed ST")


def _attached_mode(
    runtime: RulesContext,
    state: PlayState,
    actor_id: str,
    item_id: str,
    selected: MeleeMode | RangedMode,
) -> MeleeMode | RangedMode:
    if not isinstance(selected, RangedMode) or selected.attachment is None:
        return selected
    attachment = selected.attachment
    entries = {entry.definition_id: entry for entry in catalog(runtime).entries}
    hosts: list[tuple[EquipmentProfile, RangedMode]] = []
    for item in state.resources.items:
        if item.id == item_id or item.owner_id != actor_id or not item.equipped or not item.ready:
            continue
        profile = entries[item.definition_id]
        hosts.extend(
            (profile, mode)
            for mode in profile.modes
            if isinstance(mode, RangedMode)
            and isinstance(profile.technology_level, int)
            and profile.technology_level >= attachment.minimum_host_tl
            and (
                profile.definition_id == attachment.host_definition_id
                if attachment.kind == "integral"
                else mode.skill_id in attachment.host_skill_ids
            )
        )
    if not hosts:
        raise ValidationError("Attached launcher requires its authored ready host weapon")
    return (
        selected.model_copy(update={"bulk": hosts[0][1].bulk})
        if attachment.inherit_bulk
        else selected
    )


def _validate_required_mount(
    state: PlayState,
    actor_id: str,
    required_definition_id: str | None,
) -> None:
    if required_definition_id is not None and not any(
        item.owner_id == actor_id and item.definition_id == required_definition_id and item.equipped
        for item in state.resources.items
    ):
        raise ValidationError("Mounted weapon requires its authored support equipment")


def ready_reach(
    runtime: RulesContext,
    state: PlayState,
    actor_id: str,
    item_id: str,
    mode_id: str | None,
    reach: int,
) -> ResourceState:
    """Spend a Ready maneuver selecting one starred table reach."""
    item = next((i for i in state.resources.items if i.id == item_id), None)
    if item is None or item.owner_id != actor_id or not item.equipped or not item.ready:
        raise ValidationError("Reach adjustment requires an owned, equipped, ready weapon")
    entry = effective_entry(runtime, item)
    candidates = tuple(
        m for m in entry.modes if isinstance(m, MeleeMode) and (mode_id is None or m.id == mode_id)
    )
    if len(candidates) != 1 or not candidates[0].reach_requires_ready:
        raise ValidationError("Select exactly one variable-reach melee mode")
    if reach not in candidates[0].reach:
        raise ValidationError("Selected reach is unavailable for this weapon mode")
    delayed = candidates[0].long_reach_ready_turns == 2 and reach >= 3
    continuing = delayed and item.pending_melee_reach == reach
    changed = item.model_copy(
        update={
            "melee_reach": reach if not delayed or continuing else item.melee_reach,
            "pending_melee_reach": reach if delayed and not continuing else None,
            "melee_reach_ready_progress": 1 if delayed and not continuing else 0,
        }
    )
    return state.resources.model_copy(
        update={
            "items": tuple(
                changed if other.id == item_id else other for other in state.resources.items
            )
        }
    )


def recover_stuck_weapon(
    runtime: RulesContext, state: PlayState, actor_id: str, item_id: str, command_id: str
) -> ResourceState:
    """Resolve B274's Ready/ST attempt to pull a pick from its target."""
    item = next((i for i in state.resources.items if i.id == item_id), None)
    if item is None or item.owner_id != actor_id or item.stuck_target_id is None:
        return state.resources
    if item.stuck_permanently:
        raise ValidationError("A critically stuck weapon cannot be freed during combat")
    actor = build(runtime, state, actor_id)
    assert actor.statistics is not None
    check = success_roll(catalog(runtime).profile_id, actor.statistics.st, rng=runtime.rng)
    if check.outcome is Outcome.CRITICAL_FAILURE:
        changed = item.model_copy(update={"stuck_permanently": True})
        return state.resources.model_copy(
            update={
                "items": tuple(changed if i.id == item_id else i for i in state.resources.items)
            }
        )
    if not check.outcome.succeeded:
        return state.resources
    target = build(runtime, state, item.stuck_target_id)
    assert target.statistics is not None
    resources, _ = apply_injury(
        state.resources,
        Wound(
            id=f"{command_id}:free-stuck",
            actor_id=item.stuck_target_id,
            expected_revision=state.resources.revision,
            basic_damage=item.stuck_injury // 2,
            resistance=0,
            # ``stuck_injury`` stores injury already inflicted; crushing
            # with no DR applies exactly half again without a second impaling multiplier.
            damage_type="cr",
        ),
        ht=target.statistics.ht,
        dx=target.statistics.dx,
        rng=runtime.rng,
        system=True,
        ignore_dr=True,
    )
    cleared = item.model_copy(
        update={
            "stuck_target_id": None,
            "stuck_injury": 0,
            "stuck_damage_type": None,
            "stuck_permanently": False,
        }
    )
    return resources.model_copy(
        update={"items": tuple(cleared if i.id == item_id else i for i in resources.items)}
    )


def _held_reach(selected: MeleeMode | RangedMode, held: int | None) -> MeleeMode | RangedMode:
    if not isinstance(selected, MeleeMode) or not selected.reach_requires_ready:
        return selected
    chosen = held if held in selected.reach else min(selected.reach)
    return selected.model_copy(update={"reach": (chosen,)})


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
    if (
        item is None
        or item.owner_id != actor_id
        or not item.equipped
        or not item.ready
        or item.stuck_target_id is not None
    ):
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
    selected = _held_reach(selected, item.melee_reach)
    selected = _attached_mode(runtime, state, actor_id, item_id, selected)
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
        _validate_required_mount(state, actor_id, mount.required_mount_definition_id)
        if actor_id not in crew:
            raise ValidationError("A mounted weapon is fired by its own crew")
        if len(crew) != mount.crew:
            raise ValidationError("A mounted weapon needs its full crew")
        for member in crew:
            pool = next((p for p in state.resources.pools if p.id == f"hp:{member}"), None)
            if pool is None or pool.injury is None or pool.injury.incapacitated:
                raise ValidationError("A mounted weapon needs every crew member serving it")
    _validate_hand_count(
        runtime,
        state,
        actor_id,
        selected,
        hands,
        human=bool(hp.injury and hp.injury.anatomy == "human"),
        mounted=mount is not None,
    )
    if (
        mount is None
        and unavailable
        and (
            len(hands)
            not in ({1, selected.hands} if isinstance(selected, RangedMode) else {selected.hands})
            or any(unavailable_hand(unavailable, h) for h in hands)
        )
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
    if entry.technology_level == "skill-relative":
        raise ValidationError("A usable weapon requires a concrete technology level")
    if isinstance(entry.technology_level, int):
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
        if pending.shield_rush:
            attacker = build(runtime, state, pending.attacker_id)
            assert attacker.statistics is not None
            return attacker.statistics.st * 1000
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
