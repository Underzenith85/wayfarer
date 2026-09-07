"""Director scheduling receipts, subgroup recovery and captive/rescuer journeys."""

from pathlib import Path

import pytest
from test_wave9 import FakeProvider, prepare, split

from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.director import DirectorService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.providers import Orchestrator
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


@pytest.mark.parametrize("boundary", ["domain_committed", "waiting"])
async def test_scheduled_turn_waits_across_restart_without_narrating(
    tmp_path: Path, boundary: str
) -> None:
    cid, play = await prepare(tmp_path, party=True)
    await split(cid, CampaignAccess(play))
    provider = FakeProvider()
    director = DirectorService(Orchestrator(CampaignAccess(play), provider))

    def crash(phase: str) -> None:
        if phase == boundary:
            raise RuntimeError("restart")

    with pytest.raises(RuntimeError, match="restart"):
        await director.run(
            cid,
            principal_id="alice",
            actor_id="a",
            command_id="wait-a",
            text="wait",
            proposal={"kind": "wait", "ticks": 1},
            checkpoint=crash,
        )
    restarted = PlayService(AsyncSQLiteStore(tmp_path / "wave9.sqlite", 10), play.engine)
    director = DirectorService(Orchestrator(CampaignAccess(restarted), provider))
    waiting = await director.run(
        cid, principal_id="alice", actor_id="a", command_id="wait-a", text="wait"
    )
    assert not waiting.committed and not waiting.narration_available
    state = restarted._load(await restarted.store.read(cid))
    assert state.director[0].phase == "waiting"
    assert state.resources.game_time == 0
    assert provider.requests == []
    revision = state.revision
    await director.run(cid, principal_id="alice", actor_id="a", command_id="wait-a", text="wait")
    assert restarted._load(await restarted.store.read(cid)).revision == revision
    assert "wait-a" not in str(await CampaignAccess(restarted).read(cid, principal_id="bob"))
    other = await director.run(
        cid,
        principal_id="bob",
        actor_id="b",
        command_id="wait-b",
        text="wait",
        proposal={"kind": "wait", "ticks": 1},
    )
    assert other.committed
    result = await director.run(
        cid, principal_id="alice", actor_id="a", command_id="wait-a", text="wait"
    )
    assert result.committed and result.narration_available
    state = restarted._load(await restarted.store.read(cid))
    assert state.resources.game_time == 1
    assert len(state.party.receipts) == 2
    assert len(provider.requests) == 2
    assert all(r.operation == "narration" for r in provider.requests)
    assert await restarted.store.replay(cid) == await restarted.store.read(cid)


async def test_changed_subgroup_releases_uninterpreted_turn(tmp_path: Path) -> None:
    from wayfarer.orchestration.party import PartyCommand, PartyService

    cid, play = await prepare(tmp_path, party=True)
    provider = FakeProvider()
    director = DirectorService(Orchestrator(CampaignAccess(play), provider))

    def crash(phase: str) -> None:
        raise RuntimeError("restart")

    with pytest.raises(RuntimeError):
        await director.run(
            cid,
            principal_id="alice",
            actor_id="a",
            command_id="old",
            text="wait",
            checkpoint=crash,
        )
    state = play._load(await play.store.read(cid))
    await PartyService(play).execute(
        cid,
        PartyCommand(
            kind="split_party",
            id="split",
            actor_id="a",
            expected_revision=state.revision,
            target_id="scouts",
        ),
        authenticated_actor_id="a",
    )
    result = await director.run(
        cid, principal_id="alice", actor_id="a", command_id="old", text="wait"
    )
    assert not result.committed
    assert "subgroup changed" in result.narration
    assert provider.requests == []
    await director.run(
        cid,
        principal_id="alice",
        actor_id="a",
        command_id="fresh",
        text="wait",
        proposal={"kind": "wait", "ticks": 1},
    )
    assert play._load(await play.store.read(cid)).director[-1].phase == "waiting"


async def test_director_captive_rescuer_reunion_and_receipt_replay(tmp_path: Path) -> None:
    from test_wave10 import prepare as recovery_prepare
    from test_wave10 import setback

    from wayfarer.orchestration.recovery import captive

    cid, play = await recovery_prepare(tmp_path)
    await setback(cid, play, "capture")
    provider = FakeProvider()
    director = DirectorService(Orchestrator(CampaignAccess(play), provider))

    async def act(actor: str, key: str, proposal: dict[str, object] | None = None) -> bool:
        result = await director.run(
            cid,
            principal_id="alice" if actor == "a" else "bob",
            actor_id=actor,
            command_id=key,
            text=key,
            proposal=proposal,
        )
        return result.committed

    def recovery(rule: str) -> dict[str, object]:
        return {"kind": "choose_recovery", "rule_id": rule, "target_actor_id": "a"}

    assert not await act("a", "observe", recovery("observe"))
    assert provider.requests == []
    assert await act("b", "wait", {"kind": "wait", "ticks": 1})
    director = DirectorService(
        Orchestrator(CampaignAccess(PlayService(play.store, play.engine)), provider)
    )
    assert await act("a", "observe")
    assert not await act("b", "rescue", recovery("rescue"))
    assert await act("a", "assist", recovery("assist"))
    assert await act("b", "rescue")
    state = play._load(await play.store.read(cid))
    assert captive(state, "a") is None
    assert state.resources.game_time == 2
    assert not any(i.owner_id == "a" for i in state.resources.items)
    assert ("b", "clue") not in state.world.knowledge
    assert not await act("a", "gear", recovery("gear"))
    assert await act("b", "wait-gear", {"kind": "wait", "ticks": 1})
    assert await act("a", "gear")
    state = play._load(await play.store.read(cid))
    group = next(g for g in state.party.groups if "b" in g.actor_ids)
    assert await act("a", "reunion", {"kind": "rejoin_party", "target_id": group.id})
    state = play._load(await play.store.read(cid))
    assert len(state.party.groups) == 1
    assert any(i.owner_id == "a" for i in state.resources.items)
    before = await play.store.read(cid)
    for actor, key in [
        ("a", "observe"),
        ("a", "assist"),
        ("b", "rescue"),
        ("a", "gear"),
        ("a", "reunion"),
    ]:
        assert await act(actor, key)
    assert await play.store.read(cid) == before
    assert await play.store.replay(cid) == before
    bob = str((await CampaignAccess(play).read(cid, principal_id="bob"))["director"])
    assert "observe" not in bob and "assist" not in bob


async def test_rejected_scheduled_action_is_not_narrated_as_success(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path, party=True)
    await split(cid, CampaignAccess(play))
    provider = FakeProvider()
    director = DirectorService(Orchestrator(CampaignAccess(play), provider))
    await director.run(
        cid,
        principal_id="alice",
        actor_id="a",
        command_id="out",
        text="travel",
        proposal={"kind": "travel_scene", "exit_id": "to-alley"},
    )
    await director.run(
        cid,
        principal_id="bob",
        actor_id="b",
        command_id="wait-out",
        text="wait",
        proposal={"kind": "wait", "ticks": 2},
    )
    await director.run(cid, principal_id="alice", actor_id="a", command_id="out", text="travel")
    result = await director.run(
        cid,
        principal_id="alice",
        actor_id="a",
        command_id="shortcut",
        text="shortcut",
        proposal={"kind": "travel_scene", "exit_id": "return"},
    )
    assert not result.committed
    await director.run(
        cid,
        principal_id="bob",
        actor_id="b",
        command_id="wait",
        text="wait",
        proposal={"kind": "wait", "ticks": 1},
    )
    calls = len(provider.requests)
    result = await director.run(
        cid, principal_id="alice", actor_id="a", command_id="shortcut", text="shortcut"
    )
    assert not result.committed and not result.narration_available
    assert result.narration == "activity.no_longer_feasible"
    assert len(provider.requests) == calls
    state = play._load(await play.store.read(cid))
    assert next(t for t in state.director if t.id == "shortcut").phase == "clarification"
    assert next(s for s in state.actor_scenes if s.actor_id == "a").scene_id == "alley-scene"


@pytest.mark.parametrize("operation", ["intent", "narration"])
async def test_cancelled_provider_call_resumes_without_replaying(
    tmp_path: Path, operation: str
) -> None:
    import asyncio

    from wayfarer.orchestration.providers import ProviderRequest

    cid, play = await prepare(tmp_path)
    entered = asyncio.Event()

    class BlockingProvider(FakeProvider):
        async def complete(self, request: ProviderRequest) -> object:
            if request.operation == operation:
                entered.set()
                await asyncio.Event().wait()
            return await super().complete(request)

    director = DirectorService(Orchestrator(CampaignAccess(play), BlockingProvider()))
    task = asyncio.create_task(
        director.run(cid, principal_id="alice", actor_id="a", command_id="cancelled", text="wait")
    )
    await asyncio.wait_for(entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    before = play._load(await play.store.read(cid))
    assert before.resources.game_time == (1 if operation == "narration" else 0)
    director = DirectorService(Orchestrator(CampaignAccess(play), FakeProvider()))
    result = await director.run(
        cid, principal_id="alice", actor_id="a", command_id="cancelled", text="wait"
    )
    assert result.committed
    assert play._load(await play.store.read(cid)).resources.game_time == 1
    assert (
        len([e for e in await play.store.history(cid) if e.event["action"] == "typed-action"]) == 1
    )


async def test_unsupported_recovery_keeps_adjudication_visible(tmp_path: Path) -> None:
    from test_wave10 import prepare as recovery_prepare
    from test_wave10 import setback

    cid, play = await recovery_prepare(tmp_path)
    await setback(cid, play, "capture")
    provider = FakeProvider()
    result = await DirectorService(Orchestrator(CampaignAccess(play), provider)).run(
        cid,
        principal_id="alice",
        actor_id="a",
        command_id="unsupported",
        text="escape",
        proposal={"kind": "choose_recovery", "rule_id": "unsupported", "target_actor_id": "a"},
    )
    assert not result.committed and not result.narration_available
    assert "adjudication_required" in result.narration
    assert provider.requests == []
    assert play._load(await play.store.read(cid)).resources.game_time == 0
