"""Individual combat withdrawal and independent activity contracts for #330."""

from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from test_reinforcements import (
    board,
    escalation,
    reinforcement_facts,
    setup_profiled_basic,
)

from wayfarer.engine.simulation.actions import PlayState, Wait
from wayfarer.engine.simulation.combat import (
    BasicSpatialContext,
    BasicSpatialFact,
    VisibilitySpatialFact,
)
from wayfarer.engine.simulation.combat_commands import BasicMove
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.engine.simulation.maneuvers import WaitInterrupt, WaitTrigger
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.combat import (
    BasicJoinPlacement,
    CombatService,
    DeclareBasicSpatialFacts,
    JoinEncounter,
    TakeCombatTurn,
    WithdrawEncounter,
    preview_withdrawal,
)
from wayfarer.orchestration.party import PartyCommand, PartyService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.tactical_view import project
from wayfarer.transport.campaign_api import create_campaign_app
from wayfarer.transport.tactical_api import enrich


def withdraw(revision: int, actor_id: str = "a") -> WithdrawEncounter:
    return WithdrawEncounter(
        id=f"withdraw-{actor_id}",
        actor_id=actor_id,
        expected_revision=revision,
        encounter_id="fight",
        new_group_id=f"independent-{actor_id}",
    )


def safe_facts(
    others: tuple[str, ...], *, revision: int, command_id: str
) -> tuple[BasicSpatialFact, ...]:
    return tuple(
        fact.model_copy(update={"visible": False})
        if isinstance(fact, VisibilitySpatialFact)
        else fact
        for fact in reinforcement_facts("a", others, revision=revision, command_id=command_id)
    )


async def prepared_basic(tmp_path: Path) -> tuple[str, PlayService, PlayState]:
    cid, play = await setup_profiled_basic(tmp_path, 2)
    service = CombatService(play)
    await service.execute(
        cid,
        JoinEncounter(
            id="admit-c",
            actor_id="gm",
            joining_actor_id="c",
            expected_revision=2,
            encounter_id="fight",
            placement=BasicJoinPlacement(
                facts=reinforcement_facts("c", ("a", "b"), revision=2, command_id="admit-c")
            ),
        ),
        authenticated_actor_id="gm",
    )
    await service.execute(
        cid,
        TakeCombatTurn(
            id="flee",
            actor_id="a",
            expected_revision=3,
            encounter_id="fight",
            maneuver="move",
            basic_move=BasicMove(reference_actor_id="b", direction="withdraw"),
        ),
        authenticated_actor_id="a",
    )
    await service.execute(
        cid,
        DeclareBasicSpatialFacts(
            id="safe-boundary",
            actor_id="gm",
            expected_revision=4,
            encounter_id="fight",
            facts=safe_facts(("b", "c"), revision=4, command_id="safe-boundary"),
        ),
        authenticated_actor_id="gm",
    )
    play = play.for_campaign(await play.store.read(cid))
    return cid, play, play._load(await play.store.read(cid))


async def test_basic_withdrawal_preserves_state_and_rejoins_without_a_free_turn(
    tmp_path: Path,
) -> None:
    cid, play, state = await prepared_basic(tmp_path)
    before = state.encounters[0]
    actor_before = next(p for p in before.participants if p.actor_id == "a")
    member = next(m for m in state.members if "a" in m.actor_ids)
    offered = enrich(play, state, project(play, state, member, "a"), member)
    assert offered.encounters == ()
    assert offered.withdrawals[0].command.actor_id == "a"
    assert offered.withdrawals[0].command.expected_revision == 5
    assert offered.withdrawals[0].command.encounter_id == "fight"

    command = withdraw(5)
    first = await CombatService(play).execute(cid, command, authenticated_actor_id="a")
    restarted = CombatService(PlayService(play.store, play.engine))
    assert await restarted.execute(cid, command, authenticated_actor_id="a") == first
    departed = play._load(await play.store.read(cid))
    encounter = departed.encounters[0]
    assert encounter.status == "active"
    assert encounter.turn_order == ("b", "c")
    assert before.current_actor_id == "b"
    assert encounter.current_actor_id == "b"
    assert encounter.withdrawals[-1].actor == actor_before
    assert tuple(g.actor_ids for g in departed.party.groups) == (("b", "c"), ("a",))

    await CombatService(play).execute(
        cid,
        JoinEncounter(
            id="return-a",
            actor_id="gm",
            joining_actor_id="a",
            expected_revision=6,
            encounter_id="fight",
            placement=BasicJoinPlacement(
                facts=reinforcement_facts("a", ("b", "c"), revision=6, command_id="return-a")
            ),
        ),
        authenticated_actor_id="gm",
    )
    rejoined = play._load(await play.store.read(cid))
    restored = next(p for p in rejoined.encounters[0].participants if p.actor_id == "a")
    assert restored == actor_before
    assert rejoined.encounters[0].current_actor_id == "b"
    assert len(rejoined.party.groups) == 1
    assert await play.store.replay(cid) == await play.store.read(cid)


async def test_v2_http_executes_only_the_projected_controlled_withdrawal(
    tmp_path: Path,
) -> None:
    cid, play, state = await prepared_basic(tmp_path)
    member = next(m for m in state.members if "a" in m.actor_ids)
    other = next(m for m in state.members if "b" in m.actor_ids)
    command = enrich(play, state, project(play, state, member, "a"), member).withdrawals[0].command
    app = create_campaign_app(
        CampaignAccess(play),
        {"actor-token": member.principal_id, "other-token": other.principal_id},
        legacy_routes=True,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        url = f"http://127.0.0.1:{runner.addresses[0][1]}/api/tactical/v2/campaigns/{cid}/commands"
        async with aiohttp.ClientSession() as client:
            async with client.post(
                url,
                json={"command": command.model_dump(mode="json")},
                headers={"Authorization": "Bearer other-token"},
            ) as denied:
                assert denied.status == 403
            async with client.post(
                url,
                json={"command": command.model_dump(mode="json")},
                headers={"Authorization": "Bearer actor-token"},
            ) as accepted:
                assert accepted.status == 200, await accepted.text()
                payload = await accepted.json()
                assert payload["withdrawals"] == []
                assert "world" not in payload
    finally:
        await runner.cleanup()


async def test_current_actor_withdrawal_advances_without_reset(tmp_path: Path) -> None:
    cid, play, _ = await prepared_basic(tmp_path)
    service = CombatService(play)
    await service.execute(
        cid,
        TakeCombatTurn(
            id="b-finishes-round",
            actor_id="b",
            expected_revision=5,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        authenticated_actor_id="b",
    )
    await service.execute(
        cid,
        TakeCombatTurn(
            id="c-finishes-round",
            actor_id="c",
            expected_revision=6,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        authenticated_actor_id="c",
    )
    before = play._load(await play.store.read(cid)).encounters[0]
    assert before.current_actor_id == "a" and before.round == 2
    await service.execute(cid, withdraw(7), authenticated_actor_id="a")
    after = play._load(await play.store.read(cid)).encounters[0]
    assert after.current_actor_id == "b"
    assert after.round == 2
    assert all(not p.block_used and not p.retreat_used for p in after.participants)


async def test_withdrawn_actor_can_queue_same_scene_activity_without_duplicating_time(
    tmp_path: Path,
) -> None:
    cid, play, _ = await prepared_basic(tmp_path)
    await CombatService(play).execute(cid, withdraw(5), authenticated_actor_id="a")
    state = play._load(await play.store.read(cid))
    now = state.resources.game_time
    queued = await PartyService(play).execute(
        cid,
        PartyCommand(
            id="independent-wait",
            actor_id="a",
            expected_revision=6,
            kind="queue_activity",
            activity_json=Wait(
                id="client-inner",
                actor_id="a",
                expected_revision=6,
                ticks=1,
            ).model_dump_json(),
        ),
        authenticated_actor_id="a",
    )
    assert queued.resources.game_time == now
    assert queued.party.queue[0].actor_id == "a"
    assert next(g for g in queued.party.groups if "a" in g.actor_ids).ready_through == now + 1


async def test_hex_final_withdrawal_requires_resolved_safe_flight(tmp_path: Path) -> None:
    cid, play = await setup_profiled_basic(tmp_path, 3)
    opaque = frozenset({Hex(q=2, r=r) for r in range(-3, 4)})
    await CombatService(play).execute(
        cid,
        escalation(
            revision=2,
            b_position=Hex(q=0, r=3),
            battlefield=board(opaque=opaque),
        ),
        authenticated_actor_id="gm",
    )
    play = play.for_campaign(await play.store.read(cid))
    service = CombatService(play)
    with pytest.raises(ValidationError, match="Move maneuver"):
        await service.execute(cid, withdraw(3), authenticated_actor_id="a")
    await service.execute(
        cid,
        TakeCombatTurn(
            id="run-to-edge",
            actor_id="a",
            expected_revision=3,
            encounter_id="fight",
            maneuver="move",
            hex_path=tuple(Hex(q=q, r=0) for q in range(1, 6)),
        ),
        authenticated_actor_id="a",
    )
    state = play._load(await play.store.read(cid))
    interrupted = state.encounters[0].model_copy(
        update={
            "wait_interrupt": WaitInterrupt(
                waiter_id="b",
                actor_id="a",
                turn_index=state.encounters[0].turn_index,
                command_json="{}",
                declaration=WaitTrigger(action="attack", item_id="sword-b"),
            )
        }
    )
    with pytest.raises(ConflictError, match="resolved combat boundary"):
        preview_withdrawal(
            play,
            state.model_copy(update={"encounters": (interrupted,)}),
            interrupted,
            withdraw(4),
        )

    resources = state.resources
    await service.execute(cid, withdraw(4), authenticated_actor_id="a")
    departed = play._load(await play.store.read(cid))
    assert departed.encounters[0].status == "completed"
    assert departed.encounters[0].completion_reason == "withdrawal"
    assert departed.encounters[0].turn_order == ("b",)
    assert departed.resources.game_time == resources.game_time
    assert departed.resources.items == resources.items
    assert departed.resources.pools == resources.pools
    assert departed.resources.active_effect_ids == resources.active_effect_ids
    assert await play.store.replay(cid) == await play.store.read(cid)


async def test_visible_pursuit_and_restraints_block_withdrawal(tmp_path: Path) -> None:
    _, play, state = await prepared_basic(tmp_path)
    encounter = state.encounters[0]
    spatial = encounter.spatial
    assert isinstance(spatial, BasicSpatialContext)
    visible = next(
        fact
        for fact in spatial.facts
        if isinstance(fact, VisibilitySpatialFact)
        and fact.subject_id == "b"
        and fact.object_id == "a"
        and fact.provenance.invalidated_revision is None
    )
    unsafe = spatial.model_copy(
        update={
            "facts": tuple(
                fact.model_copy(update={"visible": True}) if fact is visible else fact
                for fact in spatial.facts
            )
        }
    )
    with pytest.raises(ValidationError, match="safe authoritative facts"):
        preview_withdrawal(
            play,
            state,
            encounter.model_copy(update={"spatial_context": unsafe}),
            withdraw(5),
        )
    restrained = encounter.model_copy(
        update={
            "participants": tuple(
                actor.model_copy(update={"pinned": True}) if actor.actor_id == "a" else actor
                for actor in encounter.participants
            )
        }
    )
    with pytest.raises(ValidationError, match="restraint"):
        preview_withdrawal(play, state, restrained, withdraw(5))
