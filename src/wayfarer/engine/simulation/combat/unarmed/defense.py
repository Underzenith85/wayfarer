"""The defenses an unarmed fighter may choose."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, fatigue_ready
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.melee.defense import defense_value
from wayfarer.engine.simulation.combat.spatial import BasicSpatialContext
from wayfarer.engine.simulation.combat.unarmed.fighters import (
    encumbrance_level,
    fighter,
    free_hands,
)
from wayfarer.engine.simulation.combat.unarmed_records import GrappleLocation
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.rules_context import RulesContext


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
    from wayfarer.engine.simulation.health.fright import can_defend

    if not can_defend(state.resources, actor_id):
        raise ValidationError("Fright condition prevents active defense")
    height_bonus = 0
    if encounter.spatial_kind == "hex":
        from wayfarer.engine.simulation.combat.tactical import defense_adjustment, height_effect

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
                reach=1,
                location=location,
                board=runtime.hex_map(encounter),
            ).defender_modifier
    if selected == "dodge":
        if item_id is not None:
            raise ValidationError("Dodge cannot select equipment")
        value, _ = defense_value(runtime, state, actor, "dodge")
        assert value is not None
        return int(value.value) + height_bonus, None
    if selected != "parry" or actor.maneuver_state.parry_forbidden:
        raise ValidationError("Only Dodge or an unarmed Parry is supported")
    if item_id is not None and item_id not in ("left-hand", "right-hand"):
        from wayfarer.engine.simulation.combat.melee.modes import mode
        from wayfarer.engine.simulation.equipment.catalog import MeleeMode

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
        value, selected_item = defense_value(
            runtime, state, actor, "parry", item_id, parry_mode_id=weapon.id
        )
        assert value is not None
        return int(value.value) + height_bonus, selected_item
    hand = item_id or next(iter(free_hands(state, encounter, actor_id)), None)
    if hand not in free_hands(state, encounter, actor_id):
        raise ValidationError("Unarmed parry requires a free usable hand")
    compiled = build(runtime, state, actor_id)
    assert compiled.statistics is not None
    hp = next(p for p in state.resources.pools if p.id == f"hp:{actor_id}")
    if hp.injury is None or hp.injury.incapacitated or not fatigue_ready(state, actor_id):
        raise ValidationError("Incapacitated actor cannot parry")
    targets = parry_candidates(runtime, state, encounter, actor_id)
    from wayfarer.engine.simulation.health.fright import stunned as fright_stunned

    penalty = (
        (-4 if hp.injury.stunned or fright_stunned(state.resources, actor_id) else 0)
        + (-3 if actor.posture == "prone" else -2 if actor.posture == "kneeling" else 0)
        + (-2 if actor.grappled else 0)
    )
    penalty += (
        actor.defense_penalty
        + actor.tactical_defense_bonus
        - 4 * int(actor.arm_locked)
        + (2 if actor.maneuver_state.enhanced_defense == "parry" else 0)
    )
    return max(targets)[0] + int(
        hp.injury.physical_traits.combat_reflexes
    ) + penalty + height_bonus - 4 * actor.parries.count(hand), hand


def parry_candidates(
    runtime: RulesContext, state: PlayState, encounter: Encounter, actor_id: str
) -> list[tuple[int, str]]:
    """Keep the actual automatically selected skill with its defense value.

    Equal-value choices are deterministic. In particular Judo beats an untrained
    DX parry on a tie, permitting its B403 follow-up without inventing a success.

    """
    compiled = build(runtime, state, actor_id)
    assert compiled.statistics is not None
    actor = fighter(encounter, actor_id)
    targets = [(compiled.statistics.dx // 2 + 3, "attribute:dx")]
    incoming_kick = (
        encounter.pending_unarmed is not None and encounter.pending_unarmed.action == "kick"
    )
    for v in compiled.sheet.values:
        if v.target in {"skill:brawling", "skill:boxing", "skill:karate", "skill:judo"}:
            score = int(v.value) // 2 + 3
            score -= 2 if v.target == "skill:boxing" and incoming_kick else 0
            if v.target in ("skill:boxing", "skill:judo", "skill:karate") and (
                encounter.pending_unarmed is not None
                and actor.retreat_attacker_id == encounter.pending_unarmed.actor_id
            ):
                score += 2  # B377: +3 total, including prepare_defense's ordinary +1.
            if v.target in ("skill:judo", "skill:karate"):
                try:
                    score -= encumbrance_level(runtime, state, actor_id)
                except ValidationError:
                    continue
            targets.append((score, v.target))
    return targets
