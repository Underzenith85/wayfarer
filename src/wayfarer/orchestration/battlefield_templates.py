"""Explicit, command-logged moves of map configuration into CombatRules."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from wayfarer.contracts import Campaign
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.advancement import MigrationEntry
from wayfarer.engine.simulation.campaign.scenario_document import digest_json
from wayfarer.engine.simulation.campaign.scenario_references import boundary
from wayfarer.engine.simulation.campaign.social_policy import parse_graph
from wayfarer.engine.simulation.combat.commands import MigrateEncounterHex
from wayfarer.engine.simulation.combat.profiles import CombatRules
from wayfarer.engine.simulation.hex_geometry import HexBattlefield
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService


def install_rules(campaign: Campaign, play: PlayService, combat: CombatRules) -> PlayService:
    """Retain immutable source provenance while recording the migrated runtime graph."""
    engine = play.sessions.bind(
        campaign["id"],
        play.engine.reviewer,
        play.engine.resources,
        play.engine.rules.model_copy(update={"combat": combat}),
    )
    encoded = campaign.get("scenario_graph_json")
    if encoded is None:
        # A campaign seeded straight through PlayService.create has no graph to
        # record against, so its migrated configuration lives on the envelope.
        # #636 removes that shape by making creation a stream command.
        campaign["combat_rules_json"] = combat.model_dump_json()
        return play.derived(engine, rng=play.rng, profiles=play.profiles)
    graph = parse_graph(encoded)
    graph = graph.model_copy(
        update={"actions": graph.actions.model_copy(update={"combat": combat})}
    )
    campaign["scenario_graph_json"] = graph.model_dump_json()
    pin = boundary(campaign)
    if pin is not None:
        digest = digest_json(graph.model_dump(mode="json"))
        reference = pin.reference
        if pin.published is None:
            reference = reference.model_copy(
                update={"content_digest": digest, "engine_digest": engine.digest}
            )
        campaign["scenario_reference_json"] = pin.model_copy(
            update={
                "graph_digest": digest,
                "runtime_digest": engine.digest,
                "reference": reference,
            }
        ).model_dump_json()
    return play.derived(engine, rng=play.rng, profiles=play.profiles)


def template_id(board: HexBattlefield, location: str) -> str:
    encoded = board.model_copy(update={"location_id": location}).model_dump_json()
    return "hex-template:" + hashlib.sha256(encoded.encode()).hexdigest()[:24]


def prepare(
    campaign: Campaign,
    play: PlayService,
    state: PlayState,
    command: MigrateEncounterHex,
) -> tuple[PlayService, PlayState, MigrateEncounterHex]:
    rules = play.engine.rules.combat
    if rules is None:
        raise ValidationError("Map migration requires combat rules")
    encounter = next((e for e in state.encounters if e.id == command.encounter_id), None)
    if encounter is None:
        raise ValidationError("Unknown encounter")
    if encounter.spatial_kind == "basic":
        scenes = play.engine.rules.scenes
        scene = (
            next((s for s in scenes.scenes if s.id == encounter.scene_id), None) if scenes else None
        )
        if scene is None:
            raise ValidationError("Basic map escalation requires its authored scene")
        location_id = scene.location_id
        source_template_id = None
        if command.battlefield.source_template_id is not None:
            raise ValidationError("Basic map escalation has no source battlefield template")
    else:
        old = next((b for b in rules.battlefields if b.id == encounter.battlefield_id), None)
        if old is None:
            raise ValidationError("Encounter references an unknown template")
        if command.battlefield.id != old.id:
            raise ValidationError("Map migration must name the current battlefield")
        location_id = old.location_id
        source_template_id = old.id
    if command.battlefield.location_id not in ("unbound", location_id):
        raise ValidationError("Map migration cannot change the encounter location")
    board = command.battlefield.model_copy(
        update={
            "id": template_id(command.battlefield, location_id),
            "location_id": location_id,
            "source_template_id": source_template_id,
        }
    )
    existing = next((b for b in rules.battlefields if b.id == board.id), None)
    if existing is not None and existing != board:
        raise ValidationError("Map template ID already has different geometry")
    combat = rules.model_copy(
        update={"battlefields": rules.battlefields + (() if existing else (board,))}
    )
    rebound = install_rules(campaign, play, combat)
    migration = MigrationEntry(
        id=command.id,
        actor_id=command.actor_id,
        revision=state.revision + 1,
        from_digest=play.engine.digest,
        to_digest=rebound.engine.digest,
        reason="Escalate encounter to an owned hex battlefield template",
    )
    state = state.model_copy(
        update={
            "configuration_digest": rebound.engine.digest,
            "migrations": state.migrations + (migration,),
        }
    )
    return rebound, state, command.model_copy(update={"battlefield": board})
