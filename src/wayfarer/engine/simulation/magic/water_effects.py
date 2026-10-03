"""B253 material consequences for an admitted successful water spell.

The lifecycle owns rolls, fatigue, elapsed time, approval and authority. This
module does not independently cast, charge energy, or consume randomness.
"""

import hashlib
from math import ceil, hypot
from typing import Literal

from pydantic import Field, model_validator

from wayfarer.engine.rules.magic.protocols import long_distance_modifier
from wayfarer.engine.simulation.magic.water_complete_source import (
    emptied_source,
    require_complete_source,
)
from wayfarer.engine.simulation.magic.water_mist_state import MistMaterial
from wayfarer.engine.simulation.magic.water_mist_state import record as record_mist
from wayfarer.engine.simulation.magic.water_parcels import drain, protect_material, selected
from wayfarer.engine.simulation.magic.water_purification import receiving_mixture
from wayfarer.engine.simulation.magic.water_state import WaterBody, latest, save
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

WaterSpell = Literal["seek-water", "purify-water", "create-water", "destroy-water"]
DISCOVERY_PREFIX = "water-discovery:"


class WaterPlan(Record):
    spell_id: WaterSpell
    target_id: Id
    source_id: Id | None = None
    gallons: int = Field(default=1, ge=1)
    # Purify's supported ordinary continuous stream; faster large-ring handling
    # remains an explicit boundary instead of an invented rate.
    seconds_per_gallon: int = Field(default=5, ge=5, le=10)
    flowing_through_ring: bool = False
    origin: tuple[int, int] = (0, 0)
    excluded_source_ids: tuple[Id, ...] = ()
    forked_stick: bool = True
    # Destroy's exact selected authored portions. The adapter must derive these
    # from actual area geometry, not accept a player's list of objects.
    destroyed_ids: tuple[Id, ...] = ()
    radius: int = Field(default=1, ge=1)
    depth_yards: int = Field(default=2, ge=1, le=2)
    area_center: tuple[int, int] = (0, 0)
    # Explicit private operation intent; absent historical plans keep their bytes.
    allow_receiver_mixing: bool = Field(default=False, exclude_if=lambda value: not value)
    purify_entire_source: bool = Field(default=False, exclude_if=lambda value: not value)
    mist_scene_id: Id | None = Field(default=None, exclude_if=lambda value: value is None)

    parcel_flow_id: Id | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def unique_sources(self) -> WaterPlan:
        if len(set(self.excluded_source_ids)) != len(self.excluded_source_ids):
            raise ValueError("Excluded water sources must be unique")
        if len(set(self.destroyed_ids)) != len(self.destroyed_ids):
            raise ValueError("Destroyed water portions must be unique")
        if self.allow_receiver_mixing and self.spell_id not in ("create-water", "purify-water"):
            raise ValueError("Receiver mixing is limited to incoming pure-water flows")
        if self.purify_entire_source and self.spell_id != "purify-water":
            raise ValueError("Complete-source intent is limited to Purify Water")
        if self.mist_scene_id is not None and (
            self.spell_id != "create-water" or self.gallons != 1 or self.allow_receiver_mixing
        ):
            raise ValueError("Mist supports exactly one gallon of Create Water")
        if self.parcel_flow_id is not None and (
            self.spell_id != "purify-water" or self.purify_entire_source
        ):
            raise ValueError("Separate-container flow is limited to partial Purify Water")
        return self


class WaterDiscovery(Record):
    actor_id: Id
    source_id: Id | None
    direction: tuple[int, int] | None
    distance_yards: int | None
    nature: str | None


def nearest(state: ResourceState, plan: WaterPlan) -> WaterBody | None:
    bodies = latest(state)
    # Exclusion only identifies sources; the host checks current caster knowledge.
    candidates = [
        body
        for body in bodies.values()
        if body.significant and body.gallons > 0 and body.object_id not in plan.excluded_source_ids
    ]
    return min(
        candidates,
        key=lambda body: (
            hypot(body.position[0] - plan.origin[0], body.position[1] - plan.origin[1]),
            body.object_id,
        ),
        default=None,
    )


def parameters(state: ResourceState, plan: WaterPlan) -> tuple[int, int, int]:
    """Raw (energy, casting seconds, skill modifier), before B236-239 reductions."""
    if plan.spell_id == "seek-water":
        body = nearest(state, plan)
        distance = (
            ceil(hypot(body.position[0] - plan.origin[0], body.position[1] - plan.origin[1]))
            if body
            else 0
        )
        return 2, 1, long_distance_modifier(distance) - (0 if plan.forked_stick else 3)
    if plan.spell_id == "purify-water":
        return plan.gallons, plan.gallons * plan.seconds_per_gallon, 0
    if plan.spell_id == "create-water":
        return 2 * plan.gallons, 1, 0
    return 3 * plan.radius, 1, 0


def validate_operation(state: ResourceState, plan: WaterPlan) -> None:
    protect_material(
        state,
        plan.parcel_flow_id,
        ()
        if plan.spell_id == "seek-water"
        else (plan.source_id, plan.target_id, *plan.destroyed_ids),
    )
    selected(
        state,
        plan.parcel_flow_id,
        plan.source_id,
        plan.target_id,
        plan.gallons,
        plan.seconds_per_gallon,
    )
    bodies = latest(state)
    if plan.spell_id == "seek-water":
        if any(source not in bodies for source in plan.excluded_source_ids):
            raise ValidationError("Seek Water exclusion requires an authored source")
        return
    target = bodies.get(plan.target_id)
    if target is None:
        raise ValidationError("Water operation requires its current authored target")
    if plan.mist_scene_id is not None:
        if target.form != "liquid" or target.gallons or target.capacity_gallons is not None:
            raise ValidationError("Mist requires an empty authored uncontained droplet receiver")
        return
    if plan.spell_id in ("purify-water", "create-water"):
        if target.capacity_gallons is None or target.form != "liquid":
            raise ValidationError("Supported water transfer requires a liquid receiving container")
        if target.gallons + plan.gallons > target.capacity_gallons:
            raise ValidationError("Receiving container lacks capacity")
        # Do not claim a mixed vessel is pure after adding pure water to dirty water.
        if target.pure_gallons != target.gallons and not plan.allow_receiver_mixing:
            raise ValidationError("Receiving container contains impure water")
    if plan.spell_id == "purify-water":
        source = bodies.get(plan.source_id or "")
        if source is None or source.object_id == target.object_id or source.form != "liquid":
            raise ValidationError("Purify Water requires a separate current liquid source")
        if source.gallons < plan.gallons or not plan.flowing_through_ring:
            raise ValidationError("Purify Water requires sufficient water flowing through a ring")
        if plan.purify_entire_source:
            require_complete_source(source, plan.gallons)
    if plan.spell_id == "destroy-water":
        _validate_destroy(bodies, target, plan)


def _validate_destroy(bodies: dict[str, WaterBody], target: WaterBody, plan: WaterPlan) -> None:
    if not plan.destroyed_ids or plan.target_id not in plan.destroyed_ids:
        raise ValidationError("Destroy Water requires its authored area portions")
    if any(body_id not in bodies for body_id in plan.destroyed_ids):
        raise ValidationError("Destroy Water area water is missing")
    selected = {
        body.object_id
        for body in bodies.values()
        if body.location_id == target.location_id
        and body.gallons > 0
        and max(
            abs(body.position[0] - plan.area_center[0]),
            abs(body.position[1] - plan.area_center[1]),
        )
        < plan.radius
    }
    if set(plan.destroyed_ids) != selected:
        raise ValidationError("Destroy Water must cover all authored water in its area")
    for body_id in plan.destroyed_ids:
        body = bodies[body_id]
        if body.location_id != target.location_id:
            raise ValidationError("Destroy Water portions must occupy the selected location")
        if (
            max(
                abs(body.position[0] - plan.area_center[0]),
                abs(body.position[1] - plan.area_center[1]),
            )
            >= plan.radius
        ):
            raise ValidationError("Water portion is outside the supported square area")
        if body.depth_yards > plan.depth_yards or body.surrounding_water:
            raise ValidationError("Deep or refilling water requires a bounded flow adapter")


def apply(state: ResourceState, plan: WaterPlan, actor_id: str, command_id: str) -> ResourceState:
    """Apply exactly once through the host's atomic receipt/revision transaction."""
    validate_operation(state, plan)
    bodies = latest(state)
    if plan.mist_scene_id is not None:
        return record_mist(
            state,
            MistMaterial(
                command_id=command_id,
                actor_id=actor_id,
                scene_id=plan.mist_scene_id,
                carrier_id=plan.target_id,
                position=bodies[plan.target_id].position,
            ),
        )
    if plan.spell_id == "seek-water":
        body = nearest(state, plan)
        vector = (
            (body.position[0] - plan.origin[0], body.position[1] - plan.origin[1]) if body else None
        )
        result = WaterDiscovery(
            actor_id=actor_id,
            source_id=body.object_id if body else None,
            direction=vector,
            distance_yards=ceil(hypot(*vector)) if vector else None,
            nature=body.nature if body else None,
        )
        return state.model_copy(
            update={
                "events": state.events
                + (
                    ResourceEvent(
                        id=DISCOVERY_PREFIX + hashlib.sha256(command_id.encode()).hexdigest(),
                        at=state.game_time,
                        target_id=actor_id,
                        kind=result.model_dump_json(),
                    ),
                )
            }
        )
    target = bodies[plan.target_id]
    if plan.spell_id == "destroy-water":
        for body_id in plan.destroyed_ids:
            body = bodies[body_id]
            state = save(
                state, body.model_copy(update={"gallons": 0, "pure_gallons": 0}), command_id
            )
        return state
    if plan.spell_id == "purify-water":
        source = bodies[plan.source_id or ""]
        # This boundary permits unmixed pure or impure sources; a mixed source
        # requires an authored flow composition rather than inventing an order.
        if (
            source.pure_gallons not in (0, source.gallons)
            and not plan.purify_entire_source
            and plan.parcel_flow_id is None
        ):
            raise ValidationError("Mixed source requires an authored flow composition")
        state, measured = drain(
            state,
            plan.parcel_flow_id,
            source,
            plan.target_id,
            plan.gallons,
            plan.seconds_per_gallon,
            command_id,
        )
        state = save(
            state,
            measured
            if measured is not None
            else emptied_source(source, plan.gallons)
            if plan.purify_entire_source
            else source.model_copy(
                update={
                    "gallons": source.gallons - plan.gallons,
                    "pure_gallons": max(0, source.pure_gallons - plan.gallons),
                }
            ),
            command_id,
        )
    if plan.allow_receiver_mixing:
        return save(state, receiving_mixture(target, plan.gallons), command_id)
    return save(
        state,
        target.model_copy(
            update={
                "gallons": target.gallons + plan.gallons,
                "pure_gallons": target.pure_gallons + plan.gallons,
            }
        ),
        command_id,
    )
