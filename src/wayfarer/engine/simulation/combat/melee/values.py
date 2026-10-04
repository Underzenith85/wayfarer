"""Shared validation and standard defense scores, independent of unarmed dispatch."""

from __future__ import annotations

from decimal import Decimal

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits.mastery import parry_multiplier
from wayfarer.engine.rules.effects import DerivedValue
from wayfarer.engine.rules.tables.combat import minimum_strength_penalty
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog, fatigue_ready, level
from wayfarer.engine.simulation.combat.combat_height import defense_height
from wayfarer.engine.simulation.combat.encounter import Combatant
from wayfarer.engine.simulation.combat.entangle import defense_penalty as entangle_defense_penalty
from wayfarer.engine.simulation.combat.equipment_entry import effective_entry, weapon_target
from wayfarer.engine.simulation.combat.generations import rooted_dodge_health_trait_composition
from wayfarer.engine.simulation.combat.maneuvers import ATTACK_MANEUVERS
from wayfarer.engine.simulation.combat.melee.heavy_parry import require_breakage
from wayfarer.engine.simulation.combat.melee.modes import heavy_parry_weight, mode
from wayfarer.engine.simulation.combat.objects.locations import item_hands
from wayfarer.engine.simulation.combat.special_melee import actor_reaches
from wayfarer.engine.simulation.combat.tactical import pose
from wayfarer.engine.simulation.combat.vocabulary import Defense
from wayfarer.engine.simulation.equipment.catalog import MeleeMode, inventory_load
from wayfarer.engine.simulation.health.fatigue import fatigue_value
from wayfarer.engine.simulation.health.fright_state import can_defend
from wayfarer.engine.simulation.health.fright_state import stunned as fright_stunned
from wayfarer.engine.simulation.health.hit_locations import disabled
from wayfarer.engine.simulation.health.injury import impaired_movement
from wayfarer.engine.simulation.health.symptom_state import acute_blindness
from wayfarer.engine.simulation.magic.effects import require_not_dazed
from wayfarer.engine.simulation.magic.haste_effects import bonus as haste_bonus
from wayfarer.engine.simulation.magic.rooted_feet_state import (
    active_effect as rooting,
)
from wayfarer.engine.simulation.magic.rooted_feet_state import (
    melee_weapon_penalty,
    rooted_dodge,
)
from wayfarer.engine.simulation.resources import Pool
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def defense_height_bonus(
    runtime: RulesContext, state: PlayState, participant: Combatant, reach: int = 1
) -> int:

    encounter = next(
        (
            e
            for e in state.encounters
            if e.pending_defense and e.pending_defense.defender_id == participant.actor_id
        ),
        None,
    )
    if encounter is None or encounter.spatial_kind != "hex":
        return 0
    pending = encounter.pending_defense
    assert pending is not None
    if pending.mode_id is None:
        return 0  # Preflight before the selected incoming mode is persisted.
    incoming = mode(runtime, state, pending.attacker_id, pending.weapon_id, pending.mode_id)
    if not isinstance(incoming, MeleeMode):
        return 0
    attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    board = runtime.require_hex(encounter)
    return defense_height(
        board.cell(pose(participant).position).ground,
        board.cell(pose(attacker).position).ground,
        reach=reach,
    )


def defense_selection(
    runtime: RulesContext,
    state: PlayState,
    participant: Combatant,
    selected: Defense,
    item_id: str | None,
) -> tuple[str | None, bool]:
    """Validate the choice and bind a targeted weapon before scoring or dispatch."""
    object_target = next(
        (
            e.pending_defense.target_item_id
            for e in state.encounters
            if e.status == "active"
            and e.pending_defense
            and e.pending_defense.defender_id == participant.actor_id
        ),
        None,
    )

    if object_target and next(i for i in state.resources.items if i.id == object_target).ground:
        raise ValidationError("Unheld objects have no active defense")
    targeted_weapon = weapon_target(runtime, state, object_target)
    if targeted_weapon:
        if selected == "block":
            raise ValidationError("A weapon target cannot be protected by Block")
        if selected == "parry":
            if item_id not in (None, object_target):
                raise ValidationError("Only the targeted weapon can parry this attack")
            item_id = object_target

    if not can_defend(state.resources, participant.actor_id):
        raise ValidationError("Fright condition prevents active defense")

    require_not_dazed(state.resources, participant.actor_id)
    if participant.pinned:
        raise ValidationError("Pinned actors cannot defend")
    if participant.maneuver_state.defense_forbidden or (
        selected == "parry" and participant.maneuver_state.parry_forbidden
    ):
        raise ValidationError("The selected maneuver forbids this defense")
    return item_id, targeted_weapon


def standard_defense_value(
    runtime: RulesContext,
    state: PlayState,
    participant: Combatant,
    selected: Defense,
    item_id: str | None = None,
    *,
    parry_mode_id: str | None = None,
    incoming_item_id: str | None = None,
    incoming_mode_id: str | None = None,
) -> tuple[DerivedValue | None, str | None]:
    """Score Dodge, Block, or a weapon Parry without barehanded dispatch."""
    if selected == "none":
        return None, None
    item_id, targeted_weapon = defense_selection(runtime, state, participant, selected, item_id)
    return score_defense(
        runtime,
        state,
        participant,
        selected,
        item_id,
        targeted_weapon=targeted_weapon,
        parry_mode_id=parry_mode_id,
        incoming_item_id=incoming_item_id,
        incoming_mode_id=incoming_mode_id,
    )


def _ready_defender(
    runtime: RulesContext, state: PlayState, participant: Combatant
) -> tuple[ValidatedBuild, Pool, Pool]:
    compiled = build(runtime, state, participant.actor_id, defensive=True)
    assert compiled.statistics is not None
    hp = next(p for p in state.resources.pools if p.id == f"hp:{participant.actor_id}")
    fp = next(p for p in state.resources.pools if p.id == f"fp:{participant.actor_id}")
    if not fatigue_ready(state, participant.actor_id):
        raise ValidationError("Exhausted actor cannot defend")
    if hp.injury is None or hp.injury.incapacitated:
        raise ValidationError("Incapacitated actor cannot defend")
    return compiled, hp, fp


def fencing_retreat_bonus(state: PlayState, participant: Combatant, *, fencing: bool) -> int:
    """B377: fencing gets +3 total only against the foe being retreated from."""
    if not fencing or participant.retreat_attacker_id is None:
        return 0
    return (
        2
        if any(
            (
                encounter.pending_defense is not None
                and encounter.pending_defense.defender_id == participant.actor_id
                and encounter.pending_defense.attacker_id == participant.retreat_attacker_id
            )
            or (
                encounter.pending_unarmed is not None
                and encounter.pending_unarmed.target_id == participant.actor_id
                and encounter.pending_unarmed.actor_id == participant.retreat_attacker_id
            )
            for encounter in state.encounters
        )
        else 0
    )


def _require_rooted_choice(
    state: PlayState, participant: Combatant, selected: Defense, *, shield: bool = False
) -> None:
    if rooting(state.resources, participant.actor_id) is not None and (
        selected == "block" or shield
    ):
        raise ValidationError(
            "Rooted Feet does not yet support Shield or Cloak skill classification"
        )


def _unsupported_rooted_health(hp: Pool, fp: Pool) -> bool:
    assert hp.injury is not None
    if rooted_dodge_health_trait_composition():
        return hp.current <= 0 or fp.current <= 0
    return (
        hp.current * 3 < hp.maximum
        or fp.current * 3 < fp.maximum
        or hp.injury.physical_traits.combat_reflexes
    )


def _require_rooted_dodge(
    runtime: RulesContext,
    state: PlayState,
    participant: Combatant,
    hp: Pool,
    fp: Pool,
    bonus: int,
    blind: bool,
) -> None:
    assert hp.injury is not None
    if rooting(state.resources, participant.actor_id) is not None and (
        _unsupported_rooted_health(hp, fp)
        or haste_bonus(state.resources, participant.actor_id)
        or participant.posture != "standing"
        or bonus
        or blind
        or participant.grappled
        or participant.arm_locked
        or hp.injury.stunned
        or fright_stunned(state.resources, participant.actor_id)
        or entangle_defense_penalty(participant)
        or defense_height_bonus(runtime, state, participant)
        or any(
            e.pending_defense is not None
            and e.pending_defense.defender_id == participant.actor_id
            and (
                e.pending_defense.visibility_defense_penalty
                or e.pending_defense.attention_defense_penalty
            )
            for e in state.encounters
        )
        or participant.defense_penalty
        or participant.tactical_defense_bonus
        or participant.maneuver_state.enhanced_defense == "dodge"
    ):
        raise ValidationError("Rooted Feet does not yet support this Dodge composition")


def score_defense(
    runtime: RulesContext,
    state: PlayState,
    participant: Combatant,
    selected: Defense,
    item_id: str | None = None,
    *,
    targeted_weapon: bool,
    parry_mode_id: str | None = None,
    incoming_item_id: str | None = None,
    incoming_mode_id: str | None = None,
) -> tuple[DerivedValue | None, str | None]:
    compiled, hp, fp = _ready_defender(runtime, state, participant)
    _require_rooted_choice(state, participant, selected)
    assert compiled.statistics is not None
    assert hp.injury is not None
    equipment = catalog(runtime)
    entries = {e.definition_id: e for e in equipment.entries}
    ready = [
        i
        for i in state.resources.items
        if i.id in participant.ready_item_ids
        and i.ready
        and i.equipped
        and (
            i.condition is None or not i.condition.disabled or i.condition.residual_roll is not None
        )
    ]
    shields = [
        (i, entries[i.definition_id].shield) for i in ready if entries[i.definition_id].shield
    ]

    unavailable = disabled(state.resources, participant.actor_id) | frozenset(
        g.location
        for e in state.encounters
        for g in e.grips
        if g.target_id == participant.actor_id and g.location in ("left-arm", "right-arm")
    )
    blind = acute_blindness(state.resources, participant.actor_id) or (
        "left-eye" in unavailable and "right-eye" in unavailable
    )
    bonus = max(
        (
            max(
                0,
                s.defense_bonus
                - int(
                    any(
                        h.replace("hand", "arm") in unavailable
                        for h in item_hands(state, participant.actor_id, i.id)
                    )
                ),
            )
            for i, s in shields
            if s is not None
        ),
        default=0,
    )
    if targeted_weapon:
        bonus = 0

    penalty = (
        entangle_defense_penalty(participant)
        + participant.defense_penalty
        + int(hp.injury.physical_traits.combat_reflexes)
        + participant.tactical_defense_bonus
        - 4 * int(participant.arm_locked)
        + (-1 if selected == "dodge" else -2) * int(participant.grappled)
        + (2 if participant.maneuver_state.enhanced_defense == selected else 0)
        + (-4 if hp.injury.stunned or fright_stunned(state.resources, participant.actor_id) else 0)
        + (-3 if participant.posture == "prone" else -2 if participant.posture == "kneeling" else 0)
        + (-4 if blind else 0)
    )

    if selected == "dodge":
        penalty += defense_height_bonus(runtime, state, participant)
        loaded = inventory_load(
            equipment,
            runtime.resources,
            state.resources,
            participant.actor_id,
            compiled.statistics,
        )
        if loaded.dodge is None:
            raise ValidationError("Overloaded actor cannot dodge")
        _require_rooted_dodge(runtime, state, participant, hp, fp, bonus, blind)
        return DerivedValue(
            "defense:dodge",
            Decimal(
                rooted_dodge(
                    state.resources,
                    participant.actor_id,
                    fatigue_value(
                        fp,
                        impaired_movement(
                            hp, loaded.dodge + haste_bonus(state.resources, participant.actor_id)
                        ),
                    ),
                )
                + bonus
                + penalty
            ),
            (),
        ), None
    candidates: list[tuple[int, str, str]] = []
    incoming_weight = (
        heavy_parry_weight(runtime, state, participant, incoming_item_id, incoming_mode_id)
        if selected == "parry"
        else None
    )
    for item in ready:
        if item_id is not None and item.id != item_id:
            continue

        entry = effective_entry(runtime, item)
        if (
            selected == "block"
            and entry.shield
            and entry.shield.can_block
            and not participant.block_used
            and not any(
                h.replace("hand", "arm") in unavailable
                for h in item_hands(state, participant.actor_id, item.id)
            )
        ):
            value = level(compiled, entry.shield.skill_id)
            candidates.append(
                (
                    int(value.value) // 2 + 3 + defense_height_bonus(runtime, state, participant),
                    item.id,
                    entry.shield.skill_id,
                )
            )
        if selected == "parry":
            for weapon_mode in entry.modes:
                if parry_mode_id is not None and weapon_mode.id != parry_mode_id:
                    continue
                if not isinstance(weapon_mode, MeleeMode) or weapon_mode.parry is None:
                    continue
                try:
                    mode(runtime, state, participant.actor_id, item.id, weapon_mode.id)
                except ValidationError:
                    continue
                if incoming_weight is not None:
                    # B376: the hard limit is BL (twice BL for a two-handed mode).
                    # Three times weapon weight introduces breakage, not a ban.
                    if incoming_weight > compiled.statistics.basic_lift * 1000 * weapon_mode.hands:
                        continue
                    if 0 < 3 * entry.weight_millipounds <= incoming_weight:
                        try:
                            require_breakage(entry, item)
                        except ValidationError:
                            if item_id is not None:
                                raise
                            continue
                parry = weapon_mode.parry
                if (
                    parry.unbalanced
                    and participant.last_maneuver in ATTACK_MANEUVERS
                    and participant.last_attack_item_id == item.id
                ):
                    continue
                _require_rooted_choice(
                    state, participant, selected, shield=entry.shield is not None
                )
                value = level(compiled, weapon_mode.skill_id)
                repeats = participant.parries.count(item.id)
                # Lite permits only one parry with each weapon per turn.
                if repeats and equipment.profile_id == "gurps-lite-4e-2004":
                    continue
                repeat_penalty = (
                    repeats
                    * (2 if parry.fencing else 4)
                    // parry_multiplier(
                        compiled, item.definition_id, weapon_mode.skill_id, weapon_mode.hands
                    )
                )
                candidates.append(
                    (
                        (
                            int(value.value)
                            + melee_weapon_penalty(state.resources, participant.actor_id)
                            - (
                                minimum_strength_penalty(
                                    weapon_mode.minimum_st,
                                    fatigue_value(fp, compiled.statistics.st),
                                )
                                if weapon_mode.minimum_st is not None
                                else 0
                            )
                        )
                        // 2
                        + 3
                        + parry.modifier
                        + fencing_retreat_bonus(state, participant, fencing=parry.fencing)
                        - repeat_penalty
                        + defense_height_bonus(
                            runtime,
                            state,
                            participant,
                            max(
                                actor_reaches(
                                    runtime, state, participant.actor_id, weapon_mode.reach
                                )
                            ),
                        ),
                        item.id,
                        weapon_mode.skill_id,
                    )
                )
    if not candidates:
        raise ValidationError("No available skill/equipment for this active defense")
    score, selected_item, skill = max(candidates)
    return DerivedValue(
        f"defense:{selected}:{skill}", Decimal(score + bonus + penalty), ()
    ), selected_item
