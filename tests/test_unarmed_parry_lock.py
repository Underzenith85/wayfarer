"""B403: a skilled parry licenses one arm-lock attempt on the following turn."""

from pathlib import Path

import pytest
from test_unarmed import action, defend, setup, state_of, wait
from test_unarmed_integrations import checkpoint

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.unarmed.fighters import fighter
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.tactical_view import choices
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


async def parry(cid: str, play: PlayService) -> None:
    await action(cid, play, "a", "kick")
    play.rng = RecordedDice((2, 2, 2, 2, 2, 2))
    await defend(cid, play, defense="parry")
    assert play.rng.exhausted()


async def lock(cid: str, play: PlayService) -> object:
    return await action(
        cid,
        play,
        "b",
        "arm_lock",
        hands=("left-hand", "right-hand"),
        skill="skill:judo",
        location="right-arm",
        enter=True,
    )


async def test_parry_to_lock_survives_restart_and_defense_pause(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await parry(cid, play)
    state = await state_of(cid, play)
    assert fighter(state.encounters[0], "b").unarmed_lock_opportunity == ("a", "skill:judo", 1)
    restarted = PlayService(
        AsyncSQLiteStore(tmp_path / "melee.sqlite"), play.engine, rng=RecordedDice(())
    )
    await lock(cid, restarted)
    pending = await state_of(cid, restarted)
    encounter = pending.encounters[0]
    assert encounter.pending_unarmed is not None
    assert encounter.pending_unarmed.action == "arm_lock"
    assert encounter.pending_unarmed.grip_id is None and not encounter.grips
    assert fighter(encounter, "b").position == fighter(encounter, "a").position
    command = ChooseDefense(
        id="lock-defense",
        actor_id="a",
        expected_revision=pending.revision,
        encounter_id="fight",
        defense="none",
    )
    restarted.rng = RecordedDice((2, 2, 2))
    result = await CombatService(restarted).execute(cid, command, authenticated_actor_id="a")
    after = await state_of(cid, restarted)
    grip = after.encounters[0].grips[0]
    assert grip.arm_lock and grip.holder_id == "b" and grip.target_id == "a"
    assert grip.location == "right-arm" and len(grip.hands) == 2
    assert after.encounters[0].unarmed_history[-1].checks[0].effective_target == 10
    assert restarted.rng.exhausted()
    assert (
        await CombatService(restarted).execute(cid, command, authenticated_actor_id="a") == result
    )
    assert await state_of(cid, restarted) == after


async def test_parry_opportunity_expires_when_first_turn_is_spent(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await parry(cid, play)
    await wait(cid, play, "b")
    await wait(cid, play, "a")
    before = await state_of(cid, play)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="first turn"):
        await lock(cid, play)
    assert await state_of(cid, play) == before and play.rng.exhausted()


async def test_parry_lock_revalidates_hands_before_dice(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await parry(cid, play)
    # A later grapple now controls one arm, invalidating the free-hand precondition.
    from wayfarer.engine.simulation.combat.unarmed_records import Grip

    state = await state_of(cid, play)
    encounter = state.encounters[0]
    a = fighter(encounter, "a")
    b = fighter(encounter, "b").model_copy(update={"position": a.position})
    encounter = CombatEngine._replace(encounter, b).model_copy(
        update={
            "close_pairs": (("a", "b"),),
            "grips": (
                Grip(
                    id="disruption",
                    holder_id="a",
                    target_id="b",
                    hands=("left-hand",),
                    location="right-arm",
                ),
            ),
        }
    )
    await checkpoint(cid, play, state.model_copy(update={"encounters": (encounter,)}))
    before = await state_of(cid, play)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError):
        await action(
            cid,
            play,
            "b",
            "arm_lock",
            hands=("left-hand", "right-hand"),
            skill="skill:judo",
            location="right-arm",
        )
    assert await state_of(cid, play) == before and play.rng.exhausted()


async def test_failed_parry_does_not_license_lock(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await action(cid, play, "a", "kick")
    play.rng = RecordedDice((2, 2, 2, 4, 4, 4, 3))
    await defend(cid, play, defense="parry")
    before = await state_of(cid, play)
    assert fighter(before.encounters[0], "b").unarmed_lock_opportunity is None
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="first turn"):
        await lock(cid, play)
    assert await state_of(cid, play) == before and play.rng.exhausted()


async def test_hex_lock_choices_respect_visibility_and_expiration(tmp_path: Path) -> None:
    from test_tactical import setup as tactical_setup

    cid, play = await tactical_setup(tmp_path, unarmed=True)
    await parry(cid, play)
    state = await state_of(cid, play)
    play.rng = RecordedDice(())
    offered = choices(play, state, state.encounters[0], "b", frozenset({"a", "b"}))
    locks = [choice for choice in offered if "Arm lock after parry" in choice.label]
    assert len(locks) == 2
    assert not any(
        "Arm lock after parry" in choice.label
        for choice in choices(play, state, state.encounters[0], "b", frozenset({"b"}))
    )
    assert play.rng.exhausted()
    await wait(cid, play, "b")
    await wait(cid, play, "c")
    await wait(cid, play, "a")
    state = await state_of(cid, play)
    assert not any(
        "Arm lock after parry" in choice.label
        for choice in choices(play, state, state.encounters[0], "b", frozenset({"a", "b"}))
    )
