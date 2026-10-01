"""Selected-printing B178/B484–485 consumers: supplies, failures and durable receipts."""

from fractions import Fraction
from pathlib import Path
from typing import Literal

import pytest
from test_gurps_melee import setup

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.skills.mundane.ranged import definitions as ranged_definitions
from wayfarer.engine.rules.types.general_equipment import GeneralEquipmentFeature
from wayfarer.engine.rules.types.object import ObjectCondition, ObjectProfile
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.equipment.catalog import (
    LITE_EQUIPMENT,
    LITE_SOURCE,
    Armor,
    Damage,
    EquipmentProfile,
    Money,
    RangedMode,
    Shield,
    TechnologyLevel,
)
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.engine.simulation.resources import Item, Transfer
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService, EndEncounter, RepairEquipment
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore

Kind = Literal["melee", "armor", "shield", "thrown"]


async def workshop(
    tmp_path: Path,
    kind: Kind,
    *,
    hp: int = 0,
    price: Money = 500,
    technology_level: TechnologyLevel = 2,
    tool_features: tuple[GeneralEquipmentFeature, ...] = (),
) -> tuple[str, PlayService]:
    skill = "skill:armoury-body-armor" if kind == "armor" else "skill:armoury-melee-weapons"
    profile = ObjectProfile(
        construction="homogenous",
        hp=12,
        dr=6,
        ht=12,
        repair_skill_id=skill,
        repair_tools_definition="equipment:armoury-tools",
        repair_parts_definition="equipment:spare-parts",
    )
    melee = next(e for e in LITE_EQUIPMENT.entries if e.definition_id == "equipment:broadsword")
    entry = EquipmentProfile(
        definition_id="equipment:repair-target",
        provenance=LITE_SOURCE,
        weight_millipounds=3000,
        price=price,
        technology_level=technology_level,
        slot="body" if kind == "armor" else "hand",
        modes=melee.modes
        if kind == "melee"
        else (
            RangedMode(
                id="throw",
                skill_id="skill:thrown-weapon-knife",
                minimum_st=5,
                damage=Damage(basis="thrust", adds=-1, damage_type="imp"),
                accuracy=0,
                range_basis="st",
                maximum_range=1,
                shots=1,
                reload_seconds=1,
                bulk=-1,
                thrown=True,
            ),
        )
        if kind == "thrown"
        else (),
        armor=Armor(locations=("torso",), dr=6) if kind == "armor" else None,
        shield=Shield(skill_id="skill:shield", defense_bonus=1) if kind == "shield" else None,
        durability=profile,
    )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        durability=profile,
        object_hp=hp,
        campaign_technology_level=max(2, technology_level)
        if isinstance(technology_level, int)
        else 2,
        repair_skill_technology_level=2,
        repair_tool_features=tool_features,
        extra_definitions=tuple(
            d for d in ranged_definitions() if d.id == "skill:thrown-weapon-knife"
        )
        if kind == "thrown"
        else (),
        extra_equipment=(entry,),
        extra_items=(Item(id="repair-target", owner_id="b", definition_id=entry.definition_id),),
    )
    await CombatService(play).execute(
        cid,
        EndEncounter(
            id="end", actor_id="gm", expected_revision=1, encounter_id="fight", reason="workshop"
        ),
        principal_id="gm",
    )
    return cid, play


def begin(revision: int = 2) -> RepairEquipment:
    return RepairEquipment(
        id="repair",
        actor_id="b",
        expected_revision=revision,
        encounter_id="fight",
        item_id="repair-target",
        stage="start",
    )


@pytest.mark.parametrize("kind", ["melee", "armor"])
@pytest.mark.parametrize("parts_die,parts_spent", [(1, 5), (2, 10), (6, 30)])
@pytest.mark.parametrize("dice,restored", [((3, 3, 3), 1), ((4, 4, 4), 0), ((6, 6, 6), 0)])
async def test_major_repairs_conserve_supplies_through_restart(
    tmp_path: Path,
    kind: Kind,
    parts_die: int,
    parts_spent: int,
    dice: tuple[int, int, int],
    restored: int,
) -> None:
    cid, play = await workshop(tmp_path, kind)
    before = play._load(await play.store.read(cid))
    play.rng = RecordedDice((parts_die,))
    result = await CombatService(play).execute(cid, begin(), principal_id="b")
    assert play.rng.exhausted()
    pending = play._load(await play.store.read(cid))
    task = tasks(pending.resources)[0]
    assert task.skill == 10  # IQ/A 11, +1 for $500, -2 for HP <= 0.
    assert task.parts_die == parts_die and task.parts_quantity == parts_spent
    assert task.due - task.start == 1800
    assert task.effect == ("restore-body-armor" if kind == "armor" else "restore-melee-weapon")
    remaining = next((i.quantity for i in pending.resources.items if i.id == "parts-b"), 0)
    assert remaining + parts_spent == 30
    assert {i.id: i for i in pending.resources.items if i.id != "parts-b"} == {
        i.id: i for i in before.resources.items if i.id != "parts-b"
    }
    for item_id in ("repair-target", "tool-b"):
        with pytest.raises(ConflictError, match="pending repair"):
            play.engine.resources.apply(
                pending.resources,
                Transfer(
                    id="transfer",
                    actor_id="b",
                    expected_revision=pending.resources.revision,
                    item_id=item_id,
                    quantity=1,
                    owner_id="a",
                ),
            )
    restarted = PlayService(AsyncSQLiteStore(tmp_path / "melee.sqlite"), play.engine)
    restarted.rng = RecordedDice(())
    service = CombatService(restarted)
    assert await service.execute(cid, begin(), principal_id="b") == result
    assert restarted._load(await restarted.store.read(cid)) == pending
    await restarted.execute(
        cid,
        Wait(id="work", actor_id="b", expected_revision=pending.revision, ticks=1800),
        principal_id="b",
    )
    worked = restarted._load(await restarted.store.read(cid))
    finish = RepairEquipment(
        id="finish",
        actor_id="b",
        expected_revision=worked.revision,
        encounter_id="fight",
        item_id="repair-target",
        stage="finish",
        task_id="repair",
    )
    restarted.rng = RecordedDice(dice)
    result = await service.execute(cid, finish, principal_id="b")
    assert restarted.rng.exhausted()
    after = restarted._load(await restarted.store.read(cid))
    task = tasks(after.resources)[0]
    assert task.check and task.check.effective_target == 10 and task.check.dice == dice
    assert task.restored_hp == restored and task.status == "completed"
    target = next(i for i in after.resources.items if i.id == "repair-target")
    assert target.condition and target.condition.hp == restored
    assert target.quantity == 1 and target.owner_id == "b"
    assert tuple(i for i in after.resources.items if i.id != "repair-target") == tuple(
        i for i in worked.resources.items if i.id != "repair-target"
    )
    restarted = PlayService(AsyncSQLiteStore(tmp_path / "melee.sqlite"), play.engine)
    restarted.rng = RecordedDice(())
    service = CombatService(restarted)
    assert await service.execute(cid, finish, principal_id="b") == result
    with pytest.raises(ConflictError, match="settled"):
        await service.execute(
            cid,
            finish.model_copy(update={"id": "reroll", "expected_revision": after.revision}),
            principal_id="b",
        )
    assert restarted._load(await restarted.store.read(cid)) == after
    assert await restarted.store.read(cid) == await restarted.store.replay(cid)


@pytest.mark.parametrize("kind", ["melee", "armor"])
@pytest.mark.parametrize(
    "price,target,restored",
    [
        (1000, 12, 3),
        (1001, 11, 2),
        (10000, 11, 2),
        (10001, 10, 1),
        (100000, 10, 1),
        (100001, 9, 1),
        (1000000, 9, 1),
        (1000001, 8, 0),
    ],
)
async def test_price_boundaries_change_actual_restoration(
    tmp_path: Path, kind: Kind, price: int, target: int, restored: int
) -> None:
    cid, play = await workshop(tmp_path, kind, hp=6, price=price)
    await CombatService(play).execute(cid, begin(), principal_id="b")
    pending = play._load(await play.store.read(cid))
    await play.execute(
        cid,
        Wait(id="work", actor_id="b", expected_revision=pending.revision, ticks=1800),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((3, 3, 3))
    await CombatService(play).execute(
        cid,
        RepairEquipment(
            id="finish",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            item_id="repair-target",
            stage="finish",
            task_id="repair",
        ),
        principal_id="b",
    )
    after = play._load(await play.store.read(cid))
    task = tasks(after.resources)[0]
    assert task.check and task.check.effective_target == target
    assert task.restored_hp == restored
    item = next(i for i in after.resources.items if i.id == "repair-target")
    assert item.condition and item.condition.hp == 6 + restored


@pytest.mark.parametrize("kind", ["melee", "armor"])
@pytest.mark.parametrize("problem", ["no-tools", "parts-shortage", "destroyed", "owner", "full-hp"])
async def test_ineligible_repairs_preserve_durability_inventory_and_rng(
    tmp_path: Path, kind: Kind, problem: str
) -> None:
    cid, play = await workshop(tmp_path, kind)

    def configure(campaign: Campaign) -> CommandReceipt:
        state = play._load(campaign)
        items = tuple(
            i.model_copy(update={"quantity": 29})
            if problem == "parts-shortage" and i.id == "parts-b"
            else i.model_copy(update={"owner_id": "a"})
            if problem == "owner" and i.id == "repair-target"
            else i.model_copy(
                update={"condition": ObjectCondition(hp=-12, destroyed=True, disabled=True)}
            )
            if problem == "destroyed" and i.id == "repair-target"
            else i.model_copy(update={"condition": ObjectCondition(hp=12)})
            if problem == "full-hp" and i.id == "repair-target"
            else i
            for i in state.resources.items
            if not (problem == "no-tools" and i.id == "tool-b")
        )
        state = state.model_copy(
            update={"resources": state.resources.model_copy(update={"items": items})}
        )
        campaign["play_json"] = state.model_dump_json()
        return CommandReceipt(action="combat", outcome="ineligible-workshop")

    current = await play.store.read(cid)
    await play.store.commit_turn(cid, "configure", current["revision"], "configure", configure)
    before = play._load(await play.store.read(cid))
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError):
        await CombatService(play).execute(cid, begin(before.revision), principal_id="b")
    assert play._load(await play.store.read(cid)) == before
    assert play.rng.exhausted()
    assert not tasks(before.resources)


@pytest.mark.parametrize("kind", ["melee", "armor"])
@pytest.mark.parametrize("equipment_tl", [6, "superscience"])
async def test_ineligible_technology_rejects_before_parts_roll(
    tmp_path: Path, kind: Kind, equipment_tl: TechnologyLevel
) -> None:
    cid, play = await workshop(tmp_path, kind, technology_level=equipment_tl)
    before = play._load(await play.store.read(cid))
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="four TLs|concrete equipment TL"):
        await CombatService(play).execute(cid, begin(), principal_id="b")
    assert play._load(await play.store.read(cid)) == before
    assert play.rng.exhausted()


@pytest.mark.parametrize("kind", ["shield", "thrown"])
async def test_b178_melee_specialty_repairs_all_covered_classes(tmp_path: Path, kind: Kind) -> None:
    cid, play = await workshop(tmp_path, kind, hp=6)
    await CombatService(play).execute(cid, begin(), principal_id="b")
    pending = play._load(await play.store.read(cid))
    assert tasks(pending.resources)[0].effect == "restore-melee-weapon"
    await play.execute(
        cid,
        Wait(id="work", actor_id="b", expected_revision=pending.revision, ticks=1800),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((3, 3, 3))
    await CombatService(play).execute(
        cid,
        RepairEquipment(
            id="finish",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            item_id="repair-target",
            stage="finish",
            task_id="repair",
        ),
        principal_id="b",
    )
    after = play._load(await play.store.read(cid))
    item = next(i for i in after.resources.items if i.id == "repair-target")
    assert item.condition and item.condition.hp == 9
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("kind", ["melee", "armor"])
async def test_b345_impossible_effective_skill_cannot_spend_parts(
    tmp_path: Path, kind: Kind
) -> None:
    """IQ/A 11, TL+2 -10, cheap item +1, major repair -2 gives 0, below minimum 3."""
    cid, play = await workshop(tmp_path, kind, technology_level=4)
    before = play._load(await play.store.read(cid))
    play.rng = RecordedDice((1,))
    with pytest.raises(ValidationError, match="effective skill.*3"):
        await CombatService(play).execute(cid, begin(), principal_id="b")
    assert play._load(await play.store.read(cid)) == before
    assert play.rng.randbelow(6) == 0


@pytest.mark.parametrize("kind", ["melee", "armor"])
@pytest.mark.parametrize("price,quantity", [(Fraction(1, 100), 1), (Fraction(20001, 200), 2)])
async def test_fractional_prices_never_underpay_for_major_repair_parts(
    tmp_path: Path, kind: Kind, price: Fraction, quantity: int
) -> None:
    """B485: the purchased whole parts must cover the full rolled percentage value."""
    cid, play = await workshop(tmp_path, kind, price=price)
    play.rng = RecordedDice((1,))
    await CombatService(play).execute(cid, begin(), principal_id="b")
    after = play._load(await play.store.read(cid))
    task = tasks(after.resources)[0]
    assert task.parts_quantity == quantity
    assert next(i.quantity for i in after.resources.items if i.id == "parts-b") == 30 - quantity
    assert task.parts_quantity * 10 >= price / 10


@pytest.mark.parametrize("kind", ["melee", "armor"])
@pytest.mark.parametrize("modifier,restored", [(-5, 0), (-3, 1), (-1, 2), (0, 3), (1, 4), (2, 5)])
async def test_b345_pinned_tool_features_change_actual_repairs(
    tmp_path: Path, kind: Kind, modifier: int, restored: int
) -> None:
    skill = "skill:armoury-body-armor" if kind == "armor" else "skill:armoury-melee-weapons"
    cid, play = await workshop(
        tmp_path,
        kind,
        hp=6,
        tool_features=(
            GeneralEquipmentFeature(kind="tool", skill_id=skill, modifier=modifier),
            GeneralEquipmentFeature(kind="tool", skill_id="skill:carpentry", modifier=5),
        ),
    )
    play.rng = RecordedDice(())
    await CombatService(play).execute(cid, begin(), principal_id="b")
    pending = play._load(await play.store.read(cid))
    assert tasks(pending.resources)[0].skill == 12 + modifier
    await play.execute(
        cid,
        Wait(id="work", actor_id="b", expected_revision=pending.revision, ticks=1800),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((3, 3, 3))
    await CombatService(play).execute(
        cid,
        RepairEquipment(
            id="finish",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            item_id="repair-target",
            stage="finish",
            task_id="repair",
        ),
        principal_id="b",
    )
    after = play._load(await play.store.read(cid))
    task = tasks(after.resources)[0]
    assert task.restored_hp == restored
    item = next(i for i in after.resources.items if i.id == "repair-target")
    assert item.condition and item.condition.hp == 6 + restored
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("kind", ["melee", "armor"])
@pytest.mark.parametrize("problem", ["ambiguous", "consumable", "duration"])
async def test_tool_variants_without_a_bound_consumer_reject_before_parts(
    tmp_path: Path, kind: Kind, problem: str
) -> None:
    skill = "skill:armoury-body-armor" if kind == "armor" else "skill:armoury-melee-weapons"
    feature = GeneralEquipmentFeature(
        kind="tool",
        skill_id=skill,
        consumable_definition_id="equipment:spare-parts" if problem == "consumable" else None,
        consumable_units=1 if problem == "consumable" else 0,
        duration_seconds=60 if problem == "duration" else None,
    )
    cid, play = await workshop(
        tmp_path,
        kind,
        tool_features=(feature, feature) if problem == "ambiguous" else (feature,),
    )
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="toolkit"):
        await CombatService(play).execute(cid, begin(), principal_id="b")
    assert play._load(await play.store.read(cid)) == state
    assert play.rng.exhausted()
