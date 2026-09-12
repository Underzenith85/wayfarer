"""Lossless hex-to-Basic conversion contracts for #329."""

from pathlib import Path

import pytest
from test_reinforcements import escalation, setup_profiled_basic

from wayfarer.engine.rules.types.object import GroundPosition
from wayfarer.engine.simulation.combat.encounter import PendingDefense
from wayfarer.engine.simulation.combat.spatial import (
    BasicSpatialContext,
    HexSpatialContext,
    ReachSpatialFact,
)
from wayfarer.engine.simulation.combat.tactical_transitions import migrate_basic
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.errors import ValidationError
from wayfarer.models import Campaign, CommandReceipt
from wayfarer.orchestration.combat import CombatService, MigrateEncounterBasic
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.tactical_view import project
from wayfarer.transport.tactical_api import enrich


def convert(revision: int, *, command_id: str = "to-basic") -> MigrateEncounterBasic:
    return MigrateEncounterBasic(
        id=command_id,
        actor_id="gm",
        expected_revision=revision,
        encounter_id="fight",
    )


async def setup_hex(tmp_path: Path, distance: int = 2) -> tuple[str, PlayService]:
    cid, play = await setup_profiled_basic(tmp_path, distance)
    await CombatService(play).execute(
        cid,
        escalation(revision=2, b_position=Hex(q=distance, r=0)),
        authenticated_actor_id="gm",
    )
    return cid, play.for_campaign(await play.store.read(cid))


async def test_hex_basic_round_trip_preserves_nonspatial_state_and_replay(tmp_path: Path) -> None:
    cid, play = await setup_hex(tmp_path)
    service = CombatService(play)
    state = play._load(await play.store.read(cid))
    before = state.encounters[0]
    original_board = play.rules_context.require_hex(before)

    command = convert(3)
    first = await service.execute(cid, command, authenticated_actor_id="gm")
    restarted = CombatService(PlayService(play.store, play.engine))
    assert await restarted.execute(cid, command, authenticated_actor_id="gm") == first
    converted = play._load(await play.store.read(cid)).encounters[0]
    assert isinstance(converted.spatial, BasicSpatialContext)
    assert len(converted.spatial.facts) == 11
    assert (
        converted.model_copy(
            update={"spatial_context": before.spatial_context, "participants": before.participants}
        )
        == before
    )
    assert all(
        actor.runtime_position is None and actor.hex_facing is None
        for actor in converted.participants
    )

    back = escalation(revision=4).model_copy(
        update={
            "id": "back-to-hex",
            "battlefield": original_board.model_copy(
                update={"id": "roundtrip", "location_id": "unbound", "source_template_id": None}
            ),
        }
    )
    await service.execute(cid, back, authenticated_actor_id="gm")
    play = play.for_campaign(await play.store.read(cid))
    restored = play._load(await play.store.read(cid)).encounters[0]
    assert tuple(actor.position for actor in restored.participants) == tuple(
        actor.position for actor in before.participants
    )
    assert restored.current_actor_id == before.current_actor_id
    assert restored.round == before.round
    assert await play.store.replay(cid) == await play.store.read(cid)


async def test_pending_defense_survives_without_refresh(tmp_path: Path) -> None:
    cid, play = await setup_hex(tmp_path, distance=1)
    state = play._load(await play.store.read(cid))
    pending = PendingDefense(
        id="pending-hit",
        attacker_id="a",
        defender_id="b",
        weapon_id="sword-a",
        mode_id="swing",
        allowed=("dodge", "none"),
        opened_round=state.encounters[0].round,
        opened_turn=state.encounters[0].turn_index,
    )

    def pause(campaign: Campaign) -> CommandReceipt:
        before = play._load(campaign)
        revision = before.revision + 1
        encounter = before.encounters[0].model_copy(update={"pending_defense": pending})
        play.commit(
            campaign,
            before.model_copy(
                update={
                    "revision": revision,
                    "resources": before.resources.model_copy(update={"revision": revision}),
                    "encounters": (encounter,),
                }
            ),
        )
        return CommandReceipt(action="setup", outcome="pending-defense")

    await play.store.commit_turn(cid, "seed-pending", 3, "seed-pending", pause)
    await CombatService(play).execute(cid, convert(4), authenticated_actor_id="gm")
    converted = play._load(await play.store.read(cid)).encounters[0]
    assert converted.pending_defense == pending
    assert converted.participants == tuple(
        actor.model_copy(update={"position": None, "hex_facing": None})
        for actor in state.encounters[0].participants
    )
    assert await play.store.replay(cid) == await play.store.read(cid)


async def test_same_hex_close_pair_remains_close(tmp_path: Path) -> None:
    cid, play = await setup_hex(tmp_path, distance=1)
    state = play._load(await play.store.read(cid))
    encounter = state.encounters[0]
    spatial = encounter.spatial
    assert isinstance(spatial, HexSpatialContext)
    position = encounter.participants[0].position
    assert isinstance(position, Hex)
    participants = tuple(
        actor.model_copy(update={"position": position}) if actor.actor_id == "b" else actor
        for actor in encounter.participants
    )
    placements = tuple(
        placement.model_copy(update={"position": position})
        if placement.actor_id == "b"
        else placement
        for placement in spatial.placements
    )
    encounter = encounter.model_copy(
        update={
            "participants": participants,
            "spatial_context": spatial.model_copy(update={"placements": placements}),
            "close_pairs": (("a", "b"),),
        }
    )
    converted = migrate_basic(play.rules_context, state, encounter, convert(3))
    assert isinstance(converted.spatial, BasicSpatialContext)
    reach = converted.spatial.active("reach", "a", "b")
    assert isinstance(reach, ReachSpatialFact)
    assert reach.relation == "close"
    assert converted.close_pairs == (("a", "b"),)


async def test_consequential_terrain_blocks_and_gm_projection_offers_control(
    tmp_path: Path,
) -> None:
    cid, play = await setup_hex(tmp_path)
    state = play._load(await play.store.read(cid))
    member = next(member for member in state.members if member.principal_id == "gm")
    snapshot = project(play, state, member, "a", include_object_choices=True)
    assert enrich(play, state, snapshot, member).migrations[0].command == convert(
        3, command_id="basic:3:fight"
    )
    with pytest.raises(ValidationError, match="GM authority"):
        await CombatService(play).execute(
            cid,
            convert(3).model_copy(update={"actor_id": "a"}),
            authenticated_actor_id="a",
        )
    grounded = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": tuple(
                        item.model_copy(
                            update={
                                "ground": GroundPosition(
                                    encounter_id="fight", geometry="hex", x=0, y=0
                                )
                            }
                        )
                        if item.id == "sword-a"
                        else item
                        for item in state.resources.items
                    )
                }
            )
        }
    )
    with pytest.raises(ValidationError, match="Grounded equipment"):
        migrate_basic(play.rules_context, grounded, grounded.encounters[0], convert(3))

    terrain_path = tmp_path / "terrain"
    terrain_path.mkdir()
    cid, play = await setup_profiled_basic(terrain_path, 2)
    migrate = escalation(revision=2)
    cells = tuple(
        cell.model_copy(update={"extra_cost": 1}) if cell.position.q == 3 else cell
        for cell in migrate.battlefield.cells
    )
    await CombatService(play).execute(
        cid,
        migrate.model_copy(
            update={"battlefield": migrate.battlefield.model_copy(update={"cells": cells})}
        ),
        authenticated_actor_id="gm",
    )
    play = play.for_campaign(await play.store.read(cid))
    with pytest.raises(ValidationError, match="terrain"):
        await CombatService(play).execute(cid, convert(3), authenticated_actor_id="gm")
