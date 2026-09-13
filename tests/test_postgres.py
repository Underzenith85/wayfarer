"""PostgreSQL contract tests run in CI against the reserved service."""

import asyncio
from pathlib import Path

import pytest
from support.runtime import build_play, seed_campaign
from test_actions import campaign, engine

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import Wait
from wayfarer.persistence.postgres import AsyncPostgresStore

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_postgres_concurrent_retry_and_replay(tmp_path: Path) -> None:
    from test_wave9 import prepare

    cid, play = await prepare(tmp_path, backend="postgres")
    command = Wait(id="same", actor_id="a", expected_revision=0, ticks=1)
    results = await asyncio.gather(
        *(play.execute(cid, command, authenticated_actor_id="a") for _ in range(6))
    )
    assert all(result == results[0] for result in results)
    assert await play.store.replay(cid) == await play.store.read(cid)
    assert len(await play.store.history(cid)) == 1


async def test_postgres_rolls_back_projection_and_event_together(tmp_path: Path) -> None:
    play = build_play(tmp_path, engine(), backend="postgres")
    assert isinstance(play.store, AsyncPostgresStore)
    initial = campaign(play.engine)
    await seed_campaign(play.store, initial)

    def crash(state: Campaign) -> CommandReceipt:
        state["revision"] = 99
        raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError, match="simulated crash"):
        await play.store.commit_turn(initial["id"], "crash", 0, "Rest", crash)
    assert (await play.store.read(initial["id"]))["revision"] == 0
    assert [r.command_id for r in await play.store.history(initial["id"])] == ["setup:seed"]
