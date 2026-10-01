"""Current physical defense eligibility, independent of sensory permission."""

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import catalog
from wayfarer.engine.simulation.combat.close_combat import opponents_in_close_combat
from wayfarer.engine.simulation.combat.encounter import Encounter, PendingDefense
from wayfarer.engine.simulation.combat.equipment_entry import weapon_target
from wayfarer.engine.simulation.combat.melee.defense import defense_value
from wayfarer.engine.simulation.combat.tactical import defense_adjustment
from wayfarer.engine.simulation.combat.unarmed.defense import unarmed_defense
from wayfarer.engine.simulation.combat.vocabulary import Defense
from wayfarer.engine.simulation.equipment.catalog import MeleeMode, RangedMode
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def physical_defenses(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    pending: PendingDefense,
    incoming: MeleeMode | RangedMode | None,
) -> tuple[Defense, ...]:
    """Recompute rather than widening a stale cached list after sight recovers."""
    attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    defender = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    target = pending.target_item_id
    if target and next(i for i in state.resources.items if i.id == target).ground:
        return ("none",)
    targeting_weapon = weapon_target(runtime, state, target)
    defender_close = bool(opponents_in_close_combat(encounter, defender.actor_id))
    ranged = isinstance(incoming, RangedMode)
    if isinstance(incoming, RangedMode) and (
        pending.area_aim_point is not None or incoming.mount and incoming.mount.indirect
    ):
        return ("none",)
    candidates: tuple[Defense, ...] = (
        ("dodge", "block")
        if pending.spell_cast_id is not None
        else ("dodge", "block", "parry")
        if ranged
        else ("dodge", "parry", "block")
    )
    allowed: list[Defense] = ["none"]
    for candidate in candidates:
        if candidate == "block" and (
            targeting_weapon or defender_close and not pending.shield_rush
        ):
            continue
        if isinstance(incoming, RangedMode):
            if candidate == "parry" and (
                not incoming.thrown or catalog(runtime).profile_id != "gurps-basic-set-4e-2004"
            ):
                continue
            if candidate == "block" and not (incoming.thrown or incoming.blockable):
                continue
        try:
            if not pending.shield_rush and pending.spell_cast_id is None:
                defense_adjustment(
                    encounter, attacker, defender, approach=pending.tactical_approach
                )
            defense_value(
                runtime,
                state,
                defender,
                candidate,
                target if targeting_weapon and candidate == "parry" else None,
                incoming_item_id=pending.weapon_id if isinstance(incoming, MeleeMode) else None,
                incoming_mode_id=incoming.id if isinstance(incoming, MeleeMode) else None,
            )
        except ValidationError:
            if (
                candidate != "parry"
                or not isinstance(incoming, RangedMode)
                or not incoming.catchable
                or targeting_weapon
            ):
                continue
            try:
                unarmed_defense(runtime, state, encounter, defender.actor_id, "parry", None)
            except ValidationError:
                continue
        allowed.append(candidate)
    return tuple(allowed)
