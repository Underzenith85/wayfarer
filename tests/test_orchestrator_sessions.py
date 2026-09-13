"""Campaign locks, compiled engine reuse and durable advisory worker boundaries."""

import asyncio
from pathlib import Path

import pytest
from support.runtime import build_orchestrator, build_runtime, job_worker, open_store
from test_wave9 import FakeProvider, prepare
from test_wave10 import prepare as npc_prepare

from wayfarer.engine.character.power import PowerReviewer
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.errors import ProviderError
from wayfarer.orchestration.battlefield_templates import install_rules
from wayfarer.orchestration.npcs import NPCService
from wayfarer.orchestration.sessions import SessionRegistry
from wayfarer.persistence.jobs import ProviderJob


async def test_campaign_locks_serialize_and_idle_eviction_preserves_waiters(tmp_path: Path) -> None:
    now = [0.0]
    registry = SessionRegistry(idle_seconds=10, clock=lambda: now[0])
    entered = asyncio.Event()
    order: list[int] = []

    async def second() -> None:
        entered.set()
        async with registry.serialized("a"):
            order.append(2)

    async with registry.serialized("a"):
        order.append(1)
        task = asyncio.create_task(second())
        await entered.wait()
        async with registry.serialized("b"):
            order.append(3)
        now[0] = 20
        assert registry.evict_idle() == 1  # b, never locked a or its waiter.
        assert order == [1, 3]
    await task
    assert order == [1, 3, 2]
    now[0] = 40
    assert registry.evict_idle() == 1


async def test_compiled_engine_constructed_once_for_shared_configuration(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    count = 0

    def construct(
        reviewer: PowerReviewer, resources: ResourceEngine, rules: ActionRules
    ) -> ActionEngine:
        nonlocal count
        count += 1
        return ActionEngine(reviewer, resources, rules)

    registry = SessionRegistry(engine_factory=construct)
    first = registry.bind(cid, play.engine.reviewer, play.engine.resources, play.engine.rules)
    second = registry.bind(
        "another", play.engine.reviewer, play.engine.resources, play.engine.rules
    )
    assert first is second and count == 1


async def test_two_runtimes_over_two_stores_share_no_lock_or_partition(tmp_path: Path) -> None:
    """A registry and a job partition belong to one runtime, never to the process."""
    cid, play = await prepare(tmp_path)
    other_cid, other_play = await prepare(tmp_path / "second")

    first = build_runtime(play)
    second = build_runtime(other_play, partition="second")
    assert first.play.sessions is not second.play.sessions
    assert first.jobs is not second.jobs
    assert (first.partition, second.partition) == ("default", "second")

    held = asyncio.Event()

    async def hold() -> None:
        async with first.play.sessions.serialized(cid):
            held.set()
            await asyncio.sleep(0.05)

    task = asyncio.create_task(hold())
    await held.wait()
    # The other runtime never waits on a lock this one is holding, same id or not.
    async with second.play.sessions.serialized(cid):
        pass
    async with second.play.sessions.serialized(other_cid):
        pass
    await task


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_jobs_do_not_block_and_publish_once_to_their_audience(
    tmp_path: Path, backend: str
) -> None:
    _, play = await prepare(tmp_path, backend=backend)
    jobs = job_worker(play.store)
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
    restarted = job_worker(play.store)
    await restarted.start()
    assert (await restarted.store.read(job.id)).status == "succeeded"
    await restarted.close()
    await jobs.close()


async def test_restart_fails_interrupted_jobs_explicitly(tmp_path: Path) -> None:
    store = open_store(tmp_path, filename="restart.sqlite")
    jobs = job_worker(store)
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
    restarted = job_worker(store)
    await restarted.start()
    with pytest.raises(ProviderError, match="worker_restarted"):
        await restarted.result(job)
    published = await restarted.store.outbox("campaign", "gm", "gm")
    assert len(published) == 1 and published[0].status == "failed"
    await job_worker(store).start()
    assert await restarted.store.outbox("campaign", "gm", "gm") == published


async def test_another_partition_leaves_interrupted_work_alone(tmp_path: Path) -> None:
    """Recovery is per partition, so a second worker never fails another's job."""
    store = open_store(tmp_path, filename="partitions.sqlite")
    owner = job_worker(store, partition="west")
    await owner.start()
    job = ProviderJob(
        id="held",
        campaign_id="campaign",
        revision=1,
        principal_id="gm",
        actor_id="gm",
        kind="proposal",
        request_json="{}",
        partition="west",
        status="running",
    )
    await owner.store.create(job)
    await job_worker(store, partition="east").start()
    assert (await owner.store.read(job.id)).status == "running"


async def test_generated_npc_choice_is_durable_and_keeps_origin(tmp_path: Path) -> None:
    cid, play = await npc_prepare(tmp_path)
    provider = FakeProvider(payload='{"action_id":"unknown"}')
    llm = build_orchestrator(build_runtime(play), provider)
    state = await NPCService(play).propose_generated(
        cid, llm=llm, command_id="generated", authenticated_gm_id="gm", plan_id="patrol"
    )
    assert state.revision == 1
    receipt = (await play.store.history(cid))[-1]
    assert receipt.origin is not None and receipt.origin.proposal_type == "npc"
    jobs = await llm.jobs.store.outbox(cid, "gm", "gm")
    assert len(jobs) == 1 and jobs[0].status == "succeeded"
    assert await llm.jobs.store.outbox(cid, "alice", "a") == ()


async def test_migrated_configuration_reuses_its_compiled_engine(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    campaign = await play.store.read(cid)
    combat = play.engine.rules.combat
    assert combat is not None
    migrated = install_rules(campaign, play, combat)
    assert migrated.for_campaign(campaign).engine is migrated.engine
