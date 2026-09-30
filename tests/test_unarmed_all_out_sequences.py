"""Selected Campaigns fourth printing B365: two attacks or a Feint then one attack."""

from pathlib import Path

import pytest
from test_unarmed import defend, setup, state_of, wait
from test_unarmed_integrations import arm_defender
from test_unarmed_wait import declare, resume

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.unarmed.records import UnarmedReaction
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService, TakeUnarmedTurn
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


async def command(cid: str, play: PlayService, **changes: object) -> TakeUnarmedTurn:
    state = await state_of(cid, play)
    return TakeUnarmedTurn.model_validate(
        {
            "id": f"sequence-{state.revision}",
            "actor_id": "a",
            "expected_revision": state.revision,
            "encounter_id": "fight",
            "action": "kick",
            "target_id": "b",
            "maneuver": "all_out_attack",
            "attack_option": "double",
            **changes,
        }
    )


async def test_double_repeated_kick_durable_two_attacks_one_turn(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    attack = await command(cid, play)
    play.rng = RecordedDice(())
    service = CombatService(play)
    result = await service.execute(cid, attack, principal_id="a")
    before = await state_of(cid, play)
    assert await service.execute(cid, attack, principal_id="a") == result
    assert await state_of(cid, play) == before
    with pytest.raises(ConflictError):
        await service.execute(cid, attack.model_copy(update={"id": "stale"}), principal_id="a")
    actor = before.encounters[0].participants[0]
    assert actor.maneuver_state.defense_forbidden and actor.maneuver_state.attacks_remaining == 1
    # DX 10, kick -2: 6 hits against 8. ST 10 thrust 1d-2: die 3 deals 1.
    play.rng = RecordedDice((2, 2, 2, 3))
    await defend(cid, play)
    after = await state_of(cid, play)
    encounter = after.encounters[0]
    assert encounter.current_actor_id == "a" and len(encounter.unarmed_history) == 1
    assert encounter.unarmed_history[0].checks[0].effective_target == 8
    assert encounter.pending_unarmed is not None
    assert encounter.participants[0].maneuver_state.attacks_remaining == 0
    assert encounter.participants[0].maneuver_state.defense_forbidden
    restarted = PlayService(AsyncSQLiteStore(tmp_path / "melee.sqlite"), play.engine)
    restarted.rng = RecordedDice((2, 2, 2, 3))
    await defend(cid, restarted)
    completed = await state_of(cid, restarted)
    assert completed.encounters[0].current_actor_id == "b"
    assert len(completed.encounters[0].unarmed_history) == 2
    assert next(p for p in completed.resources.pools if p.id == "hp:b").current == 8
    assert restarted.rng.exhausted()


async def test_double_mixed_punch_kick_and_defense_forfeiture(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    attack = await command(
        cid,
        play,
        action="punch",
        hands=("right-hand",),
        enter_close_combat=True,
        second_attack=UnarmedReaction(action="kick"),
    )
    await CombatService(play).execute(cid, attack, principal_id="a")
    play.rng = RecordedDice((3, 3, 3, 4))
    await defend(cid, play)
    pending = (await state_of(cid, play)).encounters[0].pending_unarmed
    assert pending is not None and pending.action == "kick"
    play.rng = RecordedDice((2, 2, 2, 3))
    await defend(cid, play)
    after = await state_of(cid, play)
    assert [t.action for t in after.encounters[0].unarmed_history] == ["punch", "kick"]
    assert after.encounters[0].close_pairs == (("a", "b"),)
    retaliation = await command(
        cid,
        play,
        actor_id="b",
        target_id="a",
        maneuver="attack",
        attack_option=None,
        second_attack=None,
    )
    await CombatService(play).execute(cid, retaliation, principal_id="b")
    pending = (await state_of(cid, play)).encounters[0].pending_unarmed
    assert pending is not None and pending.allowed == ("none",)


async def test_failed_first_kick_balance_check_cancels_second(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await CombatService(play).execute(cid, await command(cid, play), principal_id="a")
    # Attack 12 fails against 8; balance 12 fails against DX 10 and actor falls.
    play.rng = RecordedDice((4, 4, 4, 4, 4, 4))
    await defend(cid, play)
    encounter = (await state_of(cid, play)).encounters[0]
    assert encounter.pending_unarmed is None and encounter.current_actor_id == "b"
    assert len(encounter.unarmed_history) == 1 and encounter.participants[0].posture == "prone"
    assert encounter.participants[0].maneuver_state.defense_forbidden
    assert encounter.participants[0].maneuver_state.attacks_remaining == 0
    assert play.rng.exhausted()


@pytest.mark.parametrize(
    ("first", "second", "penalty"), [((2, 2, 2), (3, 3, 3), 3), ((4, 4, 4), (3, 3, 3), 0)]
)
async def test_feint_contest_precedes_attack_and_replays(
    tmp_path: Path, first: tuple[int, ...], second: tuple[int, ...], penalty: int
) -> None:
    cid, play = await setup(tmp_path)
    attack = await command(
        cid,
        play,
        action="grapple",
        hands=("right-hand",),
        skill="skill:wrestling",
        enter_close_combat=True,
        attack_option="feint",
    )
    play.rng = RecordedDice(first + second)
    result = await CombatService(play).execute(cid, attack, principal_id="a")
    before = await state_of(cid, play)
    actor = before.encounters[0].participants[0]
    assert actor.maneuver_state.feint_penalty == penalty
    assert [c.effective_target for c in actor.maneuver_state.feint_rolls] == [11, 11]
    assert actor.maneuver_state.defense_forbidden
    play.rng = RecordedDice(())
    assert await CombatService(play).execute(cid, attack, principal_id="a") == result
    assert await state_of(cid, play) == before
    # Wrestling 11 attack, dodge 8 minus contest penalty, no extra attack bonus.
    play.rng = RecordedDice((3, 3, 3, 3, 3, 3))
    await defend(cid, play, defense="dodge")
    after = await state_of(cid, play)
    trace = after.encounters[0].unarmed_history[-1]
    assert [c.effective_target for c in trace.checks] == [11, 8 - penalty]
    assert len(after.encounters[0].unarmed_history) == 1
    assert after.encounters[0].current_actor_id == "b" and play.rng.exhausted()


@pytest.mark.parametrize(
    "changes",
    [
        {"attack_option": "feint"},
        {"attack_option": "strong", "second_attack": {"action": "kick"}},
        {"second_attack": {"action": "punch", "hands": ()}},
        {
            "action": "grapple",
            "skill": "skill:wrestling",
            "hands": ("right-hand",),
            "enter_close_combat": True,
        },
    ],
)
async def test_invalid_sequence_rejects_before_state_or_randomness(
    tmp_path: Path, changes: dict[str, object]
) -> None:
    cid, play = await setup(tmp_path)
    attack = await command(cid, play, **changes)
    before = await state_of(cid, play)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError):
        await CombatService(play).execute(cid, attack, principal_id="a")
    assert await state_of(cid, play) == before and play.rng.exhausted()


async def test_wait_interrupt_resumes_exactly_two_attacks(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await wait(cid, play, "a")
    await declare(cid, play)
    attack = await command(
        cid, play, action="punch", hands=("right-hand",), enter_close_combat=True
    )
    result = await CombatService(play).execute(cid, attack, principal_id="a")
    assert result.code == "combat.wait_triggered"
    from test_unarmed import action

    await action(cid, play, "b", "punch", target="a", hands=("right-hand",))
    play.rng = RecordedDice((5, 5, 5))  # Waiter misses, without shock to interrupted actor.
    await defend(cid, play)
    await resume(cid, play)
    play.rng = RecordedDice((3, 3, 3, 4))
    await defend(cid, play)
    play.rng = RecordedDice((3, 3, 3, 4))
    await defend(cid, play)
    after = (await state_of(cid, play)).encounters[0]
    assert [(t.actor_id, t.action) for t in after.unarmed_history] == [
        ("b", "punch"),
        ("a", "punch"),
        ("a", "punch"),
    ]
    assert after.current_actor_id == "b" and after.wait_interrupt is None
    assert after.participants[0].maneuver_state.defense_forbidden and play.rng.exhausted()


async def test_armed_parry_self_stun_cancels_other_usable_hand(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await arm_defender(cid, play)
    attack = await command(
        cid,
        play,
        action="punch",
        hands=("right-hand",),
        enter_close_combat=True,
        second_attack=UnarmedReaction(action="punch", hands=("left-hand",)),
    )
    await CombatService(play).execute(cid, attack, principal_id="a")
    before = await state_of(cid, play)
    defense = ChooseDefense(
        id="self-stun-parry",
        actor_id="b",
        expected_revision=before.revision,
        encounter_id="fight",
        defense="parry",
        item_id="sword-b",
        parry_mode_id="swing",
    )
    # DX 10 hits on 9; Parry and Broadsword succeed. Swing die 3 cripples
    # the striking right arm, and HT 12 fails the major-wound check.
    # The other free hand remains usable, but stunning forbids its attack.
    play.rng = RecordedDice((3, 3, 3, 2, 3, 3, 3, 3, 3, 3, 4, 4, 4))
    result = await CombatService(play).execute(cid, defense, principal_id="b")
    after = await state_of(cid, play)
    hp = next(p for p in after.resources.pools if p.id == "hp:a")
    assert hp.injury is not None and hp.injury.stunned
    encounter = after.encounters[0]
    assert encounter.pending_unarmed is None and encounter.current_actor_id == "b"
    assert len(encounter.unarmed_history) == 1
    assert encounter.participants[0].maneuver_state.defense_forbidden
    assert encounter.participants[0].maneuver_state.attacks_remaining == 0
    assert play.rng.exhausted()
    play.rng = RecordedDice(())
    assert await CombatService(play).execute(cid, defense, principal_id="b") == result
    assert await state_of(cid, play) == after and play.rng.exhausted()
