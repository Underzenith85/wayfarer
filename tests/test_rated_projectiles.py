"""Independent B16/B270 (Characters third printing) and B378 expectations.

Campaigns fourth printing supplies the half-damage boundary. These fixtures do
not certify the selected-printing baseline. Weapons are test data.
"""

from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError as ModelValidationError
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup, spend_fp
from test_gurps_ranged import load, scene, weapon

from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService, TakeCombatTurn
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.rules.checks import RecordedDice
from wayfarer.simulation.gurps_equipment import Damage, EquipmentCatalog, RangedMode, RatedStrength
from wayfarer.simulation.mechanics.critical_limbs import CriticalLimbResult


def rated(kind: Literal["bow", "crossbow"] = "bow", st: int = 8) -> RangedMode:
    data = weapon(bow=True).model_dump()
    data.update(
        hands=2,
        range_basis="st",
        maximum_range=2,
        half_damage_range=1,
        damage=Damage(basis="thrust", adds=1, damage_type="cr"),
        rated_strength=RatedStrength(kind=kind, st=st),
        reload_seconds=2 if kind == "bow" else 4,
    )
    return RangedMode.model_validate(data)


@pytest.mark.parametrize(("distance", "damage"), [(7.9, 3), (8, 1), (8.1, 1), (16, 1)])
async def test_bow_uses_weapon_st_for_damage_and_range(
    tmp_path: Path, distance: float, damage: int
) -> None:
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=rated(), ranged_scene=scene(distance)
    )
    await load(cid, play)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([1, 2, 2, 5])
    result = await defend(cid, play, "b")
    assert result.injury is not None
    # Weapon ST 8: thrust 1d-3, +1 ammunition, roll 5 -> 3 basic damage.
    # Shooter ST 10 would incorrectly produce 4. 1/2D is 8, Max is 16.
    assert result.injury.basic_damage == damage
    assert result.injury.hp_after == 10 - damage
    state = play._load(await play.store.read(cid))
    assert next(i.quantity for i in state.resources.items if i.id == "ammo-a") == 9
    assert not state.resources.ammunition_loads
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_bow_maximum_range_rejects_before_roll_or_expenditure(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=rated(), ranged_scene=scene(16.1)
    )
    await load(cid, play)
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="maximum ranged"):
        await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    assert await play.store.read(cid) == before


async def test_stronger_bow_cannot_be_drawn(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=rated(st=11), ranged_scene=scene()
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="Bow ST exceeds"):
        await turn(
            cid,
            play,
            "a",
            "ready",
            item_id="sword-a",
            mode_id="ranged",
            reload_ammunition_id="ammo-a",
        )
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("maneuver", ["aim", "attack"])
async def test_fatigue_prevents_using_a_previously_loaded_bow(
    tmp_path: Path, maneuver: str
) -> None:
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=rated(), ranged_scene=scene()
    )
    await load(cid, play)
    await spend_fp(play, cid, "a", 8)
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="Bow ST exceeds"):
        await turn(cid, play, "a", maneuver, item_id="sword-a", target_id="b", mode_id="ranged")
    assert await play.store.read(cid) == before


async def test_crossbow_damage_does_not_fall_with_wielder_fatigue(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=rated("crossbow", 10), ranged_scene=scene()
    )
    await load(cid, play)
    await load(cid, play)
    await spend_fp(play, cid, "a", 8)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([1, 2, 2, 3])
    result = await defend(cid, play, "b")
    assert result.injury is not None and result.injury.basic_damage == 2


async def test_half_damage_boundary_for_existing_basic_weapon(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=weapon(), ranged_scene=scene(10)
    )
    await load(cid, play)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([3, 3, 3, 5])
    result = await defend(cid, play, "b")
    assert result.injury is not None and result.injury.basic_damage == 2


async def test_critical_self_wound_uses_weapon_st(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=rated(),
        ranged_scene=scene(),
    )
    await turn(cid, play, "a", "ready", item_id="sword-a", ready_hand="both")
    await turn(cid, play, "b", "do_nothing")
    await load(cid, play)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([6, 6, 6, 1, 2, 2, 1, 2, 2, 1, 1, 5])
    result = await defend(cid, play, "b")
    assert result.injury is not None and result.injury.adjudication_required is None
    state = play._load(await play.store.read(cid))
    limb = CriticalLimbResult.model_validate_json(
        next(e.kind for e in state.resources.events if e.id.startswith("critical-limb:"))
    )
    assert limb.injury == 3
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 7
    assert next(i.quantity for i in state.resources.items if i.id == "ammo-a") == 9
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize(("st", "seconds"), [(8, 4), (10, 4), (11, 8), (12, 8)])
async def test_crossbow_reload_timing_interruptions_and_restart(
    tmp_path: Path, st: int, seconds: int
) -> None:
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=rated("crossbow", st), ranged_scene=scene()
    )
    for index in range(seconds):
        before = play._load(await play.store.read(cid))
        command = TakeCombatTurn(
            id=f"load-{index}",
            actor_id="a",
            expected_revision=before.revision,
            encounter_id="fight",
            maneuver="ready",
            item_id="sword-a",
            mode_id="ranged",
            reload_ammunition_id="ammo-a",
        )
        result = await CombatService(play).execute(cid, command, authenticated_actor_id="a")
        assert isinstance(play.store, AsyncSQLiteStore)
        play = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))
        assert await CombatService(play).execute(cid, command, authenticated_actor_id="a") == result
        state = play._load(await play.store.read(cid))
        ammunition = state.resources.ammunition_loads[0]
        assert ammunition.rounds == (1 if index == seconds - 1 else 0)
        assert ammunition.reload_progress == (0 if index == seconds - 1 else index + 1)
        assert next(i.quantity for i in state.resources.items if i.id == "ammo-a") == 10
        await turn(cid, play, "b", "do_nothing")
        if index == 1:
            await turn(cid, play, "a", "do_nothing")
            await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([3, 3, 3, 3])
    result = await defend(cid, play, "b")
    assert result.injury is not None
    # ST8 thrust-3, ST10 thrust-2, ST11/12 thrust-1; +1 ammunition.
    assert result.injury.basic_damage == {8: 1, 10: 2, 11: 3, 12: 3}[st]
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize(
    ("st", "message"), [(13, "cocking-aid"), (14, "cocking-aid"), (15, "too high")]
)
async def test_crossbows_requiring_missing_protocols_reject(
    tmp_path: Path, st: int, message: str
) -> None:
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=rated("crossbow", st), ranged_scene=scene()
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match=message):
        await turn(
            cid,
            play,
            "a",
            "ready",
            item_id="sword-a",
            mode_id="ranged",
            reload_ammunition_id="ammo-a",
        )
    assert await play.store.read(cid) == before


def test_rating_is_opt_in_and_validated() -> None:
    assert "rated_strength" not in weapon().model_dump()
    for field, value in (
        ("hands", 1),
        ("shots", 2),
        ("reload_seconds", 0),
        ("range_basis", "yards"),
    ):
        data = rated().model_dump()
        data[field] = value
        with pytest.raises(ModelValidationError, match="Rated bows"):
            RangedMode.model_validate(data)


async def test_rating_rejects_lite_and_unlisted_damage_rows(tmp_path: Path) -> None:
    _, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=rated(), ranged_scene=scene()
    )
    assert play.engine.rules.combat is not None
    catalog = play.engine.rules.combat.gurps_equipment
    assert catalog is not None
    data = catalog.model_dump()
    data["profile_id"] = "gurps-lite-4e-2004"
    with pytest.raises(ModelValidationError, match="exact Basic Set"):
        EquipmentCatalog.model_validate(data)
    encoded = catalog.model_dump_json().replace('"st":8', '"st":41')
    with pytest.raises(ValidationError, match="no damage row"):
        EquipmentCatalog.model_validate_json(encoded)
