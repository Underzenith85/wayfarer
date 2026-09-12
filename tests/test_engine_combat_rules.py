"""The three combat rules #561 moved below orchestration, pinned against the engine.

Each is exercised directly on engine state, without a campaign transaction. The
transaction path still covers the same rules through the maneuver, object and
replay fixtures, which must not move.
"""

from pathlib import Path
from typing import Final, Literal

import pytest
from test_gurps_maneuvers import turn
from test_gurps_melee import setup
from test_gurps_ranged import weapon

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.object import ObjectProfile
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.melee.attack import waive_off_hand_penalty
from wayfarer.engine.simulation.combat.melee.defense import exert_defense
from wayfarer.engine.simulation.combat.melee.modes import mode_reach, require_two_weapon_modes
from wayfarer.engine.simulation.equipment.catalog import Damage, MeleeMode, Parry
from wayfarer.errors import ValidationError

BASIC: Final = "gurps-basic-set-4e-2004"
AMBIDEXTERITY = Purchase(definition_id="trait:ambidexterity")


def melee(hands: Literal[1, 2], reach: tuple[int, ...] = (1,)) -> MeleeMode:
    return MeleeMode(
        id=f"swing-{hands}",
        skill_id="skill:broadsword",
        minimum_st=10,
        damage=Damage(basis="swing", adds=1, damage_type="cr"),
        reach=reach,
        hands=hands,
        parry=Parry(),
    )


def test_reach_comes_from_the_declared_mode() -> None:
    assert mode_reach(melee(1)) == 1
    assert mode_reach(melee(2, reach=(1, 2))) == 2
    assert mode_reach(weapon(bow=True)) == 1


def test_two_weapon_double_requires_a_one_handed_melee_mode_in_each_hand() -> None:
    require_two_weapon_modes(melee(1), melee(1))
    for first, second in (
        (melee(1), melee(2)),
        (melee(2), melee(1)),
        (melee(1), weapon(bow=True)),
        (weapon(thrown=True), melee(1)),
    ):
        with pytest.raises(ValidationError, match="one-handed melee modes"):
            require_two_weapon_modes(first, second)


async def test_ambidexterity_waives_both_off_hand_penalties(tmp_path: Path) -> None:
    for name, purchases in (("ambidextrous", (AMBIDEXTERITY,)), ("plain", ())):
        (tmp_path / name).mkdir()
        cid, play = await setup(
            tmp_path / name,
            BASIC,
            free_defender_hand=True,
            physical_purchases=purchases,
            extra_purchases=purchases,
        )
        state = play._load(await play.store.read(cid))
        encounter = state.encounters[0]
        attacker = next(p for p in encounter.participants if p.actor_id == "a")
        committed = CombatEngine._replace(
            encounter,
            attacker.model_copy(
                update={
                    "maneuver_state": attacker.maneuver_state.model_copy(
                        update={"attack_bonus": -4, "second_attack_penalty": -4}
                    )
                }
            ),
        )
        waived = waive_off_hand_penalty(play.rules_context, state, committed, "a")
        after = next(p for p in waived.participants if p.actor_id == "a").maneuver_state
        expected = 0 if purchases else -4
        assert (after.attack_bonus, after.second_attack_penalty) == (expected, expected)
        assert waived == committed if not purchases else waived != committed


async def test_defense_downgrades_to_none_when_exertion_fails(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, BASIC)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    state = play._load(await play.store.read(cid))
    encounter = state.encounters[0]
    assert encounter.pending_defense is not None

    play.rng = RecordedDice([])
    _, _, kept = exert_defense(
        play.rules_context, state, encounter, "b", "defend", "parry", "sword-b"
    )
    assert kept == "parry"

    spent = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "pools": tuple(
                        pool.model_copy(update={"current": 0}) if pool.id == "fp:b" else pool
                        for pool in state.resources.pools
                    )
                }
            )
        }
    )
    play.rng = RecordedDice([5, 5, 5])  # B426: Will 10 fails; the defender collapses.
    after, _, downgraded = exert_defense(
        play.rules_context, spent, encounter, "b", "defend", "parry", "sword-b"
    )
    assert downgraded == "none"
    fatigue = next(p for p in after.resources.pools if p.id == "fp:b").fatigue
    assert fatigue is not None and fatigue.collapsed
    assert (
        exert_defense(play.rules_context, spent, encounter, "b", "declined", "none", None)[2]
        == "none"
    )


async def test_defense_downgrades_to_none_when_the_implement_breaks(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        BASIC,
        durability=ObjectProfile(construction="homogenous", hp=12, dr=0, ht=12),
        object_hp=0,
    )
    play.rng = RecordedDice([3, 3, 3])  # The attacking sword survives its own stress.
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    state = play._load(await play.store.read(cid))
    encounter = state.encounters[0]
    assert encounter.pending_defense is not None

    play.rng = RecordedDice([6, 6, 6, 2, 3, 3, 3])  # The parrying sword fails HT 12.
    after, _, downgraded = exert_defense(
        play.rules_context, state, encounter, "b", "defend", "parry", "sword-b"
    )
    assert downgraded == "none"
    sword = next(i for i in after.resources.items if i.id == "sword-b")
    assert sword.condition is not None and sword.condition.disabled and not sword.ready
