"""SQLite transaction, authority, privacy and concurrent retry evidence."""

import asyncio
from pathlib import Path

import pytest
from test_abilities import spec
from test_ability_service import setup
from test_spells import command, context

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spells import SpellService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.rules.checks import RecordedDice
from wayfarer.simulation.actions import PlayState, Wait
from wayfarer.simulation.spells import SpellCommand, SpellContext, active_spells


def resolve(play: PlayService, state: PlayState, value: SpellCommand) -> SpellContext:
    return context()


async def test_concurrent_completion_restart_and_private_rolls(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, spec())
    service = SpellService(play, resolve)
    await service.execute(cid, command(), authenticated_gm_id="gm")
    await play.execute(
        cid, Wait(id="time", actor_id="a", expected_revision=1, ticks=1), authenticated_actor_id="a"
    )
    play.rng = RecordedDice([3, 3, 3])
    complete = command(2, kind="complete")
    results = await asyncio.gather(
        *(service.execute(cid, complete, authenticated_gm_id="gm") for _ in range(3))
    )
    assert all(r == results[0] for r in results)
    assert results[0].energy_spent == 1
    saved = await play.store.read(cid)
    assert saved == await play.store.replay(cid)
    state = play._load(saved)
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 9
    assert active_spells(state.resources)[0].expires_at == 61

    def forbidden(play: PlayService, state: PlayState, value: SpellCommand) -> SpellContext:
        raise AssertionError("A committed retry must not resolve or roll")

    restarted = PlayService(
        AsyncSQLiteStore(tmp_path / "abilities.sqlite", 10), play.engine, rng=RecordedDice([])
    )
    assert (
        await SpellService(restarted, forbidden).execute(cid, complete, authenticated_gm_id="gm")
        == results[0]
    )
    assert await restarted.store.read(cid) == saved
    events = await CampaignAccess(restarted).events(cid, principal_id="b")
    assert "effective_target" not in str(events)
    assert "approved" not in str(events)
    with pytest.raises(ConflictError):
        await service.execute(
            cid, complete.model_copy(update={"id": "stale"}), authenticated_gm_id="gm"
        )


async def test_player_cannot_inject_context_and_invalid_target_is_atomic(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, spec())
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="director authority"):
        await SpellService(play, resolve).execute(cid, command(), authenticated_gm_id="a")

    def hidden(play: PlayService, state: PlayState, value: SpellCommand) -> SpellContext:
        return context().model_copy(update={"target_id": "unseen"})

    with pytest.raises(ValidationError, match="not perceived"):
        await SpellService(play, hidden).execute(cid, command(), authenticated_gm_id="gm")
    assert await play.store.read(cid) == before
