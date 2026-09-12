"""Independent Characters B278 and Campaigns B382/B407 numeric fixtures.

Most mechanics fixtures are synthetic; one inspected catalog row binds them to
production data. Frozen source certification remains #191/#180.
"""

from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError as ModelError
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup
from test_gurps_ranged import load, scene, weapon

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.skills.mundane.ranged import definitions
from wayfarer.engine.rules.types.firearm import FirearmSpec
from wayfarer.engine.simulation.combat.firearm_transitions import ServiceRecord
from wayfarer.engine.simulation.combat.firearms import MalfunctionRecord, save_malfunction
from wayfarer.engine.simulation.equipment.basic.catalog import BASIC_EQUIPMENT
from wayfarer.engine.simulation.equipment.catalog import Damage, EquipmentCatalog, RangedMode
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService, TakeCombatTurn
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


def firearm(
    action: Literal["repeating", "revolver"] = "repeating", malf: int | None = None
) -> RangedMode:
    data = weapon().model_dump()
    data.update(
        damage=Damage(basis="fixed", dice=1, damage_type="pi"),
        firearm=FirearmSpec(
            technology_level=6,
            action=action,
            malfunction_override=malf,
            armoury_skill_id="skill:armoury",
        ),
    )
    return RangedMode.model_validate(data)


def production_firearm(definition_id: str) -> RangedMode:
    entry = next(v for v in BASIC_EQUIPMENT.entries if v.definition_id == definition_id)
    assert len(entry.modes) == 1 and isinstance(entry.modes[0], RangedMode)
    return entry.modes[0]


@pytest.mark.parametrize(
    ("tl", "quality", "expected"),
    [
        (5, "cheap", 15),
        (5, "ordinary", 16),
        (5, "fine", 17),
        (6, "ordinary", 17),
        (6, "cheap", 16),
        (6, "fine", 18),
        (8, "very-fine", 18),
    ],
)
def test_source_malfunction_numbers(tl: int, quality: str, expected: int) -> None:
    assert (
        FirearmSpec.model_validate(
            dict(technology_level=tl, action="repeating", quality=quality)
        ).malfunction_number
        == expected
    )


async def test_b278_production_revolver_uses_authoritative_burst_failure(tmp_path: Path) -> None:
    mode = production_firearm("equipment:snub-revolver-38")
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=mode,
        ranged_scene=scene(),
        extra_definitions=definitions(),
        extra_purchases=(Purchase(definition_id="skill:guns-pistol", amount=4),),
        campaign_technology_level=6,
    )
    # B278's 3i protocol takes three Ready maneuvers for each round.
    for _ in range(9):
        await turn(
            cid,
            play,
            "a",
            "ready",
            item_id="sword-a",
            mode_id="shot",
            reload_ammunition_id="ammo-a",
        )
        await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="shot", shots=3)
    state = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="production-failure",
        actor_id="b",
        encounter_id="fight",
        expected_revision=state.revision,
        defense="dodge",
    )
    play.rng = RecordedDice([6, 6, 6, 3, 3, 3])
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="b")
    assert result.injury is not None
    assert (result.injury.malfunction, result.injury.shots_fired, result.injury.hits) == (
        "stoppage",
        1,
        0,
    )
    saved = play._load(await play.store.read(cid))
    assert saved.resources.ammunition_loads[0].rounds == 2
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_b278_chambered_pistol_loads_printed_total_capacity(tmp_path: Path) -> None:
    mode = production_firearm("equipment:auto-pistol-9mm-tl6")
    assert (mode.shots, mode.chamber_capacity) == (9, 1)
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=mode,
        ranged_scene=scene(),
        extra_definitions=definitions(),
        extra_purchases=(Purchase(definition_id="skill:guns-pistol", amount=4),),
        campaign_technology_level=6,
    )
    for _ in range(3):
        await turn(
            cid,
            play,
            "a",
            "ready",
            item_id="sword-a",
            mode_id="shot",
            reload_ammunition_id="ammo-a",
        )
        await turn(cid, play, "b", "do_nothing")
    state = play._load(await play.store.read(cid))
    assert state.resources.ammunition_loads[0].rounds == 9
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize(
    ("table", "kind", "fired"),
    [
        ((1, 1, 1), "mechanical", 0),
        ((1, 1, 2), "mechanical", 0),
        ((1, 2, 2), "misfire", 0),
        ((2, 3, 3), "misfire", 0),
        ((3, 3, 3), "stoppage", 1),
        ((3, 4, 4), "stoppage", 1),
        ((4, 4, 4), "misfire", 0),
        ((4, 5, 5), "misfire", 0),
        ((5, 5, 5), "mechanical", 0),
        ((6, 6, 6), "mechanical", 0),
    ],
)
async def test_malfunction_precedes_critical_miss_and_retries_once(
    tmp_path: Path,
    table: tuple[int, int, int],
    kind: str,
    fired: int,
) -> None:
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=firearm(), ranged_scene=scene()
    )
    await load(cid, play)
    await turn(
        cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged", shots=6
    )
    state = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="failure",
        actor_id="b",
        encounter_id="fight",
        expected_revision=state.revision,
        defense="dodge",
    )
    play.rng = RecordedDice([6, 6, 6, *table])
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="b")
    assert result.injury is not None
    assert result.injury.malfunction == kind and result.injury.malfunction_table == table
    assert result.injury.critical_table == () and result.injury.defense is None
    assert result.injury.shots_fired == fired and result.injury.hits == 0
    assert result.injury.adjudication_required is None
    assert isinstance(play.store, AsyncSQLiteStore)
    play = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))
    assert await CombatService(play).execute(cid, command, authenticated_actor_id="b") == result
    state = play._load(await play.store.read(cid))
    assert state.resources.ammunition_loads[0].rounds == 6 - fired
    assert next(i.quantity for i in state.resources.items if i.id == "ammo-a") == 10 - fired
    assert next(i for i in state.resources.items if i.id == "sword-a").firearm_failure is not None
    events = [e for e in state.resources.events if e.id.startswith("firearm-malfunction:")]
    assert len(events) == 1
    record = MalfunctionRecord.model_validate_json(events[0].kind)
    assert record.original_attack.dice == (6, 6, 6)
    assert record.ammunition_load is not None
    assert record.ammunition_load.rounds == 6 and record.trace == result.injury
    assert save_malfunction(state.resources, record) == state.resources
    with pytest.raises(ConflictError):
        save_malfunction(
            state.resources, record.model_copy(update={"attacker_build_revision": "changed"})
        )
    assert await play.store.read(cid) == await play.store.replay(cid)


async def malfunction(
    tmp_path: Path,
    table: tuple[int, int, int],
    action: Literal["repeating", "revolver"] = "repeating",
) -> tuple[str, PlayService]:
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=firearm(action), ranged_scene=scene()
    )
    await load(cid, play)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([6, 6, 6, *table])
    await defend(cid, play, "b")
    await turn(cid, play, "b", "do_nothing")
    return cid, play


async def test_failure_cannot_be_bypassed_with_ready_reload_or_unload(tmp_path: Path) -> None:
    cid, play = await malfunction(tmp_path, (3, 3, 3))
    await turn(cid, play, "a", "ready", item_id="sword-a")
    await turn(cid, play, "b", "do_nothing")
    before = await play.store.read(cid)
    for options in (
        dict(maneuver="attack", target_id="b", mode_id="ranged"),
        dict(maneuver="ready", reload_ammunition_id="ammo-a", mode_id="ranged"),
        dict(maneuver="ready", unload_ammunition=True),
    ):
        with pytest.raises(ValidationError, match="failure"):
            await turn(cid, play, "a", item_id="sword-a", **options)
        assert await play.store.read(cid) == before


@pytest.mark.parametrize("dice,hits", [((3, 3, 3), 1), ((4, 5, 5), 0)])
async def test_stoppage_fires_only_one_without_rapid_fire_bonus(
    tmp_path: Path, dice: tuple[int, int, int], hits: int
) -> None:
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=firearm(malf=8), ranged_scene=scene()
    )
    await load(cid, play)
    await turn(
        cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged", shots=6
    )
    play.rng = RecordedDice([*dice, 3, 3, 3, *([2] if hits else [])])
    result = await defend(cid, play, "b")
    assert result.injury is not None
    assert result.injury.attack.effective_target == 13
    assert result.injury.shots_fired == 1 and result.injury.hits == hits
    assert result.injury.per_hit_damage == ((2,) if hits else ())


async def test_revolver_advances_past_misfire_on_next_attack(tmp_path: Path) -> None:
    cid, play = await malfunction(tmp_path, (2, 2, 2), "revolver")
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([3, 3, 3, 2])
    result = await defend(cid, play, "b")
    assert result.injury is not None and result.injury.shots_fired == 1
    state = play._load(await play.store.read(cid))
    assert state.resources.ammunition_loads[0].rounds == 4
    assert next(i.quantity for i in state.resources.items if i.id == "ammo-a") == 8
    assert next(i for i in state.resources.items if i.id == "sword-a").firearm_failure is None


@pytest.mark.parametrize("roll,remaining", [((1, 2, 2), False), ((4, 4, 4), True)])
async def test_stoppage_clearing_three_readies_and_interruption(
    tmp_path: Path,
    roll: tuple[int, int, int],
    remaining: bool,
) -> None:
    cid, play = await malfunction(tmp_path, (3, 3, 3))
    for index in range(3):
        state = play._load(await play.store.read(cid))
        command = TakeCombatTurn(
            id=f"clear-{index}",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="ready",
            item_id="sword-a",
            mode_id="ranged",
            firearm_service="clear",
        )
        play.rng = RecordedDice(list(roll) if index == 2 else [])
        result = await CombatService(play).execute(cid, command, authenticated_actor_id="a")
        assert isinstance(play.store, AsyncSQLiteStore)
        play = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))
        assert await CombatService(play).execute(cid, command, authenticated_actor_id="a") == result
        await turn(cid, play, "b", "do_nothing")
        if index == 0:
            await turn(cid, play, "a", "do_nothing")
            await turn(cid, play, "b", "do_nothing")
    state = play._load(await play.store.read(cid))
    assert (
        next(i for i in state.resources.items if i.id == "sword-a").firearm_failure is not None
    ) == remaining
    record = ServiceRecord.model_validate_json(
        next(e.kind for e in state.resources.events if e.id == "firearm-service:clear-2")
    )
    assert record.roll.effective_target == 9  # DX skill 13 -> IQ skill 13, stoppage -4.
    assert state.resources.ammunition_loads[0].rounds == 5
    assert await play.store.read(cid) == await play.store.replay(cid)


def test_firearm_metadata_is_opt_in_and_bounded() -> None:
    assert "firearm" not in weapon().model_dump()
    with pytest.raises(ModelError):
        FirearmSpec(technology_level=2, action="repeating")
    for changes in (
        dict(thrown=True),
        dict(blockable=True),
        dict(damage=Damage(basis="fixed", dice=1, damage_type="burn")),
    ):
        with pytest.raises(ModelError):
            RangedMode.model_validate(firearm().model_dump() | changes)


async def test_lite_catalog_cannot_enable_malfunctions(tmp_path: Path) -> None:
    _, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=firearm(), ranged_scene=scene()
    )
    assert play.engine.rules.combat is not None
    equipment = play.engine.rules.combat.gurps_equipment
    assert equipment is not None
    with pytest.raises(ModelError, match="exact Basic Set"):
        EquipmentCatalog.model_validate(
            equipment.model_dump() | {"profile_id": "gurps-lite-4e-2004"}
        )


async def service_turn(cid: str, play: PlayService, operation: str, skill: str = "weapon") -> None:
    await turn(
        cid,
        play,
        "a",
        "ready",
        item_id="sword-a",
        mode_id="ranged",
        firearm_service=operation,
        firearm_service_skill=skill,
    )
    await turn(cid, play, "b", "do_nothing")


async def test_misfire_diagnosis_and_clearing_discard_one_round(tmp_path: Path) -> None:
    cid, play = await malfunction(tmp_path, (2, 2, 2))
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="Diagnose"):
        await service_turn(cid, play, "clear")
    assert await play.store.read(cid) == before
    play.rng = RecordedDice([4, 4, 4])
    await service_turn(cid, play, "diagnose", "armoury")
    state = play._load(await play.store.read(cid))
    record = ServiceRecord.model_validate_json(
        next(e.kind for e in state.resources.events if e.id.startswith("firearm-service:"))
    )
    assert record.roll.effective_target == 13  # Armoury 11 + 2 for misfire.
    assert record.after is not None and record.after.diagnosed
    assert state.resources.ammunition_loads[0].rounds == 6
    for index in range(3):
        play.rng = RecordedDice([4, 4, 4] if index == 2 else [])
        await service_turn(cid, play, "clear", "armoury")
    state = play._load(await play.store.read(cid))
    assert next(i for i in state.resources.items if i.id == "sword-a").firearm_failure is None
    assert state.resources.ammunition_loads[0].rounds == 5
    assert next(i.quantity for i in state.resources.items if i.id == "ammo-a") == 9
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_failed_diagnosis_does_not_reveal_or_clear_failure(tmp_path: Path) -> None:
    cid, play = await malfunction(tmp_path, (2, 2, 2))
    play.rng = RecordedDice([6, 6, 6])
    await service_turn(cid, play, "diagnose")
    state = play._load(await play.store.read(cid))
    failure = next(i for i in state.resources.items if i.id == "sword-a").firearm_failure
    assert failure is not None and not failure.diagnosed and failure.kind == "misfire"
    assert state.resources.ammunition_loads[0].rounds == 6


async def test_critical_clearing_failure_becomes_mechanical(tmp_path: Path) -> None:
    cid, play = await malfunction(tmp_path, (3, 3, 3))
    for index in range(3):
        play.rng = RecordedDice([6, 6, 6] if index == 2 else [])
        await service_turn(cid, play, "clear")
    state = play._load(await play.store.read(cid))
    failure = next(i for i in state.resources.items if i.id == "sword-a").firearm_failure
    assert failure is not None and failure.kind == "mechanical" and not failure.diagnosed
    assert failure.progress == 0


async def seed_repair_progress(cid: str, play: PlayService) -> None:
    snapshot = await play.store.read(cid)

    def commit(campaign: Campaign) -> CommandReceipt:
        state = play._load(campaign)
        item = next(i for i in state.resources.items if i.id == "sword-a")
        assert item.firearm_failure is not None
        failure = item.firearm_failure.model_copy(
            update={
                "progress": 3598,
                "service_actor_id": "a",
                "service_kind": "repair",
                "service_skill": "armoury",
            }
        )
        resources = state.resources.model_copy(
            update={
                "revision": state.revision + 1,
                "items": tuple(
                    i.model_copy(update={"firearm_failure": failure}) if i.id == item.id else i
                    for i in state.resources.items
                ),
            }
        )
        updated = state.model_copy(update={"resources": resources, "revision": state.revision + 1})
        play.engine.validate(updated)
        campaign["revision"], campaign["play_json"] = updated.revision, updated.model_dump_json()
        return CommandReceipt(action="resource", outcome="elapsed repair work")

    await play.store.commit_turn(
        cid, "repair-fixture", snapshot["revision"], "repair-fixture", commit
    )


@pytest.mark.parametrize(
    "dice,expected", [((3, 3, 3), None), ((4, 4, 4), "mechanical"), ((6, 6, 6), "destroyed")]
)
async def test_hourly_repair_success_failure_and_destruction(
    tmp_path: Path,
    dice: tuple[int, int, int],
    expected: str | None,
) -> None:
    cid, play = await malfunction(tmp_path, (1, 1, 1))
    play.rng = RecordedDice([3, 3, 3])
    await service_turn(cid, play, "diagnose")
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="Armoury"):
        await service_turn(cid, play, "repair")
    # The final two seconds exercise the 3600-Ready boundary without 7200 SQL turns.
    await seed_repair_progress(cid, play)
    await service_turn(cid, play, "repair", "armoury")
    state = play._load(await play.store.read(cid))
    failure = next(i for i in state.resources.items if i.id == "sword-a").firearm_failure
    assert failure is not None and failure.progress == 3599
    command = TakeCombatTurn(
        id="finish-repair",
        actor_id="a",
        expected_revision=state.revision,
        encounter_id="fight",
        maneuver="ready",
        item_id="sword-a",
        mode_id="ranged",
        firearm_service="repair",
        firearm_service_skill="armoury",
    )
    play.rng = RecordedDice(list(dice))
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="a")
    assert isinstance(play.store, AsyncSQLiteStore)
    play = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))
    assert await CombatService(play).execute(cid, command, authenticated_actor_id="a") == result
    state = play._load(await play.store.read(cid))
    failure = next(i for i in state.resources.items if i.id == "sword-a").firearm_failure
    assert (failure.kind if failure else None) == expected
    assert failure is None or failure.progress == 0
    assert state.resources.ammunition_loads[0].rounds == 6
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_service_rejects_occupied_hands_before_dice(tmp_path: Path) -> None:
    # Defender owns a ready shield and sword; fail with the hand constraint before a roll.
    cid, play = await malfunction(tmp_path, (3, 3, 3))
    state = play._load(await play.store.read(cid))
    from wayfarer.engine.simulation.combat.firearm_transitions import service

    shield = next(i for i in state.resources.items if i.id == "shield-b").model_copy(
        update={"owner_id": "a"}
    )
    resources = state.resources.model_copy(
        update={"items": tuple(shield if i.id == shield.id else i for i in state.resources.items)}
    )
    command = TakeCombatTurn(
        id="occupied",
        actor_id="a",
        expected_revision=state.revision,
        encounter_id="fight",
        maneuver="ready",
        item_id="sword-a",
        firearm_service="clear",
    )
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="two available hands"):
        service(
            play.rules_context,
            state.model_copy(update={"resources": resources}),
            state.encounters[0],
            command,
            validate_only=True,
        )
