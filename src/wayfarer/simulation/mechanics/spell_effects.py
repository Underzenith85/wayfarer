"""Execute area fire using shared hazard deadlines and authoritative placements."""

import hashlib
import json

from wayfarer.errors import ValidationError
from wayfarer.rules.hazard_types import HazardSchedule, HazardSpec
from wayfarer.simulation.abilities import damage_resistance
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.combat import CombatEngine, GridPoint
from wayfarer.simulation.hazards import HazardCommand, apply_hazard
from wayfarer.simulation.hex_geometry import Hex
from wayfarer.simulation.mechanics.gurps_melee import build
from wayfarer.simulation.rules_context import RulesContext
from wayfarer.simulation.spells import active_spells

PREFIX = "spell-fire:"


def armor(
    runtime: RulesContext, state: PlayState, actor_id: str, *, large_area: bool = False
) -> int:
    equipment = runtime.rules.combat.gurps_equipment if runtime.rules.combat else None
    dr = 0
    if equipment:
        entries = {e.definition_id: e for e in equipment.entries}
        worn = tuple(
            entry.armor
            for item in state.resources.items
            if item.owner_id == actor_id
            and item.equipped
            and (item.condition is None or not item.condition.disabled)
            for entry in (entries[item.definition_id],)
            if entry.armor
        )
        dr = max((a.dr for a in worn if "torso" in a.locations), default=0)
        if large_area:
            # B400: torso plus least-protected exposed location, rounded up.
            locations = ("torso", "skull", "face", "neck", "arms", "hands", "legs", "feet")
            least = min(
                max((a.dr for a in worn if location in a.locations), default=0)
                for location in locations
            )
            dr = (dr + least + 1) // 2
    compiled = build(runtime, state, actor_id)
    return dr + damage_resistance(state.resources, actor_id, build_revision=compiled.revision)


def checkpoint(runtime: RulesContext, state: PlayState) -> PlayState:
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
            current = build(runtime, state, schedule.actor_id)
            assert current.statistics
            schedule = schedule.model_copy(
                update={
                    "resistance": armor(runtime, state, schedule.actor_id, large_area=True),
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
                rng=runtime.rng,
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
        center = (
            Hex(q=effect.position[0], r=effect.position[1])
            if effect.geometry == "hex"
            else GridPoint(x=effect.position[0], y=effect.position[1])
        )
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
            compiled = build(runtime, state, actor_id)
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
                reference="B246/B433",
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
                resistance=armor(runtime, state, actor_id, large_area=True),
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
                rng=runtime.rng,
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


def crossings(
    runtime: RulesContext,
    state: PlayState,
    before: PlayState,
    actor_id: str,
    encounter_id: str,
    path: tuple[Hex, ...],
    command_id: str,
) -> PlayState:
    """B433 partial-turn flame contact, including paths ending outside the area."""
    old_encounter = next(e for e in before.encounters if e.id == encounter_id)
    fires = tuple(
        e
        for e in active_spells(before.resources)
        if e.execute_effects
        and e.execution_version == 2
        and e.spell_id == "create-fire"
        and e.encounter_id == encounter_id
    )
    if not fires:
        return state
    if old_encounter.spatial_kind == "basic":
        raise ValidationError("Basic movement through an area spell requires GM adjudication")
    new_encounter = next(e for e in state.encounters if e.id == encounter_id)
    origin = next(p.position for p in old_encounter.participants if p.actor_id == actor_id)
    destination = next(p.position for p in new_encounter.participants if p.actor_id == actor_id)
    if not path and origin == destination:
        return state
    points = (origin,) + path if path else (origin, destination)
    resources = state.resources
    compiled = build(runtime, state, actor_id)
    assert compiled.statistics
    hp = next(p for p in resources.pools if p.id == "hp:" + actor_id)
    assert hp.injury
    for effect in fires:
        if (
            not effect.execute_effects
            or effect.spell_id != "create-fire"
            or effect.encounter_id != encounter_id
            or effect.position is None
        ):
            continue
        center = (
            Hex(q=effect.position[0], r=effect.position[1])
            if effect.geometry == "hex"
            else GridPoint(x=effect.position[0], y=effect.position[1])
        )
        if not any(CombatEngine.distance(center, point) < effect.radius for point in points):
            continue
        sid = (
            "spell-crossing:"
            + hashlib.sha256(f"{effect.cast_id}:{actor_id}:{hp.injury.turn}".encode()).hexdigest()
        )
        if any(h.id == sid for h in resources.hazards):
            continue
        spec = HazardSpec(
            id=sid,
            kind="fire",
            scene_id=effect.location_id or "",
            delay=0,
            interval=1,
            cycles=1,
            damage_dice=1,
            damage_add=-3,
            resistible=False,
            reference="B433/B400",
        )
        schedule = HazardSchedule(
            id=sid,
            actor_id=actor_id,
            spec=spec,
            started=resources.game_time,
            due=resources.game_time,
            remaining=1,
            ht=compiled.statistics.ht,
            will=compiled.statistics.will,
            swimming=compiled.statistics.ht,
            resistance=armor(runtime, state, actor_id, large_area=True),
        )
        for kind in ("enter", "resolve"):
            resources, _ = apply_hazard(
                resources,
                HazardCommand(
                    id=sid + ":" + kind,
                    actor_id=actor_id,
                    expected_revision=resources.revision,
                    kind=kind,
                    hazard_id=sid,
                ),
                schedule,
                rng=runtime.rng,
                system=True,
            )
    return state.model_copy(
        update={"resources": resources.model_copy(update={"revision": state.revision})}
    )
