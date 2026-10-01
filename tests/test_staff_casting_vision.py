"""B239: remembered subjects, current sight, and the distinct B240 Staff touch."""

from pathlib import Path
from typing import Literal

import pytest
from test_combat_sensory_authority import change
from test_lock_spell_persistence import cast as cast_lock
from test_lock_spell_persistence import declare as declare_locks
from test_lock_spell_persistence import prepare as prepare_lock
from test_staff_casting import prepare

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.location import HumanBody, LastingInjury
from wayfarer.engine.rules.types.symptoms import SymptomDebt, SymptomEffect, SymptomSpec
from wayfarer.engine.simulation.actions import PlayState, Wait
from wayfarer.engine.simulation.magic.effects import dazed
from wayfarer.engine.simulation.magic.lock_bindings import LockChannel
from wayfarer.engine.simulation.magic.lock_host import DeclareLockChannel
from wayfarer.engine.simulation.magic.spells import SpellCommand, SpellResult, latest
from wayfarer.engine.simulation.magic.staff_casting import touching
from wayfarer.engine.simulation.magic.staff_casting_state import ObserveStaffTouch, intents
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService, TakeCombatTurn
from wayfarer.orchestration.locks import LockService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spells import SpellService
from wayfarer.orchestration.staff_casting import StaffCastingService


def symptoms(state: PlayState, *, active: bool = True) -> PlayState:
    return state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "symptom_effects": (
                        SymptomEffect(
                            id="blind",
                            actor_id="a",
                            pool_id="hp:a",
                            source_id="source",
                            spec=SymptomSpec(kind="blindness"),
                            active=active,
                        ),
                    ),
                    "symptom_debts": (
                        SymptomDebt(
                            id="blind-debt",
                            pool_id="hp:a",
                            source_id="source",
                            remaining=6 if active else 4,
                        ),
                    ),
                }
            )
        }
    )


def injured_eyes(state: PlayState, *, both: bool = True) -> PlayState:
    hp = next(p for p in state.resources.pools if p.id == "hp:a")
    assert hp.injury is not None
    eyes: tuple[Literal["left-eye", "right-eye"], ...] = (
        ("left-eye", "right-eye") if both else ("left-eye",)
    )
    injuries = tuple(
        LastingInjury(
            id=eye,
            location=eye,
            kind="disabled",
            duration="timed",
            inflicted_at=0,
            injury=1,
            recovery_at=10,
        )
        for eye in eyes
    )
    hp = hp.model_copy(
        update={
            "injury": hp.injury.model_copy(
                update={"anatomy": "human", "lasting_injuries": injuries}
            )
        }
    )
    return state.model_copy(
        update={
            "actors": tuple(
                a.model_copy(update={"body": HumanBody(anatomy="human")})
                if a.actor_id == "a"
                else a
                for a in state.actors
            ),
            "resources": state.resources.model_copy(
                update={"pools": tuple(hp if p.id == hp.id else p for p in state.resources.pools)}
            ),
        }
    )


async def start(cid: str, play: PlayService, command: SpellCommand) -> PlayState:
    state = play._load(await play.store.read(cid))
    await SpellService(play).execute(
        cid, command.model_copy(update={"expected_revision": state.revision}), principal_id="a"
    )
    return play._load(await play.store.read(cid))


async def complete(
    cid: str, play: PlayService, command: SpellCommand
) -> tuple[PlayState, SpellCommand, SpellResult]:
    state = play._load(await play.store.read(cid))
    await play.execute(
        cid,
        Wait(id="one-second", actor_id="a", expected_revision=state.revision, ticks=1),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    command = command.model_copy(
        update={"id": "complete", "kind": "complete", "expected_revision": state.revision}
    )
    dice = RecordedDice((2, 2, 2))
    play.rng = dice
    result = await SpellService(play).execute(cid, command, principal_id="a")
    assert dice.exhausted()
    return play._load(await play.store.read(cid)), command, result


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("cause", ["symptoms", "two-eyes", "one-eye"])
@pytest.mark.parametrize("staff", [True, False])
async def test_current_sight_controls_remembered_regular_subject(
    tmp_path: Path, backend: str, cause: str, staff: bool
) -> None:
    cid, play, original = await prepare(tmp_path, backend, distance=2)
    command = original if staff else original.model_copy(update={"cast_id": "plain"})
    await change(
        play,
        cid,
        symptoms if cause == "symptoms" else lambda s: injured_eyes(s, both=cause == "two-eyes"),
    )
    started = await start(cid, play, command)
    # B239 adds -5 to range only when both sight and actual contact are absent.
    target = 14 - (0 if staff else 2) - (0 if cause == "one-eye" else 5)
    assert "b" in {e.id for e in started.world.perspective("a").entities}
    assert latest(started.resources)[command.cast_id].skill == target
    after, _, result = await complete(cid, play, command)
    assert result.checks[0].effective_target == target
    assert result.checks[0].dice == (2, 2, 2)
    assert result.outcome == "active" and result.energy_spent == 1
    assert latest(after.resources)[command.cast_id].skill == target
    assert next(p.current for p in after.resources.pools if p.id == "fp:a") == 9
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("lose_touch", [False, True])
async def test_blind_staff_contact_removes_penalty_only_while_current(
    tmp_path: Path, backend: str, lose_touch: bool
) -> None:
    cid, play, command = await prepare(tmp_path, backend, distance=1, pointing=False)
    await change(play, cid, symptoms)
    state = play._load(await play.store.read(cid))
    await StaffCastingService(play).execute(
        cid,
        ObserveStaffTouch(
            id="touch", actor_id="a", expected_revision=state.revision, cast_id="cast"
        ),
        principal_id="gm",
    )
    started = await start(cid, play, command)
    original = latest(started.resources)["cast"]
    assert original.skill == 14
    if lose_touch:
        await StaffCastingService(play).execute(
            cid,
            ObserveStaffTouch(
                id="lost-touch",
                actor_id="a",
                expected_revision=started.revision,
                cast_id="cast",
                touching=False,
            ),
            principal_id="gm",
        )
    after, _, result = await complete(cid, play, command)
    assert "b" in {e.id for e in after.world.perspective("a").entities}
    assert touching(play.rules_context, after, intents(after.resources)[0]) is not lose_touch
    assert result.checks[0].effective_target == (8 if lose_touch else 14)
    effect = latest(after.resources)["cast"]
    assert (effect.cost, effect.hp_energy, effect.ready_at, effect.required_turns) == (
        original.cost,
        original.hp_energy,
        original.ready_at,
        original.required_turns,
    )
    assert result.energy_spent == 1 and effect.phase == "active"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("onset", [False, True])
async def test_blindness_onset_or_recovery_before_roll_refreshes_unseen_only(
    tmp_path: Path, backend: str, onset: bool
) -> None:
    cid, play, command = await prepare(tmp_path, backend, distance=5)
    await change(play, cid, lambda s: symptoms(s, active=not onset))
    started = await start(cid, play, command)
    original = latest(started.resources)["cast"]
    assert original.skill == (11 if onset else 6)
    await change(play, cid, lambda s: symptoms(s, active=onset))
    after, _, result = await complete(cid, play, command)
    effect = latest(after.resources)["cast"]
    assert result.checks[0].effective_target == (6 if onset else 11)
    assert (effect.cost, effect.hp_energy, effect.ready_at, effect.required_turns) == (
        original.cost,
        original.hp_energy,
        original.ready_at,
        original.required_turns,
    )
    assert result.energy_spent == 1 and effect.phase == "active"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_blindness_after_recorded_roll_does_not_rewrite_retry(
    tmp_path: Path, backend: str
) -> None:
    cid, play, command = await prepare(tmp_path, backend, distance=5)
    await start(cid, play, command)
    completed, complete_command, original = await complete(cid, play, command)
    effect = latest(completed.resources)["cast"]
    assert original.checks[0].effective_target == 11
    await change(play, cid, symptoms)
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    retried = await SpellService(play).execute(cid, complete_command, principal_id="a")
    assert retried == original
    assert latest(play._load(await play.store.read(cid)).resources)["cast"] == effect
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_blindness_does_not_admit_an_unlocated_subject(tmp_path: Path, backend: str) -> None:
    cid, play, command = await prepare(tmp_path, backend, visible=False)
    await change(play, cid, symptoms)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="not perceived"):
        await start(cid, play, command)
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_combat_final_concentration_uses_current_blindness(
    tmp_path: Path, backend: str
) -> None:
    cid, play, command = await prepare(tmp_path, backend, distance=2, combat=True, spell="daze")
    started = await start(cid, play, command)
    original = latest(started.resources)["cast"]
    assert original.skill == 14
    await change(play, cid, symptoms)
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="target-waits",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    dice = RecordedDice((2, 2, 2, 6, 6, 6))
    play.rng = dice
    result = await SpellService(play).execute(
        cid,
        command.model_copy(
            update={
                "id": "final-second",
                "kind": "concentrate",
                "expected_revision": state.revision,
            }
        ),
        principal_id="a",
    )
    assert dice.exhausted()
    after = play._load(await play.store.read(cid))
    effect = latest(after.resources)["cast"]
    assert result.checks[0].effective_target == 9
    assert result.energy_spent == 3 and dazed(after.resources, "b")
    assert (effect.cost, effect.hp_energy, effect.required_turns) == (
        original.cost,
        original.hp_energy,
        original.required_turns,
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("direct_touch", [False, True])
async def test_blind_regular_lock_preserves_its_existing_touch_exception(
    tmp_path: Path, backend: str, direct_touch: bool
) -> None:
    cid, play = await prepare_lock(tmp_path, backend)
    await declare_locks(play, cid)
    await change(play, cid, symptoms)
    state = play._load(await play.store.read(cid))
    await LockService(play).execute(
        cid,
        DeclareLockChannel(
            id="current-lock",
            actor_id="gm",
            expected_revision=state.revision,
            channel=LockChannel(
                id="current-lock",
                actor_id="a",
                target_id="chest",
                location_id="dock",
                spell_id="lockmaster",
                distance_yards=1,
                visible=True,
                touching=direct_touch,
            ),
        ),
        principal_id="gm",
    )
    result, _ = await cast_lock(
        play, cid, "lockmaster", "blind-lock", channel="current-lock", dice=(2, 2, 2)
    )
    assert result.checks[0].effective_target == (14 if direct_touch else 8)
