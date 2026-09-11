"""Explicit, command-logged moves of map configuration into CombatRules."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from wayfarer.errors import ValidationError
from wayfarer.models import Campaign
from wayfarer.simulation.action_engine import ActionEngine
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.advancement import MigrationEntry
from wayfarer.simulation.combat import CombatRules
from wayfarer.simulation.combat_commands import MigrateEncounterHex
from wayfarer.simulation.hex_geometry import HexBattlefield
from wayfarer.simulation.scenario_document import digest_json
from wayfarer.simulation.scenario_references import boundary
from wayfarer.simulation.social_policy import parse_graph

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService


def install_rules(campaign: Campaign, play: PlayService, combat: CombatRules) -> PlayService:
    """Retain immutable source provenance while recording the migrated runtime graph."""
    from wayfarer.orchestration.play import PlayService

    engine = ActionEngine(
        play.engine.reviewer,
        play.engine.resources,
        play.engine.rules.model_copy(update={"combat": combat}),
    )
    if encoded := campaign.get("scenario_graph_json"):
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
    else:
        # Pre-scenario typed campaigns retain a CombatRules fragment, never maps on encounters.
        campaign["combat_rules_json"] = combat.model_dump_json()
    return PlayService(play.store, engine, rng=play.rng, profiles=play.profiles)


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
    old = next((b for b in rules.battlefields if b.id == encounter.battlefield_id), None)
    if old is None:
        raise ValidationError("Encounter references an unknown template")
    if command.battlefield.id != old.id:
        raise ValidationError("Map migration must name the current battlefield")
    if command.battlefield.location_id not in ("unbound", old.location_id):
        raise ValidationError("Map migration cannot change the encounter location")
    board = command.battlefield.model_copy(
        update={
            "id": template_id(command.battlefield, old.location_id),
            "location_id": old.location_id,
            "source_template_id": old.id,
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
        reason="Move hex battlefield template into CombatRules",
    )
    state = state.model_copy(
        update={
            "configuration_digest": rebound.engine.digest,
            "migrations": state.migrations + (migration,),
        }
    )
    return rebound, state, command.model_copy(update={"battlefield": board})


def lift_embedded(
    campaign: Campaign,
    play: PlayService,
    *,
    command_id: str,
    actor_id: str,
) -> tuple[PlayService, PlayState]:
    """Upgrade a legacy checkpoint without ever constructing an Encounter with a map."""
    import json

    from wayfarer import validation

    raw = validation.mapping(validation.decode(campaign.get("play_json", "null")))
    rules = play.engine.rules.combat
    if rules is None or raw.get("configuration_digest") != play.engine.digest:
        raise ValidationError("Legacy map migration requires the recorded rules configuration")
    encounters = validation.sequence(raw.get("encounters"))
    templates = list(rules.battlefields)
    lifted = False
    normalized: list[dict[str, object]] = []
    for item in encounters:
        encounter = validation.mapping(item)
        normalized.append(encounter)
        embedded = encounter.pop("hex_battlefield", None)
        if embedded is None:
            continue
        old = next((b for b in templates if b.id == encounter.get("battlefield_id")), None)
        if old is None:
            raise ValidationError("Legacy encounter references an unknown template")
        board = HexBattlefield.model_validate_json(json.dumps(embedded))
        if board.id != old.id or board.location_id not in ("unbound", old.location_id):
            raise ValidationError("Embedded map differs from its recorded location or identity")
        board = board.model_copy(
            update={
                "id": template_id(board, old.location_id),
                "location_id": old.location_id,
                "source_template_id": old.id,
            }
        )
        existing = next((b for b in templates if b.id == board.id), None)
        if existing is not None and existing != board:
            raise ValidationError("Map template ID already has different geometry")
        if existing is None:
            templates.append(board)
        encounter.update({"battlefield_id": board.id, "spatial_kind": "hex"})
        lifted = True
    if not lifted:
        raise ValidationError("Campaign has no embedded maps to migrate")
    rebound = install_rules(
        campaign, play, rules.model_copy(update={"battlefields": tuple(templates)})
    )
    raw["encounters"] = normalized
    raw["configuration_digest"] = rebound.engine.digest
    state = PlayState.model_validate_json(json.dumps(raw))
    entry = MigrationEntry(
        id=command_id,
        actor_id=actor_id,
        revision=state.revision + 1,
        from_digest=play.engine.digest,
        to_digest=rebound.engine.digest,
        reason="Lift legacy embedded hex maps into CombatRules",
    )
    return rebound, state.model_copy(update={"migrations": state.migrations + (entry,)})


async def migrate_embedded_maps(
    play: PlayService,
    cid: str,
    *,
    command_id: str,
    actor_id: str,
    expected_revision: int,
) -> Campaign:
    import json

    from wayfarer.models import CommandReceipt
    from wayfarer.orchestration.entropy import commit_command

    play = play.for_campaign(await play.store.read(cid))
    if actor_id not in play.engine.reviewer.gm_ids:
        raise ValidationError("Map migration requires GM authority")
    payload = json.dumps(
        {
            "operation": "map-template-migration",
            "command": {
                "id": command_id,
                "actor_id": actor_id,
                "expected_revision": expected_revision,
            },
        },
        sort_keys=True,
    )

    def resolve(campaign: Campaign) -> CommandReceipt:
        bound, state = lift_embedded(campaign, play, command_id=command_id, actor_id=actor_id)
        state = state.model_copy(
            update={
                "revision": state.revision + 1,
                "resources": state.resources.model_copy(update={"revision": state.revision + 1}),
            }
        )
        bound.commit(campaign, state)
        return CommandReceipt(action="combat", outcome="Map templates migrated")

    result = await commit_command(
        play.store,
        cid,
        command_id,
        expected_revision,
        payload,
        resolve,
        actor_id=actor_id,
        rng=play.rng,
    )
    return result["state"]
