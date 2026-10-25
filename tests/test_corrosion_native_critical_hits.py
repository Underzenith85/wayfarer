"""B556 torso critical hits preserve physical B379 erosion provenance."""

from pathlib import Path

import pytest
from support.runtime import played
from test_composed_attack_host import declare
from test_corrosion_composed_host import fixture
from test_corrosion_critical_pause import establish_beam_knowledge, execute, prepare_response

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.critical_hit import body_hit_effects
from wayfarer.engine.simulation.equipment.corrosion_state import erosion
from wayfarer.engine.simulation.health.injury import InjuryResult
from wayfarer.orchestration.corrosion_generations import KEY
from wayfarer.orchestration.owner_damage_records import ChooseOwnerDamage, OwnerDamagePending
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import SetRealPlayClock, snapshot
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.replay import verify_commands


def table(row: int) -> tuple[int, int, int]:
    return next(
        (a, b, c) for a in range(1, 7) for b in range(1, 7) for c in range(1, 7) if a + b + c == row
    )


async def clock(play: PlayService, cid: str) -> None:
    state = play._load(await play.store.read(cid))
    await TaskService(play).execute(
        cid,
        SetRealPlayClock(id="clock", actor_id="gm", expected_revision=state.revision, running=True),
        principal_id="gm",
    )


async def accept_damage(play: PlayService, cid: str) -> object:
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, OwnerDamagePending)
    return await TaskService(play).execute(
        cid,
        ChooseOwnerDamage(
            id="accept",
            actor_id="a",
            expected_revision=state.revision,
            pending_id=pending.id,
            choice="accept",
        ),
        principal_id="alice",
    )


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("task", [False, True])
@pytest.mark.parametrize("row", range(3, 19))
async def test_all_native_body_rows_score_packet_then_erode_actual_armor(
    tmp_path: Path, backend: str, task: bool, row: int
) -> None:
    cid, play, source, _ = await fixture(tmp_path, backend, innate_beam_skill=True)
    await establish_beam_knowledge(play, cid)
    if task:
        await clock(play, cid)
    await declare(play, cid, source)
    effects = body_hit_effects(table(row))
    basic = (12 if effects.maximum_damage else 6) * effects.basic_multiplier
    dr = 2 if effects.halve_dr else 5
    injury = basic - dr
    major = injury > 5 or effects.force_major_wound
    packet = () if effects.maximum_damage else (3, 3)
    checks = (3, 3, 3) if major else ()
    play.rng = RecordedDice((1, 1, 1) + table(row) + packet + (() if task and packet else checks))
    result = await execute(play, cid, await prepare_response(play, cid, task))
    assert play.rng.exhausted()
    if task and packet:
        state = play._load(await play.store.read(cid))
        pending = snapshot(state).pending
        assert isinstance(pending, OwnerDamagePending) and pending.original == packet
        assert (
            pending.preparation.consequences and pending.preparation.consequences.corrosion_critical
        )
        play.rng = RecordedDice(checks)
        result = await accept_damage(play, cid)
        assert play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    hp = next(p for p in state.resources.pools if p.id == "hp:b")
    loss = erosion(state.resources, "plate-b")
    assert hp.current == 10 - injury
    assert hp.injury and hp.injury.shock == min(
        8 if effects.double_shock else 4, injury * (2 if effects.double_shock else 1)
    )
    assert loss and loss.basic_damage == basic and loss.total_loss == min(5, basic // 5)
    assert loss.protection.resistance == 5 and loss.protection.original_dr == 5
    event = next(e for e in state.resources.events if e.id.startswith("injury:attack-defense-use:"))

    wound = InjuryResult.model_validate_json(event.kind)
    assert wound.effective_resistance == dr and wound.injury == injury
    assert any(c.reason == "major-wound" for c in wound.checks) == major
    encounter = state.encounters[0]
    assert encounter.blocked_reason is None and encounter.pending_defense is None
    sword = next(i for i in state.resources.items if i.id == "sword-b")
    assert sword.owner_id == "b"
    assert sword.ready is (not effects.drop_all_held)
    assert bool(sword.world_ground_location_id) == effects.drop_all_held
    assert snapshot(state).pending is None
    # Maximum damage reaches its consequence immediately and consumes no damage dice.
    assert result is not None


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("task", [False, True])
async def test_native_drop_row_drops_real_sword_even_when_armor_stops_all_damage(
    tmp_path: Path, backend: str, task: bool
) -> None:
    cid, play, source, _ = await fixture(tmp_path, backend, innate_beam_skill=True)
    await establish_beam_knowledge(play, cid)
    if task:
        await clock(play, cid)
    await declare(play, cid, source)
    play.rng = RecordedDice((1, 1, 1, 4, 4, 4, 2, 3))
    await execute(play, cid, await prepare_response(play, cid, task))
    if task:
        play.rng = RecordedDice(())
        await accept_damage(play, cid)
    assert play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    loss = erosion(state.resources, "plate-b")
    assert loss and loss.total_loss == 1
    sword = next(i for i in state.resources.items if i.id == "sword-b")
    assert sword.owner_id == "b" and not sword.ready
    assert sword.world_ground_location_id == "dock"


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("row,packet", [(3, (1, 1)), (5, (1, 2))])
async def test_multiplied_subfive_roll_causes_real_erosion(
    tmp_path: Path, backend: str, row: int, packet: tuple[int, int]
) -> None:
    cid, play, source, _ = await fixture(tmp_path, backend, innate_beam_skill=True)
    await establish_beam_knowledge(play, cid)
    await declare(play, cid, source)
    play.rng = RecordedDice((1, 1, 1) + table(row) + packet)
    await execute(play, cid, await prepare_response(play, cid, False))
    assert play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    loss = erosion(state.resources, "plate-b")
    assert loss and loss.basic_damage == 6 and loss.total_loss == 1
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 9


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("task", [False, True])
@pytest.mark.parametrize("row,seed", [(3, 21727), (4, 16444), (6, 738), (12, 103)])
async def test_native_critical_packet_reexecutes_from_original_genesis(
    tmp_path: Path, backend: str, task: bool, row: int, seed: int
) -> None:
    cid, play, source, initial = await fixture(tmp_path, backend, innate_beam_skill=True)
    await establish_beam_knowledge(play, cid)
    if task:
        await clock(play, cid)
    await declare(play, cid, source)
    play.seeds = lambda: f"{seed:064x}"
    await execute(play, cid, await prepare_response(play, cid, task))
    if task and not body_hit_effects(table(row)).maximum_damage:
        await accept_damage(play, cid)
    final = await play.store.read(cid)
    state = play._load(final)
    assert state.encounters[0].blocked_reason is None
    history = await played(play.store, cid)
    bind = next(r for r in history if r.command_id == "bind")
    assert bind.command_input and KEY in bind.command_input
    replayed, checks = await verify_commands(
        initial,
        history,
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert play._load(replayed) == state
    assert {k: v for k, v in replayed.items() if k != "play_json"} == {
        k: v for k, v in final.items() if k != "play_json"
    }
    assert await play.store.replay(cid) == final
