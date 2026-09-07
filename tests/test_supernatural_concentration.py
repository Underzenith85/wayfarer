"""Cross-service authority invariants; these are not source-certification fixtures."""

import asyncio
from pathlib import Path

import pytest
from test_abilities import command as ability_command
from test_abilities import context as ability_context
from test_abilities import spec, world
from test_ability_service import setup
from test_actions import campaign
from test_spell_service import resolve
from test_spells import command, context

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.abilities import AbilityService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spells import SpellService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.conformance import CoverageStatus, capability, require_verified
from wayfarer.simulation.abilities import apply_ability, interrupt_concentration
from wayfarer.simulation.actions import Wait
from wayfarer.simulation.concentration import require_idle_concentration
from wayfarer.simulation.resources import ResourceEvent, ResourceState
from wayfarer.simulation.spells import active_spells, apply_spell


@pytest.mark.parametrize("first", ["spell", "ability"])
async def test_services_share_commitment_and_cancel_releases_it(tmp_path: Path, first: str) -> None:
    cid, play = await setup(tmp_path, spec(), magic=True)
    spells, abilities = SpellService(play, resolve), AbilityService(play)
    if first == "spell":
        await spells.execute(cid, command(), authenticated_gm_id="gm")
    else:
        await abilities.execute(cid, ability_command(), principal_id="a")
    before = await play.store.read(cid)
    with pytest.raises(ConflictError, match="already concentrating"):
        if first == "spell":
            await abilities.execute(cid, ability_command(1), principal_id="a")
        else:
            await spells.execute(cid, command(1), authenticated_gm_id="gm")
    assert await play.store.read(cid) == before
    if first == "spell":
        await spells.execute(cid, command(1, kind="cancel"), authenticated_gm_id="gm")
        assert (
            await abilities.execute(cid, ability_command(2), principal_id="a")
        ).outcome == "concentrating"
    else:
        await abilities.execute(cid, ability_command(1, "cancel"), principal_id="a")
        await play.execute(
            cid,
            Wait(id="finish-second", actor_id="a", expected_revision=2, ticks=1),
            authenticated_actor_id="a",
        )
        assert (
            await spells.execute(cid, command(3), authenticated_gm_id="gm")
        ).outcome == "casting"
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_racing_spell_and_ability_commit_only_one_and_retry_survives_restart(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path, spec(), magic=True)
    results = await asyncio.gather(
        SpellService(play, resolve).execute(cid, command(), authenticated_gm_id="gm"),
        AbilityService(play).execute(cid, ability_command(), principal_id="a"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ConflictError) for result in results) == 1
    saved = await play.store.read(cid)
    assert saved["revision"] == 1
    restarted = PlayService(
        AsyncSQLiteStore(tmp_path / "abilities.sqlite", 10), play.engine, rng=RecordedDice([])
    )
    if not isinstance(results[0], BaseException):
        assert (
            await SpellService(restarted, resolve).execute(cid, command(), authenticated_gm_id="gm")
        ) == results[0]
    else:
        assert (
            await AbilityService(restarted).execute(cid, ability_command(), principal_id="a")
        ) == results[1]
    assert await play.store.read(cid) == saved == await play.store.replay(cid)


async def test_reducer_guard_survives_restart_and_missed_spell_deadline(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, spec(), magic=True)
    await SpellService(play, resolve).execute(cid, command(), authenticated_gm_id="gm")
    state = play._load(await play.store.read(cid)).resources
    restored = ResourceState.model_validate_json(state.model_dump_json()).model_copy(
        update={"game_time": 100}
    )
    # Activation and Detect analysis must both reject before costs or dice.
    for kind in ("activate", "analyze"):
        with pytest.raises(ConflictError, match="already concentrating"):
            apply_ability(
                restored,
                world(),
                ability_command(1, kind),
                spec(),
                ability_context(spec()),
                rng=RecordedDice([]),
                system=True,
            )
    require_idle_concentration(restored, "b")
    distracted = interrupt_concentration(restored, "a", "defense", distraction=True)
    with pytest.raises(ConflictError, match="already concentrating"):
        require_idle_concentration(distracted, "a")
    abandoned = interrupt_concentration(restored, "a", "move")
    require_idle_concentration(abandoned, "a")


async def test_ability_reducer_cannot_overlap_itself_or_start_a_spell(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, spec(), magic=True)
    state = play._load(await play.store.read(cid)).resources
    started, _, _ = apply_ability(
        state,
        world(),
        ability_command(),
        spec(),
        ability_context(spec()),
        rng=RecordedDice([]),
        system=True,
    )
    restored = ResourceState.model_validate_json(started.model_dump_json())
    with pytest.raises(ConflictError, match="already concentrating"):
        apply_ability(
            restored,
            world(),
            ability_command(1),
            spec(),
            ability_context(spec()),
            rng=RecordedDice([]),
            system=True,
        )
    with pytest.raises(ConflictError, match="already concentrating"):
        apply_spell(restored, command(1), context(), rng=RecordedDice([]), system=True)


async def test_active_spell_is_not_pending_concentration(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, spec(), magic=True)
    spells = SpellService(play, resolve)
    await spells.execute(cid, command(), authenticated_gm_id="gm")
    await play.execute(
        cid,
        Wait(id="time", actor_id="a", expected_revision=1, ticks=1),
        authenticated_actor_id="a",
    )
    play.rng = RecordedDice([3, 3, 3])
    await spells.execute(cid, command(2, kind="complete"), authenticated_gm_id="gm")
    play.rng = RecordedDice([])
    result = await AbilityService(play).execute(cid, ability_command(3), principal_id="a")
    assert result.outcome == "concentrating"
    assert len(active_spells(play._load(await play.store.read(cid)).resources)) == 1


@pytest.mark.parametrize("prefix", ["spell:", "ability:"])
async def test_scenario_cannot_seed_supernatural_execution(tmp_path: Path, prefix: str) -> None:
    _, play = await setup(tmp_path, spec(), magic=True)
    forged = ResourceState(
        events=(ResourceEvent(id=prefix + "forged", at=0, target_id="a", kind="{}"),)
    )
    with pytest.raises(ValidationError, match="cannot seed supernatural"):
        play.initial_state(campaign(play.engine), world(), forged, ())


def test_representative_abilities_are_partial_and_still_block_certification() -> None:
    assert capability("gurps.supernatural.abilities").status is CoverageStatus.PARTIAL
    with pytest.raises(ValidationError, match="not verified"):
        require_verified("gurps.supernatural.abilities")
