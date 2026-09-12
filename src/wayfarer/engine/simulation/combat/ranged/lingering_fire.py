"""Fire a shot leaves burning behind it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import Encounter, PendingDefense
from wayfarer.engine.simulation.equipment.catalog import RangedMode

if TYPE_CHECKING:
    from wayfarer.engine.simulation.rules_context import RulesContext


def _schedule_lingering_fire(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    weapon: RangedMode,
    *,
    pending: PendingDefense,
    target_id: str,
    basic_damage: int,
    hit: bool,
) -> PlayState:
    """Bind a B433 clothing fire to this scene and the shared hazard clock."""
    if not hit or weapon.sprayer is None or not weapon.sprayer.ignites or basic_damage < 3:
        return state
    assert encounter.scene_id is not None
    import hashlib
    import json

    from wayfarer.engine.rules.types.hazard import HazardSchedule, HazardSpec
    from wayfarer.engine.simulation.actors import build
    from wayfarer.engine.simulation.health.hazards import HazardCommand, apply_hazard
    from wayfarer.engine.simulation.magic.area_fire import armor

    source = (
        "sprayer-fire:"
        + hashlib.sha256(
            json.dumps(
                [encounter.id, pending.attacker_id, pending.weapon_id, weapon.id, target_id],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    if any(
        hazard.active and hazard.spec.id == source and hazard.actor_id == target_id
        for hazard in state.resources.hazards
    ):
        return state
    serial = sum(
        hazard.spec.id == source and hazard.actor_id == target_id
        for hazard in state.resources.hazards
    )
    schedule_id = (
        "exposure:"
        + hashlib.sha256(
            json.dumps([source, target_id, serial], separators=(",", ":")).encode()
        ).hexdigest()
    )
    compiled = build(runtime, state, target_id)
    assert compiled.statistics is not None
    spec = HazardSpec(
        id=source,
        kind="fire",
        scene_id=encounter.scene_id,
        delay=1,
        interval=1,
        # Fire remains until an authoritative leave/extinguish action ends it.
        # The large bound prevents an unbounded persisted schedule.
        cycles=100000,
        damage_dice=1,
        damage_add=-1 if basic_damage >= 10 else -4,
        resistible=False,
        reference="Basic Set B433/B400",
    )
    schedule = HazardSchedule(
        id=schedule_id,
        actor_id=target_id,
        spec=spec,
        started=state.resources.game_time,
        due=state.resources.game_time + 1,
        remaining=spec.cycles,
        ht=compiled.statistics.ht,
        will=compiled.statistics.will,
        swimming=compiled.statistics.ht,
        resistance=armor(runtime, state, target_id, large_area=True),
        full_hp=compiled.statistics.hp,
    )
    resources, _ = apply_hazard(
        state.resources,
        HazardCommand(
            id=schedule_id + ":enter",
            actor_id=target_id,
            expected_revision=state.resources.revision,
            kind="enter",
            hazard_id=source,
        ),
        schedule,
        rng=runtime.rng,
        system=True,
    )
    return state.model_copy(update={"resources": resources})
