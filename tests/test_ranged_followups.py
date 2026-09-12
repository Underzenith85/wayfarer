"""Independent examples: Campaigns fourth printing B373,376,382,399,556.

Characters third printing B147 supplies One Eye. These are numeric regression
fixtures, not certification against the selected-printing baseline.
"""

from pathlib import Path

import pytest
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup
from test_gurps_ranged import load, scene, weapon

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService, TakeCombatTurn
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.rules.checks import Outcome, RecordedDice
from wayfarer.simulation.gurps_equipment import Damage
from wayfarer.simulation.ranged_critical import RangedCritical, save_ranged_critical


@pytest.mark.parametrize(
    ("table", "damage"),
    [
        ((1, 1, 1), 6),
        ((1, 1, 3), 4),
        ((1, 1, 4), 6),
        ((3, 3, 3), 2),
        ((3, 3, 4), 2),
        ((3, 4, 4), 2),
        ((5, 5, 5), 6),
        ((5, 5, 6), 4),
        ((6, 6, 6), 6),
    ],
)
async def test_body_critical_damage_and_restart_receipt(
    tmp_path: Path, table: tuple[int, int, int], damage: int
) -> None:
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=weapon(), ranged_scene=scene()
    )
    await load(cid, play)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    state = play._load(await play.store.read(cid))
    original_target = state.encounters[0].participants[1]
    command = ChooseDefense(
        id="critical-result",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="dodge",
    )
    play.rng = RecordedDice(
        [1, 1, 1, *table, *([] if sum(table) in (6, 15) else [2]), *([1, 1, 1] * 4)]
    )
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="b")
    assert result.injury is not None
    assert result.injury.defense is None
    assert result.injury.basic_damage == damage
    assert result.injury.hp_after == 10 - damage
    assert result.injury.adjudication_required is None
    assert isinstance(play.store, AsyncSQLiteStore)
    play = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))
    assert await CombatService(play).execute(cid, command, authenticated_actor_id="b") == result
    saved = play._load(await play.store.read(cid))
    events = [e for e in saved.resources.events if e.id.startswith("ranged-critical:")]
    assert len(events) == 1
    context = RangedCritical.model_validate_json(events[0].kind)
    assert context.defender == original_target
    assert context.trace == result.injury
    assert context.ammunition_load is not None and context.ammunition_load.rounds == 6
    assert next(i.quantity for i in saved.resources.items if i.id == "ammo-a") == 9
    assert save_ranged_critical(saved.resources, context) == saved.resources
    with pytest.raises(ConflictError):
        save_ranged_critical(
            saved.resources, context.model_copy(update={"defender_build_revision": "changed"})
        )
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_ranged_failure_by_ten_is_not_a_critical_miss(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=weapon(thrown=True),
        ranged_scene=scene(100),
    )
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([5, 5, 5])
    result = await defend(cid, play, "b")
    assert result.injury is not None
    assert result.injury.attack.effective_target == 3
    assert result.injury.attack.margin == -12
    assert result.injury.attack.outcome is Outcome.FAILURE
    assert result.injury.critical_table == ()
    assert result.injury.adjudication_required is None


@pytest.mark.parametrize(
    ("location", "target", "dice", "injury"),
    [
        ("vitals", 10, [3, 3, 3, 2, 1, 1, 1], 6),
        ("left-hand", 5, [1, 2, 2, 2], 2),
        ("random", 13, [3, 3, 3, 2, 3, 3, 2], 2),
    ],
)
async def test_projectile_locations_use_signed_injury_reducer(
    tmp_path: Path, location: str, target: int, dice: list[int], injury: int
) -> None:
    ranged = weapon(thrown=True).model_copy(
        update={"damage": Damage(basis="fixed", dice=1, damage_type="imp")}
    )
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", human=True, ranged_mode=ranged, ranged_scene=scene()
    )
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        target_id="b",
        mode_id="ranged",
        hit_location=location,
    )
    play.rng = RecordedDice(dice)
    result = await defend(cid, play, "b")
    assert result.injury is not None
    assert result.injury.attack.effective_target == target
    assert result.injury.injury == injury
    assert result.injury.location == ("right-arm" if location == "random" else location)
    assert result.injury.location_dice == ((2, 3, 3) if location == "random" else ())
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_thrown_parry_penalty_and_next_turn_reset(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=weapon(thrown=True), ranged_scene=scene()
    )
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([3, 3, 3, 3, 3, 3])
    result = await defend(cid, play, "b", "parry", item_id="sword-b")
    assert result.injury is not None and result.injury.defense is not None
    # Broadsword 13 -> Parry 9 + shield DB 1 - thrown penalty 1 = 9.
    assert result.injury.defense.effective_target == 9
    assert result.injury.hits == 0
    state = play._load(await play.store.read(cid))
    # The next actor is b, so starting that turn resets per-turn defenses.
    assert state.encounters[0].participants[1].parries == ()


async def test_per_round_loading_and_fire_cancels_partial_round(tmp_path: Path) -> None:
    ranged = weapon().model_copy(update={"reload_protocol": "per-round"})
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=ranged, ranged_scene=scene()
    )
    await load(cid, play)
    await turn(
        cid, play, "a", "ready", item_id="sword-a", mode_id="ranged", reload_ammunition_id="ammo-a"
    )
    await turn(cid, play, "b", "do_nothing")
    state = play._load(await play.store.read(cid))
    assert state.resources.ammunition_loads[0].rounds == 1
    assert state.resources.ammunition_loads[0].reload_progress == 1
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([4, 4, 4, 1])
    await defend(cid, play, "b")
    state = play._load(await play.store.read(cid))
    assert not state.resources.ammunition_loads
    assert next(i.quantity for i in state.resources.items if i.id == "ammo-a") == 9


async def test_unload_releases_reservations_once_across_restart(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=weapon(), ranged_scene=scene()
    )
    await load(cid, play)
    state = play._load(await play.store.read(cid))
    command = TakeCombatTurn(
        id="unload",
        actor_id="a",
        encounter_id="fight",
        expected_revision=state.revision,
        maneuver="ready",
        item_id="sword-a",
        mode_id="ranged",
        unload_ammunition=True,
    )
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="a")
    assert isinstance(play.store, AsyncSQLiteStore)
    play = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))
    assert await CombatService(play).execute(cid, command, authenticated_actor_id="a") == result
    state = play._load(await play.store.read(cid))
    assert not state.resources.ammunition_loads
    assert next(i.quantity for i in state.resources.items if i.id == "ammo-a") == 10
    await turn(cid, play, "b", "do_nothing")
    with pytest.raises(ValidationError, match="loaded weapon"):
        await turn(cid, play, "a", "ready", item_id="sword-a", unload_ammunition=True)
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("table", [(1, 3, 3), (3, 5, 5), (5, 5, 6)])
async def test_ranged_critical_balance_does_not_fall(
    tmp_path: Path, table: tuple[int, int, int]
) -> None:
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=weapon(), ranged_scene=scene()
    )
    await load(cid, play)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([6, 6, 6, *table])
    result = await defend(cid, play, "b")
    assert result.injury is not None and result.injury.adjudication_required is None
    state = play._load(await play.store.read(cid))
    actor = state.encounters[0].participants[0]
    assert actor.posture == "standing"
    assert actor.defense_penalty == -2
    assert state.encounters[0].blocked_reason is None
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("profile", ["gurps-lite-4e-2004", "gurps-basic-set-4e-2004"])
async def test_reload_protocols_and_locations_fail_closed_before_dice(
    tmp_path: Path, profile: str
) -> None:
    from typing import Literal, cast

    cid, play = await setup(
        tmp_path,
        cast(Literal["gurps-lite-4e-2004", "gurps-basic-set-4e-2004"], profile),
        ranged_mode=weapon(),
        ranged_scene=scene(),
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError):
        await turn(
            cid,
            play,
            "a",
            "ready",
            item_id="sword-a",
            unload_ammunition=True,
            reload_ammunition_id="ammo-a",
        )
    with pytest.raises(ValidationError):
        await turn(
            cid,
            play,
            "a",
            "attack",
            item_id="sword-a",
            target_id="b",
            mode_id="ranged",
            hit_location="vitals",
        )
    assert await play.store.read(cid) == before


@pytest.mark.parametrize(
    ("table", "damage", "injury"),
    [
        ((1, 1, 1), 6, 24),  # head 3: maximum damage, ignore skull DR
        ((1, 1, 2), 2, 4),  # head 4: skull DR 2 halves upward to 1
        ((1, 1, 3), 2, 4),  # head 5 does not double basic damage
        ((3, 3, 3), 2, 0),  # head 9: ordinary damage is stopped by skull DR
        ((5, 5, 5), 6, 16),  # head 15: maximum normal damage, skull DR applies
        ((6, 6, 6), 6, 16),  # head 18: triple basic damage, skull DR applies
    ],
)
async def test_projectile_critical_head_table(
    tmp_path: Path, table: tuple[int, int, int], damage: int, injury: int
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=weapon(thrown=True),
        ranged_scene=scene(),
    )
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        target_id="b",
        mode_id="ranged",
        hit_location="skull",
    )
    play.rng = RecordedDice(
        [1, 1, 1, *table, *([] if sum(table) in (3, 15) else [2]), *([1, 1, 1] * 8)]
    )
    result = await defend(cid, play, "b")
    assert result.injury is not None
    assert result.injury.basic_damage == damage
    assert result.injury.injury == injury
    assert result.injury.adjudication_required is None
    assert result.injury.location == "skull"
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_critical_parry_stores_context_without_melee_fallback(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=weapon(thrown=True),
        ranged_scene=scene(),
    )
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([3, 3, 3, 6, 6, 6, 1, 1, 1])
    result = await defend(cid, play, "b", "parry", item_id="sword-b")
    assert result.injury is not None
    assert result.injury.adjudication_required == "ranged-critical-parry"
    assert result.injury.injury == 0
    state = play._load(await play.store.read(cid))
    context = RangedCritical.model_validate_json(
        next(e.kind for e in state.resources.events if e.id.startswith("ranged-critical:"))
    )
    assert (
        context.trace.defense is not None
        and context.trace.defense.outcome is Outcome.CRITICAL_FAILURE
    )
    assert context.trace.critical_table == (1, 1, 1)
    assert context.weapon.thrown
    assert next(i for i in context.items if i.id == "sword-a").ready
    assert any(i.id == "sword-a" for i in state.resources.expended_items)


@pytest.mark.parametrize(("aim_first", "target"), [(False, 10), (True, 12)])
async def test_one_eye_aim_removes_ranged_penalty_even_with_zero_acc(
    tmp_path: Path, aim_first: bool, target: int
) -> None:
    from wayfarer.rules.location_types import LastingInjury
    from wayfarer.simulation.mechanics.gurps_ranged import resolve

    ranged = weapon(thrown=True).model_copy(update={"accuracy": 0})
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", human=True, ranged_mode=ranged, ranged_scene=scene()
    )
    if aim_first:
        await turn(cid, play, "a", "aim", item_id="sword-a", target_id="b", mode_id="ranged")
        await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    state = play._load(await play.store.read(cid))
    hp = next(p for p in state.resources.pools if p.id == "hp:a")
    assert hp.injury is not None
    hp = hp.model_copy(
        update={
            "injury": hp.injury.model_copy(
                update={
                    "lasting_injuries": (
                        LastingInjury(
                            id="old-eye",
                            location="left-eye",
                            kind="crippled",
                            duration="permanent",
                            inflicted_at=0,
                            injury=2,
                        ),
                    )
                }
            )
        }
    )
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={"pools": tuple(hp if p.id == hp.id else p for p in state.resources.pools)}
            )
        }
    )
    play.rng = RecordedDice([4, 4, 5])  # ordinary miss in both cases; no damage dice
    _, _, trace = resolve(
        play.rules_context,
        state,
        state.encounters[0],
        ranged,
        "none",
        None,
        second_defense=None,
        second_item_id=None,
    )
    assert trace.attack.effective_target == target
