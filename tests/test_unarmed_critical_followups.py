"""Independent B557 cases from the supplied Campaigns table, not reducer output."""

from pathlib import Path

import pytest
from test_unarmed import action, defend, setup, state_of, wait
from test_unarmed_integrations import arm_defender, checkpoint

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.maneuvers import ManeuverState
from wayfarer.engine.simulation.combat.unarmed import fighter, guard_control
from wayfarer.engine.simulation.equipment.catalog import Damage, MeleeMode, Parry
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService, TakeUnarmedTurn
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


async def resolve(
    cid: str, play: PlayService, dice: tuple[int, ...], *, armed: bool = False
) -> PlayState:
    state = await state_of(cid, play)
    command = ChooseDefense(
        id="critical-followup",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="parry" if armed else "none",
        item_id="sword-b" if armed else None,
        parry_mode_id="swing" if armed else None,
    )
    play.rng = RecordedDice(dice)
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="b")
    assert play.rng.exhausted()
    after = await state_of(cid, play)
    assert isinstance(play.store, AsyncSQLiteStore)
    restarted = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice(()))
    assert (
        await CombatService(restarted).execute(cid, command, authenticated_actor_id="b") == result
    )
    assert await state_of(cid, restarted) == after
    return after


@pytest.mark.parametrize("table", [(1, 2, 6), (1, 3, 6), (1, 4, 6)])
async def test_lost_balance_prevents_free_grip_actions_until_own_turn(
    tmp_path: Path, table: tuple[int, ...]
) -> None:
    cid, play = await setup(tmp_path, third_actor=True)
    await action(cid, play, "a", "kick")
    state = await resolve(cid, play, (6, 6, 6) + table)
    encounter = state.encounters[0]
    assert fighter(encounter, "a").unarmed_balance_lost
    assert fighter(encounter, "a").defense_penalty == -2
    # No dice or grip lookup may run, even for a free action in a Wait reaction.
    for kind in ("release", "lock_damage"):
        command = TakeUnarmedTurn.model_validate(
            {
                "id": kind,
                "actor_id": "a",
                "target_id": "b",
                "encounter_id": "fight",
                "expected_revision": state.revision,
                "action": kind,
                "grip_id": "unused",
            }
        )
        with pytest.raises(ValidationError, match="Lost balance"):
            guard_control(encounter, command, state)
    await wait(cid, play, "b")
    middle = (await state_of(cid, play)).encounters[0]
    assert fighter(middle, "a").unarmed_balance_lost
    await wait(cid, play, "c")
    after = (await state_of(cid, play)).encounters[0]
    assert not fighter(after, "a").unarmed_balance_lost
    assert fighter(after, "a").defense_penalty == 0


@pytest.mark.parametrize("benefit", ["evaluate", "feint"])
async def test_dropped_guard_doubles_next_foes_benefit_and_expires(
    tmp_path: Path, benefit: str
) -> None:
    cid, play = await setup(tmp_path, third_actor=True)
    await action(cid, play, "a", "kick")
    state = await resolve(cid, play, (6, 6, 6, 1, 6, 6))
    encounter = state.encounters[0]
    assert fighter(encounter, "a").unarmed_guard_dropped
    assert encounter.blocked_reason is None
    b = fighter(encounter, "b").model_copy(
        update={
            "last_maneuver": benefit,
            "maneuver_state": ManeuverState(
                evaluate_target_id="a" if benefit == "evaluate" else None,
                evaluate_bonus=2 if benefit == "evaluate" else 0,
                feint_target_id="a" if benefit == "feint" else None,
                feint_penalty=2 if benefit == "feint" else 0,
            ),
        }
    )
    await checkpoint(
        cid,
        play,
        state.model_copy(
            update={
                "encounters": (CombatEngine._replace(encounter, b),),
            }
        ),
    )
    await action(cid, play, "b", "kick")
    # B557: Evaluate +2 becomes +4; Feint -2 becomes -4 on top of the guard's -2.
    play.rng = RecordedDice((2, 2, 2, 4, 4, 4, 3))
    await defend(cid, play, defense="dodge")
    state = await state_of(cid, play)
    trace = state.encounters[0].unarmed_history[-1]
    assert trace.checks[0].effective_target == (12 if benefit == "evaluate" else 8)
    assert trace.checks[1].effective_target == (6 if benefit == "evaluate" else 2)
    assert play.rng.exhausted()
    assert fighter(state.encounters[0], "a").unarmed_guard_dropped
    await wait(cid, play, "c")
    after = (await state_of(cid, play)).encounters[0]
    assert not fighter(after, "a").unarmed_guard_dropped


@pytest.mark.parametrize(("table", "damage"), [((1, 1, 3), 4), ((1, 1, 4), 2), ((4, 6, 6), 4)])
async def test_critical_fall_on_ready_impaling_weapon_uses_attacker_strength(
    tmp_path: Path, table: tuple[int, ...], damage: int
) -> None:
    cid, play = await setup(
        tmp_path,
        melee_modes=(
            MeleeMode(
                id="thrust",
                skill_id="skill:broadsword",
                minimum_st=10,
                reach=(1,),
                damage=Damage(basis="thrust", adds=1, damage_type="imp"),
                parry=Parry(),
            ),
        ),
    )
    await arm_defender(cid, play)
    await action(cid, play, "a", "kick")
    # ST 10 thrust is 1d-2, broadsword thrust is +1: die 3 gives 2 imp = 4 injury.
    state = await resolve(cid, play, (6, 6, 6) + table + (3,))
    encounter = state.encounters[0]
    assert encounter.blocked_reason is None
    assert encounter.unarmed_history[-1].effect_dice == (3,)
    assert fighter(encounter, "a").posture == "prone"
    hp = next(p for p in state.resources.pools if p.id == "hp:a")
    assert hp.current == 10 - damage
    assert all(i.ready for i in state.resources.items if i.id == "sword-b")


@pytest.mark.parametrize(
    ("table", "ready", "equipped", "posture"),
    [
        ((1, 1, 6), False, True, "standing"),
        ((1, 2, 6), False, False, "standing"),
        ((1, 3, 6), False, False, "standing"),
        ((1, 4, 6), False, False, "standing"),
        ((1, 5, 6), False, True, "standing"),
        ((2, 6, 6), False, False, "standing"),
        ((4, 6, 6), True, True, "prone"),
    ],
)
async def test_armed_critical_parry_uses_weapon_table_and_keeps_incoming_hit(
    tmp_path: Path, table: tuple[int, ...], ready: bool, equipped: bool, posture: str
) -> None:
    cid, play = await setup(tmp_path)
    await arm_defender(cid, play)
    await action(cid, play, "a", "kick")
    state = await resolve(cid, play, (2, 2, 2, 6, 6, 6) + table + (3,), armed=True)
    encounter = state.encounters[0]
    trace = encounter.unarmed_history[-1]
    assert trace.table_dice == table and trace.won and trace.injury == 1
    assert encounter.blocked_reason is None
    item = next(i for i in state.resources.items if i.id == "sword-b")
    assert item.ready == ready and item.equipped == equipped
    assert (item.ground is not None) == (not equipped)
    assert fighter(encounter, "b").posture == posture
    assert ("sword-b" in fighter(encounter, "b").ready_item_ids) == ready


@pytest.mark.parametrize("benefit", ["evaluate", "feint"])
async def test_weapon_attack_also_observes_dropped_guard(tmp_path: Path, benefit: str) -> None:
    from test_gurps_melee import attack
    from test_gurps_melee import setup as melee_setup

    cid, play = await melee_setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    await attack(cid, play)
    state = await state_of(cid, play)
    encounter = state.encounters[0]
    a = fighter(encounter, "a").model_copy(
        update={
            "maneuver_state": ManeuverState(
                evaluate_target_id="b" if benefit == "evaluate" else None,
                evaluate_bonus=2 if benefit == "evaluate" else 0,
                feint_target_id="b" if benefit == "feint" else None,
                feint_penalty=2 if benefit == "feint" else 0,
            ),
        }
    )
    b = fighter(encounter, "b").model_copy(
        update={
            "unarmed_guard_dropped": True,
            "defense_penalty": -2,
            "maneuver_state": ManeuverState(enhanced_defense="double"),
        }
    )
    encounter = CombatEngine._replace(CombatEngine._replace(encounter, a), b)
    await checkpoint(cid, play, state.model_copy(update={"encounters": (encounter,)}))
    before = await state_of(cid, play)
    command = ChooseDefense(
        id="weapon-guard",
        actor_id="b",
        expected_revision=before.revision,
        encounter_id="fight",
        defense="dodge",
        second_defense="block",
    )
    # Ordinary failures of both defenses; one damage point avoids a major wound.
    play.rng = RecordedDice((3, 3, 3, 3, 3, 3, 3, 3, 3, 1))
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="b")
    assert result.injury is not None
    trace = result.injury
    assert trace.attack.effective_target == (17 if benefit == "evaluate" else 13)
    assert trace.defense is not None and trace.second_defense is not None
    assert trace.defense.effective_target == (7 if benefit == "evaluate" else 3)
    assert trace.second_defense.effective_target == (8 if benefit == "evaluate" else 4)
    assert play.rng.exhausted()
