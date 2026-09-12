"""B419-420/B426 eligibility, independent of the command ending the turn."""

from pathlib import Path

import pytest
from test_gurps_melee import attack, choice, setup

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.commands import SetEncounterOpposition, TakeCombatTurn
from wayfarer.engine.simulation.combat.encounter import CombatAllegiance, SideOpposition
from wayfarer.engine.simulation.combat.settlement import (
    combat_ready,
    remaining_opposition,
    settle_encounter,
)
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat.context import CombatContext
from wayfarer.orchestration.combat.steps import reduce_combat


def condition(state: PlayState, actor: str, kind: str) -> PlayState:
    pools = []
    for pool in state.resources.pools:
        if pool.id == f"hp:{actor}":
            injury = pool.injury
            assert injury is not None
            if kind == "missing-injury":
                pool = pool.model_copy(update={"injury": None})
            elif kind in ("unconscious", "dead", "stunned", "prone"):
                pool = pool.model_copy(update={"injury": injury.model_copy(update={kind: True})})
            elif kind == "negative-hp":
                pool = pool.model_copy(update={"current": -1})
        if pool.id == f"fp:{actor}":
            fatigue = pool.fatigue
            assert fatigue is not None
            if kind == "missing-fatigue":
                pool = pool.model_copy(update={"fatigue": None})
            elif kind in ("collapsed", "heart_attack"):
                pool = pool.model_copy(
                    update={"current": 0, "fatigue": fatigue.model_copy(update={kind: True})}
                )
            elif kind == "fatigue-unconscious":
                pool = pool.model_copy(
                    update={"fatigue": fatigue.model_copy(update={"unconscious": True})}
                )
            elif kind in ("zero-fp", "negative-fp", "minimum-fp"):
                current = {"zero-fp": 0, "negative-fp": -1, "minimum-fp": -pool.maximum}[kind]
                pool = pool.model_copy(update={"current": current})
        pools.append(pool)
    return state.model_copy(
        update={"resources": state.resources.model_copy(update={"pools": tuple(pools)})}
    )


def allegiance_policy(
    state: PlayState,
    sides: dict[str, str | None],
    pairs: tuple[tuple[str, str], ...],
    *,
    automatic: bool = True,
    reinforcements_expected: bool = False,
) -> PlayState:
    encounter = state.encounters[0].model_copy(
        update={
            "allegiances": tuple(
                CombatAllegiance(actor_id=actor_id, side_id=side_id)
                for actor_id, side_id in sides.items()
            ),
            "oppositions": tuple(SideOpposition(side_ids=pair) for pair in pairs),
            "completion_policy": "automatic" if automatic else "gm",
            "reinforcements_expected": reinforcements_expected,
        }
    )
    return state.model_copy(update={"encounters": (encounter,)})


@pytest.mark.parametrize(
    ("kind", "ready"),
    [
        ("negative-hp", True),
        ("zero-fp", True),
        ("negative-fp", True),
        ("stunned", True),
        ("prone", True),
        ("collapsed", False),
        ("unconscious", False),
        ("dead", False),
        ("minimum-fp", False),
        ("fatigue-unconscious", False),
        ("heart_attack", False),
    ],
)
async def test_basic_set_eligibility(tmp_path: Path, kind: str, ready: bool) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    state = allegiance_policy(
        play._load(await play.store.read(cid)),
        {"a": "heroes", "b": "foes"},
        (("foes", "heroes"),),
    )
    state = condition(state, "b", kind)
    play.rng = RecordedDice([])
    assert combat_ready(state, "b", gurps=True) is ready
    settled = settle_encounter(play.rules_context, state, state.encounters[0])
    assert settled.status == ("active" if ready else "completed")


@pytest.mark.parametrize("defense", [False, True])
@pytest.mark.parametrize("third_actor", [False, True])
async def test_both_commands_settle_fatigue_and_skip_initiative(
    tmp_path: Path, defense: bool, third_actor: bool
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", third_actor=third_actor)
    if defense:
        await attack(cid, play)
    state = play._load(await play.store.read(cid))
    state = allegiance_policy(
        state,
        {"a": "heroes", "b": "foes", **({"c": "foes"} if third_actor else {})},
        (("foes", "heroes"),),
    )
    state = condition(state, "b", "collapsed")
    play.rng = RecordedDice([3, 3, 3, 1]) if defense else RecordedDice([])
    command = (
        choice()
        if defense
        else TakeCombatTurn(
            id="rest",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        )
    )
    after, result = reduce_combat(state, command, CombatContext(play, state))
    encounter = after.encounters[0]
    assert encounter.status == ("active" if third_actor else "completed")
    if third_actor:
        assert encounter.current_actor_id == "c"
    else:
        assert encounter.completion_reason == "opposition_incapacitated"
        assert not result.available


@pytest.mark.parametrize("defense", [False, True])
@pytest.mark.parametrize("kind", ["missing-injury", "missing-fatigue"])
async def test_missing_records_fail_identically(tmp_path: Path, defense: bool, kind: str) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", third_actor=True)
    if defense:
        await attack(cid, play)
    # An uninvolved third actor ensures action resolution does not mask settlement validation.
    state = condition(play._load(await play.store.read(cid)), "c", kind)
    play.rng = RecordedDice([3, 3, 3, 1]) if defense else RecordedDice([])
    command = (
        choice()
        if defense
        else TakeCombatTurn(
            id="rest",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        )
    )
    with pytest.raises(ValidationError, match="explicit GURPS") as error:
        reduce_combat(state, command, CombatContext(play, state))
    assert error.value.reference == ("hp:c" if kind == "missing-injury" else "fp:c")


async def test_pending_defense_is_preserved_even_when_only_one_fighter_is_ready(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    await attack(cid, play)
    state = condition(play._load(await play.store.read(cid)), "b", "collapsed")
    encounter = state.encounters[0]
    assert encounter.pending_defense is not None
    assert settle_encounter(play.rules_context, state, encounter) is encounter


async def test_two_allied_survivors_complete_when_the_opposed_side_falls(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", third_actor=True)
    state = play._load(await play.store.read(cid))
    state = allegiance_policy(
        state,
        {"a": "heroes", "b": "foes", "c": "heroes"},
        (("foes", "heroes"),),
    )
    state = condition(state, "b", "unconscious")
    settled = settle_encounter(play.rules_context, state, state.encounters[0])
    assert settled.status == "completed"
    assert settled.completion_reason == "opposition_incapacitated"


async def test_neutral_survivor_does_not_keep_defeated_opposition_active(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", third_actor=True)
    state = play._load(await play.store.read(cid))
    state = allegiance_policy(
        state,
        {"a": "heroes", "b": "foes", "c": None},
        (("foes", "heroes"),),
    )
    state = condition(state, "b", "collapsed")
    settled = settle_encounter(play.rules_context, state, state.encounters[0])
    assert settled.status == "completed"


async def test_no_survivors_has_distinct_completion_reason(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    state = play._load(await play.store.read(cid))
    state = allegiance_policy(
        state,
        {"a": "heroes", "b": "foes"},
        (("foes", "heroes"),),
    )
    state = condition(condition(state, "a", "unconscious"), "b", "collapsed")
    settled = settle_encounter(play.rules_context, state, state.encounters[0])
    assert settled.status == "completed"
    assert settled.completion_reason == "no_combatants_ready"


async def test_two_remaining_sides_continue_when_they_are_still_opposed(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", third_actor=True)
    state = play._load(await play.store.read(cid))
    state = allegiance_policy(
        state,
        {"a": "alpha", "b": "beta", "c": "gamma"},
        (("alpha", "beta"), ("alpha", "gamma"), ("beta", "gamma")),
    )
    state = condition(state, "b", "unconscious")
    ready = frozenset({"a", "c"})
    assert remaining_opposition(state.encounters[0], ready)
    assert settle_encounter(play.rules_context, state, state.encounters[0]).status == "active"


@pytest.mark.parametrize(
    ("automatic", "reinforcements_expected"),
    [(False, False), (True, True)],
)
async def test_gm_policy_or_expected_reinforcements_keep_combat_timing(
    tmp_path: Path, automatic: bool, reinforcements_expected: bool
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    state = play._load(await play.store.read(cid))
    state = allegiance_policy(
        state,
        {"a": "heroes", "b": "foes"},
        (("foes", "heroes"),),
        automatic=automatic,
        reinforcements_expected=reinforcements_expected,
    )
    state = condition(state, "b", "unconscious")
    assert settle_encounter(play.rules_context, state, state.encounters[0]).status == "active"


async def test_changed_allegiance_removes_remaining_opposition(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    state = play._load(await play.store.read(cid))
    after, result = reduce_combat(
        state,
        SetEncounterOpposition(
            id="change-sides",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            allegiances=(
                CombatAllegiance(actor_id="a", side_id="heroes"),
                CombatAllegiance(actor_id="b", side_id="heroes"),
            ),
            oppositions=(SideOpposition(side_ids=("foes", "heroes")),),
            completion_policy="automatic",
        ),
        CombatContext(play, state),
    )
    settled = after.encounters[0]
    assert settled.status == "completed"
    assert settled.completion_reason == "opposition_resolved"
    assert result.code == "combat.opposition_changed"


async def test_prototype_missing_injury_uses_explicit_hp_policy(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    state = condition(play._load(await play.store.read(cid)), "b", "missing-injury")
    assert combat_ready(state, "b", gurps=False)
    hp = next(p for p in state.resources.pools if p.id == "hp:b")
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "pools": tuple(
                        p.model_copy(update={"current": 0}) if p.id == hp.id else p
                        for p in state.resources.pools
                    )
                }
            )
        }
    )
    assert not combat_ready(state, "b", gurps=False)
