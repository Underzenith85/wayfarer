"""Authenticated tactical-v2 Basic combat workflow contracts for #327."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiohttp
from aiohttp import web
from test_basic_combat import provenance, start_basic
from test_encounter_context import setup
from test_reinforcements import board, reinforcement_facts, setup_profiled_basic

from wayfarer.models import Record
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.combat import (
    BasicJoinPlacement,
    DeclareBasicSpatialFacts,
    HexPlacement,
    JoinEncounter,
    MigrateEncounterHex,
)
from wayfarer.orchestration.equipment_view import TacticalSnapshotV2
from wayfarer.simulation.combat import VisibilitySpatialFact
from wayfarer.simulation.hex_geometry import Hex, Pose
from wayfarer.transport.campaign_api import create_campaign_app


@asynccontextmanager
async def api(
    tmp_path: Path, *, profiled: bool = False, distance: int = 2
) -> AsyncIterator[tuple[str, str]]:
    cid, play = (
        await setup_profiled_basic(tmp_path, distance) if profiled else await setup(tmp_path)
    )
    principals = (
        {"alice-token": "a", "bob-token": "b", "charlie-token": "c", "gm-token": "gm"}
        if profiled
        else {f"{principal}-token": principal for principal in ("alice", "scout", "gm")}
    )
    app = create_campaign_app(
        CampaignAccess(play),
        principals,
        legacy_routes=True,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{runner.addresses[0][1]}/api/tactical/v2/campaigns/{cid}", cid
    finally:
        await runner.cleanup()


async def post(
    client: aiohttp.ClientSession, url: str, principal: str, command: Record
) -> aiohttp.ClientResponse:
    return await client.post(
        f"{url}/commands",
        json={"command": command.model_dump(mode="json")},
        headers={"Authorization": f"Bearer {principal}-token"},
    )


async def read(
    client: aiohttp.ClientSession, url: str, principal: str, actor_id: str
) -> TacticalSnapshotV2:
    async with client.get(
        url,
        params={"actor_id": actor_id},
        headers={"Authorization": f"Bearer {principal}-token"},
    ) as response:
        assert response.status == 200, await response.text()
        return TacticalSnapshotV2.model_validate_json(await response.text())


async def test_basic_player_projection_choices_adjudication_and_privacy(tmp_path: Path) -> None:
    async with api(tmp_path, profiled=True) as (url, _cid), aiohttp.ClientSession() as client:
        view = await read(client, url, "alice", "a")
        assert view.encounters == ()
        assert view.activity.kind == "combat" and view.activity.representation == "basic"
        assert tuple(actor.id for actor in view.basic_encounters[0].actors) == ("a", "b")
        approach = next(
            choice.command
            for choice in view.basic_encounters[0].choices
            if choice.label.startswith("Approach ")
        )
        async with await post(client, url, "alice", approach) as moved:
            assert moved.status == 200, await moved.text()

        hidden = await read(client, url, "alice", "a")
        assert tuple(actor.id for actor in hidden.basic_encounters[0].actors) == ("a",)
        bob = await read(client, url, "charlie", "c")
        assert bob.activity.kind == "independent"
        assert bob.basic_encounters == ()

        declaration = DeclareBasicSpatialFacts(
            id="restore-visibility",
            actor_id="gm",
            expected_revision=3,
            encounter_id="fight",
            facts=(
                VisibilitySpatialFact(
                    subject_id="a",
                    object_id="b",
                    visible=True,
                    provenance=provenance(
                        3,
                        source="gm-adjudication",
                        source_id="restore-visibility",
                    ),
                ),
            ),
        )
        player_declaration = declaration.model_copy(
            update={
                "id": "player-fact",
                "actor_id": "a",
                "facts": tuple(
                    fact.model_copy(
                        update={
                            "provenance": fact.provenance.model_copy(
                                update={"source_id": "player-fact", "declared_by": "a"}
                            )
                        }
                    )
                    for fact in declaration.facts
                ),
            }
        )
        async with await post(client, url, "alice", player_declaration) as denied_fact:
            assert denied_fact.status == 400
            assert "GM combat workflow requires GM authority" in await denied_fact.text()
        async with await post(client, url, "gm", declaration) as declared:
            assert declared.status == 200, await declared.text()
        restored = await read(client, url, "alice", "a")
        assert tuple(actor.id for actor in restored.basic_encounters[0].actors) == ("a", "b")


async def test_basic_start_requires_gm_authority_on_v2(tmp_path: Path) -> None:
    async with api(tmp_path) as (url, _cid), aiohttp.ClientSession() as client:
        opening = start_basic(5)
        async with await post(client, url, "alice", opening) as denied:
            assert denied.status == 403
        async with await post(client, url, "gm", opening) as accepted:
            assert accepted.status == 200, await accepted.text()


async def test_gm_basic_start_reinforcement_and_hex_escalation_use_v2(tmp_path: Path) -> None:
    async with api(tmp_path, profiled=True) as (url, _cid), aiohttp.ClientSession() as client:
        admission = JoinEncounter(
            id="admit-c",
            actor_id="gm",
            joining_actor_id="c",
            expected_revision=2,
            encounter_id="fight",
            placement=BasicJoinPlacement(
                facts=reinforcement_facts("c", ("a", "b"), revision=2, command_id="admit-c")
            ),
        )
        async with await post(client, url, "gm", admission) as joined:
            assert joined.status == 200, await joined.text()
        gm_view = await read(client, url, "gm", "gm")
        assert tuple(actor.id for actor in gm_view.basic_encounters[0].actors) == ("a", "b", "c")

        migration = MigrateEncounterHex(
            id="escalate",
            actor_id="gm",
            expected_revision=3,
            encounter_id="fight",
            battlefield=board(),
            placements=(
                HexPlacement(actor_id="a", pose=Pose(position=Hex(q=0, r=0), facing=0)),
                HexPlacement(actor_id="b", pose=Pose(position=Hex(q=2, r=0), facing=3)),
                HexPlacement(actor_id="c", pose=Pose(position=Hex(q=-1, r=2), facing=3)),
            ),
        )
        async with await post(client, url, "gm", migration) as escalated:
            assert escalated.status == 200, await escalated.text()
            result = TacticalSnapshotV2.model_validate_json(await escalated.text())
            assert result.basic_encounters == ()
        tactical = await read(client, url, "alice", "a")
        assert tactical.activity.representation == "hex"
        assert tactical.encounters[0].coordinate_system == "hex-axial-v1"


async def test_basic_pending_defense_reconnect_retry_and_stale_choice(tmp_path: Path) -> None:
    async with (
        api(tmp_path, profiled=True, distance=1) as (url, _cid),
        aiohttp.ClientSession() as client,
    ):
        attacker = await read(client, url, "alice", "a")
        attack = next(
            choice.command
            for choice in attacker.basic_encounters[0].choices
            if choice.label.startswith("Attack ")
        )
        async with await post(client, url, "alice", attack) as first:
            assert first.status == 200
            first_body = await first.text()
        async with await post(client, url, "alice", attack) as retry:
            assert retry.status == 200
            assert await retry.text() == first_body

        defender = await read(client, url, "bob", "b")
        dodge = next(
            choice.command
            for choice in defender.basic_encounters[0].choices
            if choice.label == "Dodge defense"
        )
        async with await post(client, url, "bob", dodge) as defended:
            assert defended.status == 200, await defended.text()
        stale = dodge.model_copy(update={"id": "stale-defense"})
        async with await post(client, url, "bob", stale) as rejected:
            assert rejected.status == 409
            assert "refresh and choose again" in await rejected.text()
