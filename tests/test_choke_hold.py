"""Independent Choke Hold expectations from Campaigns fourth printing B404/B436."""

from pathlib import Path

import pytest
from test_tactical import setup as tactical_setup
from test_unarmed import action, defend, state_of, wait
from test_unarmed_integrations import checkpoint

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.unarmed.fighters import fighter
from wayfarer.engine.simulation.health.hazards import HazardCommand, apply_hazard
from wayfarer.engine.simulation.resources import Advance
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import (
    CombatService,
    EndEncounter,
    ResolveChokeEffects,
    TakeUnarmedTurn,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


async def setup(tmp_path: Path, holder: str = "a") -> tuple[str, PlayService]:
    cid, play = await tactical_setup(tmp_path, unarmed=True)
    state = await state_of(cid, play)
    encounter = state.encounters[0]
    target = "b" if holder == "a" else "a"
    defender = fighter(encounter, target).model_copy(
        update={"hex_facing": 0 if holder == "a" else 3}
    )
    await checkpoint(
        cid,
        play,
        state.model_copy(update={"encounters": (CombatEngine._replace(encounter, defender),)}),
    )
    if holder == "b":
        await wait(cid, play, "a")
    return cid, play


async def hold(cid: str, play: PlayService, holder: str = "a", skill: str = "skill:judo") -> str:
    state = await state_of(cid, play)
    target = "b" if holder == "a" else "a"
    command = TakeUnarmedTurn.model_validate(
        {
            "id": "choke-hold",
            "actor_id": holder,
            "expected_revision": state.revision,
            "encounter_id": "fight",
            "action": "grapple",
            "skill": skill,
            "target_id": target,
            "location": "neck",
            "hands": ("left-hand", "right-hand"),
            "enter_close_combat": True,
            "choke_hold": True,
        }
    )
    play.rng = RecordedDice(())
    result = await CombatService(play).execute(cid, command, authenticated_actor_id=holder)
    assert result.available == ("none",)
    pending = await state_of(cid, play)
    assert pending.encounters[0].pending_unarmed is not None
    assert pending.encounters[0].pending_unarmed.choke_hold
    assert isinstance(play.store, AsyncSQLiteStore)
    restarted = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice(()))
    assert (
        await CombatService(restarted).execute(cid, command, authenticated_actor_id=holder)
        == result
    )
    play.rng = RecordedDice((2, 2, 2))
    await defend(cid, play)
    state = await state_of(cid, play)
    trace = state.encounters[0].unarmed_history[-1]
    assert trace.checks[0].effective_target == 8
    assert trace.injury == 0 and trace.basic_damage == 0
    assert fighter(state.encounters[0], target).grappled
    assert play.rng.exhausted()
    grip = state.encounters[0].grips[0]
    assert grip.choke_hold and grip.hazard_id is not None
    return grip.id


async def settle(cid: str, play: PlayService, grip: str, target: str) -> None:
    state = await state_of(cid, play)
    command = ResolveChokeEffects(
        id=f"settle-{state.revision}",
        actor_id=target,
        expected_revision=state.revision,
        encounter_id="fight",
        grip_id=grip,
    )
    result = await CombatService(play).execute(cid, command, authenticated_actor_id=target)
    after = await state_of(cid, play)
    assert isinstance(play.store, AsyncSQLiteStore)
    restarted = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice(()))
    assert (
        await CombatService(restarted).execute(cid, command, authenticated_actor_id=target)
        == result
    )
    assert await state_of(cid, restarted) == after


@pytest.mark.parametrize("holder", ["a", "b"])
@pytest.mark.parametrize("skill", ["skill:judo", "skill:wrestling"])
async def test_choke_hold_ticks_on_holders_following_turn_and_replays(
    tmp_path: Path, holder: str, skill: str
) -> None:
    cid, play = await setup(tmp_path, holder)
    grip = await hold(cid, play, holder, skill)
    target = "b" if holder == "a" else "a"
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="holder"):
        await settle(cid, play, grip, target)
    if holder == "a":
        await wait(cid, play, "b")
    await wait(cid, play, "c")
    if holder == "b":
        with pytest.raises(ValidationError, match="holder"):
            await settle(cid, play, grip, target)
        await wait(cid, play, "a")
    before = await state_of(cid, play)
    with pytest.raises(ConflictError, match="suffocation"):
        await wait(cid, play, holder)
    assert await state_of(cid, play) == before
    await settle(cid, play, grip, target)
    after = await state_of(cid, play)
    assert next(p for p in after.resources.pools if p.id == f"fp:{target}").current == 9
    assert after.encounters[0].current_actor_id == holder
    with pytest.raises(ValidationError, match="holder"):
        await settle(cid, play, grip, target)
    await wait(cid, play, holder)
    assert play.rng.exhausted()


async def test_escape_before_holder_turn_cancels_due_tick(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "b")
    grip = await hold(cid, play, "b")
    await wait(cid, play, "c")
    play.rng = RecordedDice((2, 2, 2, 5, 5, 5))
    await action(cid, play, "a", "break_free", grip=grip)
    state = await state_of(cid, play)
    trace = state.encounters[0].unarmed_history[-1]
    assert trace.won and not state.encounters[0].grips
    assert trace.checks[1].effective_target == 16
    assert not state.resources.hazards[0].active
    assert next(p for p in state.resources.pools if p.id == "fp:a").current == 10
    assert play.rng.exhausted()


async def test_optional_crushing_damage_gets_plus_three(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    grip = await hold(cid, play)
    await wait(cid, play, "b")
    await wait(cid, play, "c")
    await settle(cid, play, grip, "b")
    play.rng = RecordedDice((3, 4, 4, 3, 3, 4))
    await action(cid, play, "a", "strangle", grip=grip)
    state = await state_of(cid, play)
    trace = state.encounters[0].unarmed_history[-1]
    assert trace.checks[0].effective_target == 13
    assert trace.basic_damage == 2 and trace.injury == 3
    assert state.resources.hazards[0].cycle == 1
    assert state.resources.hazards[0].combat_turn is not None
    assert state.resources.hazards[0].combat_turn.round == 3
    assert play.rng.exhausted()


@pytest.mark.parametrize(
    "change",
    [
        {"skill": "attribute:dx"},
        {"hands": ("left-hand",)},
        {"location": "torso"},
        {"enter_close_combat": False},
        {"action": "kick"},
    ],
)
async def test_invalid_choke_intent_rejects_before_dice(
    tmp_path: Path, change: dict[str, object]
) -> None:
    cid, play = await setup(tmp_path)
    before = await state_of(cid, play)
    command = {
        "id": "invalid",
        "kind": "take_unarmed_turn",
        "actor_id": "a",
        "expected_revision": before.revision,
        "encounter_id": "fight",
        "action": "grapple",
        "target_id": "b",
        "location": "neck",
        "skill": "skill:judo",
        "hands": ("left-hand", "right-hand"),
        "enter_close_combat": True,
        "choke_hold": True,
        **change,
    }
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError):
        await CombatService(play).execute(cid, command, authenticated_actor_id="a")
    assert await state_of(cid, play) == before and play.rng.exhausted()


async def test_partial_release_rejected_then_full_release_stops_exposure(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path)
    grip = await hold(cid, play)
    await wait(cid, play, "b")
    await wait(cid, play, "c")
    await settle(cid, play, grip, "b")
    before = await state_of(cid, play)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="both hands"):
        await action(cid, play, "a", "release", grip=grip, hands=("left-hand",))
    assert await state_of(cid, play) == before
    await action(cid, play, "a", "release", grip=grip)
    after = await state_of(cid, play)
    assert not after.encounters[0].grips and not after.resources.hazards[0].active
    assert not fighter(after.encounters[0], "b").grappled
    assert next(p for p in after.resources.pools if p.id == "fp:b").current == 9


async def test_ending_combat_keeps_no_air_exposure_on_shared_clock(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await hold(cid, play)
    state = await state_of(cid, play)
    await CombatService(play).execute(
        cid,
        EndEncounter(
            id="end-hold",
            actor_id="gm",
            expected_revision=state.revision,
            encounter_id="fight",
            reason="scene-ended",
        ),
        authenticated_actor_id="gm",
    )
    state = await state_of(cid, play)
    hazard = state.resources.hazards[0]
    assert hazard.active and hazard.combat_turn is None
    assert hazard.no_air_since == 0 and hazard.due == 1
    resources = play.engine.resources.apply(
        state.resources,
        Advance(
            id="next-second",
            actor_id="b",
            expected_revision=state.resources.revision,
            to=1,
        ),
        system=True,
        rng=play.rng,
    )
    resources, result = apply_hazard(
        resources,
        HazardCommand(
            id="ordinary-exposure",
            actor_id="b",
            expected_revision=resources.revision,
            kind="resolve",
            hazard_id=hazard.spec.id,
        ),
        hazard,
        rng=play.rng,
        system=True,
    )
    assert result.fp_lost == 1
    assert next(p for p in resources.pools if p.id == "fp:b").current == 9
