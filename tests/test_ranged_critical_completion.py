"""Independent Campaigns fourth-printing B376, B382, B399, B556-557 cases.

Numeric expectations are handwritten. First-printing/errata certification is
separately gated by the source audit; these cases do not certify that profile.
"""

from pathlib import Path
from typing import Literal

import pytest
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup
from test_gurps_ranged import load, scene, weapon

from wayfarer.orchestration.combat import ChooseDefense, CombatService
from wayfarer.orchestration.critical_limbs import CriticalLimbResult
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.rules.checks import RecordedDice
from wayfarer.simulation.ranged_critical import RangedCritical


@pytest.mark.parametrize(
    "table,ready,equipped",
    [
        ((2, 3, 3), False, True),
        ((4, 4, 4), False, True),
        ((3, 3, 3), False, False),
        ((3, 3, 4), False, False),
        ((3, 4, 4), False, False),
        ((4, 5, 5), False, False),
    ],
)
async def test_miss_weapon_handling_and_lost_response(
    tmp_path: Path,
    table: tuple[int, int, int],
    ready: bool,
    equipped: bool,
) -> None:
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", human=True, ranged_mode=weapon(), ranged_scene=scene()
    )
    await load(cid, play)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    state = play._load(await play.store.read(cid))
    cmd = ChooseDefense(
        id="miss",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    play.rng = RecordedDice([6, 6, 6, *table])
    result = await CombatService(play).execute(cid, cmd, authenticated_actor_id="b")
    assert result.injury and result.injury.adjudication_required is None
    assert isinstance(play.store, AsyncSQLiteStore)
    restarted = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))
    assert await CombatService(restarted).execute(cid, cmd, authenticated_actor_id="b") == result
    saved = play._load(await play.store.read(cid))
    item = next(i for i in saved.resources.items if i.id == "sword-a")
    assert (item.ready, item.equipped) == (ready, equipped)
    assert saved.encounters[0].participants[0].hand_bindings == ()
    assert next(i.quantity for i in saved.resources.items if i.id == "ammo-a") == 9
    assert saved.resources.ammunition_loads[0].rounds == 5
    context = RangedCritical.model_validate_json(
        next(e.kind for e in saved.resources.events if e.id.startswith("ranged-critical:"))
    )
    assert context.table_rolls == (table,)
    assert context.subject_id == "a" and context.affected_item_id == "sword-a"
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("thrown", [False, True])
@pytest.mark.parametrize("second,injury", [((1, 2, 2), 4), ((2, 2, 2), 2), ((3, 3, 3), 0)])
async def test_all_ranged_self_wounds_reroll_once(
    tmp_path: Path,
    thrown: bool,
    second: tuple[int, int, int],
    injury: int,
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=weapon(thrown=thrown),
        ranged_scene=scene(),
    )
    if not thrown:
        await load(cid, play)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([6, 6, 6, 1, 2, 2, *second, *([1, 1, 4] if injury else [])])
    result = await defend(cid, play, "b")
    assert result.injury and result.injury.adjudication_required is None
    saved = play._load(await play.store.read(cid))
    assert next(p.current for p in saved.resources.pools if p.id == "hp:a") == 10 - injury
    limb = CriticalLimbResult.model_validate_json(
        next(e.kind for e in saved.resources.events if e.id.startswith("critical-limb:"))
    )
    assert limb.table_rolls == ((1, 2, 2), second)
    assert limb.injury == injury
    context = RangedCritical.model_validate_json(
        next(e.kind for e in saved.resources.events if e.id.startswith("ranged-critical:"))
    )
    assert context.table_rolls == ((1, 2, 2), second)
    assert context.trace.critical_table == second
    if thrown:
        assert [i.id for i in saved.resources.expended_items] == ["sword-a"]
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_ranged_shoulder_strain_uses_wielding_arm(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", human=True, ranged_mode=weapon(), ranged_scene=scene()
    )
    await load(cid, play)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([6, 6, 6, 5, 5, 5])
    result = await defend(cid, play, "b")
    assert result.injury and result.injury.adjudication_required is None
    state = play._load(await play.store.read(cid))
    hp = next(p for p in state.resources.pools if p.id == "hp:a")
    assert hp.injury and hp.current == 10
    strain = hp.injury.lasting_injuries[0]
    assert strain.location == "right-arm"
    assert strain.recovery_at == strain.inflicted_at + 1800
    assert next(i for i in state.resources.items if i.id == "sword-a").ready


async def test_critical_thrown_parry_drop_also_takes_incoming_hit(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=weapon(thrown=True),
        ranged_scene=scene(),
    )
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([3, 3, 3, 6, 6, 6, 3, 3, 3, 2])
    result = await defend(cid, play, "b", "parry", item_id="sword-b")
    assert result.injury and result.injury.adjudication_required is None
    assert result.injury.injury == 2
    saved = play._load(await play.store.read(cid))
    assert not next(i for i in saved.resources.items if i.id == "sword-b").ready
    context = RangedCritical.model_validate_json(
        next(e.kind for e in saved.resources.events if e.id.startswith("ranged-critical:"))
    )
    assert context.subject_id == "b" and context.affected_item_id == "sword-b"


@pytest.mark.parametrize(
    "quality,rolls,broken",
    [
        ("ordinary", [1, 1, 1], True),
        ("cheap", [3, 3, 3], True),
        ("resistant", [1, 1, 1, 3, 3, 3], False),
        ("resistant", [1, 1, 1, 6, 6, 6], True),
        ("resistant", [1, 2, 2, 1, 1, 1, 6, 6, 6], True),
    ],
)
async def test_typed_breakage_and_resistant_second_roll(
    tmp_path: Path,
    quality: Literal["ordinary", "cheap", "resistant"],
    rolls: list[int],
    broken: bool,
) -> None:
    from wayfarer.orchestration.ranged_misses import resolve_miss
    from wayfarer.rules.object_types import ObjectCondition, ObjectProfile

    ranged = weapon()
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", human=True, ranged_mode=ranged, ranged_scene=scene()
    )
    await load(cid, play)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    state = play._load(await play.store.read(cid))
    combat = play.engine.rules.combat
    assert combat and combat.gurps_equipment
    durability = ObjectProfile(construction="unliving", hp=12, dr=4, ht=10)
    equipment = combat.gurps_equipment.model_copy(
        update={
            "entries": tuple(
                e.model_copy(update={"critical_breakage": quality, "durability": durability})
                if e.definition_id == "equipment:broadsword"
                else e
                for e in combat.gurps_equipment.entries
            )
        }
    )
    play.engine.rules = play.engine.rules.model_copy(
        update={"combat": combat.model_copy(update={"gurps_equipment": equipment})}
    )
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": tuple(
                        i.model_copy(update={"condition": ObjectCondition(hp=12)})
                        if i.id == "sword-a"
                        else i
                        for i in state.resources.items
                    )
                }
            )
        }
    )
    play.rng = RecordedDice(rolls[3:])
    state, encounter, result, blocker = resolve_miss(
        play, state, state.encounters[0], tuple(rolls[:3])
    )
    assert blocker is None
    item = next(i for i in state.resources.items if i.id == "sword-a")
    assert item.condition and item.condition.disabled is broken
    assert item.condition.hp == 12  # Breakage disables; it does not fabricate HP damage.
    assert not item.ready and not item.equipped
    assert encounter.participants[0].hand_bindings == ()
    assert tuple(d for r in result.table_rolls for d in r) == tuple(rolls)
    assert result.resolved
    assert CriticalLimbResult.model_validate_json(result.model_dump_json()) == result


async def test_random_burst_has_independent_locations_and_replays(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", human=True, ranged_mode=weapon(), ranged_scene=scene()
    )
    await load(cid, play)
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        target_id="b",
        mode_id="ranged",
        shots=3,
        hit_location="random",
    )
    state = play._load(await play.store.read(cid))
    cmd = ChooseDefense(
        id="random-burst",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    # Skill 13, roll 9, Rcl 2 -> three hits: right arm, torso, left leg.
    play.rng = RecordedDice([3, 3, 3, 2, 3, 3, 1, 3, 3, 3, 2, 4, 4, 5, 3])
    result = await CombatService(play).execute(cid, cmd, authenticated_actor_id="b")
    assert result.injury
    assert result.injury.hits == 3
    assert result.injury.per_hit_locations == ("right-arm", "torso", "left-leg")
    assert result.injury.per_hit_location_dice == ((2, 3, 3), (3, 3, 3), (4, 4, 5))
    assert result.injury.per_hit_damage == (1, 2, 3)
    assert result.injury.per_hit_injury == (1, 2, 3)
    assert result.injury.hp_after == 4
    assert isinstance(play.store, AsyncSQLiteStore)
    restarted = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))
    assert await CombatService(restarted).execute(cid, cmd, authenticated_actor_id="b") == result
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("thrown", [False, True])
async def test_broken_weapon_cas_restart_and_retry(tmp_path: Path, thrown: bool) -> None:
    from wayfarer.errors import ValidationError

    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=weapon(thrown=thrown),
        ranged_scene=scene(),
        critical_breakage="resistant",
    )
    if not thrown:
        await load(cid, play)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    state = play._load(await play.store.read(cid))
    cmd = ChooseDefense(
        id="break",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    play.rng = RecordedDice([6, 6, 6, 1, 1, 1, 6, 6, 6])
    result = await CombatService(play).execute(cid, cmd, authenticated_actor_id="b")
    assert result.injury and result.injury.adjudication_required is None
    saved = play._load(await play.store.read(cid))
    item = next(
        i for i in saved.resources.items + saved.resources.expended_items if i.id == "sword-a"
    )
    assert item.condition and item.condition.disabled
    assert not item.ready and not item.equipped
    record = RangedCritical.model_validate_json(
        next(e.kind for e in saved.resources.events if e.id.startswith("ranged-critical:"))
    )
    assert record.table_rolls == ((1, 1, 1), (6, 6, 6))
    assert record.trace.critical_table == (6, 6, 6)
    assert isinstance(play.store, AsyncSQLiteStore)
    restarted = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))
    assert await CombatService(restarted).execute(cid, cmd, authenticated_actor_id="b") == result
    assert restarted._load(await restarted.store.read(cid)).resources == saved.resources
    await turn(cid, restarted, "b", "do_nothing")
    before = await restarted.store.read(cid)
    with pytest.raises(ValidationError):
        await turn(cid, restarted, "a", "ready", item_id="sword-a")
    assert await restarted.store.read(cid) == before
    assert await play.store.read(cid) == await play.store.replay(cid)
