"""Derived activity lookup and scene ownership; no new clock or persisted mode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record
from wayfarer.simulation.combat import Battlefield, CombatRules, Encounter
from wayfarer.simulation.hex_geometry import HexBattlefield
from wayfarer.simulation.noncombat import NoncombatEncounter
from wayfarer.simulation.party import QueuedActivity, Subgroup
from wayfarer.simulation.scenes import SceneRules

if TYPE_CHECKING:
    from wayfarer.simulation.actions import PlayState


class EncounterSceneBinding(Record):
    encounter_id: Id
    scene_id: Id


@dataclass(frozen=True)
class ActorActivity:
    """Internal context, not a player projection: membership never grants knowledge."""

    scene_id: str | None
    group: Subgroup | None
    encounter: Encounter | None
    group_encounter: Encounter | None
    queued: QueuedActivity | None
    noncombat: tuple[NoncombatEncounter, ...]

    @property
    def spatial_kind(self) -> Literal["square", "hex"] | None:
        if self.encounter is None:
            return None
        return "hex" if self.encounter.hex_battlefield is not None else "square"

    def battlefield(self, rules: CombatRules) -> Battlefield | HexBattlefield | None:
        if self.encounter is None:
            return None
        if self.encounter.hex_battlefield is not None:
            return self.encounter.hex_battlefield
        return next(b for b in rules.battlefields if b.id == self.encounter.battlefield_id)


def activity_for(state: PlayState, actor_id: str) -> ActorActivity:
    if not any(a.actor_id == actor_id for a in state.actors):
        raise ValidationError("Unknown play actor")
    group = next((g for g in state.party.groups if actor_id in g.actor_ids), None)
    encounters = tuple(e for e in state.encounters if e.status == "active")
    return ActorActivity(
        scene_id=next((c.scene_id for c in state.actor_scenes if c.actor_id == actor_id), None),
        group=group,
        encounter=next((e for e in encounters if actor_id in e.turn_order), None),
        group_encounter=next(
            (e for e in encounters if group and set(e.turn_order) & set(group.actor_ids)), None
        ),
        queued=next((q for q in state.party.queue if group and q.group_id == group.id), None),
        noncombat=tuple(
            e for e in state.noncombat if e.actor_id == actor_id and e.status == "choice"
        ),
    )


def bind_scene(
    encounter: Encounter,
    scenes: SceneRules | None,
    combat: CombatRules,
    scene_id: str | None = None,
) -> Encounter:
    """Infer only a unique authored location mapping; never invent legacy scenes."""
    if scenes is None:
        raise ValidationError("Configure scene rules through explicit campaign migration first")
    battlefield = next((b for b in combat.battlefields if b.id == encounter.battlefield_id), None)
    if battlefield is None:
        raise ValidationError("Unknown encounter battlefield")
    candidates = tuple(s.id for s in scenes.scenes if s.location_id == battlefield.location_id)
    if encounter.scene_id is not None:
        if scene_id not in (None, encounter.scene_id):
            raise ValidationError("Encounter scene is already bound")
        scene_id = encounter.scene_id
    if scene_id is None:
        if len(candidates) != 1:
            raise ValidationError("Encounter requires an explicit scene mapping")
        scene_id = candidates[0]
    if scene_id not in candidates:
        raise ValidationError("Encounter scene and battlefield location disagree")
    return encounter.model_copy(update={"version": 2, "scene_id": scene_id})


def migrate_unique(
    state: PlayState, scenes: SceneRules | None, combat: CombatRules | None
) -> PlayState:
    """Read-time structural normalization; historical stored events remain untouched."""
    if scenes is None or combat is None:
        return state
    encounters: list[Encounter] = []
    for encounter in state.encounters:
        if encounter.version == 1:
            battlefield = next(
                (b for b in combat.battlefields if b.id == encounter.battlefield_id), None
            )
            candidates = tuple(
                s for s in scenes.scenes if battlefield and s.location_id == battlefield.location_id
            )
            if len(candidates) == 1:
                encounter = bind_scene(encounter, scenes, combat)
        encounters.append(encounter)
    return state.model_copy(update={"encounters": tuple(encounters)})


def validate_contexts(
    state: PlayState, scenes: SceneRules | None, combat: CombatRules | None
) -> None:
    cursors = {c.actor_id: c.scene_id for c in state.actor_scenes}
    entities = {e.id: e for e in state.world.entities}
    scene_map = {s.id: s for s in scenes.scenes} if scenes else {}
    occupied_actors: set[str] = set()
    occupied_groups: set[str] = set()
    for encounter in state.encounters:
        if (encounter.version == 2) != (encounter.scene_id is not None):
            raise ValidationError("Invalid encounter scene version")
        if encounter.scene_id is not None:
            if combat is None:
                raise ValidationError("Encounter requires combat rules")
            bind_scene(encounter, scenes, combat)
        if encounter.status != "active":
            continue
        participants = set(encounter.turn_order)
        if participants & occupied_actors:
            raise ValidationError("Actor participates in multiple active encounters")
        occupied_actors |= participants
        if scenes is not None:
            actor_scenes = {cursors.get(a) for a in participants}
            if len(actor_scenes) != 1 or None in actor_scenes:
                raise ValidationError("Combat participants must share one scene")
            actual_scene = next(iter(actor_scenes))
            if encounter.scene_id is not None and actual_scene != encounter.scene_id:
                raise ValidationError("Combat participant and encounter scenes disagree")
            scene = scene_map.get(actual_scene or "")
            if scene is None or any(
                entities[a].location_id != scene.location_id for a in participants
            ):
                raise ValidationError("Combat actor scene and world location disagree")
        if state.party.groups:
            groups = tuple(g for g in state.party.groups if participants & set(g.actor_ids))
            if len(groups) != 1 or not participants <= set(groups[0].actor_ids):
                raise ValidationError("Combat participants must share one subgroup")
            if groups[0].id in occupied_groups:
                raise ValidationError("Subgroup participates in multiple active encounters")
            occupied_groups.add(groups[0].id)
