"""Stress equipment in use and synchronize its availability with the encounter."""

import hashlib

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import catalog
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.equipment_entry import weapon_target
from wayfarer.engine.simulation.equipment.objects import StressObject, apply_object
from wayfarer.engine.simulation.rules_context import RulesContext


def synchronize(state: PlayState, encounter: Encounter) -> Encounter:
    return encounter.model_copy(
        update={
            "participants": tuple(
                p.model_copy(
                    update={
                        "ready_item_ids": tuple(
                            i.id
                            for i in state.resources.items
                            if i.owner_id == p.actor_id and i.ready and i.equipped
                        ),
                        "hand_bindings": tuple(
                            (i, h)
                            for i, h in p.hand_bindings
                            if any(
                                x.id == i and x.owner_id == p.actor_id and x.ready and x.equipped
                                for x in state.resources.items
                            )
                        ),
                    }
                )
                for p in encounter.participants
            )
        }
    )


def stress(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    actor_id: str,
    command_id: str,
    item_ids: tuple[str, ...],
) -> tuple[PlayState, Encounter]:
    """Only equipment actually used by this action; idle equipment never rolls."""
    for item_id in dict.fromkeys(item_ids):
        item = next(i for i in state.resources.items if i.id == item_id)
        condition = item.condition
        if (
            condition is None
            or condition.disabled
            or condition.hp > 0
            or condition.last_stress_at == state.resources.game_time
        ):
            continue
        resources, _ = apply_object(
            runtime.resources,
            state.resources,
            StressObject(
                id="combat-stress:"
                + hashlib.sha256(f"{command_id}:{item_id}".encode()).hexdigest(),
                actor_id=actor_id,
                expected_revision=state.resources.revision,
                item_id=item_id,
            ),
            system=True,
            rng=runtime.rng,
        )
        state = state.model_copy(update={"resources": resources})
    return state, synchronize(state, encounter)


def defense_stress(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    actor_id: str,
    command_id: str,
    item_id: str | None,
) -> tuple[PlayState, Encounter]:
    """Stress the selected implement and shields that contribute defense bonus."""

    entries = {e.definition_id: e for e in catalog(runtime).entries}
    pending = encounter.pending_defense
    targeted_weapon = weapon_target(runtime, state, pending.target_item_id if pending else None)
    items = tuple(
        i.id
        for i in state.resources.items
        if i.owner_id == actor_id
        and i.equipped
        and i.ready
        and (i.id == item_id or (not targeted_weapon and entries[i.definition_id].shield))
    )
    return stress(runtime, state, encounter, actor_id, command_id, items)


def worn_stress(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    actor_id: str,
    command_id: str,
) -> tuple[PlayState, Encounter]:
    """Worn protection is in use even while its wearer does not attack."""

    armor = {e.definition_id for e in catalog(runtime).entries if e.armor is not None}
    return stress(
        runtime,
        state,
        encounter,
        actor_id,
        command_id,
        tuple(
            i.id
            for i in state.resources.items
            if i.owner_id == actor_id and i.equipped and i.definition_id in armor
        ),
    )
