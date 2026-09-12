"""Independent numeric cases: Lite (August 2004) 27-29; Basic B270, B372-375, B550.

Test-only weapon statistics; no catalog completeness or printing certification claim.
"""

from pathlib import Path

import pytest
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup

from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService, TakeCombatTurn
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.ranged_tables import range_penalty, rapid_fire_bonus
from wayfarer.simulation.combat import RangedSituation
from wayfarer.simulation.gurps_equipment import Damage, RangedMode
from wayfarer.simulation.resources import Consume, Transfer


def weapon(*, thrown: bool = False, bow: bool = False) -> RangedMode:
    return RangedMode(
        id="ranged",
        skill_id="skill:broadsword",
        minimum_st=10,
        damage=Damage(basis="fixed", dice=1, damage_type="cr"),
        accuracy=2,
        range_basis="yards",
        maximum_range=100,
        half_damage_range=10,
        shots=1 if thrown or bow else 6,
        reload_seconds=2,
        rate_of_fire=1 if thrown or bow else 6,
        recoil=2,
        bulk=-4,
        ammunition_id=None if thrown else "equipment:ammo",
        thrown=thrown,
        blockable=bow,
    )


def scene(distance: float = 2, speed: float = 0, size: int = 0) -> tuple[RangedSituation, ...]:
    return (
        RangedSituation(
            attacker_id="a",
            defender_id="b",
            distance_yards=distance,
            speed_yards_per_second=speed,
            size_modifier=size,
        ),
    )


async def load(cid: str, play: PlayService) -> None:
    for _ in range(2):
        await turn(
            cid,
            play,
            "a",
            "ready",
            item_id="sword-a",
            mode_id="ranged",
            reload_ammunition_id="ammo-a",
        )
        await turn(cid, play, "b", "do_nothing")


@pytest.mark.parametrize(
    ("distance", "expected"),
    [(2, 0), (3, -1), (3.1, -2), (7, -3), (10, -4), (20, -6), (30, -7), (100, -10)],
)
def test_range_table(distance: float, expected: int) -> None:
    assert range_penalty(distance) == expected


@pytest.mark.parametrize(
    ("shots", "expected"),
    [(99, 6), (100, 7), (199, 7), (200, 8), (399, 8), (400, 9), (1600, 11)],
)
def test_b373_high_rate_of_fire_bonus_continues_by_doubling(shots: int, expected: int) -> None:
    assert rapid_fire_bonus(shots) == expected


def test_tactical_v2_accepts_high_cyclic_burst_without_widening_v1() -> None:
    from wayfarer.simulation.combat_commands import TakeCombatTurn
    from wayfarer.transport.tactical_v1_commands import TakeCombatTurn as TakeCombatTurnV1

    command = TakeCombatTurn(
        id="high-cyclic",
        actor_id="attacker",
        expected_revision=0,
        encounter_id="encounter",
        maneuver="attack",
        item_id="weapon",
        target_id="target",
        mode_id="ranged",
        shots=1600,
    )
    assert command.shots == 1600
    with pytest.raises(ValueError):
        TakeCombatTurnV1.model_validate(command.model_dump())


async def test_thrown_weapon_leaves_inventory_and_retries_once(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, ranged_mode=weapon(thrown=True), ranged_scene=scene())
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([4, 4, 4, 2])
    result = await defend(cid, play, "b")
    assert result.injury is not None
    assert result.injury.attack.effective_target == 13
    assert result.injury.per_hit_damage == (2,)
    assert result.injury.hp_after == 8
    state = play._load(await play.store.read(cid))
    assert "sword-a" not in {i.id for i in state.resources.items}
    assert state.resources.expended_items[0].id == "sword-a"
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_burst_applies_each_hit_separately(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=weapon(), ranged_scene=scene()
    )
    await load(cid, play)
    await turn(
        cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged", shots=6
    )
    # Skill 13 + RoF 1 = 14; roll 10 -> three hits with Rcl 2, each for 1 HP.
    play.rng = RecordedDice([3, 3, 4, 1, 1, 1])
    result = await defend(cid, play, "b")
    assert result.injury is not None
    assert result.injury.attack.effective_target == 14
    assert result.injury.hits == 3
    assert result.injury.per_hit_injury == (1, 1, 1)
    assert result.injury.hp_after == 7
    state = play._load(await play.store.read(cid))
    assert next(i.quantity for i in state.resources.items if i.id == "ammo-a") == 4
    assert not state.resources.ammunition_loads


async def test_aim_range_speed_size_and_half_damage(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, ranged_mode=weapon(bow=True), ranged_scene=scene(11, 4, 2))
    await load(cid, play)
    for _ in range(3):
        await turn(cid, play, "a", "aim", item_id="sword-a", target_id="b", mode_id="ranged")
        await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([4, 4, 4, 5])
    result = await defend(cid, play, "b")
    assert result.injury is not None
    # 13 + Acc 2 + two extra seconds + SM 2 - range/speed 5 = 14.
    assert result.injury.attack.effective_target == 14
    assert result.injury.per_hit_damage == (2,)
    assert result.injury.hp_after == 8


async def test_interrupted_reload_restart_retry_and_reservation(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, ranged_mode=weapon(bow=True), ranged_scene=scene())
    await turn(
        cid, play, "a", "ready", item_id="sword-a", mode_id="ranged", reload_ammunition_id="ammo-a"
    )
    await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "do_nothing")
    assert isinstance(play.store, AsyncSQLiteStore)
    play = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))
    state = play._load(await play.store.read(cid))
    command = TakeCombatTurn(
        id="reload-complete",
        actor_id="a",
        expected_revision=state.revision,
        encounter_id="fight",
        maneuver="ready",
        item_id="sword-a",
        mode_id="ranged",
        reload_ammunition_id="ammo-a",
    )
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="a")
    assert await CombatService(play).execute(cid, command, authenticated_actor_id="a") == result
    state = play._load(await play.store.read(cid))
    assert state.resources.ammunition_loads[0].rounds == 1
    assert state.resources.ammunition_loads[0].reload_progress == 0
    assert next(i.quantity for i in state.resources.items if i.id == "ammo-a") == 10
    for resource_command in (
        Consume(
            id="consume",
            actor_id="a",
            expected_revision=state.resources.revision,
            item_id="ammo-a",
            quantity=10,
        ),
        Transfer(
            id="transfer",
            actor_id="a",
            expected_revision=state.resources.revision,
            item_id="ammo-a",
            quantity=10,
            owner_id="b",
        ),
    ):
        with pytest.raises(ValidationError):
            play.engine.resources.apply(state.resources, resource_command)
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_unsupported_fire_and_unloaded_reject_without_dice(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, ranged_mode=weapon(), ranged_scene=scene())
    before = play._load(await play.store.read(cid))
    play.rng = RecordedDice([])
    for shots in (1, 2, 7):
        with pytest.raises(ValidationError):
            await turn(
                cid,
                play,
                "a",
                "attack",
                item_id="sword-a",
                target_id="b",
                mode_id="ranged",
                shots=shots,
            )
        assert play._load(await play.store.read(cid)) == before


async def test_burst_dodge_margin_removes_only_some_hits(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=weapon(), ranged_scene=scene()
    )
    await load(cid, play)
    await turn(
        cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged", shots=6
    )
    # 14 - 8 gives four hits; Dodge 9 (including shield DB) roll 8 avoids two.
    play.rng = RecordedDice([2, 3, 3, 2, 3, 3, 1, 1])
    result = await defend(cid, play, "b", "dodge")
    assert result.injury is not None and result.injury.defense is not None
    assert result.injury.defense.effective_target == 9
    assert result.injury.hits == 2
    assert result.injury.per_hit_damage == (1, 1)


async def test_missed_single_shot_restart_and_lost_response_conserve_ammo(tmp_path: Path) -> None:
    from wayfarer.orchestration.combat import ChooseDefense

    cid, play = await setup(tmp_path, ranged_mode=weapon(bow=True), ranged_scene=scene())
    await load(cid, play)
    state = play._load(await play.store.read(cid))
    attack = TakeCombatTurn(
        id="shot-once",
        actor_id="a",
        expected_revision=state.revision,
        encounter_id="fight",
        maneuver="attack",
        item_id="sword-a",
        target_id="b",
        mode_id="ranged",
    )
    result = await CombatService(play).execute(cid, attack, authenticated_actor_id="a")
    assert await CombatService(play).execute(cid, attack, authenticated_actor_id="a") == result
    assert isinstance(play.store, AsyncSQLiteStore)
    play = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([5, 5, 5]))
    state = play._load(await play.store.read(cid))
    defense = ChooseDefense(
        id="resolve-once",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    result = await CombatService(play).execute(cid, defense, authenticated_actor_id="b")
    assert await CombatService(play).execute(cid, defense, authenticated_actor_id="b") == result
    assert result.injury is not None and result.injury.hits == 0
    state = play._load(await play.store.read(cid))
    assert next(i.quantity for i in state.resources.items if i.id == "ammo-a") == 9
    assert not state.resources.ammunition_loads
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_firearm_forbids_block_and_parry_including_second_defense(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, ranged_mode=weapon(), ranged_scene=scene())
    await load(cid, play)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].pending_defense is not None
    assert state.encounters[0].pending_defense.allowed == ("none", "dodge")
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError):
        await defend(cid, play, "b", "block", item_id="shield-b")
    with pytest.raises(ValidationError):
        await defend(cid, play, "b", "dodge", second_defense="parry", second_item_id="sword-b")
    assert play._load(await play.store.read(cid)) == state


async def test_minimum_st_penalty_and_determined_bonus(tmp_path: Path) -> None:
    mode = weapon(thrown=True).model_copy(update={"minimum_st": 12})
    cid, play = await setup(tmp_path, ranged_mode=mode, ranged_scene=scene())
    await turn(
        cid,
        play,
        "a",
        "all_out_attack",
        item_id="sword-a",
        target_id="b",
        mode_id="ranged",
        attack_option="determined",
    )
    play.rng = RecordedDice([4, 4, 4, 1])
    result = await defend(cid, play, "b")
    assert result.injury is not None
    assert result.injury.attack.effective_target == 12  # skill 13 - ST deficit 2 + ranged AoA 1
    assert result.injury.per_hit_damage == (1,)


async def test_basic_critical_drop_is_persisted_without_melee_fallback(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=weapon(), ranged_scene=scene()
    )
    await load(cid, play)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([6, 6, 6, 3, 3, 3])
    result = await defend(cid, play, "b")
    assert result.injury is not None
    assert result.injury.adjudication_required is None
    assert result.injury.critical_table == (3, 3, 3)
    state = play._load(await play.store.read(cid))
    assert next(i.quantity for i in state.resources.items if i.id == "ammo-a") == 9
    assert state.encounters[0].blocked_reason is None
    assert not next(i for i in state.resources.items if i.id == "sword-a").ready
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_switching_ranged_modes_resets_aim_seconds(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path, ranged_fixture=True, ranged_mode=weapon(thrown=True), ranged_scene=scene()
    )
    await turn(cid, play, "a", "aim", item_id="sword-a", target_id="b", mode_id="ranged")
    await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "a", "aim", item_id="sword-a", target_id="b", mode_id="throw-fixture")
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].participants[0].maneuver_state.aim_seconds == 1


async def test_critical_block_readiness_matches_inventory(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, ranged_mode=weapon(thrown=True), ranged_scene=scene())
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([4, 4, 4, 6, 6, 6, 1])
    result = await defend(cid, play, "b", "block", item_id="shield-b")
    assert result.injury is not None and result.injury.per_hit_damage == (1,)
    state = play._load(await play.store.read(cid))
    assert not next(i.ready for i in state.resources.items if i.id == "shield-b")
