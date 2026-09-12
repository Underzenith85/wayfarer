"""Independent numeric cases: Basic Set B365, B370, B376, B382, B556-557.

The historical selected-printing equivalence audit remains outstanding.
"""

from pathlib import Path

import pytest
from test_unarmed import action, defend, setup, state_of

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Campaign, CommandReceipt
from wayfarer.orchestration.combat import ChooseDefense, CombatService, TakeUnarmedTurn
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


async def checkpoint(cid: str, play: PlayService, state: PlayState) -> None:
    def commit(campaign: Campaign) -> CommandReceipt:
        play.engine.validate(state)
        campaign["play_json"] = state.model_dump_json()
        return CommandReceipt(action="combat", outcome="fixture")

    await play.store.commit_turn(
        cid, f"fixture-{state.revision}", state.revision, "fixture", commit
    )


@pytest.mark.parametrize(
    ("table", "damage", "damage_dice", "extra"),
    [
        ((1, 1, 1), 3, (3,), ()),
        ((1, 1, 2), 1, (3,), ()),
        ((1, 1, 3), 2, (3,), ()),
        ((1, 1, 4), 4, (), ()),
        ((1, 1, 5), 1, (3,), (3, 3, 3)),
        ((1, 1, 6), 1, (3,), ()),
        ((1, 2, 6), 1, (3,), ()),
        ((1, 3, 6), 1, (3,), ()),
        ((1, 4, 6), 1, (3,), ()),
        ((1, 5, 6), 1, (3,), ()),
        ((1, 6, 6), 1, (3,), (3, 3, 3)),
        ((2, 6, 6), 1, (3,), (3, 3, 3)),
        ((3, 6, 6), 4, (), ()),
        ((4, 6, 6), 2, (3,), ()),
        ((5, 6, 6), 1, (3,), ()),
        ((6, 6, 6), 3, (3,), ()),
    ],
)
async def test_every_critical_hit_table_entry_replays(
    tmp_path: Path,
    table: tuple[int, ...],
    damage: int,
    damage_dice: tuple[int, ...],
    extra: tuple[int, ...],
) -> None:
    cid, play = await setup(tmp_path)
    await action(cid, play, "a", "kick")
    before = await state_of(cid, play)
    command = ChooseDefense(
        id="critical-hit",
        actor_id="b",
        expected_revision=before.revision,
        encounter_id="fight",
        defense="dodge",
    )
    play = PlayService(AsyncSQLiteStore(tmp_path / "melee.sqlite"), play.engine)
    play.rng = RecordedDice((1, 1, 1) + table + damage_dice + extra)
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="b")
    after = await state_of(cid, play)
    trace = after.encounters[0].unarmed_history[-1]
    assert trace.won and trace.blocked_reason is None
    assert trace.basic_damage == damage and trace.injury == damage
    assert len(trace.checks) == 1  # Critical hit bypasses the declared defense.
    assert trace.table_dice == table and trace.damage_dice == damage_dice
    assert next(p for p in after.resources.pools if p.id == "hp:b").current == 10 - damage
    assert play.rng.exhausted()
    restarted = PlayService(AsyncSQLiteStore(tmp_path / "melee.sqlite"), play.engine)
    restarted.rng = RecordedDice(())
    assert (
        await CombatService(restarted).execute(cid, command, authenticated_actor_id="b") == result
    )
    assert await state_of(cid, restarted) == after
    with pytest.raises(ConflictError):
        await CombatService(restarted).execute(
            cid, command.model_copy(update={"defense": "none"}), authenticated_actor_id="b"
        )


@pytest.mark.parametrize(
    ("table", "extra", "posture", "penalty"),
    [
        ((1, 1, 6), (), "prone", 0),
        ((1, 2, 6), (), "standing", -2),
        ((1, 3, 6), (), "standing", -2),
        ((1, 4, 6), (), "standing", -2),
        ((1, 5, 6), (2, 2, 2), "standing", 0),
        ((1, 5, 6), (3, 3, 3), "prone", 0),
    ],
)
async def test_critical_miss_balance_and_kick_trip(
    tmp_path: Path,
    table: tuple[int, ...],
    extra: tuple[int, ...],
    posture: str,
    penalty: int,
) -> None:
    cid, play = await setup(tmp_path)
    await action(cid, play, "a", "kick")
    play.rng = RecordedDice((6, 6, 6) + table + extra)
    await defend(cid, play)
    state = await state_of(cid, play)
    encounter = state.encounters[0]
    actor = next(p for p in encounter.participants if p.actor_id == "a")
    assert actor.posture == posture and actor.defense_penalty == penalty
    assert encounter.blocked_reason is None and encounter.current_actor_id == "b"
    assert encounter.unarmed_history[-1].table_dice == table
    assert play.rng.exhausted()


async def test_critical_dodge_falls_without_table_and_takes_attack(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await action(cid, play, "a", "kick")
    play.rng = RecordedDice((2, 2, 2, 6, 6, 6, 3))
    await defend(cid, play, defense="dodge")
    state = await state_of(cid, play)
    trace = state.encounters[0].unarmed_history[-1]
    assert trace.won and trace.injury == 1 and trace.table_dice == ()
    assert next(p for p in state.encounters[0].participants if p.actor_id == "b").posture == "prone"
    assert play.rng.exhausted()


async def test_critical_defense_applies_unarmed_miss_to_attacker(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await action(cid, play, "a", "kick")
    play.rng = RecordedDice((2, 2, 2, 1, 1, 1, 1, 1, 6))
    await defend(cid, play, defense="parry")
    state = await state_of(cid, play)
    assert not state.encounters[0].unarmed_history[-1].won
    assert next(p for p in state.encounters[0].participants if p.actor_id == "a").posture == "prone"
    assert (
        next(p for p in state.encounters[0].participants if p.actor_id == "b").posture == "standing"
    )
    assert play.rng.exhausted()


@pytest.mark.parametrize(
    ("maneuver", "option", "target", "damage"),
    [
        ("all_out_attack", "determined", 12, 1),
        ("all_out_attack", "strong", 8, 3),
        ("move_and_attack", None, 4, 0),
    ],
)
async def test_maneuver_commitments(
    tmp_path: Path, maneuver: str, option: str | None, target: int, damage: int
) -> None:
    cid, play = await setup(tmp_path)
    state = await state_of(cid, play)
    command = TakeUnarmedTurn.model_validate(
        {
            "id": "maneuver",
            "actor_id": "a",
            "expected_revision": state.revision,
            "encounter_id": "fight",
            "action": "kick",
            "target_id": "b",
            "maneuver": maneuver,
            "attack_option": option,
        }
    )
    await CombatService(play).execute(cid, command, authenticated_actor_id="a")
    # Move and Attack misses on 6, then succeeds on its ordinary DX balance check.
    play.rng = RecordedDice((2, 2, 2, 3) if damage else (2, 2, 2, 3, 3, 3))
    await defend(cid, play)
    state = await state_of(cid, play)
    trace = state.encounters[0].unarmed_history[-1]
    assert trace.checks[0].effective_target == target and trace.basic_damage == damage
    actor = next(p for p in state.encounters[0].participants if p.actor_id == "a")
    assert actor.maneuver_state.defense_forbidden == (maneuver == "all_out_attack")
    assert actor.maneuver_state.parry_forbidden == (maneuver == "move_and_attack")
    assert play.rng.exhausted()


@pytest.mark.parametrize("option", ["double", "feint", None])
async def test_unsupported_all_out_combinations_reject_before_dice(
    tmp_path: Path, option: str | None
) -> None:
    cid, play = await setup(tmp_path)
    before = await state_of(cid, play)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError):
        await CombatService(play).execute(
            cid,
            {
                "id": "unsupported",
                "kind": "take_unarmed_turn",
                "actor_id": "a",
                "expected_revision": before.revision,
                "encounter_id": "fight",
                "action": "kick",
                "target_id": "b",
                "maneuver": "all_out_attack",
                "attack_option": option,
            },
            authenticated_actor_id="a",
        )
    assert await state_of(cid, play) == before and play.rng.exhausted()


@pytest.mark.parametrize(("roll", "readied"), [((2, 2, 2), True), ((3, 3, 3), False)])
async def test_grappled_ready_free_hand_dx_and_replay(
    tmp_path: Path, roll: tuple[int, ...], readied: bool
) -> None:
    from wayfarer.orchestration.combat import TakeCombatTurn

    cid, play = await setup(tmp_path)
    await action(cid, play, "a", "grapple", hands=("left-hand",), enter=True)
    play.rng = RecordedDice((3, 3, 3))
    await defend(cid, play)
    state = await state_of(cid, play)
    command = TakeCombatTurn(
        id="draw",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        maneuver="ready",
        item_id="sword-b",
        ready_hand="right-hand",
    )
    play.rng = RecordedDice(roll)
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="b")
    after = await state_of(cid, play)
    assert next(i for i in after.resources.items if i.id == "sword-b").ready == readied
    assert len([e for e in after.resources.events if e.id.startswith("grapple-ready:")]) == 1
    assert play.rng.exhausted()
    restarted = PlayService(
        AsyncSQLiteStore(tmp_path / "melee.sqlite"), play.engine, rng=RecordedDice(())
    )
    assert (
        await CombatService(restarted).execute(cid, command, authenticated_actor_id="b") == result
    )
    assert await state_of(cid, restarted) == after


async def test_partial_release_frees_only_selected_hand(tmp_path: Path) -> None:
    from test_unarmed import wait

    from wayfarer.engine.simulation.combat.unarmed import free_hands

    cid, play = await setup(tmp_path)
    await action(cid, play, "a", "grapple", hands=("left-hand", "right-hand"), enter=True)
    play.rng = RecordedDice((3, 3, 3))
    await defend(cid, play)
    await wait(cid, play, "b")
    state = await state_of(cid, play)
    grip = state.encounters[0].grips[0]
    play.rng = RecordedDice(())
    await action(cid, play, "a", "release", hands=("left-hand",), grip=grip.id)
    state = await state_of(cid, play)
    assert state.encounters[0].grips[0].hands == ("right-hand",)
    assert free_hands(state, state.encounters[0], "a") == ("left-hand",)
    assert state.encounters[0].current_actor_id == "a" and play.rng.exhausted()


@pytest.mark.parametrize(("roll", "stunned"), [((3, 3, 3), False), ((4, 4, 4), True)])
def test_crippled_arm_pain_reuses_injury_without_hp_or_duplicate_crippling(
    roll: tuple[int, ...], stunned: bool
) -> None:
    from wayfarer.engine.rules.types.injury import InjuryStatus
    from wayfarer.engine.rules.types.location import LastingInjury
    from wayfarer.engine.simulation.health.injury import Wound, apply_injury
    from wayfarer.engine.simulation.resources import Pool, ResourceState

    injury = LastingInjury(
        id="old", location="left-arm", kind="crippled", duration="pending", inflicted_at=0, injury=6
    )
    state = ResourceState(
        pools=(
            Pool(
                id="hp:a",
                current=4,
                maximum=10,
                injury=InjuryStatus(
                    profile_id="gurps-basic-set-4e-2004",
                    anatomy="human",
                    lasting_injuries=(injury,),
                ),
            ),
        )
    )
    command = Wound(
        id="pain",
        actor_id="a",
        expected_revision=0,
        basic_damage=7,
        resistance=0,
        damage_type="cr",
        location="left-arm",
    )
    rng = RecordedDice(roll)
    after, result = apply_injury(state, command, ht=10, rng=rng, system=True, pain_only=True)
    assert after.pools[0].current == 4 and result.injury == 0 and result.uncapped_injury == 7
    status = after.pools[0].injury
    assert status is not None and status.shock == 4 and status.stunned == stunned
    assert status.lasting_injuries == (injury,) and rng.exhausted()
    assert apply_injury(
        after, command, ht=10, rng=RecordedDice(()), system=True, pain_only=True
    ) == (after, result)
    with pytest.raises(ConflictError):
        apply_injury(after, command, ht=10, rng=RecordedDice(()), system=True)


@pytest.mark.parametrize(("choice", "score"), [("dodge", 11), ("parry", 11)])
async def test_unarmed_strike_hex_retreat(tmp_path: Path, choice: str, score: int) -> None:
    from test_tactical import setup as tactical_setup

    from wayfarer.engine.simulation.hex_geometry import Hex

    cid, play = await tactical_setup(tmp_path, unarmed=True)
    await action(cid, play, "a", "kick")
    before = await state_of(cid, play)
    command = ChooseDefense.model_validate(
        {
            "id": "retreat",
            "actor_id": "b",
            "expected_revision": before.revision,
            "encounter_id": "fight",
            "defense": choice,
            "retreat": {"q": 2, "r": 0},
        }
    )
    play.rng = RecordedDice((2, 2, 2, 3, 3, 3))
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="b")
    after = await state_of(cid, play)
    trace = after.encounters[0].unarmed_history[-1]
    assert trace.checks[1].effective_target == score and not trace.won
    assert next(p for p in after.encounters[0].participants if p.actor_id == "b").position == Hex(
        q=2, r=0
    )
    assert play.rng.exhausted()
    restarted = PlayService(
        AsyncSQLiteStore(tmp_path / "melee.sqlite"), play.engine, rng=RecordedDice(())
    )
    assert (
        await CombatService(restarted).execute(cid, command, authenticated_actor_id="b") == result
    )


async def arm_defender(cid: str, play: PlayService) -> None:
    state = await state_of(cid, play)
    encounter = state.encounters[0]
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": tuple(
                        i.model_copy(update={"ready": True, "equipped": True})
                        if i.id == "sword-b"
                        else i
                        for i in state.resources.items
                    )
                }
            ),
            "actors": tuple(
                a.model_copy(update={"held_item_hands": (("sword-b", "right-hand"),)})
                if a.actor_id == "b"
                else a
                for a in state.actors
            ),
            "encounters": (
                encounter.model_copy(
                    update={
                        "participants": tuple(
                            p.model_copy(
                                update={
                                    "ready_item_ids": ("sword-b",),
                                    "hand_bindings": (("sword-b", "right-hand"),),
                                }
                            )
                            if p.actor_id == "b"
                            else p
                            for p in encounter.participants
                        )
                    }
                ),
            ),
        }
    )
    await checkpoint(cid, play, state)


@pytest.mark.parametrize(("counter", "injury"), [((3, 3, 3, 1), 3), ((5, 5, 5), 0)])
async def test_armed_parry_separate_skill_check_and_leg_injury(
    tmp_path: Path, counter: tuple[int, ...], injury: int
) -> None:
    cid, play = await setup(tmp_path)
    await arm_defender(cid, play)
    await action(cid, play, "a", "kick")
    state = await state_of(cid, play)
    command = ChooseDefense(
        id="sword-parry",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="parry",
        item_id="sword-b",
        parry_mode_id="swing",
    )
    play.rng = RecordedDice((2, 2, 2, 2, 3, 3) + counter)
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="b")
    after = await state_of(cid, play)
    trace = after.encounters[0].unarmed_history[-1]
    assert not trace.won and trace.injury == 0
    assert len(trace.effect_checks) == 1 and trace.effect_checks[0].effective_target == 13
    assert trace.effect_dice == ((1,) if injury else ())
    assert next(p for p in after.resources.pools if p.id == "hp:a").current == 10 - injury
    assert next(p for p in after.resources.pools if p.id == "hp:b").current == 10
    assert play.rng.exhausted()
    restarted = PlayService(
        AsyncSQLiteStore(tmp_path / "melee.sqlite"), play.engine, rng=RecordedDice(())
    )
    assert (
        await CombatService(restarted).execute(cid, command, authenticated_actor_id="b") == result
    )
    assert await state_of(cid, restarted) == after


@pytest.mark.parametrize(
    ("table", "extra", "damage", "strained"),
    [
        ((1, 1, 2), (), 1, True),
        ((5, 6, 6), (), 1, True),
        ((1, 1, 3), (5,), 3, False),
        ((1, 1, 4), (5,), 1, False),
        ((4, 6, 6), (5,), 3, False),
    ],
)
async def test_unarmed_strain_and_solid_object_results(
    tmp_path: Path, table: tuple[int, ...], extra: tuple[int, ...], damage: int, strained: bool
) -> None:
    cid, play = await setup(tmp_path)
    await action(cid, play, "a", "kick")
    play.rng = RecordedDice((6, 6, 6) + table + extra)
    await defend(cid, play)
    after = await state_of(cid, play)
    trace = after.encounters[0].unarmed_history[-1]
    hp = next(p for p in after.resources.pools if p.id == "hp:a")
    assert hp.current == 10 - damage and hp.injury is not None
    assert trace.blocked_reason is None and trace.effect_dice == extra
    if strained:
        wound = hp.injury.lasting_injuries[0]
        assert wound.location == "right-leg" and wound.kind == "disabled"
        assert wound.recovery_at == wound.inflicted_at + 1800
        assert (
            next(p for p in after.encounters[0].participants if p.actor_id == "a").posture
            == "prone"
        )
    assert play.rng.exhausted()


@pytest.mark.parametrize("selected_mode", [None, "not-a-mode"])
async def test_ambiguous_or_unknown_armed_parry_rejected_before_dice(
    tmp_path: Path, selected_mode: str | None
) -> None:
    cid, play = await setup(tmp_path)
    await arm_defender(cid, play)
    await action(cid, play, "a", "kick")
    before = await state_of(cid, play)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError):
        await CombatService(play).execute(
            cid,
            ChooseDefense(
                id="bad-mode",
                actor_id="b",
                expected_revision=before.revision,
                encounter_id="fight",
                defense="parry",
                item_id="sword-b",
                parry_mode_id=selected_mode,
            ),
            authenticated_actor_id="b",
        )
    assert await state_of(cid, play) == before and play.rng.exhausted()


async def test_evaluate_is_used_by_next_unarmed_attack_only(tmp_path: Path) -> None:
    from test_unarmed import wait

    from wayfarer.orchestration.combat import TakeCombatTurn

    cid, play = await setup(tmp_path)
    state = await state_of(cid, play)
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="evaluate",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="evaluate",
            target_id="b",
        ),
        authenticated_actor_id="a",
    )
    await wait(cid, play, "b")
    await action(cid, play, "a", "kick")
    play.rng = RecordedDice((3, 3, 3, 3))
    await defend(cid, play)
    state = await state_of(cid, play)
    assert state.encounters[0].unarmed_history[-1].checks[0].effective_target == 9
    await wait(cid, play, "b")
    await action(cid, play, "a", "kick")
    play.rng = RecordedDice((2, 2, 2, 3))
    await defend(cid, play)
    state = await state_of(cid, play)
    assert state.encounters[0].unarmed_history[-1].checks[0].effective_target == 8
    assert play.rng.exhausted()


async def test_neck_missed_by_one_hits_torso_and_retains_intent(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await action(cid, play, "a", "punch", hands=("left-hand",), enter=True, location="neck")
    play.rng = RecordedDice((2, 2, 2, 5))
    await defend(cid, play)
    state = await state_of(cid, play)
    trace = state.encounters[0].unarmed_history[-1]
    assert trace.checks[0].effective_target == 5 and trace.checks[0].margin == -1
    assert trace.won and trace.injury == 2
    assert trace.intent is not None and trace.intent.location == "neck"
    assert trace.resolved_location == "torso" and play.rng.exhausted()


async def test_long_weapon_cannot_parry_in_close_combat(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await arm_defender(cid, play)
    await action(cid, play, "a", "punch", hands=("left-hand",), enter=True)
    before = await state_of(cid, play)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="reach C"):
        await CombatService(play).execute(
            cid,
            ChooseDefense(
                id="close-parry",
                actor_id="b",
                expected_revision=before.revision,
                encounter_id="fight",
                defense="parry",
                item_id="sword-b",
                parry_mode_id="swing",
            ),
            authenticated_actor_id="b",
        )
    assert await state_of(cid, play) == before and play.rng.exhausted()
