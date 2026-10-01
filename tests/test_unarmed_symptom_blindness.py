"""Private-source expectations: Characters B109/B124; Campaigns B370-371/B394/B552."""

from pathlib import Path
from typing import Final, Literal

import pytest
from pydantic import ValidationError as SchemaError
from support.runtime import build_play, seed_campaign
from test_basic_combat import start_basic
from test_combat_sensory_authority import change
from test_gurps_melee import setup as melee_setup
from test_unarmed import action, defend, state_of

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.conformance import BASELINE_ID
from wayfarer.engine.rules.types.symptoms import SymptomDebt, SymptomEffect, SymptomSpec
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.commands import TakeUnarmedTurn
from wayfarer.engine.simulation.combat.sensory_host import DeclareCombatSense, RevokeCombatSense
from wayfarer.engine.simulation.combat.sensory_state import ExactLocation, NonvisualObservation
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.combat.unarmed.random_strike import pending_id, random_strike
from wayfarer.engine.simulation.hex_geometry import Cell, Hex, HexBattlefield
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService
from wayfarer.orchestration.combat.unarmed_host import RandomUnarmedService, RandomUnarmedStrike
from wayfarer.orchestration.combat_senses import CombatSensesService
from wayfarer.orchestration.play import PlayService
from wayfarer.transport.tactical_v1_commands import TakeUnarmedTurn as FrozenUnarmed

Context = Literal["basic", "square", "hex"]
PROFILE: Final = "gurps-basic-set-4e-2004"


async def prepare(path: Path, backend: str, context: Context = "square") -> tuple[str, PlayService]:
    placements = (
        (
            Placement(actor_id="a", position=Hex(q=0, r=0), hex_facing=0),
            Placement(actor_id="b", position=Hex(q=1, r=0), hex_facing=3),
        )
        if context == "hex"
        else None
    )
    board = (
        HexBattlefield(
            id="dock",
            coordinate_system="hex-axial-v1",
            profile_id=PROFILE,
            baseline_id=BASELINE_ID,
            location_id="dock",
            cells=tuple(Cell(position=Hex(q=q, r=r)) for q in range(4) for r in range(3)),
        )
        if context == "hex"
        else None
    )
    cid, original = await melee_setup(
        path / "source",
        PROFILE,
        human=True,
        unarmed_fixture=True,
        scene_bound=context == "basic",
        start_encounter=context != "basic",
        aware_of=("a", "b"),
        placements=placements,
        battlefield=board,
    )
    if context == "basic":
        await CombatService(original).execute(cid, start_basic(1), principal_id="gm")
    play = build_play(path, original.engine, backend=backend, rng=RecordedDice(()))
    await seed_campaign(play.store, await original.store.read(cid))

    def disarm(state: PlayState) -> PlayState:
        return state.model_copy(
            update={
                "world": state.world.learn("a", "promise"),
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(update={"ready": False, "equipped": False})
                            for i in state.resources.items
                        )
                    }
                ),
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

    await change(play, cid, disarm)
    return cid, play


def blindness(state: PlayState, actor: str, *, active: bool = True, darkness: int = 0) -> PlayState:
    effects = tuple(e for e in state.resources.symptom_effects if e.actor_id != actor)
    debts = tuple(d for d in state.resources.symptom_debts if d.pool_id != f"hp:{actor}")
    return state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "symptom_effects": effects
                    + (
                        SymptomEffect(
                            id=f"blind-{actor}",
                            actor_id=actor,
                            pool_id=f"hp:{actor}",
                            source_id=f"flash-{actor}",
                            spec=SymptomSpec(kind="blindness"),
                            active=active,
                        ),
                    ),
                    "symptom_debts": debts
                    + (
                        SymptomDebt(
                            id=f"blind-debt-{actor}",
                            pool_id=f"hp:{actor}",
                            source_id=f"flash-{actor}",
                            remaining=6 if active else 0,
                        ),
                    ),
                }
            ),
            "encounters": tuple(
                e.model_copy(update={"darkness_penalty": darkness}) for e in state.encounters
            ),
        }
    )


async def locate(
    cid: str,
    play: PlayService,
    actor: str,
    *,
    located: bool = True,
    aware: bool = True,
    exact: bool = False,
) -> None:
    state = await state_of(cid, play)
    await CombatSensesService(play).execute(
        cid,
        DeclareCombatSense(
            id=f"sense-{state.revision}",
            actor_id=actor,
            target_id="b" if actor == "a" else "a",
            encounter_id="fight",
            expected_revision=state.revision,
            observation=NonvisualObservation(
                basis="touch",
                explanation="Contact with the opponent supplies a real tactile cue",
                located=located,
                attack_awareness="The contact reveals the incoming attack" if aware else None,
                exact_location=ExactLocation(
                    basis="continuous-contact",
                    explanation="Maintained contact fixes the foe's location",
                )
                if exact
                else None,
            ),
        ),
        principal_id="gm",
    )


async def random_command(
    cid: str, play: PlayService, identifier: str = "random-kick"
) -> RandomUnarmedStrike:
    state = await state_of(cid, play)
    return RandomUnarmedStrike(
        id=identifier,
        actor_id="a",
        target_id="b",
        encounter_id="fight",
        expected_revision=state.revision,
        action="kick",
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("context", ["basic", "square", "hex"])
async def test_b394_random_strike_requires_proof_and_replays_full_location(
    tmp_path: Path, backend: str, context: Context
) -> None:
    cid, play = await prepare(tmp_path, backend, context)
    await change(play, cid, lambda s: blindness(s, "a", darkness=-7))
    command = await random_command(cid, play)
    before = await state_of(cid, play)
    with pytest.raises(ValidationError):
        await RandomUnarmedService(play).execute(cid, command, principal_id="a")
    assert await state_of(cid, play) == before
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    await locate(cid, play, "a")
    command = await random_command(cid, play)
    declared = await RandomUnarmedService(play).execute(cid, command, principal_id="a")
    play = PlayService(
        play.store, play.engine, rng=RecordedDice((1, 1, 1, 1, 2, 2, 3, 3, 4, 4, 2, 2, 2))
    )
    state = await state_of(cid, play)
    defense = ChooseDefense(
        id="resolve-random",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    result = await CombatService(play).execute(cid, defense, principal_id="b")
    assert result.unarmed is not None
    # DX 10, ordinary kick -2, sudden blindness -10; darkness does not add -7.
    assert result.unarmed.checks[0].effective_target == -2
    assert result.unarmed.injury == 2 and result.unarmed.resolved_location is None
    after = await state_of(cid, play)
    record = random_strike(after, "fight", pending_id(command.id), "a", "b")
    assert (
        record is not None
        and record.resolved_location == "face"
        and record.location_dice == (1, 2, 2)
    )
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 8
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    await change(play, cid, lambda s: blindness(s, "a", active=False))
    play.rng = RecordedDice(())
    assert await RandomUnarmedService(play).execute(cid, command, principal_id="a") == declared
    assert await CombatService(play).execute(cid, defense, principal_id="b") == result
    with pytest.raises(ConflictError):
        await RandomUnarmedService(play).execute(
            cid, command.model_copy(update={"foot": "left-foot"}), principal_id="a"
        )
    with pytest.raises(ConflictError):
        await RandomUnarmedService(play).execute(
            cid, command.model_copy(update={"id": "stale-new"}), principal_id="a"
        )
    with pytest.raises(ValidationError, match="authorized"):
        await RandomUnarmedService(play).execute(cid, command, principal_id="b")
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    ("located", "choice", "expected"), [(False, "dodge", 4), (True, "parry", 4)]
)
async def test_b394_blind_unarmed_defenses_apply_one_penalty(
    tmp_path: Path, backend: str, located: bool, choice: str, expected: int
) -> None:
    cid, play = await prepare(tmp_path, backend)
    await change(play, cid, lambda s: blindness(s, "b"))
    await locate(cid, play, "b", located=located)
    await action(cid, play, "a", "kick")
    before = await state_of(cid, play)
    assert before.encounters[0].pending_unarmed is not None
    assert ("parry" in before.encounters[0].pending_unarmed.allowed) == located
    play.rng = RecordedDice((2, 2, 2, 1, 2, 2, 3))
    await defend(cid, play, defense=choice)
    after = await state_of(cid, play)
    trace = after.encounters[0].unarmed_history[-1]
    assert trace.checks[1].effective_target == expected
    assert trace.won and trace.injury == 1 and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_b394_pending_onset_unaware_and_recovery_refresh_choices(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    await action(cid, play, "a", "kick")
    await change(play, cid, lambda s: blindness(s, "b"))
    before = await state_of(cid, play)
    for choice in ("dodge", "parry"):
        with pytest.raises(ValidationError):
            await defend(cid, play, defense=choice)
        assert await state_of(cid, play) == before
    await locate(cid, play, "b", located=False)
    with pytest.raises(ValidationError):
        await defend(cid, play, defense="parry")
    await change(play, cid, lambda s: blindness(s, "b", active=False))
    play.rng = RecordedDice((2, 2, 2, 2, 2, 2))
    await defend(cid, play, defense="parry")
    trace = (await state_of(cid, play)).encounters[0].unarmed_history[-1]
    assert trace.checks[1].effective_target == 8 and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_b394_defense_proof_revocation_precedes_dice(tmp_path: Path, backend: str) -> None:
    cid, play = await prepare(tmp_path, backend)
    await change(play, cid, lambda s: blindness(s, "b"))
    await locate(cid, play, "b")
    await action(cid, play, "a", "kick")
    state = await state_of(cid, play)
    await CombatSensesService(play).execute(
        cid,
        RevokeCombatSense(
            id="silence",
            actor_id="b",
            target_id="a",
            encounter_id="fight",
            expected_revision=state.revision,
            explanation="The tactile warning has ceased",
        ),
        principal_id="gm",
    )
    before = await state_of(cid, play)
    with pytest.raises(ValidationError):
        await defend(cid, play, defense="parry")
    assert await state_of(cid, play) == before
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


def test_b394_random_intent_does_not_expand_frozen_v1_location() -> None:
    for command in (TakeUnarmedTurn, FrozenUnarmed):
        with pytest.raises(SchemaError):
            command.model_validate(
                {
                    "id": "public-random",
                    "actor_id": "a",
                    "target_id": "b",
                    "encounter_id": "fight",
                    "expected_revision": 1,
                    "action": "kick",
                    "location": "random",
                }
            )
    with pytest.raises(SchemaError):
        RandomUnarmedStrike.model_validate(
            {
                "id": "private",
                "actor_id": "a",
                "target_id": "b",
                "encounter_id": "fight",
                "expected_revision": 1,
                "action": "kick",
                "location": "neck",
            }
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_b124_named_strikes_and_new_grapples_fail_without_entropy(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    await change(play, cid, lambda s: blindness(s, "a"))
    await locate(cid, play, "a", exact=True)
    before = await state_of(cid, play)
    for attack, location in (("kick", "neck"), ("kick", "torso"), ("grapple", "torso")):
        with pytest.raises(ValidationError, match="random strike"):
            await action(
                cid,
                play,
                "a",
                attack,
                location=location,
                hands=("left-hand",) if attack == "grapple" else (),
            )
        assert await state_of(cid, play) == before
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_b394_separate_exact_proof_is_minus_four(tmp_path: Path, backend: str) -> None:
    cid, play = await prepare(tmp_path, backend)
    await change(play, cid, lambda s: blindness(s, "a", darkness=-8))
    await locate(cid, play, "a", exact=True)
    command = await random_command(cid, play)
    await RandomUnarmedService(play).execute(cid, command, principal_id="a")
    # DX10, kick -2, genuinely exact nonvisual location -4. A miss still has
    # its random hit-location dice, followed by the failed kick's balance check.
    play.rng = RecordedDice((2, 2, 2, 3, 3, 3, 2, 2, 2))
    await defend(cid, play)
    trace = (await state_of(cid, play)).encounters[0].unarmed_history[-1]
    assert trace.checks[0].effective_target == 4 and not trace.won
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_b394_second_defense_revalidates_and_each_penalty_is_once(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)

    def double(state: PlayState) -> PlayState:
        encounter = state.encounters[0]
        return state.model_copy(
            update={
                "encounters": (
                    encounter.model_copy(
                        update={
                            "participants": tuple(
                                p.model_copy(
                                    update={
                                        "maneuver_state": p.maneuver_state.model_copy(
                                            update={"enhanced_defense": "double"}
                                        )
                                    }
                                )
                                if p.actor_id == "b"
                                else p
                                for p in encounter.participants
                            )
                        }
                    ),
                )
            }
        )

    await change(play, cid, double)
    await change(play, cid, lambda s: blindness(s, "b"))
    await locate(cid, play, "b", located=False)
    await action(cid, play, "a", "kick")
    state = await state_of(cid, play)
    choice = ChooseDefense(
        id="double",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="dodge",
        second_defense="parry",
        second_item_id="left-hand",
    )
    with pytest.raises(ValidationError):
        await CombatService(play).execute(cid, choice, principal_id="b")
    assert await state_of(cid, play) == state
    await locate(cid, play, "b")
    current = await state_of(cid, play)
    choice = choice.model_copy(update={"expected_revision": current.revision})
    play.rng = RecordedDice((2, 2, 2, 1, 2, 2, 1, 2, 2, 3))
    result = await CombatService(play).execute(cid, choice, principal_id="b")
    assert result.unarmed is not None
    assert [c.effective_target for c in result.unarmed.checks] == [8, 4, 4]
    assert result.unarmed.defenses == (("dodge", None), ("parry", "left-hand"))
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("context", ["basic", "square", "hex"])
async def test_b370_existing_tactile_grip_survives_blindness(
    tmp_path: Path, backend: str, context: Context
) -> None:
    cid, play = await prepare(tmp_path, backend, context)
    await action(cid, play, "a", "grapple", hands=("left-hand", "right-hand"), enter=True)
    play.rng = RecordedDice((3, 3, 3))
    await defend(cid, play)
    state = await state_of(cid, play)
    grip = state.encounters[0].grips[0]
    await change(play, cid, lambda s: blindness(s, "b"))
    play.rng = RecordedDice((2, 2, 2, 6, 5, 5))
    await action(cid, play, "b", "break_free", grip=grip.id)
    assert not (await state_of(cid, play)).encounters[0].grips
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("context", ["basic", "hex"])
async def test_b394_admitted_defense_survives_its_atomic_retreat(
    tmp_path: Path, backend: str, context: Context
) -> None:
    cid, play = await prepare(tmp_path, backend, context)
    await change(play, cid, lambda s: blindness(s, "b"))
    await locate(cid, play, "b", located=False)
    await action(cid, play, "a", "kick")
    state = await state_of(cid, play)
    command = ChooseDefense(
        id="blind-retreat",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="dodge",
        basic_retreat=context == "basic",
        retreat=Hex(q=2, r=0) if context == "hex" else None,
    )
    play.rng = RecordedDice((2, 2, 2, 2, 2, 2))
    result = await CombatService(play).execute(cid, command, principal_id="b")
    assert result.unarmed is not None
    # Base Dodge8, blind -4, retreat +3.
    assert result.unarmed.checks[1].effective_target == 7
    assert not result.unarmed.won and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_random_unarmed_canonical_execute_recorded(tmp_path: Path, backend: str) -> None:
    from wayfarer.orchestration.replay import execute_recorded

    cid, play = await prepare(tmp_path, backend)
    await change(play, cid, lambda s: blindness(s, "a"))
    await locate(cid, play, "a")
    # This uses the command boundary's recorded seed rather than scripted dice.
    play = build_play(tmp_path, play.engine, store=play.store)
    before = await play.store.read(cid)
    command = await random_command(cid, play, "seeded-random")
    await RandomUnarmedService(play).execute(cid, command, principal_id="a")
    declared = await state_of(cid, play)
    declaration_record = (await play.store.history(cid))[-1]
    defense = ChooseDefense(
        id="seeded-defense",
        actor_id="b",
        expected_revision=declared.revision,
        encounter_id="fight",
        defense="none",
    )
    await CombatService(play).execute(cid, defense, principal_id="b")
    resolved = await state_of(cid, play)
    defense_record = (await play.store.history(cid))[-1]
    assert declaration_record.reexecutable and defense_record.reexecutable
    replay = build_play(tmp_path / "isolated-replay", play.engine)
    await seed_campaign(replay.store, before)
    await execute_recorded(replay, declaration_record)
    assert await state_of(cid, replay) == declared
    await execute_recorded(replay, defense_record)
    assert await state_of(cid, replay) == resolved
    replay_record = random_strike(
        await state_of(cid, replay), "fight", pending_id(command.id), "a", "b"
    )
    original_record = random_strike(resolved, "fight", pending_id(command.id), "a", "b")
    assert replay_record == original_record and replay_record is not None
    assert replay_record.location_dice
