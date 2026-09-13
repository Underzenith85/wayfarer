"""One builder for the real runtime with injected fakes.

Every seam the orchestrator has is a constructor argument: the store, the session
registry behind it, the engine factory, the instant source, the seed source and
the job partition. A test that wants a different clock, a different engine or a
scripted provider passes one in here; nothing in ``tests/`` patches a module.
"""

from __future__ import annotations

import itertools
import os
import secrets
from collections.abc import Callable
from pathlib import Path

import pytest

from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.orchestration.clock import CommandInstant
from wayfarer.orchestration.entropy import SeedSource
from wayfarer.orchestration.jobs import ProviderJobs
from wayfarer.orchestration.medical import EnvironmentResolver
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.providers import Orchestrator, StructuredProvider
from wayfarer.orchestration.runtime import CampaignRuntime, CampaignStores
from wayfarer.orchestration.sessions import EngineFactory, SessionRegistry, Store
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
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


def job_worker(store: Store, *, partition: str = "default") -> ProviderJobs:
    """A provider-job worker over its own handles; two of them model a restart."""
    return ProviderJobs(CampaignStores.on(store).jobs, partition=partition)


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
