"""Private closed square-scene admission and B253 one-gallon mist extinction."""

import hashlib
from typing import Literal

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.magic.spell_state import RuntimeSpellEvent as SpellEvent
from wayfarer.engine.simulation.magic.spell_state import SpellResult, event_id
from wayfarer.engine.simulation.magic.spells import active_spells
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PREFIX = "water-scene:"


class MistPosition(Record):
    entity_id: Id
    point: tuple[int, int]


class MistSource(Record):
    hazard_id: Id
    cells: tuple[tuple[int, int], ...]


class MistItemFootprint(Record):
    item_id: Id
    definition_id: Id
    owner_id: Id
    cells: tuple[tuple[int, int], ...]


class MistScene(Record):
    id: Id
    location_id: Id
    positions: tuple[MistPosition, ...]
    sources: tuple[MistSource, ...] = ()
    item_footprints: tuple[MistItemFootprint, ...] = ()


class FireCarrier(Record):
    kind: Literal["item", "hazard", "spell"]
    id: Id
    cells: tuple[tuple[int, int], ...]


class AdmittedMist(Record):
    scene: MistScene
    inventory: tuple[FireCarrier, ...]


class DeclareWaterScene(Command):
    kind: Literal["declare-water-scene"] = "declare-water-scene"
    scene: MistScene


def scenes(resources: ResourceState) -> tuple[AdmittedMist, ...]:
    return tuple(
        AdmittedMist.model_validate_json(e.kind)
        for e in resources.events
        if e.id.startswith(PREFIX)
    )


def _positions(state: PlayState, scene: MistScene) -> dict[str, tuple[int, int]]:
    entities = {e.id: e for e in state.world.entities}
    placed = {p.entity_id: p.point for p in scene.positions}
    occupants = {e.id for e in entities.values() if e.location_id == scene.location_id}
    if len(placed) != len(scene.positions) or set(placed) != occupants:
        raise ValidationError("Mist scene requires exact positions for all current occupants")
    if any(
        e.status == "active" and any(p.actor_id in occupants for p in e.participants)
        for e in state.encounters
    ):
        raise ValidationError("Mist scene does not support an active combat placement")
    for encounter in state.encounters:
        for participant in encounter.participants:
            if participant.actor_id not in occupants:
                continue
            point = participant.position
            if not isinstance(point, GridPoint) or placed[participant.actor_id] != (
                point.x,
                point.y,
            ):
                raise ValidationError("Mist placement disagrees with canonical encounter geometry")
    return placed


def _item_fires(
    state: PlayState, scene: MistScene, placed: dict[str, tuple[int, int]]
) -> list[FireCarrier]:
    entities = {e.id: e for e in state.world.entities}
    found: list[FireCarrier] = []
    footprints = {p.item_id: p for p in scene.item_footprints}
    if len(footprints) != len(scene.item_footprints):
        raise ValidationError("Mist object footprints must be unique")
    if any(i.condition is not None and i.condition.burning for i in state.resources.expended_items):
        raise ValidationError("Mist cannot resolve a burning expended-item carrier")
    burning_items: set[str] = set()
    for item in state.resources.items:
        if item.condition is None or not item.condition.burning:
            continue
        owner = entities.get(item.owner_id)
        location = item.world_ground_location_id or (owner.location_id if owner else None)
        if location is None:
            raise ValidationError("Burning item has no current canonical scene")
        if location != scene.location_id:
            continue
        if item.ground is not None or item.container_id is not None:
            raise ValidationError("Mist cannot admit ambiguous ground or contained fire")
        key = item.id if item.world_ground_location_id else item.owner_id
        if key not in placed:
            raise ValidationError("Burning item requires its exact scene position")
        footprint = footprints.get(item.id)
        if (
            footprint is None
            or (footprint.definition_id, footprint.owner_id) != (item.definition_id, item.owner_id)
            or footprint.cells != (placed[key],)
            or item.quantity != 1
        ):
            raise ValidationError(
                "Burning item requires its complete current single-cell footprint"
            )
        burning_items.add(item.id)
        found.append(FireCarrier(kind="item", id=item.id, cells=footprint.cells))
    if set(footprints) != burning_items:
        raise ValidationError("Mist object footprint is stale or outside its admitted scene")
    return found


def _spell_fires(state: PlayState, scene: MistScene) -> list[FireCarrier]:
    found: list[FireCarrier] = []
    for effect in active_spells(state.resources):
        if effect.spell_id not in ("create-fire", "fireball"):
            continue
        if effect.location_id is None:
            raise ValidationError("Fire spell has no current canonical scene")
        if effect.location_id != scene.location_id:
            continue
        if effect.spell_id != "create-fire" or not effect.execute_effects:
            raise ValidationError("Mist does not support this fire spell carrier")
        if effect.position is None or effect.geometry != "square" or effect.radius != 1:
            raise ValidationError("Mist requires an exact single-cell square fire footprint")
        if (
            effect.area is not None
            and effect.area.cells
            and effect.area.cells != (effect.position,)
        ):
            raise ValidationError("Mist does not support partial multi-cell fire extinction")
        found.append(FireCarrier(kind="spell", id=effect.cast_id, cells=(effect.position,)))
    return found


def _hazard_fires(state: PlayState, scene: MistScene) -> list[FireCarrier]:
    entities = {e.id: e for e in state.world.entities}
    found: list[FireCarrier] = []
    sources = {p.hazard_id: p.cells for p in scene.sources}
    if len(sources) != len(scene.sources):
        raise ValidationError("Mist fire sources must be unique")
    ids: set[str] = set()
    for hazard in state.resources.hazards:
        if not hazard.active or hazard.spec.kind != "fire":
            continue
        if hazard.spec.scene_id != scene.location_id:
            # An encounter identifier is not a world scene identifier.
            if hazard.spec.scene_id not in entities:
                raise ValidationError("Fire hazard requires a canonical world scene")
            continue
        if hazard.spec.id not in sources or hazard.combat_turn is not None:
            raise ValidationError("Every fire hazard requires a supported exact source placement")
        if len(sources[hazard.spec.id]) != 1:
            raise ValidationError("Mist only admits a complete single-cell fire source footprint")
        if hazard.spec.id.startswith("sprayer-fire:"):
            raise ValidationError("Mist does not support a moving sprayer clothing-fire source")
        if hazard.spec.id.startswith("spell-fire:"):
            source_effect = next(
                (
                    e
                    for e in active_spells(state.resources)
                    if hazard.spec.id
                    == "spell-fire:" + hashlib.sha256(e.cast_id.encode()).hexdigest()
                    and e.spell_id == "create-fire"
                ),
                None,
            )
            if source_effect is None or sources[hazard.spec.id] != (source_effect.position,):
                raise ValidationError(
                    "Fire exposure placement disagrees with its actual source spell"
                )
        ids.add(hazard.spec.id)
        found.append(FireCarrier(kind="hazard", id=hazard.id, cells=sources[hazard.spec.id]))
    if set(sources) != ids:
        raise ValidationError("Mist scene contains stale or unauthored fire sources")
    return found


def inventory(state: PlayState, scene: MistScene) -> tuple[FireCarrier, ...]:
    """Reject unknown positions/carriers rather than treating them as outside."""
    placed = _positions(state, scene)
    found = (
        _item_fires(state, scene, placed) + _spell_fires(state, scene) + _hazard_fires(state, scene)
    )
    return tuple(sorted(found, key=lambda f: (f.kind, f.id)))


def declare(state: PlayState, command: DeclareWaterScene) -> ResourceState:
    if any(s.scene.id == command.scene.id for s in scenes(state.resources)):
        raise ConflictError("Mist scene admissions cannot be replaced")
    admission = AdmittedMist(scene=command.scene, inventory=inventory(state, command.scene))
    return state.resources.model_copy(
        update={
            "events": state.resources.events
            + (
                ResourceEvent(
                    id=PREFIX + hashlib.sha256(command.id.encode()).hexdigest(),
                    at=state.resources.game_time,
                    target_id=command.actor_id,
                    kind=admission.model_dump_json(),
                ),
            )
        }
    )


def require_scene(state: PlayState, scene_id: str, location: str) -> AdmittedMist:
    admission = next((s for s in scenes(state.resources) if s.scene.id == scene_id), None)
    if admission is None or admission.scene.location_id != location:
        raise ValidationError("Mist requires its declared current scene")
    current = next(s for s in reversed(scenes(state.resources)) if s.scene.location_id == location)
    if current != admission:
        raise ValidationError("Mist scene admission is no longer current")
    if inventory(state, admission.scene) != admission.inventory:
        raise ValidationError("Mist fire inventory or placement changed after admission")
    if any(
        h.active
        and h.spec.kind == "fire"
        and h.spec.scene_id == location
        and h.due <= state.resources.game_time
        for h in state.resources.hazards
    ):
        raise ConflictError("Resolve due fire exposure before mist casting")
    return admission


def extinguish(
    state: PlayState, admission: AdmittedMist, center: tuple[int, int], command_id: str
) -> PlayState:
    """The existing square radius-one convention covers exactly its center cell."""
    selected = {(f.kind, f.id) for f in admission.inventory if f.cells == (center,)}
    resources = state.resources
    effects = {e.cast_id: e for e in active_spells(resources)}
    for carrier in admission.inventory:
        if carrier.kind != "spell" or ("spell", carrier.id) not in selected:
            continue
        effect = effects[carrier.id]
        resources = resources.model_copy(
            update={
                "events": resources.events
                + (
                    ResourceEvent(
                        id=event_id(command_id + ":mist:" + effect.cast_id),
                        at=resources.game_time,
                        target_id=effect.target_id,
                        kind=SpellEvent(
                            effect=effect.model_copy(update={"phase": "ended"}),
                            result=SpellResult(outcome="cancelled"),
                        ).model_dump_json(),
                    ),
                )
            }
        )
    if any(
        h.active and ("hazard", h.id) in selected and h.due <= resources.game_time
        for h in resources.hazards
    ):
        raise ConflictError("Resolve due fire exposure before mist extinction")
    resources = resources.model_copy(
        update={
            "items": tuple(
                i.model_copy(
                    update={"condition": i.condition.model_copy(update={"burning": False})}
                )
                if ("item", i.id) in selected and i.condition is not None
                else i
                for i in resources.items
            ),
            "hazards": tuple(
                h.model_copy(update={"active": False}) if ("hazard", h.id) in selected else h
                for h in resources.hazards
            ),
        }
    )
    return state.model_copy(update={"resources": resources})
