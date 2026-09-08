"""Independent 2004 Basic Set expectations: B182, B200, B203, B228, B370-371.

Source-artifact audit is still required; fixtures are not implementation output.
"""

from pathlib import Path

import pytest
from test_gurps_melee import setup as melee_setup
from test_statistics import BASIC, LITE

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Campaign, Event
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    TakeCombatTurn,
    TakeUnarmedTurn,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.unarmed import settle_control
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.gurps_checks import replay_success
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.combat import Encounter
from wayfarer.simulation.unarmed import Grip, contest, striking_bonus, wrestling_bonus


async def setup(
    tmp_path: Path, *, third_actor: bool = False, traits: tuple[str, ...] = ()
) -> tuple[str, PlayService]:
    cid, play = await melee_setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        unarmed_fixture=True,
        third_actor=third_actor,
        traits=traits,
    )

    def disarm(campaign: Campaign) -> Event:
        state = play._load(campaign)
        resources = state.resources.model_copy(
            update={
                "items": tuple(
                    i.model_copy(update={"ready": False, "equipped": False})
                    for i in state.resources.items
                )
            }
        )
        state = state.model_copy(
            update={
                "resources": resources,
                "actors": tuple(a.model_copy(update={"held_item_hands": ()}) for a in state.actors),
                "encounters": tuple(
                    e.model_copy(
                        update={
                            "participants": tuple(
                                p.model_copy(update={"ready_item_ids": (), "hand_bindings": ()})
                                for p in e.participants
                            )
                        }
                    )
                    for e in state.encounters
                ),
            }
        )
        play.engine.validate(state)
        campaign["play_json"] = state.model_dump_json()
        return Event(input="fixture", action="combat", outcome="disarmed", roll=None)

    await play.store.commit_turn(cid, "disarm-fixture", 1, "fixture", disarm)
    return cid, play


async def state_of(cid: str, play: PlayService) -> PlayState:
    return play._load(await play.store.read(cid))


async def action(
    cid: str,
    play: PlayService,
    actor: str,
    value: str,
    *,
    target: str | None = None,
    hands: tuple[str, ...] = (),
    enter: bool = False,
    grip: str | None = None,
    skill: str = "attribute:dx",
    location: str = "torso",
) -> object:
    state = await state_of(cid, play)
    return await CombatService(play).execute(
        cid,
        {
            "kind": "take_unarmed_turn",
            "id": f"u-{state.revision}",
            "actor_id": actor,
            "expected_revision": state.revision,
            "encounter_id": "fight",
            "action": value,
            "target_id": target or ("b" if actor == "a" else "a"),
            "hands": hands,
            "enter_close_combat": enter,
            "grip_id": grip,
            "skill": skill,
            "location": location,
        },
        authenticated_actor_id=actor,
    )


async def defend(cid: str, play: PlayService, *, defense: str = "none") -> None:
    state = await state_of(cid, play)
    pending = state.encounters[0].pending_unarmed
    assert pending is not None
    await CombatService(play).execute(
        cid,
        ChooseDefense.model_validate(
            {
                "id": f"d-{state.revision}",
                "actor_id": pending.target_id,
                "expected_revision": state.revision,
                "encounter_id": "fight",
                "defense": defense,
            }
        ),
        authenticated_actor_id=pending.target_id,
    )


async def wait(cid: str, play: PlayService, actor: str) -> None:
    state = await state_of(cid, play)
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id=f"wait-{state.revision}",
            actor_id=actor,
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        authenticated_actor_id=actor,
    )


@pytest.mark.parametrize(
    ("skill", "dx", "level", "expected"),
    [
        ("attribute:dx", 10, 10, 0),
        ("skill:brawling", 10, 11, 0),
        ("skill:brawling", 10, 12, 1),
        ("skill:boxing", 10, 10, 0),
        ("skill:boxing", 10, 11, 1),
        ("skill:boxing", 10, 12, 2),
        ("skill:karate", 10, 9, 0),
        ("skill:karate", 10, 10, 1),
        ("skill:karate", 10, 11, 2),
    ],
)
def test_independent_striking_bonuses(skill: str, dx: int, level: int, expected: int) -> None:
    assert striking_bonus(skill, dx, level) == expected


@pytest.mark.parametrize(("level", "expected"), [(None, 0), (10, 0), (11, 1), (12, 2), (20, 2)])
def test_wrestling_st(level: int | None, expected: int) -> None:
    assert wrestling_bonus(10, level) == expected


def test_regular_pin_round_does_not_fast_forward() -> None:
    dice = RecordedDice((3, 3, 3, 3, 3, 3))
    won, checks, decided = contest(BASIC, "a", "b", 20, 18, regular=True, rng=dice)
    assert not won and not decided and dice.exhausted()
    assert [c.base_target for c in checks] == [20, 18]
    assert [sum(m.value for m in c.modifiers) for c in checks] == [-6, -6]


def test_break_free_two_hands_independent_expectation() -> None:
    dice = RecordedDice((3, 3, 3, 3, 3, 3))
    won, checks, decided = contest(BASIC, "escapee", "holder", 10, 15, rng=dice)
    assert not won and decided and dice.exhausted()
    assert [c.margin for c in checks] == [1, 6]


async def test_grapple_restart_replay_escape(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    play.rng = RecordedDice((3, 3, 3))
    await action(cid, play, "a", "grapple", hands=("left-hand", "right-hand"), enter=True)
    before = await state_of(cid, play)
    assert before.encounters[0].pending_unarmed is not None
    assert before.encounters[0].grips == ()
    restarted = PlayService(AsyncSQLiteStore(tmp_path / "melee.sqlite"), play.engine)
    restarted.rng = RecordedDice((3, 3, 3))
    command = ChooseDefense(
        id="defend",
        actor_id="b",
        expected_revision=before.revision,
        encounter_id="fight",
        defense="none",
    )
    first = await CombatService(restarted).execute(cid, command, authenticated_actor_id="b")
    assert restarted.rng.exhausted()
    restarted.rng = RecordedDice(())
    assert await CombatService(restarted).execute(cid, command, authenticated_actor_id="b") == first
    held = await state_of(cid, restarted)
    grip = held.encounters[0].grips[0]
    assert grip.hands == ("left-hand", "right-hand")
    assert next(p for p in held.encounters[0].participants if p.actor_id == "b").grappled
    restarted.rng = RecordedDice((2, 2, 2, 6, 5, 5))
    await action(cid, restarted, "b", "break_free", grip=grip.id)
    escaped = await state_of(cid, restarted)
    assert not escaped.encounters[0].grips
    assert not any(p.grappled for p in escaped.encounters[0].participants)
    assert restarted.rng.exhausted()


async def test_unauthorized_and_changed_payload_never_roll(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="authorized"):
        await CombatService(play).execute(
            cid,
            TakeUnarmedTurn(
                id="bad",
                actor_id="a",
                expected_revision=1,
                encounter_id="fight",
                action="kick",
                target_id="b",
            ),
            authenticated_actor_id="b",
        )
    await action(cid, play, "a", "grapple", hands=("left-hand",), enter=True)
    state = await state_of(cid, play)
    with pytest.raises(ConflictError):
        await action(cid, play, "a", "kick")
    command = ChooseDefense(
        id="receipt",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    play.rng = RecordedDice((3, 3, 3))
    await CombatService(play).execute(cid, command, authenticated_actor_id="b")
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid, command.model_copy(update={"defense": "dodge"}), authenticated_actor_id="b"
        )


async def test_takedown_pin_escape_cooldown(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await action(cid, play, "a", "grapple", hands=("left-hand", "right-hand"), enter=True)
    play.rng = RecordedDice((3, 3, 3))
    await defend(cid, play)
    grip = (await state_of(cid, play)).encounters[0].grips[0]
    await wait(cid, play, "b")
    play.rng = RecordedDice((2, 2, 2, 4, 4, 4))
    await action(cid, play, "a", "takedown", grip=grip.id)
    assert (await state_of(cid, play)).encounters[0].participants[1].posture == "prone"
    await wait(cid, play, "b")
    play.rng = RecordedDice((3, 3, 3, 4, 4, 4))
    await action(cid, play, "a", "pin", grip=grip.id)
    pinned = (await state_of(cid, play)).encounters[0]
    assert pinned.grips[0].pinned
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="ten seconds"):
        await action(cid, play, "b", "break_free", grip=grip.id)
    with pytest.raises(ValidationError, match="Pinned"):
        await action(cid, play, "b", "kick")
    assert play.rng.exhausted()


async def test_punch_and_missed_kick_balance(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await action(cid, play, "a", "punch", hands=("left-hand",), enter=True)
    # ST10 thrust 1d-2, punch -1: damage die 5 produces 2 crushing damage.
    play.rng = RecordedDice((3, 3, 3, 5))
    await defend(cid, play)
    trace = (await state_of(cid, play)).encounters[0].unarmed_history[-1]
    assert trace.basic_damage == trace.injury == 2
    assert trace.damage_dice == (5,)
    assert replay_success(trace.checks[0]) == trace.checks[0]
    await action(cid, play, "b", "kick")
    # DX10-2-shock2=6, total 12 is an ordinary miss; DX balance total 12 fails.
    play.rng = RecordedDice((4, 4, 4, 4, 4, 4))
    await defend(cid, play)
    assert (await state_of(cid, play)).encounters[0].participants[1].posture == "prone"


async def test_critical_dice_persist_and_block(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await action(cid, play, "a", "kick")
    play.rng = RecordedDice((6, 6, 6, 1, 1, 1))
    await defend(cid, play)
    state = await state_of(cid, play)
    assert state.encounters[0].blocked_reason is not None
    assert state.encounters[0].unarmed_history[-1].table_dice == (1, 1, 1)
    assert play.rng.exhausted()
    with pytest.raises(ConflictError):
        await action(cid, play, "a", "kick")


async def test_incapacitation_releases_holder_only(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await action(cid, play, "a", "grapple", hands=("left-hand",), enter=True)
    play.rng = RecordedDice((3, 3, 3))
    await defend(cid, play)
    state = await state_of(cid, play)

    def unconscious(actor_id: str) -> PlayState:
        pools = []
        for pool in state.resources.pools:
            if pool.id == f"hp:{actor_id}":
                assert pool.injury is not None
                pool = pool.model_copy(
                    update={"injury": pool.injury.model_copy(update={"unconscious": True})}
                )
            pools.append(pool)
        return state.model_copy(
            update={"resources": state.resources.model_copy(update={"pools": tuple(pools)})}
        )

    assert settle_control(unconscious("b"), state.encounters[0]).grips
    assert not settle_control(unconscious("a"), state.encounters[0]).grips


async def test_release_is_free_and_hands_cannot_be_reused(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await action(cid, play, "a", "grapple", hands=("left-hand",), enter=True)
    play.rng = RecordedDice((3, 3, 3))
    await defend(cid, play)
    await wait(cid, play, "b")
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="free"):
        await action(cid, play, "a", "punch", hands=("left-hand",))
    state = await state_of(cid, play)
    await action(cid, play, "a", "release", grip=state.encounters[0].grips[0].id)
    after = await state_of(cid, play)
    assert after.encounters[0].current_actor_id == "a"
    assert after.resources.game_time == state.resources.game_time
    assert not after.encounters[0].grips


def test_profile_rejection_and_grip_schema() -> None:
    with pytest.raises(ValidationError, match="exact"):
        contest(LITE, "a", "b", 10, 10, rng=RecordedDice(()))
    with pytest.raises(ValueError, match="distinct"):
        Grip(id="g", holder_id="a", target_id="b", hands=("left-hand", "left-hand"))
    with pytest.raises(ValueError):
        Encounter.model_validate({})


async def test_arm_lock_requires_surviving_grapple_and_damages_once(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await action(
        cid,
        play,
        "a",
        "grapple",
        hands=("left-hand", "right-hand"),
        enter=True,
        skill="skill:wrestling",
    )
    play.rng = RecordedDice((3, 3, 3))
    await defend(cid, play)
    await wait(cid, play, "b")
    grip = (await state_of(cid, play)).encounters[0].grips[0]
    await action(
        cid,
        play,
        "a",
        "arm_lock",
        hands=grip.hands,
        skill="skill:wrestling",
        location="left-arm",
        grip=grip.id,
    )
    play.rng = RecordedDice((3, 3, 3))
    await defend(cid, play)
    state = await state_of(cid, play)
    locked = state.encounters[0].grips[0]
    assert locked.arm_lock and locked.location == "left-arm"
    play.rng = RecordedDice((3, 3, 3, 3, 3, 3))
    await action(cid, play, "b", "break_free", grip=locked.id)
    state = await state_of(cid, play)
    assert state.encounters[0].grips[0].escape_penalty == 1
    # Wrestling11 versus ST/HT10, equal rolls: exactly 1 crushing arm injury.
    play.rng = RecordedDice((3, 3, 3, 3, 3, 3))
    await action(cid, play, "a", "lock_damage", grip=locked.id)
    state = await state_of(cid, play)
    assert state.encounters[0].unarmed_history[-1].injury == 1
    assert state.encounters[0].current_actor_id == "a"
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="once"):
        await action(cid, play, "a", "lock_damage", grip=locked.id)


async def test_strangle_reuses_durable_suffocation_and_release(tmp_path: Path) -> None:
    from wayfarer.orchestration.combat import ResolveChokeEffects

    cid, play = await setup(tmp_path)
    await action(
        cid, play, "a", "grapple", hands=("left-hand", "right-hand"), enter=True, location="neck"
    )
    play.rng = RecordedDice((2, 3, 3))
    await defend(cid, play)
    await wait(cid, play, "b")
    grip = (await state_of(cid, play)).encounters[0].grips[0]
    # ST10 rolls 8 versus ST/HT10 roll 10: 2 crushing neck damage -> 3 injury.
    play.rng = RecordedDice((2, 3, 3, 3, 3, 4))
    await action(cid, play, "a", "strangle", grip=grip.id)
    state = await state_of(cid, play)
    assert state.encounters[0].unarmed_history[-1].injury == 3
    assert state.resources.hazards[0].spec.kind == "suffocation"
    await wait(cid, play, "b")
    state = await state_of(cid, play)
    play.rng = RecordedDice(())
    command = ResolveChokeEffects(
        id="choke-due",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        grip_id=grip.id,
    )
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="b")
    assert await CombatService(play).execute(cid, command, authenticated_actor_id="b") == result
    state = await state_of(cid, play)
    assert next(p for p in state.resources.pools if p.id == "fp:b").current == 9
    await action(cid, play, "a", "release", grip=grip.id)
    state = await state_of(cid, play)
    assert not state.encounters[0].grips
    assert not state.resources.hazards[0].active


async def test_third_party_knockout_releases_grip_through_combat_transaction(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path, third_actor=True)
    await action(cid, play, "a", "grapple", hands=("left-hand", "right-hand"), enter=True)
    play.rng = RecordedDice((3, 3, 3))
    await defend(cid, play)
    await wait(cid, play, "b")
    await action(cid, play, "c", "kick", target="a")
    # ST20 kick 2d-1 = 11 injury; HT10 knockdown roll 16 causes unconsciousness.
    play.rng = RecordedDice((2, 3, 3, 6, 6, 5, 5, 6))
    await defend(cid, play)
    state = await state_of(cid, play)
    hp = next(p for p in state.resources.pools if p.id == "hp:a")
    assert hp.injury is not None and hp.injury.unconscious
    assert not state.encounters[0].grips
    assert not next(p for p in state.encounters[0].participants if p.actor_id == "b").grappled
    assert state.encounters[0].status == "active"
    assert state.encounters[0].current_actor_id == "b"
    assert play.rng.exhausted()


def test_shared_independent_unarmed_ledger() -> None:
    from test_gurps_conformance import check_cases

    for case in check_cases("unarmed"):
        inputs, expected = case["input"], case["expected"]
        assert isinstance(inputs, dict) and isinstance(expected, dict)
        if inputs["operation"] == "striking_bonus":
            assert (
                striking_bonus(inputs["skill"], inputs["dx"], inputs["level"]) == expected["bonus"]
            )
        elif inputs["operation"] == "wrestling_bonus":
            assert wrestling_bonus(inputs["dx"], inputs["level"]) == expected["bonus"]
        else:
            dice = RecordedDice(inputs["dice"])
            won, checks, decided = contest(
                BASIC,
                "a",
                "b",
                inputs["first"],
                inputs["second"],
                regular=inputs["regular"],
                rng=dice,
            )
            assert won == expected["won"] and decided == expected["decided"]
            assert [c.margin for c in checks] == expected["margins"]
            assert dice.exhausted()


async def test_combat_reflexes_reaches_the_unarmed_parry(tmp_path: Path) -> None:
    """B43 applies to a bare-handed Parry too, not only the armed defenses.

    The fixture parries with Judo at DX 10 and 4 points (level 10): 10/2 + 3 = 8,
    and Combat Reflexes makes it 9.
    """
    from wayfarer.orchestration.unarmed import unarmed_defense

    for reflexes, expected in ((False, 8), (True, 9)):
        cid, play = await setup(
            tmp_path / f"reflexes-{reflexes}",
            traits=("trait:combat-reflexes",) if reflexes else (),
        )
        state = await state_of(cid, play)
        score, hand = unarmed_defense(
            play, state, state.encounters[0], "b", "parry", None, attacker_id="a"
        )
        assert (score, hand) == (expected, "left-hand")


async def test_grappled_arm_cannot_parry_with_held_weapon(tmp_path: Path) -> None:
    from wayfarer.orchestration.gurps_melee import defense_value

    cid, play = await setup(tmp_path)
    await action(cid, play, "a", "grapple", hands=("left-hand",), enter=True, location="left-arm")
    play.rng = RecordedDice((2, 3, 3))
    await defend(cid, play)
    state = await state_of(cid, play)
    # The target held a weapon in the captured limb: readiness alone cannot permit parry.
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": tuple(
                        i.model_copy(update={"equipped": True, "ready": True})
                        if i.id == "sword-b"
                        else i
                        for i in state.resources.items
                    )
                }
            ),
            "actors": tuple(
                a.model_copy(update={"held_item_hands": (("sword-b", "left-hand"),)})
                if a.actor_id == "b"
                else a
                for a in state.actors
            ),
        }
    )
    participant = (
        state.encounters[0]
        .participants[1]
        .model_copy(
            update={"hand_bindings": (("sword-b", "left-hand"),), "ready_item_ids": ("sword-b",)}
        )
    )
    with pytest.raises(ValidationError):
        defense_value(play, state, participant, "parry", "sword-b")
    assert play.rng.exhausted()
