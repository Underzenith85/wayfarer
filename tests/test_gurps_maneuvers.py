"""Independent B363-366 maneuver expectations, provisional 2004 baseline."""

from pathlib import Path

import pytest
from test_gurps_melee import setup

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat import CombatResult
from wayfarer.engine.simulation.mechanics.gurps_melee import defense_value
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService, ResumeInterruptedTurn, TakeCombatTurn
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


async def turn(
    cid: str, play: PlayService, actor: str, maneuver: str, **options: object
) -> CombatResult:
    state = play._load(await play.store.read(cid))
    return await CombatService(play).execute(
        cid,
        {
            "kind": "take_combat_turn",
            "id": f"turn-{state.revision}",
            "expected_revision": state.revision,
            "actor_id": actor,
            "encounter_id": "fight",
            "maneuver": maneuver,
            **options,
        },
        authenticated_actor_id=actor,
    )


async def defend(
    cid: str, play: PlayService, actor: str, defense: str = "none", **options: object
) -> CombatResult:
    state = play._load(await play.store.read(cid))
    return await CombatService(play).execute(
        cid,
        {
            "kind": "choose_defense",
            "id": f"defend-{state.revision}",
            "expected_revision": state.revision,
            "actor_id": actor,
            "encounter_id": "fight",
            "defense": defense,
            **options,
        },
        authenticated_actor_id=actor,
    )


@pytest.mark.parametrize(("option", "target", "damage"), [("determined", 17, 4), ("strong", 13, 6)])
async def test_all_out_attack_bonus_and_forbidden_defense(
    tmp_path: Path, option: str, target: int, damage: int
) -> None:
    cid, play = await setup(tmp_path)
    await turn(
        cid,
        play,
        "a",
        "all_out_attack",
        item_id="sword-a",
        target_id="b",
        mode_id="swing",
        attack_option=option,
    )
    play.rng = RecordedDice([4, 4, 4, 3, 3, 3, 3])
    result = await defend(cid, play, "b")
    assert result.injury is not None
    assert result.injury.attack.effective_target == target
    assert result.injury.basic_damage == damage
    state = play._load(await play.store.read(cid))
    actor = state.encounters[0].participants[0]
    for defense in ("dodge", "parry", "block"):
        with pytest.raises(ValidationError, match="forbids"):
            defense_value(play.rules_context, state, actor, defense)


async def test_evaluate_stacks_expires_and_survives_defense(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    for _ in range(3):
        await turn(cid, play, "a", "evaluate", target_id="b")
        await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    play.rng = RecordedDice([4, 4, 4, 2])
    result = await defend(cid, play, "b")
    assert result.injury is not None and result.injury.attack.effective_target == 16
    await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "a", "do_nothing")
    actor = (play._load(await play.store.read(cid))).encounters[0].participants[0]
    assert actor.maneuver_state.evaluate_bonus == 0


async def test_move_attack_cap_and_no_parry(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await turn(cid, play, "a", "move_and_attack", item_id="sword-a", target_id="b", mode_id="swing")
    play.rng = RecordedDice([4, 4, 4])
    result = await defend(cid, play, "b")
    assert result.injury is not None and result.injury.attack.effective_target == 9
    state = play._load(await play.store.read(cid))
    actor = state.encounters[0].participants[0]
    with pytest.raises(ValidationError, match="forbids"):
        defense_value(play.rules_context, state, actor, "parry")
    assert defense_value(play.rules_context, state, actor, "dodge")[0] is not None


async def test_feint_independent_margins(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    play.rng = RecordedDice([3, 3, 3, 4, 4, 4])
    await turn(cid, play, "a", "feint", item_id="sword-a", target_id="b", mode_id="swing")
    actor = (play._load(await play.store.read(cid))).encounters[0].participants[0]
    assert actor.maneuver_state.feint_penalty == 3  # skill 13: margins 4 versus 1
    await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    play.rng = RecordedDice([4, 4, 4, 3, 3, 3, 2])
    result = await defend(cid, play, "b", "parry", item_id="sword-b")
    assert result.injury is not None and result.injury.defense is not None
    assert result.injury.defense.effective_target == 7  # 13 // 2 + 3 + shield DB 1 - 3


async def test_all_out_double_is_two_durable_attacks_one_turn(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await turn(
        cid,
        play,
        "a",
        "all_out_attack",
        item_id="sword-a",
        target_id="b",
        mode_id="swing",
        attack_option="double",
    )
    play.rng = RecordedDice([5, 5, 5])
    await defend(cid, play, "b")
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].pending_defense is not None
    assert state.encounters[0].current_actor_id == "a"
    assert state.resources.game_time == 0
    play.rng = RecordedDice([5, 5, 5])
    await defend(cid, play, "b")
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].pending_defense is None
    assert state.encounters[0].current_actor_id == "b"
    assert len(state.encounters[0].wounds) == 2


async def test_all_out_defense_double_rolls_second_only_after_failure(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await turn(cid, play, "a", "all_out_defense", defense_option="double")
    await turn(cid, play, "b", "attack", item_id="sword-b", target_id="a", mode_id="swing")
    play.rng = RecordedDice([4, 4, 4, 4, 4, 4, 2, 2, 2])
    result = await defend(cid, play, "a", "parry", second_defense="dodge")
    assert result.injury is not None and result.injury.second_defense is not None
    assert result.injury.injury == 0


async def test_wait_restart_resume_once(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await turn(
        cid,
        play,
        "a",
        "wait",
        wait_trigger={
            "actor_id": "b",
            "action": "attack",
            "reaction": "attack",
            "item_id": "sword-a",
            "reaction_target_id": "b",
            "mode_id": "swing",
        },
    )
    paused = await turn(cid, play, "b", "attack", item_id="sword-b", target_id="a", mode_id="swing")
    assert paused.code == "combat.wait_triggered"
    assert isinstance(play.store, AsyncSQLiteStore)
    play = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    play.rng = RecordedDice([5, 5, 5])
    await defend(cid, play, "b")
    state = play._load(await play.store.read(cid))
    interrupt = state.encounters[0].wait_interrupt
    assert interrupt is not None and interrupt.ready
    command = ResumeInterruptedTurn(
        id="resume", actor_id="b", expected_revision=state.revision, encounter_id="fight"
    )
    play.rng = RecordedDice([])
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="b")
    assert (play._load(await play.store.read(cid))).encounters[0].pending_defense is not None
    assert await CombatService(play).execute(cid, command, authenticated_actor_id="b") == result
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid, command.model_copy(update={"id": "resume-again"}), authenticated_actor_id="b"
        )
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_concentrate_defense_distraction(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await turn(cid, play, "a", "concentrate")
    await turn(cid, play, "b", "attack", item_id="sword-b", target_id="a", mode_id="swing")
    play.rng = RecordedDice([4, 4, 4, 2, 2, 2, 4, 4, 4])
    await defend(cid, play, "a", "parry")
    actor = (play._load(await play.store.read(cid))).encounters[0].participants[0]
    assert not actor.maneuver_state.concentrating


async def test_illegal_aim_melee_and_overlong_step_leave_state(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    before = play._load(await play.store.read(cid))
    with pytest.raises(ValidationError, match="ranged"):
        await turn(cid, play, "a", "aim", item_id="sword-a", target_id="b")
    assert play._load(await play.store.read(cid)) == before
    with pytest.raises(ValidationError, match="allowance"):
        await turn(cid, play, "a", "evaluate", target_id="b", destination={"x": 0, "y": 3})
    assert play._load(await play.store.read(cid)) == before


def test_unknown_parameters_and_options_reject() -> None:
    with pytest.raises(ValueError):
        TakeCombatTurn(
            id="bad",
            actor_id="a",
            expected_revision=0,
            encounter_id="fight",
            maneuver="all_out_attack",
            attack_option="invented",  # type: ignore[arg-type]
        )


async def test_aim_accumulates_and_active_defense_loses_bonus(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, ranged_fixture=True)
    for expected in (2, 3, 4, 4):
        await turn(cid, play, "a", "aim", item_id="sword-a", target_id="b", mode_id="throw-fixture")
        state = play._load(await play.store.read(cid))
        assert state.encounters[0].participants[0].maneuver_state.aim_bonus == expected
        if expected != 4 or state.revision < 8:
            await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "b", "attack", item_id="sword-b", target_id="a", mode_id="swing")
    play.rng = RecordedDice([4, 4, 4, 2, 2, 2])
    await defend(cid, play, "a", "parry")
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].participants[0].maneuver_state.aim_bonus == 0


async def test_all_out_feint_applies_to_same_turn_attack(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    play.rng = RecordedDice([3, 3, 3, 4, 4, 4])
    await turn(
        cid,
        play,
        "a",
        "all_out_attack",
        item_id="sword-a",
        target_id="b",
        mode_id="swing",
        attack_option="feint",
    )
    play.rng = RecordedDice([4, 4, 4, 3, 3, 3, 2])
    result = await defend(cid, play, "b", "parry")
    assert result.injury is not None and result.injury.defense is not None
    assert result.injury.defense.effective_target == 7


async def test_enhanced_defense_persists_until_new_selection(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await turn(cid, play, "a", "all_out_defense", defense_option="parry")
    await turn(cid, play, "b", "do_nothing")
    state = play._load(await play.store.read(cid))
    value, _ = defense_value(
        play.rules_context, state, state.encounters[0].participants[0], "parry"
    )
    assert value is not None and value.value == 11
    await turn(cid, play, "a", "do_nothing")
    state = play._load(await play.store.read(cid))
    value, _ = defense_value(
        play.rules_context, state, state.encounters[0].participants[0], "parry"
    )
    assert value is not None and value.value == 9


async def test_posture_step_and_attack_cannot_also_move(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await turn(cid, play, "a", "evaluate", target_id="b", posture="kneeling", facing="south")
    actor = play._load(await play.store.read(cid)).encounters[0].participants[0]
    assert actor.posture == "kneeling" and actor.facing == "south"
    await turn(cid, play, "b", "do_nothing")
    with pytest.raises(ValidationError, match="posture step"):
        await turn(
            cid,
            play,
            "a",
            "attack",
            target_id="b",
            item_id="sword-a",
            mode_id="swing",
            posture="standing",
            destination={"x": 0, "y": 1},
        )


async def test_double_stops_after_weapon_dropped_without_rolling_back(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    await turn(
        cid,
        play,
        "a",
        "all_out_attack",
        item_id="sword-a",
        target_id="b",
        mode_id="swing",
        attack_option="double",
    )
    play.rng = RecordedDice([6, 6, 6, 3, 3, 4])
    result = await defend(cid, play, "b")
    assert result.injury is not None and result.injury.critical_table == (3, 3, 4)
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].pending_defense is None
    assert state.encounters[0].current_actor_id == "b"
    assert not next(i for i in state.resources.items if i.id == "sword-a").ready


async def test_unready_after_attack_requires_ready_and_forbids_double(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, ready_after_attack=True)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="usable twice"):
        await turn(
            cid,
            play,
            "a",
            "all_out_attack",
            item_id="sword-a",
            target_id="b",
            mode_id="swing",
            attack_option="double",
        )
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    play.rng = RecordedDice([5, 5, 5])
    await defend(cid, play, "b")
    await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "a", "ready", item_id="sword-a")
    state = play._load(await play.store.read(cid))
    assert next(i for i in state.resources.items if i.id == "sword-a").ready


async def test_second_defense_unavailable_rejects_before_attack_roll(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    before = play._load(await play.store.read(cid))
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="Double"):
        await defend(cid, play, "b", "parry", second_defense="dodge")
    assert play._load(await play.store.read(cid)) == before
