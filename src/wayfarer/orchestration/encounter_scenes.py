"""Explicit GM scene binding for ambiguous legacy encounter checkpoints."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import Field

from wayfarer.errors import ValidationError
from wayfarer.models import Campaign, Event
from wayfarer.orchestration.play import PlayService
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.encounter_context import EncounterSceneBinding, bind_scene
from wayfarer.simulation.resources import Id, Record


class MigrateEncounterScenes(Record):
    kind: Literal["migrate_encounter_scenes"] = "migrate_encounter_scenes"
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)
    bindings: tuple[EncounterSceneBinding, ...] = Field(min_length=1, max_length=100)


class EncounterSceneService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    async def execute(
        self, cid: str, command: MigrateEncounterScenes, *, authenticated_gm_id: str
    ) -> PlayState:
        bound = self.play.for_campaign(await self.play.store.read(cid))
        if bound is not self.play:
            return await EncounterSceneService(bound).execute(
                cid, command, authenticated_gm_id=authenticated_gm_id
            )
        if (
            command.actor_id != authenticated_gm_id
            or authenticated_gm_id not in self.play.engine.reviewer.gm_ids
        ):
            raise ValidationError("Encounter scene migration requires GM authority")
        payload = json.dumps(
            {"operation": "encounter-scenes", "command": command.model_dump(mode="json")},
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> Event:
            state = self.play._load(campaign)
            bindings = {b.encounter_id: b.scene_id for b in command.bindings}
            if len(bindings) != len(command.bindings) or not set(bindings) <= {
                e.id for e in state.encounters
            }:
                raise ValidationError("Migration requires unique existing encounter IDs")
            combat = self.play.engine.rules.combat
            if combat is None:
                raise ValidationError("Campaign has no combat configuration")
            encounters = tuple(
                bind_scene(e, self.play.engine.rules.scenes, combat, bindings[e.id])
                if e.id in bindings
                else e
                for e in state.encounters
            )
            revision = state.revision + 1
            updated = state.model_copy(
                update={
                    "encounters": encounters,
                    "revision": revision,
                    "resources": state.resources.model_copy(update={"revision": revision}),
                }
            )
            # Structural only: no checkpoint, clocks, dice, discovery or effects.
            self.play.engine.validate(updated)
            campaign["revision"], campaign["play_json"] = revision, updated.model_dump_json()
            return Event(
                input=payload,
                action="encounter-scenes",
                outcome=command.model_dump_json(),
                roll=None,
            )

        committed = await self.play.store.commit_turn(
            cid,
            command.id,
            command.expected_revision,
            payload,
            resolve,
            actor_id=authenticated_gm_id,
        )
        return self.play._load(committed["state"])
