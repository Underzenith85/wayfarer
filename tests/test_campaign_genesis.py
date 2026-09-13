"""Creating a campaign is its stream's first command, once per identity (#636)."""

from pathlib import Path

import pytest
from support.runtime import build_runtime
from test_scenes import configured
from test_wave11 import graph_fixture
from test_wave12 import service

from wayfarer.engine.simulation.campaign.setup import CreateSetup
from wayfarer.errors import ConflictError
from wayfarer.orchestration.setup import SetupService


async def test_retrying_a_creation_returns_the_same_lobby_and_one_receipt(
    tmp_path: Path,
) -> None:
    setup = service(tmp_path)
    graph = graph_fixture()
    command = CreateSetup(id="new", brief=graph.brief, graph=graph)
    first = await setup.create(command, principal_id="alice")
    again = await setup.create(command, principal_id="alice")
    assert first == again
    cid = str(first["id"])
    records = await setup.play.store.history(cid)
    assert [r.command_id for r in records] == ["setup:create"]
    assert records[0].event == {"action": "setup", "outcome": "draft"}
    assert records[0].expected_revision == 0 and records[0].resulting_revision == 0


async def test_the_same_command_id_with_a_different_payload_conflicts(tmp_path: Path) -> None:
    setup = service(tmp_path)
    graph = graph_fixture()
    await setup.create(CreateSetup(id="new", brief=graph.brief, graph=graph), principal_id="alice")
    with pytest.raises(ConflictError, match="already used for different input"):
        await setup.create(CreateSetup(id="new", brief=graph.brief), principal_id="alice")


async def test_listing_answers_one_principal_from_the_index(tmp_path: Path) -> None:
    setup = service(tmp_path)
    graph = graph_fixture()
    mine = await setup.create(
        CreateSetup(id="mine", brief=graph.brief, graph=graph), principal_id="alice"
    )
    theirs = await setup.create(
        CreateSetup(id="theirs", brief=graph.brief, graph=graph), principal_id="bob"
    )
    assert {str(c["id"]) for c in await setup.listing(principal_id="alice")} == {str(mine["id"])}
    assert {str(c["id"]) for c in await setup.listing(principal_id="bob")} == {str(theirs["id"])}
    assert await setup.listing(principal_id="eve") == []
    # The index is the store's, not the lobby's: it answers without loading both.
    assert {c["id"] for c in await setup.play.store.listing("alice")} == {str(mine["id"])}
    assert {c["id"] for c in await setup.play.store.listing()} == {
        str(mine["id"]),
        str(theirs["id"]),
    }


async def test_a_seeded_campaign_opens_its_own_stream(tmp_path: Path) -> None:
    from test_actions import actor_setup, campaign, engine, resource_seed, world

    from wayfarer.orchestration.play import PlayService
    from wayfarer.persistence.async_sqlite import AsyncSQLiteStore

    reducer = engine()
    play = PlayService(AsyncSQLiteStore(tmp_path / "seeded.sqlite", 10), reducer)
    initial = campaign(reducer)
    runtime = build_runtime(play)
    await runtime.seed(initial, world(), resource_seed(), (actor_setup(),))
    records = await play.store.history(initial["id"])
    assert [r.command_id for r in records] == ["setup:seed"]
    # Seeding twice is the same command, so it replays rather than conflicting.
    await runtime.seed(initial, world(), resource_seed(), (actor_setup(),))
    assert len(await play.store.history(initial["id"])) == 1


async def test_every_seat_reaches_the_principal_index(tmp_path: Path) -> None:
    setup: SetupService = service(tmp_path)
    engine, _ = configured()
    graph = graph_fixture()
    created = await setup.create(
        CreateSetup(id="shared", brief=graph.brief, graph=graph), principal_id="alice"
    )
    cid = str(created["id"])
    assert {c["id"] for c in await setup.play.store.listing("alice")} == {cid}
    assert await setup.play.store.listing("bob") == []
