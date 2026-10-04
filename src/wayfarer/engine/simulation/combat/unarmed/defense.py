"""The defenses an unarmed fighter may choose."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from wayfarer.engine.character.traits.mastery import trained_by_master
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog, fatigue_ready
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.melee.modes import mode
from wayfarer.engine.simulation.combat.melee.values import standard_defense_value
from wayfarer.engine.simulation.combat.spatial import BasicSpatialContext
from wayfarer.engine.simulation.combat.tactical import defense_adjustment, height_effect
from wayfarer.engine.simulation.combat.unarmed.fighters import (
    encumbrance_level,
    fighter,
    free_hands,
)
from wayfarer.engine.simulation.combat.unarmed.random_strike import random_strike
from wayfarer.engine.simulation.combat.unarmed.records import GrappleLocation
from wayfarer.engine.simulation.combat.unarmed.senses import visibility
from wayfarer.engine.simulation.combat.visibility import external_defense_penalty
from wayfarer.engine.simulation.equipment.catalog import MeleeMode
from wayfarer.engine.simulation.health.fright_state import can_defend
from wayfarer.engine.simulation.health.fright_state import stunned as fright_stunned
from wayfarer.engine.simulation.health.symptom_state import acute_blindness
from wayfarer.engine.simulation.magic.rooted_feet_state import active_effect
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.combat.commands import ChooseDefense
    from wayfarer.engine.simulation.rules_context import RulesContext


def _require_barehand_classification(state: PlayState, actor_id: str) -> None:
    if active_effect(state.resources, actor_id) is not None:
        raise ValidationError("Rooted Feet barehand Parry classification is unsupported")


def _require_rooted_dodge_modifiers(
    state: PlayState, actor_id: str, height_bonus: int, external_penalty: int
) -> None:
    if active_effect(state.resources, actor_id) is not None and (
        height_bonus != 0 or external_penalty != 0
    ):
        raise ValidationError("Rooted Feet unarmed Dodge composition is unsupported")


def unarmed_defense(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    actor_id: str,
    selected: str,
    item_id: str | None,
    attacker_id: str | None = None,
    location: GrappleLocation = "torso",
    mode_id: str | None = None,
    *,
    incoming_melee: MeleeMode | None = None,
) -> tuple[int | None, str | None]:
    actor = fighter(encounter, actor_id)
    if mode_id is not None and (
        selected != "parry" or item_id in (None, "left-hand", "right-hand")
    ):
        raise ValidationError("A parry damage mode requires a selected weapon")
    if selected == "none":
        if item_id is not None:
            raise ValidationError("No defense cannot select equipment")
        return None, None
    if actor.pinned or actor.maneuver_state.defense_forbidden:
        raise ValidationError("Actor cannot defend")

    if not can_defend(state.resources, actor_id):
        raise ValidationError("Fright condition prevents active defense")
    source_id = encounter.pending_unarmed.actor_id if encounter.pending_unarmed else attacker_id
    sensory_penalty, external_penalty = _sensory_penalties(
        state, encounter, actor_id, selected, source_id
    )
    height_bonus = 0
    if encounter.spatial_kind == "hex":
        source_id = (
            encounter.pending_unarmed.actor_id
            if encounter.pending_unarmed is not None
            else attacker_id
        )
        if source_id is not None:
            attacker = fighter(encounter, source_id)
            defense_adjustment(encounter, attacker, actor)
            height_bonus = height_effect(
                encounter,
                attacker,
                actor,
                reach=max(incoming_melee.reach) if incoming_melee is not None else 1,
                location=location,
                board=runtime.hex_map(encounter),
            ).defender_modifier
    if selected == "dodge":
        if item_id is not None:
            raise ValidationError("Dodge cannot select equipment")
        _require_rooted_dodge_modifiers(state, actor_id, height_bonus, external_penalty)
        value, _ = standard_defense_value(runtime, state, actor, "dodge")
        assert value is not None
        return int(value.value) + height_bonus + external_penalty, None
    if selected != "parry" or actor.maneuver_state.parry_forbidden:
        raise ValidationError("Only Dodge or an unarmed Parry is supported")
    if item_id is not None and item_id not in ("left-hand", "right-hand"):
        weapon = mode(runtime, state, actor_id, item_id, mode_id)
        if not isinstance(weapon, MeleeMode):
            raise ValidationError("Armed parry requires an unambiguous melee mode")
        source_id = encounter.pending_unarmed.actor_id if encounter.pending_unarmed else attacker_id
        if (
            source_id is not None
            and (
                tuple(sorted((source_id, actor.actor_id))) in encounter.close_pairs
                if isinstance(encounter.spatial, BasicSpatialContext)
                else fighter(encounter, source_id).position == actor.position
            )
            and 0 not in weapon.reach
        ):
            raise ValidationError("A weapon parry in close combat requires reach C")
        value, selected_item = standard_defense_value(
            runtime, state, actor, "parry", item_id, parry_mode_id=weapon.id
        )
        assert value is not None
        return int(value.value) + height_bonus + external_penalty, selected_item
    _require_barehand_classification(state, actor_id)
    hand = item_id or next(iter(free_hands(state, encounter, actor_id)), None)
    if hand not in free_hands(state, encounter, actor_id):
        raise ValidationError("Unarmed parry requires a free usable hand")
    compiled = build(runtime, state, actor_id)
    assert compiled.statistics is not None
    hp = next(p for p in state.resources.pools if p.id == f"hp:{actor_id}")
    if hp.injury is None or hp.injury.incapacitated or not fatigue_ready(state, actor_id):
        raise ValidationError("Incapacitated actor cannot parry")
    targets = parry_candidates(runtime, state, encounter, actor_id, incoming_melee=incoming_melee)

    penalty = (
        (-4 if hp.injury.stunned or fright_stunned(state.resources, actor_id) else 0)
        + (-3 if actor.posture == "prone" else -2 if actor.posture == "kneeling" else 0)
        + (-2 if actor.grappled else 0)
    )
    penalty += sensory_penalty
    penalty += (
        actor.defense_penalty
        + actor.tactical_defense_bonus
        - 4 * int(actor.arm_locked)
        + (2 if actor.maneuver_state.enhanced_defense == "parry" else 0)
    )
    return max(targets)[0] + int(
        hp.injury.physical_traits.combat_reflexes
    ) + penalty + height_bonus - (2 if trained_by_master(compiled) else 4) * actor.parries.count(
        hand
    ), hand


def parry_candidates(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    actor_id: str,
    *,
    incoming_melee: MeleeMode | None = None,
) -> list[tuple[int, str]]:
    """Keep the actual automatically selected skill with its defense value.

    Equal-value choices are deterministic. In particular Judo beats an untrained
    DX parry on a tie, permitting its B403 follow-up without inventing a success.

    """
    actor = fighter(encounter, actor_id)
    compiled = build(runtime, state, actor_id, defensive=True)
    assert compiled.statistics is not None
    weapon_penalty = (
        3 if incoming_melee is not None and incoming_melee.damage.basis != "thrust" else 0
    )
    targets = [(compiled.statistics.dx // 2 + 3 - weapon_penalty, "attribute:dx")]
    incoming_kick = (
        encounter.pending_unarmed is not None and encounter.pending_unarmed.action == "kick"
    )
    for v in compiled.sheet.values:
        if v.target in {"skill:brawling", "skill:boxing", "skill:karate", "skill:judo"}:
            score = int(v.value) // 2 + 3
            if v.target not in ("skill:judo", "skill:karate"):
                score -= weapon_penalty
            score -= 2 if v.target == "skill:boxing" and incoming_kick else 0
            if v.target in ("skill:boxing", "skill:judo", "skill:karate") and (
                (
                    encounter.pending_unarmed is not None
                    and actor.retreat_attacker_id == encounter.pending_unarmed.actor_id
                )
                or (
                    incoming_melee is not None
                    and encounter.pending_defense is not None
                    and actor.retreat_attacker_id == encounter.pending_defense.attacker_id
                )
            ):
                score += 2  # B377: +3 total, including prepare_defense's ordinary +1.
            if v.target in ("skill:judo", "skill:karate"):
                try:
                    score -= encumbrance_level(runtime, state, actor_id)
                except ValidationError:
                    continue
            targets.append((score, v.target))
    return targets


def sensory_encounter(state: PlayState, encounter: Encounter) -> Encounter:
    """Use current transaction-entry proof through its own valid retreat.

    A previously committed move is already present here and fails the evidence
    scope. Defense preparation may then move this local encounter atomically.
    """
    original = next((e for e in state.encounters if e.id == encounter.id), None)
    if (
        original is not None
        and encounter.pending_unarmed is not None
        and (
            original.pending_unarmed is not None
            and original.pending_unarmed.id == encounter.pending_unarmed.id
        )
    ):
        return original
    return encounter


def allowed_defenses(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    attacker_id: str,
    target_id: str,
    location: GrappleLocation,
    *,
    choke_hold: bool = False,
) -> tuple[Literal["dodge", "parry", "none"], ...]:
    allowed: list[Literal["dodge", "parry", "none"]] = ["none"]
    choices: tuple[Literal["dodge", "parry"], ...] = () if choke_hold else ("dodge", "parry")
    for choice in choices:
        try:
            unarmed_defense(
                runtime,
                state,
                encounter,
                target_id,
                choice,
                None,
                attacker_id=attacker_id,
                location=location,
            )
        except ValidationError:
            if choice != "parry":
                continue
            candidates = (
                (i.id, m.id)
                for i in state.resources.items
                if i.id in fighter(encounter, target_id).ready_item_ids
                for e in catalog(runtime).entries
                if e.definition_id == i.definition_id
                for m in e.modes
            )
            for item, selected_mode in candidates:
                try:
                    unarmed_defense(
                        runtime,
                        state,
                        encounter,
                        target_id,
                        choice,
                        item,
                        attacker_id=attacker_id,
                        location=location,
                        mode_id=selected_mode,
                    )
                except ValidationError:
                    continue
                break
            else:
                continue
        allowed.insert(0, choice)
    return tuple(allowed)


def refresh_unarmed_senses(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    command: ChooseDefense,
    *,
    attack_captured: bool = False,
) -> Encounter:
    """Validate both live choices before defensive movement, exertion or dice."""
    pending = encounter.pending_unarmed
    if pending is None:
        return encounter
    if command.actor_id != pending.target_id:
        raise ValidationError("Defense is not authorized for this unarmed attack")
    visibility(
        state, encounter, pending.actor_id, pending.target_id, validate_attack=not attack_captured
    )
    if (
        not attack_captured
        and acute_blindness(state.resources, pending.actor_id)
        and random_strike(state, encounter.id, pending.id, pending.actor_id, pending.target_id)
        is None
    ):
        raise ValidationError("Blind unarmed attacks require a private random strike")
    allowed = allowed_defenses(
        runtime,
        state,
        encounter,
        pending.actor_id,
        pending.target_id,
        pending.location,
        choke_hold=pending.choke_hold,
    )
    if command.defense not in allowed or (
        command.second_defense is not None and command.second_defense not in allowed
    ):
        raise ValidationError("Current sensory awareness does not permit that defense")
    for choice, item, selected_mode in (
        (command.defense, command.item_id, command.parry_mode_id),
        (command.second_defense, command.second_item_id, command.second_parry_mode_id),
    ):
        if choice is not None:
            unarmed_defense(
                runtime,
                state,
                encounter,
                command.actor_id,
                choice,
                item,
                location=pending.location,
                mode_id=selected_mode,
            )
    return encounter.model_copy(
        update={"pending_unarmed": pending.model_copy(update={"allowed": allowed})}
    )


def _sensory_penalties(
    state: PlayState,
    encounter: Encounter,
    actor_id: str,
    selected: str,
    source_id: str | None,
) -> tuple[int, int]:
    """Validate awareness and distinguish bare-hand from already-scored penalties."""
    sensory_penalty = -4 if acute_blindness(state.resources, actor_id) else 0
    external_penalty = 0
    if source_id is not None:
        sensory = visibility(
            state,
            sensory_encounter(state, encounter),
            source_id,
            actor_id,
            validate_attack=False,
        )
        if selected not in sensory.defenses:
            raise ValidationError("Current sensory awareness does not permit that defense")
        external_penalty = external_defense_penalty(state, actor_id, sensory.defense_penalty)
        sensory_penalty = (
            -4 if acute_blindness(state.resources, actor_id) else sensory.defense_penalty
        )
    return sensory_penalty, external_penalty
