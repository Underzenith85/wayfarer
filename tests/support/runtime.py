"""One builder for the real runtime with injected fakes.

Every seam the orchestrator has is a constructor argument: the store, the session
registry behind it, the engine factory, the instant source, the seed source and
the job partition. A test that wants a different clock, a different engine or a
scripted provider passes one in here; nothing in ``tests/`` patches a module.
"""

from __future__ import annotations

import itertools
import json
import os
import secrets
from collections.abc import Callable
from pathlib import Path

import pytest

from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, PlayState
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.world import World
from wayfarer.orchestration.clock import CommandInstant
from wayfarer.orchestration.entropy import SeedSource
from wayfarer.orchestration.medical import EnvironmentResolver
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.process_kinds import registered
from wayfarer.orchestration.processes import ProcessRegistry
from wayfarer.orchestration.provider_contracts import StructuredProvider
from wayfarer.orchestration.providers import Orchestrator
from wayfarer.orchestration.runtime import CampaignRuntime, CampaignStores
from wayfarer.orchestration.sessions import EngineFactory, SessionRegistry, Store
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.events import CommandRecord
from wayfarer.persistence.postgres import AsyncPostgresStore

InstantSource = Callable[[], CommandInstant]

# A recognisable, deterministic wall clock: 2023-11-14T22:13:20Z, one second apart.
FIRST_INSTANT = 1_700_000_000_000_000


def fixed_instants(start: int = FIRST_INSTANT, step: int = 1_000_000) -> InstantSource:
    """A monotonic command clock a test can predict without reading the real one."""
    counter = itertools.count(start, step)
    return lambda: CommandInstant(next(counter))


def scripted_seeds(start: int = 1) -> SeedSource:
    """256-bit command seeds in a fixed order, so a recorded log replays."""
    counter = itertools.count(start)
    return lambda: format(next(counter), "064x")


def open_store(
    tmp_path: Path, *, backend: str = "sqlite", filename: str = "runtime.sqlite"
) -> Store:
    """The one place a test opens a store; a missing Postgres URL skips the test."""
    if backend == "postgres":
        url = os.environ.get("WAYFARER_TEST_DATABASE_URL")
        if url is None:
            pytest.skip("WAYFARER_TEST_DATABASE_URL is not configured")
        return AsyncPostgresStore(url, 10)
    return AsyncSQLiteStore(tmp_path / filename, 10)


def job_worker(store: Store, *, partition: str = "default") -> ProcessRegistry:
    """A provider-job worker over its own handles; two of them model a restart."""
    return registered(ProcessRegistry(CampaignStores.on(store).processes, partition=partition))


def build_play(
    tmp_path: Path,
    engine: ActionEngine,
    *,
    rng: RandomSource = secrets,
    instants: InstantSource | None = None,
    seeds: SeedSource | None = None,
    engine_factory: EngineFactory = ActionEngine,
    sessions: SessionRegistry | None = None,
    backend: str = "sqlite",
    store: Store | None = None,
    filename: str = "runtime.sqlite",
) -> PlayService:
    """A play service over a temporary store, a private lock table and fake sources."""
    return PlayService(
        open_store(tmp_path, backend=backend, filename=filename) if store is None else store,
        engine,
        rng=rng,
        sessions=SessionRegistry(engine_factory=engine_factory) if sessions is None else sessions,
        instants=fixed_instants() if instants is None else instants,
        seeds=scripted_seeds() if seeds is None else seeds,
    )


def build_runtime(
    play: PlayService,
    *,
    medical_environment: EnvironmentResolver | None = None,
    partition: str = "default",
) -> CampaignRuntime:
    """The container every adapter receives, built in one place.

    Tests wrap the service their scenario fixture produced. Each runtime owns its
    session registry and job partition, so two runtimes in one test share no lock
    and no worker. Later steps of #631 add arguments here — the provider, the
    scheduler — and no test call site changes.
    """
    return CampaignRuntime(play, medical_environment, partition=partition)


def build_orchestrator(
    runtime: CampaignRuntime,
    provider: StructuredProvider,
    *,
    timeout: float = 20,
    attempts: int = 2,
) -> Orchestrator:
    """The provider facade over a runtime; step 5 moves it into the constructor."""
    return Orchestrator(runtime, provider, timeout=timeout, attempts=attempts)


async def seed_play(
    play: PlayService,
    campaign: Campaign,
    world: World,
    resources: ResourceState,
    actors: tuple[ActorSetup, ...],
    members: tuple[CampaignMember, ...] | None = None,
) -> PlayState:
    """Seed a campaign the way a trusted activation does: genesis as a command.

    `PlayService.create` used to insert a row; #636 made creation the first entry
    in the stream, so a fixture goes through the runtime that owns that write.
    """
    return await build_runtime(play).seed(campaign, world, resources, actors, members)


async def seed_campaign(store: Store, campaign: Campaign) -> Campaign:
    """Commit a hand-built campaign envelope as its own genesis.

    For fixtures that construct the envelope themselves and never load a play
    checkpoint from it.
    """
    result = await store.commit_genesis(
        campaign,
        command_id="setup:seed",
        text=json.dumps({"operation": "seed", "campaign": campaign["id"]}, sort_keys=True),
    )
    return result["state"]


async def played(store: Store, cid: str) -> list[CommandRecord]:
    """The commands a campaign played, after the genesis receipt that created it.

    Creation is the stream's first command since #636, so a test counting what a
    scenario committed asks for this rather than the whole log.
    """
    return (await store.history(cid))[1:]
