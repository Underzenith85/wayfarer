"""Campaign locks, compiled engine reuse and durable advisory worker boundaries."""

import asyncio
from pathlib import Path

import pytest
from test_wave9 import prepare

from wayfarer.errors import ProviderError
from wayfarer.orchestration.jobs import ProviderJobs
from wayfarer.orchestration.sessions import SessionRegistry
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.jobs import ProviderJob


async def test_campaign_locks_serialize_and_idle_eviction_preserves_waiters(tmp_path: Path) -> None:
    store = AsyncSQLiteStore(tmp_path / "locks.sqlite")
    now = [0.0]
    registry = SessionRegistry(idle_seconds=10, clock=lambda: now[0])
    entered = asyncio.Event()
    order: list[int] = []

    async def second() -> None:
        entered.set()
        async with registry.serialized(store, "a"):
            order.append(2)

    async with registry.serialized(store, "a"):
        order.append(1)
        task = asyncio.create_task(second())
        await entered.wait()
        async with registry.serialized(store, "b"):
            order.append(3)
        now[0] = 20
        assert registry.evict_idle() == 1  # b, never locked a or its waiter.
        assert order == [1, 3]
    await task
    assert order == [1, 3, 2]
    now[0] = 40
    assert registry.evict_idle() == 1


async def test_compiled_engine_constructed_once_for_shared_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wayfarer.orchestration import sessions

    cid, play = await prepare(tmp_path)
    registry = SessionRegistry()
    from wayfarer.simulation.action_engine import ActionEngine

    original = ActionEngine
    count = 0

    def construct(*args: object, **kwargs: object) -> object:
        nonlocal count
        count += 1
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(sessions, "ActionEngine", construct)
    first = registry.bind(
        play.store, cid, play.engine.reviewer, play.engine.resources, play.engine.rules
    )
    second = registry.bind(
        play.store, "another", play.engine.reviewer, play.engine.resources, play.engine.rules
    )
    assert first is second and count == 1


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_jobs_do_not_block_and_publish_once_to_their_audience(
    tmp_path: Path, backend: str
) -> None:
    _, play = await prepare(tmp_path, backend=backend)
    jobs = ProviderJobs(play.store)
    release, entered = asyncio.Event(), asyncio.Event()
    calls = 0

    async def provider() -> str:
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return '{"text":"Only the captive sees this."}'

    job = await asyncio.wait_for(
        jobs.submit(
            cid="campaign",
            revision=2,
            principal="captive",
            actor="a",
            kind="narration",
            key="first",
            request_json="{}",
            run=provider,
        ),
        2,
    )
    await entered.wait()
    assert not jobs.tasks[job.id].done()
    duplicate = await jobs.submit(
        cid="campaign",
        revision=2,
        principal="captive",
        actor="a",
        kind="narration",
        key="duplicate",
        request_json="{}",
        run=provider,
    )
    assert duplicate.id == job.id
    assert await jobs.store.outbox("campaign", "rescuer", "b") == ()
    release.set()
    await jobs.result(job)
    assert calls == 1
    assert len(await jobs.store.outbox("campaign", "captive", "a")) == 1
    restarted = ProviderJobs(play.store)
    await restarted.start()
    assert (await restarted.store.read(job.id)).status == "succeeded"
    await restarted.close()
    await jobs.close()


async def test_restart_fails_interrupted_jobs_explicitly(tmp_path: Path) -> None:
    store = AsyncSQLiteStore(tmp_path / "restart.sqlite")
    jobs = ProviderJobs(store)
    await jobs.start()
    job = ProviderJob(
        id="interrupted",
        campaign_id="campaign",
        revision=1,
        principal_id="gm",
        actor_id="gm",
        kind="proposal",
        request_json="{}",
        status="running",
    )
    await jobs.store.create(job)
    restarted = ProviderJobs(store)
    await restarted.start()
    with pytest.raises(ProviderError, match="worker_restarted"):
        await restarted.result(job)
    published = await restarted.store.outbox("campaign", "gm", "gm")
    assert len(published) == 1 and published[0].status == "failed"
    await ProviderJobs(store).start()
    assert await restarted.store.outbox("campaign", "gm", "gm") == published


async def test_generated_npc_choice_is_durable_and_keeps_origin(tmp_path: Path) -> None:
    from test_wave9 import FakeProvider
    from test_wave10 import prepare as npc_prepare

    from wayfarer.orchestration.access import CampaignAccess
    from wayfarer.orchestration.npcs import NPCService
    from wayfarer.orchestration.providers import Orchestrator

    cid, play = await npc_prepare(tmp_path)
    provider = FakeProvider(payload='{"action_id":"unknown"}')
    llm = Orchestrator(CampaignAccess(play), provider)
    state = await NPCService(play).propose_generated(
        cid, llm=llm, command_id="generated", authenticated_gm_id="gm", plan_id="patrol"
    )
    assert state.revision == 1
    receipt = (await play.store.history(cid))[-1]
    assert receipt.origin is not None and receipt.origin.proposal_type == "npc"
    jobs = await llm.jobs.store.outbox(cid, "gm", "gm")
    assert len(jobs) == 1 and jobs[0].status == "succeeded"
    assert await llm.jobs.store.outbox(cid, "alice", "a") == ()
