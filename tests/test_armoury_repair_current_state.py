"""B483 reduced modes end at one-third HP; B484 failed repairs restore nothing."""

import secrets
from pathlib import Path

import pytest
from support.runtime import build_play, seed_campaign
from test_combat_sensory_authority import change
from test_gurps_melee import setup

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.rules.types.object import ObjectProfile
from wayfarer.engine.simulation.actions import PlayState, Wait
from wayfarer.engine.simulation.combat.equipment_entry import effective_entry
from wayfarer.engine.simulation.equipment.catalog import (
    LITE_EQUIPMENT,
    LITE_SOURCE,
    Armor,
    EquipmentProfile,
)
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.engine.simulation.resources import Item
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService, EndEncounter, RepairEquipment
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands


async def fixture(
    path: Path, backend: str, skill_id: str, maximum: int = 12, initial_hp: int = 2
) -> tuple[str, PlayService, str]:
    armor = skill_id == "skill:armoury-body-armor"
    original_entry = (
        EquipmentProfile(
            definition_id="equipment:repair-armor",
            provenance=LITE_SOURCE,
            weight_millipounds=3000,
            price=500,
            technology_level=2,
            slot="body",
            armor=Armor(locations=("torso",), dr=6),
        )
        if armor
        else next(e for e in LITE_EQUIPMENT.entries if e.definition_id == "equipment:broadsword")
    )
    reduced = original_entry.model_copy(update={"definition_id": "equipment:reduced-repair-item"})
    profile = ObjectProfile(
        construction="homogenous",
        hp=maximum,
        dr=6,
        ht=12,
        repair_skill_id=skill_id,
        repair_tools_definition="equipment:armoury-tools",
        repair_parts_definition="equipment:spare-parts" if initial_hp <= 0 else None,
        reduced_effectiveness_definitions=(reduced.definition_id,),
    )
    target_id = "armor-b" if armor else "sword-b"
    cid, original = await setup(
        path / "source",
        "gurps-basic-set-4e-2004",
        durability=profile,
        object_hp=initial_hp,
        campaign_technology_level=2,
        repair_skill_technology_level=2,
        extra_equipment=(reduced, original_entry.model_copy(update={"durability": profile}))
        if armor
        else (reduced,),
        extra_items=(Item(id=target_id, owner_id="b", definition_id=original_entry.definition_id),)
        if armor
        else (),
    )
    play = build_play(path, original.engine, backend=backend, rng=RecordedDice(()))
    await seed_campaign(play.store, await original.store.read(cid))
    await CombatService(play).execute(
        cid,
        EndEncounter(
            id="end", actor_id="gm", expected_revision=1, encounter_id="fight", reason="workshop"
        ),
        principal_id="gm",
    )

    def damaged(state: PlayState) -> PlayState:
        return state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            item.model_copy(
                                update={
                                    "condition": item.condition.model_copy(
                                        update={
                                            "reduced_definition_id": reduced.definition_id,
                                        }
                                    )
                                }
                            )
                            if item.id == target_id and item.condition
                            else item
                            for item in state.resources.items
                        )
                    }
                )
            }
        )

    await change(play, cid, damaged)
    return cid, play, target_id


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("skill_id", ["skill:armoury-melee-weapons", "skill:armoury-body-armor"])
@pytest.mark.parametrize(
    "maximum,dice,hp,reduced",
    [
        (12, (3, 3, 3), 5, False),
        (12, (3, 3, 4), 4, False),
        (13, (3, 3, 4), 4, True),
        (13, (3, 3, 3), 5, False),
        (12, (4, 4, 4), 3, True),
        (12, (6, 6, 6), 2, True),
    ],
)
async def test_repair_restores_normal_equipment_only_at_source_threshold(
    tmp_path: Path,
    backend: str,
    skill_id: str,
    maximum: int,
    dice: tuple[int, int, int],
    hp: int,
    reduced: bool,
) -> None:
    cid, play, target_id = await fixture(tmp_path, backend, skill_id, maximum)
    service = CombatService(play)
    state = play._load(await play.store.read(cid))
    start = RepairEquipment(
        id="repair",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        item_id=target_id,
        stage="start",
    )
    await service.execute(cid, start, principal_id="b")
    state = play._load(await play.store.read(cid))
    task = tasks(state.resources)[0]
    assert task.due - task.start == 1800 and task.skill == 12
    early = RepairEquipment(
        id="early",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        item_id=target_id,
        stage="finish",
        task_id=start.id,
    )
    saved = await play.store.read(cid)
    with pytest.raises(ConflictError, match="deadline"):
        await service.execute(cid, early, principal_id="b")
    with pytest.raises(ValidationError, match="authorized"):
        await service.execute(cid, early, principal_id="a")
    assert isinstance(play.rng, RecordedDice)
    assert saved == await play.store.read(cid) and play.rng.exhausted()
    await play.execute(
        cid,
        Wait(id="work", actor_id="b", expected_revision=state.revision, ticks=1800),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    with pytest.raises(ConflictError):
        await service.execute(cid, early, principal_id="b")
    old = next(i for i in state.resources.items if i.id == target_id)
    finish = RepairEquipment(
        id="finish",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        item_id=target_id,
        stage="finish",
        task_id=start.id,
    )
    play.rng = RecordedDice(dice)
    result = await service.execute(cid, finish, principal_id="b")
    after = play._load(await play.store.read(cid))
    item = next(i for i in after.resources.items if i.id == target_id)
    assert item.condition and item.condition.hp == hp
    assert bool(item.condition.reduced_definition_id) == reduced
    assert effective_entry(play.rules_context, item).definition_id == (
        "equipment:reduced-repair-item" if reduced else old.definition_id
    )
    assert item.owner_id == old.owner_id and item.quantity == old.quantity
    assert tuple(i for i in after.resources.items if i.id != target_id) == tuple(
        i for i in state.resources.items if i.id != target_id
    )
    assert tasks(after.resources)[0].restored_hp == hp - 2
    play.engine.resources.validate(after.resources)
    assert play.rng.exhausted()
    saved = await play.store.read(cid)
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice(()))
    assert await CombatService(restarted).execute(cid, finish, principal_id="b") == result
    assert saved == await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("skill_id", ["skill:armoury-melee-weapons", "skill:armoury-body-armor"])
async def test_major_armoury_repair_conserves_source_priced_parts_on_retry(
    tmp_path: Path,
    backend: str,
    skill_id: str,
) -> None:
    cid, play, target_id = await fixture(tmp_path, backend, skill_id, initial_hp=0)
    service = CombatService(play)
    state = play._load(await play.store.read(cid))
    start = RepairEquipment(
        id="repair",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        item_id=target_id,
        stage="start",
    )
    play.rng = RecordedDice((2,))
    result = await service.execute(cid, start, principal_id="b")
    state = play._load(await play.store.read(cid))
    task = tasks(state.resources)[0]
    # B485: $500 * (2 * 10%) / $10 per owned part = ten parts.
    assert task.parts_die == 2 and task.parts_quantity == 10 and task.skill == 10
    assert next(i.quantity for i in state.resources.items if i.id == "parts-b") == 20
    assert play.rng.exhausted()
    play.rng = RecordedDice(())
    assert await service.execute(cid, start, principal_id="b") == result
    await play.execute(
        cid,
        Wait(id="work", actor_id="b", expected_revision=state.revision, ticks=1800),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    finish = RepairEquipment(
        id="finish",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        item_id=target_id,
        stage="finish",
        task_id=start.id,
    )
    play.rng = RecordedDice((3, 3, 3))
    result = await service.execute(cid, finish, principal_id="b")
    after = play._load(await play.store.read(cid))
    item = next(i for i in after.resources.items if i.id == target_id)
    assert item.condition and item.condition.hp == 1 and item.condition.reduced_definition_id
    assert tasks(after.resources)[0].restored_hp == 1
    assert next(i.quantity for i in after.resources.items if i.id == "parts-b") == 20
    assert play.rng.exhausted()
    saved = await play.store.read(cid)
    play.rng = RecordedDice(())
    assert await service.execute(cid, finish, principal_id="b") == result
    assert saved == await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("skill_id", ["skill:armoury-melee-weapons", "skill:armoury-body-armor"])
async def test_threshold_repair_seed_reexecution_matches_entire_campaign(
    tmp_path: Path,
    backend: str,
    skill_id: str,
) -> None:
    cid, play, target_id = await fixture(tmp_path, backend, skill_id)
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        RepairEquipment(
            id="repair",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            item_id=target_id,
            stage="start",
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    await play.execute(
        cid,
        Wait(id="work", actor_id="b", expected_revision=state.revision, ticks=1800),
        principal_id="b",
    )
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    state = play._load(initial)
    for number in range(1000):
        seed = f"{number:064x}"
        rng = SeededRandom(seed)
        if sum(rng.randbelow(6) + 1 for _ in range(3)) == 9:
            break
    else:
        raise AssertionError("No deterministic source oracle seed")
    play.rng = secrets
    play.seeds = lambda: seed
    await CombatService(play).execute(
        cid,
        RepairEquipment(
            id="finish",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            item_id=target_id,
            stage="finish",
            task_id="repair",
        ),
        principal_id="b",
    )
    records = (await play.store.history(cid))[count:]
    events = [e for e in await play.store.stream(cid) if e.command_id == "finish"]
    replayed, checks = await verify_commands(
        initial,
        records,
        events,
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert len(checks) == 1 and checks[0].folded and checks[0].reexecuted
    assert replayed == await play.store.read(cid) == await play.store.replay(cid)
    item = next(i for i in play._load(replayed).resources.items if i.id == target_id)
    assert (
        item.condition and item.condition.hp == 5 and item.condition.reduced_definition_id is None
    )
