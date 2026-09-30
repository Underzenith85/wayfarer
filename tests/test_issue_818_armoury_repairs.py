"""Actual Armoury restoration through the existing equipment transaction (#818).

Numerical expectations retain the B484 repair oracle already exercised by
test_object_combat: half an hour, margin HP with a minimum one, capped at damage.
No standalone arts-procedure receipt is accepted as repaired equipment.
"""

from pathlib import Path

import pytest
from test_gurps_melee import setup

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.object import ObjectProfile
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.equipment.catalog import LITE_SOURCE, Armor, EquipmentProfile
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.engine.simulation.resources import Item
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService, EndEncounter, RepairEquipment


@pytest.mark.parametrize(
    "skill_id,effect",
    [
        ("skill:armoury-melee-weapons", "restore-melee-weapon"),
        ("skill:armoury-body-armor", "restore-body-armor"),
    ],
)
@pytest.mark.parametrize(
    "dice,initial_hp,restored",
    [([3, 3, 3], 6, 3), ([4, 4, 4], 6, 1), ([6, 6, 6], 6, 0), ([3, 3, 3], 11, 1)],
)
async def test_restore_actual_equipment_and_retry(
    tmp_path: Path,
    skill_id: str,
    effect: str,
    dice: list[int],
    initial_hp: int,
    restored: int,
) -> None:
    profile = ObjectProfile(
        construction="homogenous",
        hp=12,
        dr=6,
        ht=12,
        repair_skill_id=skill_id,
        repair_tools_definition="equipment:armoury-tools",
    )
    body_armor = skill_id == "skill:armoury-body-armor"
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        durability=profile,
        object_hp=initial_hp,
        extra_equipment=(
            (
                EquipmentProfile(
                    definition_id="equipment:repair-armor",
                    provenance=LITE_SOURCE,
                    weight_millipounds=3000,
                    price=500,
                    technology_level=2,
                    slot="body",
                    armor=Armor(locations=("torso",), dr=6),
                    durability=profile,
                ),
            )
            if body_armor
            else ()
        ),
        extra_items=(
            (Item(id="armor-b", owner_id="b", definition_id="equipment:repair-armor"),)
            if body_armor
            else ()
        ),
    )
    service = CombatService(play)
    await service.execute(
        cid,
        EndEncounter(
            id="end",
            actor_id="gm",
            expected_revision=1,
            encounter_id="fight",
            reason="workshop",
        ),
        principal_id="gm",
    )
    item_id = "armor-b" if body_armor else "sword-b"
    begin = RepairEquipment(
        id="repair",
        actor_id="b",
        expected_revision=2,
        encounter_id="fight",
        item_id=item_id,
        stage="start",
    )
    play.rng = RecordedDice([])
    before = play._load(await play.store.read(cid))
    if body_armor:
        with pytest.raises(ValidationError, match="specialty"):
            await service.execute(
                cid, begin.model_copy(update={"item_id": "sword-b"}), principal_id="b"
            )
        assert play._load(await play.store.read(cid)) == before
    await service.execute(cid, begin, principal_id="b")
    pending = play._load(await play.store.read(cid))
    task = tasks(pending.resources)[0]
    assert (task.procedure_id, task.effect) == (skill_id, effect)
    assert task.due - task.start == 1800
    assert task.restored_hp == 0 and task.status == "pending"
    finish = RepairEquipment(
        id="finish",
        actor_id="b",
        expected_revision=pending.revision,
        encounter_id="fight",
        item_id=item_id,
        stage="finish",
        task_id="repair",
    )
    with pytest.raises(ConflictError, match="deadline"):
        await service.execute(cid, finish, principal_id="b")
    with pytest.raises(ValidationError, match="authorized"):
        await service.execute(cid, finish, principal_id="a")
    assert play._load(await play.store.read(cid)) == pending
    await play.execute(
        cid,
        Wait(id="work", actor_id="b", expected_revision=pending.revision, ticks=1800),
        principal_id="b",
    )
    worked = play._load(await play.store.read(cid))
    with pytest.raises(ConflictError):
        await service.execute(cid, finish, principal_id="b")
    assert play._load(await play.store.read(cid)) == worked
    finish = finish.model_copy(update={"expected_revision": worked.revision})
    play.rng = RecordedDice(dice)
    result = await service.execute(cid, finish, principal_id="b")
    after = play._load(await play.store.read(cid))
    task = tasks(after.resources)[0]
    assert task.status == "completed" and task.restored_hp == restored
    assert task.check and task.check.effective_target == 12
    original = next(item for item in worked.resources.items if item.id == item_id)
    repaired = next(item for item in after.resources.items if item.id == item_id)
    assert repaired.condition and repaired.condition.hp == initial_hp + restored
    assert repaired.owner_id == original.owner_id == "b"
    assert repaired.quantity == original.quantity == 1
    assert tuple(
        item for item in after.resources.items if item.id != item_id
    ) == tuple(item for item in worked.resources.items if item.id != item_id)
    if restored == 0:
        assert repaired == original
    play.rng = RecordedDice([])
    assert await service.execute(cid, finish, principal_id="b") == result
    assert play._load(await play.store.read(cid)) == after
