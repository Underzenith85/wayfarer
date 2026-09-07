"""Execute area fire using shared hazard deadlines and authoritative placements."""

import hashlib
import json

from wayfarer.orchestration.gurps_melee import build
from wayfarer.orchestration.play import PlayService
from wayfarer.rules.hazard_types import HazardSchedule, HazardSpec
from wayfarer.simulation.abilities import damage_resistance
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.combat import CombatEngine, GridPoint
from wayfarer.simulation.hazards import HazardCommand, apply_hazard
from wayfarer.simulation.spells import active_spells

PREFIX = "spell-fire:"


def armor(play: PlayService, state: PlayState, actor_id: str) -> int:
    equipment = play.engine.rules.combat.gurps_equipment if play.engine.rules.combat else None
    dr = 0
    if equipment:
        entries = {e.definition_id: e for e in equipment.entries}
        dr = max(
            (
                entry.armor.dr
                for item in state.resources.items
                if item.owner_id == actor_id
                and item.equipped
                and (item.condition is None or not item.condition.disabled)
                for entry in (entries[item.definition_id],)
                if entry.armor and "torso" in entry.armor.locations
            ),
            default=0,
        )
    compiled = build(play, state, actor_id)
    return dr + damage_resistance(state.resources, actor_id, build_revision=compiled.revision)


def checkpoint(play: PlayService, state: PlayState) -> PlayState:
    """Settle elapsed exposure before ending spells or moving their occupants.

    ResourceEngine prevents crossing an unsettled hazard deadline, so this hook
    never retrospectively estimates damage or banks skipped seconds.
    """
    resources = state.resources
    if not any(h.spec.id.startswith(PREFIX) for h in resources.hazards) and not any(
        e.execute_effects and e.spell_id == "create-fire" for e in active_spells(resources)
    ):
        return state
    for schedule in resources.hazards:
        if (
            schedule.spec.id.startswith(PREFIX)
            and schedule.active
            and schedule.due == resources.game_time
        ):
            current = build(play, state, schedule.actor_id)
            assert current.statistics
            schedule = schedule.model_copy(
                update={
                    "resistance": armor(play, state, schedule.actor_id),
                    "ht": current.statistics.ht,
                    "will": current.statistics.will,
                }
            )
            resources = resources.model_copy(
                update={
                    "hazards": tuple(
                        schedule if h.id == schedule.id else h for h in resources.hazards
                    )
                }
            )
            resources, _ = apply_hazard(
                resources,
                HazardCommand(
                    id="fire-tick:"
                    + hashlib.sha256(f"{schedule.id}:{schedule.cycle}".encode()).hexdigest(),
                    actor_id=schedule.actor_id,
                    expected_revision=resources.revision,
                    kind="resolve",
                    hazard_id=schedule.spec.id,
                ),
                schedule,
                rng=play.rng,
                system=True,
            )
    state = state.model_copy(update={"resources": resources})
    exposed: set[tuple[str, str]] = set()
    for effect in active_spells(resources):
        if (
            not effect.execute_effects
            or effect.spell_id != "create-fire"
            or effect.position is None
        ):
            continue
        encounter = next((e for e in state.encounters if e.id == effect.encounter_id), None)
        if encounter is None or effect.expires_at is None:
            continue
        center = GridPoint(x=effect.position[0], y=effect.position[1])
        entities = {e.id: e for e in state.world.entities}
        for participant in encounter.participants:
            if (
                entities[participant.actor_id].location_id != effect.location_id
                or CombatEngine.distance(center, participant.position) >= effect.radius
            ):
                continue
            actor_id = participant.actor_id
            hazard_id = PREFIX + hashlib.sha256(effect.cast_id.encode()).hexdigest()
            exposed.add((hazard_id, actor_id))
            old = next(
                (
                    h
                    for h in resources.hazards
                    if h.spec.id == hazard_id and h.actor_id == actor_id and h.active
                ),
                None,
            )
            if old:
                continue
            compiled = build(play, state, actor_id)
            assert compiled.statistics
            serial = sum(
                h.spec.id == hazard_id and h.actor_id == actor_id for h in resources.hazards
            )
            sid = (
                "exposure:"
                + hashlib.sha256(json.dumps([actor_id, hazard_id, serial]).encode()).hexdigest()
            )
            spec = HazardSpec(
                id=hazard_id,
                kind="fire",
                scene_id=effect.location_id or "",
                delay=1,
                interval=1,
                cycles=effect.expires_at - resources.game_time,
                damage_dice=1,
                damage_add=-1,
                resistible=False,
                reference="B246/B434",
            )
            schedule = HazardSchedule(
                id=sid,
                actor_id=actor_id,
                spec=spec,
                started=resources.game_time,
                due=resources.game_time + 1,
                remaining=spec.cycles,
                ht=compiled.statistics.ht,
                will=compiled.statistics.will,
                swimming=compiled.statistics.ht,
                resistance=armor(play, state, actor_id),
            )
            resources, _ = apply_hazard(
                resources,
                HazardCommand(
                    id=sid + ":enter",
                    actor_id=actor_id,
                    expected_revision=resources.revision,
                    kind="enter",
                    hazard_id=hazard_id,
                ),
                schedule,
                rng=play.rng,
                system=True,
            )
    resources = resources.model_copy(
        update={
            "revision": state.revision,
            "hazards": tuple(
                h.model_copy(update={"active": False})
                if h.spec.id.startswith(PREFIX) and (h.spec.id, h.actor_id) not in exposed
                else h
                for h in resources.hazards
            ),
        }
    )
    ready = {i.id for i in resources.items if i.ready and i.equipped}
    alive = {
        p.id.removeprefix("hp:")
        for p in resources.pools
        if p.id.startswith("hp:") and (not p.injury.incapacitated if p.injury else p.current > 0)
    }
    encounters = []
    for encounter in state.encounters:
        if encounter.status != "active":
            encounters.append(encounter)
            continue
        encounter = encounter.model_copy(
            update={
                "participants": tuple(
                    p.model_copy(
                        update={"ready_item_ids": tuple(i for i in p.ready_item_ids if i in ready)}
                    )
                    for p in encounter.participants
                )
            }
        )
        if len(alive.intersection(encounter.turn_order)) < 2:
            encounter = encounter.model_copy(
                update={
                    "status": "completed",
                    "completion_reason": "incapacitation",
                    "pending_defense": None,
                    "pending_unarmed": None,
                    "wait_interrupt": None,
                }
            )
        encounters.append(encounter)
    return state.model_copy(update={"resources": resources, "encounters": tuple(encounters)})
