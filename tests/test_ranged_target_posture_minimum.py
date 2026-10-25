"""B345 final voluntary targets join B551 in real ranged source hosts."""

import json
import secrets
from functools import cache
from pathlib import Path

import pytest
from test_composed_attack_host import declare, defense, fixture
from test_drowsy_toxins import produce
from test_gurps_maneuvers import turn
from test_ranged_target_posture_hosts import charged

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.simulation.health.toxins import DrinkCommand
from wayfarer.errors import ValidationError
from wayfarer.orchestration import ranged_target_posture_generations as generations
from wayfarer.orchestration.combat import ChooseDefense, CombatService
from wayfarer.orchestration.drinking import DrinkingEnvironment, DrinkingService
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.opponent_attack_records import BeginOpponentAttack
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.spells import SpellService
from wayfarer.orchestration.task_records import SetRealPlayClock
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.replay import verify_commands


@cache
def floor_seed() -> str:
    for number in range(100000):
        seed = f"{number:064x}"
        rng = SeededRandom(seed)
        if tuple(rng.randbelow(6) + 1 for _ in range(3)) == (2, 2, 2):
            return seed
    raise AssertionError("No final-target consequence seed")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_composed_final_two_refuses_before_launch_but_legacy_four_is_preserved(
    tmp_path: Path, backend: str, enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, source = await fixture(
        tmp_path, backend, society_knows_beam=True, skill_points=0, levels=1
    )
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "change_posture", posture="sitting")
    play.rng = RecordedDice((4, 4, 4))
    await produce(play, cid)
    monkeypatch.setattr(generations, "ACTIVE", enabled)
    before, history = await play.store.read(cid), await play.store.history(cid)
    play.rng = RecordedDice(())
    if enabled:
        with pytest.raises(ValidationError, match="effective skill at least 3"):
            await declare(play, cid, source)
        assert await play.store.read(cid) == before
        assert await play.store.history(cid) == history
    else:
        await declare(play, cid, source)
        monkeypatch.setattr(generations, "ACTIVE", True)
        play.rng = RecordedDice((2, 2, 2))
        _, result = await defense(play, cid)
        assert result.injury and result.injury.attack.effective_target == 4
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_composed_default_six_sitting_minus_two_tipsy_minus_one_allows_final_three(
    tmp_path: Path, backend: str
) -> None:
    cid, play, source = await fixture(
        tmp_path, backend, society_knows_beam=True, skill_points=0, levels=1
    )
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "change_posture", posture="sitting")
    play.rng = RecordedDice((5, 5, 5))
    result = await DrinkingService(play, lambda *_: DrinkingEnvironment()).execute(
        cid,
        DrinkCommand(
            id="source-tipsy",
            actor_id="a",
            expected_revision=(await play.store.read(cid))["revision"],
            kind="drink",
            drinks=3,
        ),
        principal_id="a",
    )
    assert result.level == "tipsy"
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng, play.seeds = secrets, floor_seed
    await declare(play, cid, source)
    _, defended = await defense(play, cid)
    assert defended.injury and defended.injury.attack.effective_target == 3
    assert defended.injury.attack.dice == (2, 2, 2)
    records = (await play.store.history(cid))[count:]
    identifiers = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in identifiers],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "composed-floor-replay"),
    )
    assert replayed == await play.store.read(cid) and len(checks) == 2
    assert all(check.folded and check.reexecuted for check in checks)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("route", ["defense", "original"])
async def test_composed_current_drowsy_before_first_original_refuses_without_new_entropy(
    tmp_path: Path, backend: str, route: str
) -> None:
    cid, play, source = await fixture(
        tmp_path, backend, society_knows_beam=True, skill_points=0, levels=1
    )
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "change_posture", posture="sitting")
    await declare(play, cid, source)
    play.rng = RecordedDice((4, 4, 4))
    await produce(play, cid)
    task = TaskService(play)
    if route == "original":
        state = play._load(await play.store.read(cid))
        await task.execute(
            cid,
            SetRealPlayClock(
                id="clock", actor_id="gm", expected_revision=state.revision, running=True
            ),
            principal_id="gm",
        )
    before, history = await play.store.read(cid), await play.store.history(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="effective skill at least 3"):
        if route == "original":
            state = play._load(before)
            pending = state.encounters[0].pending_defense
            assert pending
            await task.execute(
                cid,
                BeginOpponentAttack(
                    id="original",
                    actor_id="b",
                    expected_revision=state.revision,
                    encounter_id="fight",
                    attack_id=pending.id,
                ),
                principal_id="gm",
            )
        else:
            await defense(play, cid)
    assert play.rng.exhausted()
    assert await play.store.read(cid) == before and await play.store.history(cid) == history


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("points,drowsy,expected", [(4, False, 3), (4, True, 1), (8, True, 2)])
@pytest.mark.parametrize("enabled", [False, True])
async def test_paid_fireball_final_target_admission_precedes_original_and_preserves_legacy(
    tmp_path: Path,
    backend: str,
    points: int,
    drowsy: bool,
    expected: int,
    enabled: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid, play, release = await charged(tmp_path, backend, projectile_points=points)
    if drowsy:
        play.rng = RecordedDice((4, 4, 4))
        await produce(play, cid)
    state = play._load(await play.store.read(cid))
    release = release.model_copy(
        update={"expected_revision": state.revision, "hit_location": "left-eye"}
    )
    monkeypatch.setattr(generations, "ACTIVE", enabled)
    before, history = await play.store.read(cid), await play.store.history(cid)
    if enabled and expected < 3:
        play.rng = RecordedDice(())
        with pytest.raises(ValidationError, match="effective skill at least 3"):
            await SpellService(play).execute(cid, release, principal_id="a")
        assert play.rng.exhausted()
        assert await play.store.read(cid) == before and await play.store.history(cid) == history
        return
    count = len(history)
    play.rng, play.seeds = secrets, floor_seed
    await SpellService(play).execute(cid, release, principal_id="a")
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending and pending.attack_roll and pending.attack_roll.effective_target == expected
    monkeypatch.setattr(generations, "ACTIVE", not enabled)
    response = ChooseDefense(
        id="accept-original",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    result = await CombatService(play).execute(cid, response, principal_id="b")
    assert result.injury and result.injury.attack == pending.attack_roll
    records = (await play.store.history(cid))[count:]
    identifiers = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        before,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in identifiers],
        configuration_digest=play._load(before).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "missile-floor-replay"),
    )
    actual = await play.store.read(cid)
    # Fatigue's legacy extra-field insertion order can differ after event folding;
    # compare its canonical recorded data and every other campaign field exactly.
    assert {**replayed, "play_json": json.loads(replayed["play_json"])} == {
        **actual,
        "play_json": json.loads(actual["play_json"]),
    }
    assert len(checks) == 2
    assert all(check.folded and check.reexecuted for check in checks)
    play.rng = RecordedDice(())
    assert await CombatService(play).execute(cid, response, principal_id="b") == result
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_unrolled_paid_missile_rechecks_final_current_target_before_response_options(
    tmp_path: Path, backend: str
) -> None:
    cid, play, release = await charged(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    release = release.model_copy(update={"hit_location": "left-eye"})
    service = SpellService(play)
    await submit(
        play,
        cid,
        service.plan(
            play,
            member_for(state, "a"),
            release,
            principal_id="a",
            missile_attack=False,
            state=state,
        ),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    assert (
        state.encounters[0].pending_defense
        and state.encounters[0].pending_defense.attack_roll is None
    )
    play.rng = RecordedDice((4, 4, 4))
    await produce(play, cid)
    before, history = await play.store.read(cid), await play.store.history(cid)
    state = play._load(before)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="effective skill at least 3"):
        await CombatService(play).execute(
            cid,
            ChooseDefense(
                id="response",
                actor_id="b",
                expected_revision=state.revision,
                encounter_id="fight",
                defense="dodge",
                acrobatic_dodge=True,
            ),
            principal_id="b",
        )
    assert play.rng.exhausted()
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
