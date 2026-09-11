"""B483-485/B556 numeric results committed through two-identity live combat."""

import asyncio
from pathlib import Path

import pytest
from test_gurps_melee import attack, choice, setup

from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.object_types import ObjectProfile
from wayfarer.simulation.mechanics.object_combat import BreakageResult


@pytest.mark.parametrize("table", [(1, 1, 1), (1, 1, 2), (6, 6, 5), (6, 6, 6), (3, 3, 3)])
async def test_breakage_retains_custody_and_replays(
    tmp_path: Path, table: tuple[int, int, int]
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        durability=ObjectProfile(
            construction="homogenous",
            hp=12,
            dr=6,
            ht=12,
        ),
        critical_breakage="cheap" if sum(table) == 9 else "ordinary",
    )
    await attack(cid, play)
    play.rng = RecordedDice([6, 6, 6, *table])
    results = await asyncio.gather(
        *(CombatService(play).execute(cid, choice(), authenticated_actor_id="b") for _ in range(2))
    )
    assert results[0] == results[1]
    assert results[0].injury and results[0].injury.adjudication_required is None
    state = play._load(await play.store.read(cid))
    item = next(i for i in state.resources.items if i.id == "sword-a")
    assert item.owner_id == "a" and not item.ready and item.equipped
    assert item.condition and item.condition.disabled and not item.condition.destroyed
    assert item.condition.hp == 12
    assert "sword-a" not in state.encounters[0].participants[0].ready_item_ids
    assert isinstance(play.store, AsyncSQLiteStore)
    restarted = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))
    assert (
        await CombatService(restarted).execute(cid, choice(), authenticated_actor_id="b")
        == results[0]
    )
    assert restarted._load(await restarted.store.read(cid)).resources == state.resources
    with pytest.raises(ValidationError, match="authorized"):
        await CombatService(restarted).execute(cid, choice(), authenticated_actor_id="a")


@pytest.mark.parametrize("confirmation,broken", [((3, 3, 3), False), ((1, 1, 1), True)])
async def test_fine_weapon_confirmation_is_recorded_not_dispatched(
    tmp_path: Path, confirmation: tuple[int, int, int], broken: bool
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        durability=ObjectProfile(construction="homogenous", hp=12, dr=6, ht=12),
        critical_breakage="resistant",
    )
    await attack(cid, play)
    play.rng = RecordedDice([6, 6, 6, 1, 1, 1, *confirmation])
    result = await CombatService(play).execute(cid, choice(), authenticated_actor_id="b")
    state = play._load(await play.store.read(cid))
    item = next(i for i in state.resources.items if i.id == "sword-a")
    assert item.condition and item.condition.disabled is broken
    assert item.equipped is broken and not item.ready
    event = next(e for e in state.resources.events if e.id.startswith("critical-breakage:"))
    record = BreakageResult.model_validate_json(event.kind)
    assert record.table == (1, 1, 1) and record.confirmation == confirmation
    assert result.injury and result.injury.effect_dice == confirmation
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, choice(), authenticated_actor_id="b") == result


async def test_weapon_flight_landing_collision_and_remote_ready_guard(tmp_path: Path) -> None:
    from wayfarer.simulation.mechanics.weapon_flight import FlightResult, retrieve

    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    await attack(cid, play)
    # Actor a is (0,0) facing east; one yard forward lands on b at (1,0).
    play.rng = RecordedDice([6, 6, 6, 4, 5, 5, 1, 1, 6, 6, 6, 4])
    result = await CombatService(play).execute(cid, choice(), authenticated_actor_id="b")
    state = play._load(await play.store.read(cid))
    event = next(e for e in state.resources.events if e.id.startswith("critical-flight:"))
    flight = FlightResult.model_validate_json(event.kind)
    item = next(i for i in state.resources.items if i.id == "sword-a")
    assert flight.landing == item.ground
    assert (flight.landing.x, flight.landing.y) == (1, 0)
    assert flight.collisions[0].actor_id == "b"
    assert flight.collisions[0].damage_dice == (4,)
    assert flight.collisions[0].injury == 3  # floor((4+1)/2) cutting damage, times 1.5.
    assert not item.ready and not item.equipped and item.owner_id == "a"
    with pytest.raises(ValidationError, match="recorded location"):
        retrieve(state, state.encounters[0], "a", "sword-a")
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, choice(), authenticated_actor_id="b") == result


async def test_object_target_records_damage_without_hurting_owner(tmp_path: Path) -> None:
    from wayfarer.orchestration.combat import TakeCombatTurn

    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        durability=ObjectProfile(construction="homogenous", hp=12, dr=2, ht=12),
    )
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="attack",
            actor_id="a",
            expected_revision=1,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            mode_id="swing",
            target_id="b",
            target_item_id="sword-b",
        ),
        authenticated_actor_id="a",
    )
    play.rng = RecordedDice([3, 3, 3, 4])
    result = await CombatService(play).execute(cid, choice(), authenticated_actor_id="b")
    state = play._load(await play.store.read(cid))
    assert result.injury and result.injury.attack.effective_target == 9
    assert result.injury.hp_before == result.injury.hp_after == 10
    damage = state.resources.object_results[-1]
    assert damage.item_id == "sword-b" and damage.injury == 4  # floor((4+1-2)*1.5)
    assert damage.condition.hp == 8
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, choice(), authenticated_actor_id="b") == result


@pytest.mark.parametrize(
    "defense_dice,shield_injury,owner_injury", [((3, 3, 3), 7, 3), ((2, 3, 3), 0, 0)]
)
async def test_shield_only_intercepts_when_db_changes_outcome(
    tmp_path: Path, defense_dice: tuple[int, int, int], shield_injury: int, owner_injury: int
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        durability=ObjectProfile(construction="homogenous", hp=12, dr=0, ht=12),
    )
    await attack(cid, play)
    play.rng = RecordedDice([3, 3, 3, *defense_dice, *([4] if shield_injury else [])])
    result = await CombatService(play).execute(cid, choice("dodge"), authenticated_actor_id="b")
    state = play._load(await play.store.read(cid))
    assert result.injury and result.injury.injury == owner_injury
    shield = next(i for i in state.resources.items if i.id == "shield-b")
    assert shield.condition and shield.condition.hp == 12 - shield_injury


async def test_used_weapon_stress_commits_failed_attack_without_rolling_attack(
    tmp_path: Path,
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        durability=ObjectProfile(construction="homogenous", hp=12, dr=6, ht=12),
        object_hp=0,
    )
    play.rng = RecordedDice([6, 6, 6])
    await attack(cid, play)
    state = play._load(await play.store.read(cid))
    item = next(i for i in state.resources.items if i.id == "sword-a")
    assert item.condition and item.condition.disabled and item.condition.last_stress_at == 0
    assert state.encounters[0].pending_defense is None
    assert state.encounters[0].current_actor_id == "b"
    assert all(
        i.condition and i.condition.last_stress_at is None
        for i in state.resources.items
        if i.id in ("sword-b", "shield-b")
    )
    play.rng = RecordedDice([])
    await attack(cid, play)
    assert play._load(await play.store.read(cid)) == state


async def test_timed_repair_has_no_early_roll_or_ownership_bypass(tmp_path: Path) -> None:
    from wayfarer.orchestration.combat import EndEncounter, RepairEquipment
    from wayfarer.simulation.actions import Wait
    from wayfarer.simulation.object_repairs import tasks

    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        durability=ObjectProfile(
            construction="homogenous",
            hp=12,
            dr=6,
            ht=12,
            repair_skill_id="skill:armoury",
            repair_tools_definition="equipment:armoury-tools",
        ),
        object_hp=6,
    )
    await CombatService(play).execute(
        cid,
        EndEncounter(
            id="end",
            actor_id="gm",
            expected_revision=1,
            encounter_id="fight",
            reason="safe workshop",
        ),
        authenticated_actor_id="gm",
    )
    begin = RepairEquipment(
        id="repair",
        actor_id="b",
        expected_revision=2,
        encounter_id="fight",
        item_id="sword-b",
        stage="start",
    )
    play.rng = RecordedDice([])
    await CombatService(play).execute(cid, begin, authenticated_actor_id="b")
    state = play._load(await play.store.read(cid))
    assert tasks(state.resources)[0].due == state.resources.game_time + 1800
    finish = RepairEquipment(
        id="finish",
        actor_id="b",
        expected_revision=3,
        encounter_id="fight",
        item_id="sword-b",
        stage="finish",
        task_id="repair",
    )
    from wayfarer.errors import ConflictError

    with pytest.raises(ConflictError, match="deadline"):
        await CombatService(play).execute(cid, finish, authenticated_actor_id="b")
    assert play._load(await play.store.read(cid)) == state
    with pytest.raises(ValidationError, match="authorized"):
        await CombatService(play).execute(cid, finish, authenticated_actor_id="a")
    await play.execute(
        cid,
        Wait(id="work", actor_id="b", expected_revision=3, ticks=1800),
        authenticated_actor_id="b",
    )
    play.rng = RecordedDice([3, 3, 3])
    after_work = play._load(await play.store.read(cid))
    assert after_work.resources.game_time == 1800
    finish = finish.model_copy(update={"expected_revision": after_work.revision})
    result = await CombatService(play).execute(cid, finish, authenticated_actor_id="b")
    state = play._load(await play.store.read(cid))
    task = tasks(state.resources)[0]
    assert task.status == "completed" and task.check and task.check.effective_target == 12
    assert task.restored_hp == 3
    item = next(i for i in state.resources.items if i.id == "sword-b")
    assert item.condition and item.condition.hp == 9 and not item.ready
    assert isinstance(play.store, AsyncSQLiteStore)
    restarted = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))
    assert await CombatService(restarted).execute(cid, finish, authenticated_actor_id="b") == result
    with pytest.raises(ConflictError, match="settled"):
        await CombatService(restarted).execute(
            cid,
            finish.model_copy(update={"id": "reroll", "expected_revision": state.revision}),
            authenticated_actor_id="b",
        )


async def test_major_repair_pins_parts_cost_and_locks_custody(tmp_path: Path) -> None:
    from wayfarer.errors import ConflictError
    from wayfarer.orchestration.combat import EndEncounter, RepairEquipment
    from wayfarer.simulation.actions import Wait
    from wayfarer.simulation.object_repairs import tasks
    from wayfarer.simulation.resources import Transfer

    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        durability=ObjectProfile(
            construction="homogenous",
            hp=12,
            dr=6,
            ht=12,
            repair_skill_id="skill:armoury",
            repair_tools_definition="equipment:armoury-tools",
            repair_parts_definition="equipment:spare-parts",
        ),
        object_hp=0,
    )
    await CombatService(play).execute(
        cid,
        EndEncounter(
            id="end",
            actor_id="gm",
            expected_revision=1,
            encounter_id="fight",
            reason="safe workshop",
        ),
        authenticated_actor_id="gm",
    )
    play.rng = RecordedDice([2])
    begin = RepairEquipment(
        id="repair",
        actor_id="b",
        expected_revision=2,
        encounter_id="fight",
        item_id="sword-b",
        stage="start",
    )
    result = await CombatService(play).execute(cid, begin, authenticated_actor_id="b")
    state = play._load(await play.store.read(cid))
    task = tasks(state.resources)[0]
    assert task.parts_die == 2 and task.parts_quantity == 10  # $500 * 20% / $10
    assert next(i.quantity for i in state.resources.items if i.id == "parts-b") == 20
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, begin, authenticated_actor_id="b") == result
    for item_id in ("sword-b", "tool-b"):
        with pytest.raises(ConflictError, match="pending repair"):
            play.engine.resources.apply(
                state.resources,
                Transfer(
                    id="transfer",
                    actor_id="b",
                    expected_revision=state.resources.revision,
                    item_id=item_id,
                    quantity=1,
                    owner_id="a",
                ),
            )
    await play.execute(
        cid,
        Wait(id="work", actor_id="b", expected_revision=state.revision, ticks=1800),
        authenticated_actor_id="b",
    )
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice([3, 3, 3])
    await CombatService(play).execute(
        cid,
        RepairEquipment(
            id="finish",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            item_id="sword-b",
            stage="finish",
            task_id="repair",
        ),
        authenticated_actor_id="b",
    )
    state = play._load(await play.store.read(cid))
    task = tasks(state.resources)[0]
    assert task.check and task.check.effective_target == 10 and task.restored_hp == 1
    repaired = next(i for i in state.resources.items if i.id == "sword-b")
    assert repaired.condition and repaired.condition.hp == 1


def test_reviewed_additive_requests_never_accept_damage_authority() -> None:
    """Each tactical major's examples validate against that major's own request.

    `repair_equipment` is a v2 command: the v1 request deliberately keeps the
    reviewed v1 command set, so its examples live under the major that accepts
    them rather than under one that must refuse them.
    """
    import json

    from pydantic import ValidationError as SchemaError

    from wayfarer.transport.tactical_api import TacticalRequest, TacticalRequestV2

    for version, model in ((1, TacticalRequest), (2, TacticalRequestV2)):
        path = Path(f"contracts/tactical/v{version}/examples/object-commands.json")
        examples = json.loads(path.read_text())
        assert examples, path
        for example in examples:
            request = model.model_validate(example)
            assert request.command.actor_id == "a"
            for forged in ("basic_damage", "skill", "restored_hp", "due"):
                with pytest.raises(SchemaError):
                    model.model_validate({"command": {**example["command"], forged: 99}})
    # The v1 request refuses a v2-only command outright, not merely its fields.
    v2_only = json.loads(Path("contracts/tactical/v2/examples/object-commands.json").read_text())
    with pytest.raises(SchemaError):
        TacticalRequest.model_validate(v2_only[0])


async def test_cheap_weapon_breaks_on_parry_drop_exception(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        durability=ObjectProfile(construction="homogenous", hp=12, dr=6, ht=12),
        critical_breakage="cheap",
    )
    await attack(cid, play)
    play.rng = RecordedDice([3, 3, 3, 6, 6, 6, 4, 5, 5, 4, 3, 3, 3])
    result = await CombatService(play).execute(cid, choice("parry"), authenticated_actor_id="b")
    state = play._load(await play.store.read(cid))
    item = next(i for i in state.resources.items if i.id == "sword-b")
    assert item.condition and item.condition.disabled and not item.condition.destroyed
    assert result.injury and result.injury.adjudication_required is None
    assert result.injury.injury == 7  # Failed parry still admits the incoming 5 cutting damage.
