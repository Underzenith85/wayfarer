"""SQLite transaction, authority, privacy and concurrent retry evidence."""

import asyncio
from pathlib import Path

import pytest
from test_abilities import spec
from test_ability_service import setup
from test_spells import command

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState, Wait
from wayfarer.engine.simulation.mechanics.spell_bindings import SpellEnvironment
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.spells import SpellCommand, active_spells
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spells import SpellService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


def resolve(play: RulesContext, state: PlayState, value: SpellCommand) -> SpellEnvironment:
    return SpellEnvironment(target_id="b")


async def test_concurrent_completion_restart_and_private_rolls(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, spec(), magic=True)
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

    def forbidden(play: RulesContext, state: PlayState, value: SpellCommand) -> SpellEnvironment:
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
    cid, play = await setup(tmp_path, spec(), magic=True)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="director authority"):
        await SpellService(play, resolve).execute(cid, command(), authenticated_gm_id="a")

    def hidden(play: RulesContext, state: PlayState, value: SpellCommand) -> SpellEnvironment:
        return SpellEnvironment(target_id="b").model_copy(update={"target_id": "unseen"})

    with pytest.raises(ValidationError, match="not perceived"):
        await SpellService(play, hidden).execute(cid, command(), authenticated_gm_id="gm")
    assert await play.store.read(cid) == before


async def test_missing_catalog_and_unpurchased_spell_reject_before_dice(tmp_path: Path) -> None:
    from wayfarer.engine.simulation.mechanics.spell_bindings import approved_context

    cid, play = await setup(tmp_path, spec())
    state = play._load(await play.store.read(cid))
    with pytest.raises(ValidationError, match="pinned learning catalog"):
        approved_context(play.rules_context, state, command(), SpellEnvironment(target_id="b"))
    cid, play = await setup(tmp_path / "magic", spec(), magic=True)
    state = play._load(await play.store.read(cid))
    with pytest.raises(ValidationError, match="not purchased and approved"):
        approved_context(
            play.rules_context, state, command(spell="daze"), SpellEnvironment(target_id="b")
        )


async def test_approved_values_cannot_be_supplied_by_environment(tmp_path: Path) -> None:
    from pydantic import ValidationError as SchemaError

    from wayfarer.engine.simulation.mechanics.spell_bindings import approved_context

    cid, play = await setup(tmp_path, spec(), magic=True)
    state = play._load(await play.store.read(cid))
    bound = approved_context(play.rules_context, state, command(), SpellEnvironment(target_id="b"))
    assert bound.skill == 14 and bound.magery == 1 and bound.ht == 10 and bound.will == 10
    assert bound.learned == ("light",)
    assert bound.build_revision != "approved"
    with pytest.raises(SchemaError):
        SpellEnvironment.model_validate({"target_id": "b", "skill": 100, "learned": ["daze"]})
    actor = state.actors[0]
    changed = actor.model_copy(update={"approval": None})
    state = state.model_copy(update={"actors": (changed,) + state.actors[1:]})
    with pytest.raises(ValidationError, match="approved build"):
        approved_context(play.rules_context, state, command(), SpellEnvironment(target_id="b"))
