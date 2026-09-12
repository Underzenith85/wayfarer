"""Scoring an active defense and checking the choices offered."""

from __future__ import annotations

from decimal import Decimal

from wayfarer.engine.rules.effects import DerivedValue
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import exertion
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.equipment_effects import defense_stress, worn_stress
from wayfarer.engine.simulation.combat.melee.modes import mode
from wayfarer.engine.simulation.combat.melee.values import defense_selection, score_defense
from wayfarer.engine.simulation.combat.unarmed.defense import unarmed_defense
from wayfarer.engine.simulation.combat.vocabulary import Defense
from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def defense_value(
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
    if selected == "none":
        return None, None
    item_id, targeted_weapon = defense_selection(runtime, state, participant, selected, item_id)
    if selected == "parry" and item_id in ("left-hand", "right-hand"):
        encounter = next(
            (
                e
                for e in state.encounters
                if e.pending_defense and e.pending_defense.defender_id == participant.actor_id
            ),
            None,
        )
        if encounter is None or encounter.pending_defense is None:
            raise ValidationError("Barehanded projectile parry requires a pending throw")
        pending = encounter.pending_defense
        incoming = mode(runtime, state, pending.attacker_id, pending.weapon_id, pending.mode_id)
        if not isinstance(incoming, RangedMode) or not incoming.catchable:
            raise ValidationError("Barehanded projectile parry requires an opted-in thrown mode")
        encounter = CombatEngine._replace(encounter, participant)
        bare_value, hand = unarmed_defense(
            runtime,
            state,
            encounter,
            participant.actor_id,
            selected,
            item_id,
            mode_id=parry_mode_id,
        )
        assert bare_value is not None
        return DerivedValue("defense:parry", Decimal(bare_value), ()), hand
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


def exert_defense(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    actor_id: str,
    command_id: str,
    selected: Defense,
    item_id: str | None,
    *,
    parry_mode_id: str | None = None,
) -> tuple[PlayState, Encounter, Defense]:
    """Spend the defender's effort and equipment; either failing leaves no active defense.

    B426: a defender whose exertion fails is too spent to defend and takes the blow.
    Worn protection is stressed whether or not they defend. The implement chosen
    for a parry or block is stressed by that use, and one that breaks under it
    defends with nothing. A hand is not an implement and never breaks here.
    """

    if selected != "none":
        state, allowed = exertion(runtime, state, actor_id, command_id)
        if not allowed:
            selected = "none"
    state, encounter = worn_stress(runtime, state, encounter, actor_id, command_id)
    if selected == "none":
        return state, encounter, "none"
    participant = next(p for p in encounter.participants if p.actor_id == actor_id)
    _, used = defense_value(
        runtime, state, participant, selected, item_id, parry_mode_id=parry_mode_id
    )
    bare = used in ("left-hand", "right-hand")
    state, encounter = defense_stress(
        runtime, state, encounter, actor_id, command_id, None if bare else used
    )
    if used and not bare and not any(i.id == used and i.ready for i in state.resources.items):
        return state, encounter, "none"
    return state, encounter, selected


def validate_defense_choices(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    selected: Defense,
    item_id: str | None,
    second_defense: Defense | None,
    second_item_id: str | None,
    *,
    parry_mode_id: str | None = None,
    second_parry_mode_id: str | None = None,
) -> None:
    pending = encounter.pending_defense
    if pending is None:
        raise ValidationError("No attack awaits defense")
    if selected not in pending.allowed or (
        second_defense is not None and second_defense not in pending.allowed
    ):
        raise ValidationError("Defense is not available against this attack")
    defender = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    if (parry_mode_id is not None and selected != "parry") or (
        second_parry_mode_id is not None and second_defense != "parry"
    ):
        raise ValidationError("Parry damage mode requires the corresponding Parry defense")
    _, first_item = defense_value(
        runtime, state, defender, selected, item_id, parry_mode_id=parry_mode_id
    )
    if second_defense is None:
        if second_item_id is not None:
            raise ValidationError("Second defense equipment requires a second defense")
        return
    if (
        selected == "none"
        or second_defense == "none"
        or defender.maneuver_state.enhanced_defense != "double"
    ):
        raise ValidationError("Second defense requires All-Out Defense (Double)")
    _, second_item = defense_value(
        runtime, state, defender, second_defense, second_item_id, parry_mode_id=second_parry_mode_id
    )
    if selected == second_defense and not (selected == "parry" and first_item != second_item):
        raise ValidationError(
            "Double defense requires different defenses or different parrying hands"
        )
