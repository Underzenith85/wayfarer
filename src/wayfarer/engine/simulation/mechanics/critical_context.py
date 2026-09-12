"""Capture immutable context before a critical consequence pauses an encounter."""

import hashlib

from wayfarer.engine.rules.location_types import HumanLocation
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat import Encounter, GridPoint
from wayfarer.engine.simulation.critical import (
    CriticalMiss,
    CriticalWeapon,
    IncomingWound,
    save_critical,
)
from wayfarer.engine.simulation.gurps_equipment import MeleeMode
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def capture_critical(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    *,
    tables: tuple[tuple[int, ...], ...],
    defender_item: str | None,
    incoming: IncomingWound | None,
    defender_mode_id: str | None = None,
) -> PlayState:
    pending = encounter.pending_defense
    rules = runtime.rules.combat
    assert pending is not None and rules is not None and rules.gurps_equipment is not None
    equipment = rules.gurps_equipment
    parrying = (encounter.blocked_reason or "").endswith(":defender")
    actor_id = pending.defender_id if parrying else pending.attacker_id
    item_id = defender_item if parrying else pending.weapon_id
    if item_id is None:
        raise ValidationError("Critical miss has no canonical weapon")
    actor = next(a for a in state.actors if a.actor_id == actor_id)
    compiled, _ = runtime.reviewer.activate(
        actor.proposal, actor.approval, campaign_id=state.campaign_id, actor_id=actor_id
    )
    assert compiled.statistics is not None
    statistics = compiled.statistics
    item = next(i for i in state.resources.items if i.id == item_id)
    from wayfarer.engine.simulation.mechanics.object_combat import effective_entry

    entry = effective_entry(runtime, item)
    modes = tuple(
        m
        for m in entry.modes
        if isinstance(m, MeleeMode)
        and (
            (
                parrying
                and m.parry is not None
                and (defender_mode_id is None or m.id == defender_mode_id)
            )
            or (not parrying and m.id == pending.mode_id)
        )
    )
    weapons = tuple(
        CriticalWeapon(
            mode=m,
            dice=m.damage.dice
            or (statistics.swing.dice if m.damage.basis == "swing" else statistics.thrust.dice),
            adds=m.damage.adds
            + (
                0
                if m.damage.basis == "fixed"
                else statistics.swing.add
                if m.damage.basis == "swing"
                else statistics.thrust.add
            ),
        )
        for m in modes
    )
    participant = next(p for p in encounter.participants if p.actor_id == actor_id)
    hp = next(p for p in state.resources.pools if p.id == f"hp:{actor_id}")
    armor = tuple(
        e.armor
        for i in state.resources.items
        if i.owner_id == actor_id
        and i.equipped
        and (i.condition is None or not i.condition.disabled)
        for e in equipment.entries
        if e.definition_id == i.definition_id and e.armor is not None
    )
    dr_bonus = 0
    if runtime.rules.abilities is not None:
        from wayfarer.engine.simulation.abilities import damage_resistance

        dr_bonus = damage_resistance(state.resources, actor_id, build_revision=compiled.revision)
    limbs: tuple[HumanLocation, ...] = ("left-arm", "right-arm", "left-leg", "right-leg")
    record = CriticalMiss.model_validate(
        {
            "id": hashlib.sha256(pending.id.encode()).hexdigest(),
            "encounter_id": encounter.id,
            "subject_id": actor_id,
            "item_id": item_id,
            "action": "parry" if parrying else "attack",
            "table_rolls": tables,
            "weapons": weapons,
            "build_revision": compiled.revision,
            "catalog_digest": hashlib.sha256(equipment.model_dump_json().encode()).hexdigest(),
            "ht": statistics.ht,
            "created_at": state.resources.game_time,
            "position": (participant.position.x, participant.position.y)
            if isinstance(participant.position, GridPoint)
            else None,
            "hex_position": (participant.position.q, participant.position.r)
            if isinstance(participant.position, Hex)
            else None,
            "hex_facing": participant.hex_facing,
            "facing": participant.facing,
            "anatomy": hp.injury.anatomy if hp.injury else None,
            "limb_dr": tuple(
                (
                    limb,
                    dr_bonus
                    + max(
                        (
                            a.dr
                            for a in armor
                            if a
                            and (
                                limb in a.locations
                                or ("arms" if limb.endswith("arm") else "legs") in a.locations
                            )
                        ),
                        default=0,
                    ),
                )
                for limb in limbs
            ),
            "hand_bindings": participant.hand_bindings,
            "held_item_ids": tuple(
                i.id
                for i in state.resources.items
                if i.id in participant.ready_item_ids
                and any(
                    e.definition_id == i.definition_id and (e.modes or e.shield)
                    for e in equipment.entries
                )
            ),
            "incoming": incoming,
            "blocker": encounter.blocked_reason,
        }
    )
    return state.model_copy(update={"resources": save_critical(state.resources, record)})
