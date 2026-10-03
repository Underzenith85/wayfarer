"""B345 physical toolkit facts reach actual repair margins and materials."""

from pathlib import Path

import pytest
from support.armoury_defaults import Kind, declare, fixture, revision, select, wait
from test_issue_818_armoury_acceptance import begin

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.equipment.repair_parts import AssessRepairParts
from wayfarer.engine.simulation.equipment.repair_time import SelectRepairTime
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.engine.simulation.equipment.tool_context import (
    Damage,
    DeclareRepairTools,
    Quality,
    SelectRepairTools,
)
from wayfarer.errors import ValidationError
from wayfarer.orchestration.armoury import ArmouryService
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.play import PlayService


async def observe(
    play: PlayService, cid: str, quality: Quality, missing: int = 0, damage: Damage = "none"
) -> None:
    await ArmouryService(play).execute(
        cid,
        DeclareRepairTools(
            id="tools-facts",
            actor_id="gm",
            performer_id="b",
            tool_id="tool-b",
            quality=quality,
            missing_important_components=missing,
            damage=damage,
            tool_technology_level=4 if quality == "best" else None,
            expected_revision=await revision(play, cid),
        ),
        principal_id="gm",
    )


async def choose(play: PlayService, cid: str) -> SelectRepairTools:
    command = SelectRepairTools(
        id="tools-choice",
        actor_id="b",
        item_id="repair-target",
        tool_id="tool-b",
        start_command_id="repair",
        expected_revision=await revision(play, cid),
    )
    await ArmouryService(play).execute(cid, command, principal_id="b")
    return command


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "kind,source",
    [("armor", "skill:armoury-melee-weapons"), ("firearm", "skill:engineer-small-arms")],
)
@pytest.mark.parametrize(
    "quality,missing,damage,modifier",
    [
        ("basic", 0, "none", 0),
        ("good", 0, "none", 1),
        ("fine", 0, "none", 2),
        ("fine", 1, "minor", 0),
        ("good", 2, "moderate", -3),
    ],
)
async def test_quality_missing_and_damage_change_actual_repair_margin(
    tmp_path: Path,
    backend: str,
    kind: Kind,
    source: str,
    quality: Quality,
    missing: int,
    damage: Damage,
    modifier: int,
) -> None:
    cid, play = await fixture(tmp_path, backend, kind, source)
    await declare(play, cid)
    await observe(play, cid, quality, missing, damage)
    await choose(play, cid)
    await select(play, cid, source)
    # The start still owns the historical single parts die when no assessment is selected.
    play.rng = RecordedDice((1,))
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    task = tasks(play._load(await play.store.read(cid)).resources)[0]
    base = 6 if kind == "armor" else 9
    assert task.skill == base + modifier
    assert task.parts_quantity == (5 if kind == "armor" else 2) and task.due - task.start == 1800
    assert play.rng.exhausted()
    await wait(play, cid, 1800)
    play.rng = RecordedDice((2, 2, 2))
    await CombatService(play).execute(
        cid,
        begin(await revision(play, cid)).model_copy(
            update={"id": "finish", "stage": "finish", "task_id": "repair"}
        ),
        principal_id="b",
    )
    task = tasks(play._load(await play.store.read(cid)).resources)[0]
    expected = (
        min(12 if kind == "armor" else 6, max(1, base + modifier - 6))
        if base + modifier >= 6
        else 0
    )
    assert (
        task.restored_hp == expected
        and task.check
        and task.check.effective_target == base + modifier
    )
    state = play._load(await play.store.read(cid))
    item = next(i for i in state.resources.items if i.id == "repair-target")
    assert (
        item.condition
        and item.condition.hp == expected
        and item.condition.disabled == (expected == 0)
    )
    assert (
        next(i.quantity for i in state.resources.items if i.id == "parts-b")
        == 30 - task.parts_quantity
    )
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_tool_bonus_composes_actual_iq_default_time_and_assessed_parts(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend, "armor", "attribute:iq")
    await declare(play, cid)
    await observe(play, cid, "good")
    await choose(play, cid)
    await select(play, cid, "attribute:iq")
    await ArmouryService(play).execute(
        cid,
        SelectRepairTime(
            id="time",
            actor_id="b",
            item_id="repair-target",
            start_command_id="repair",
            method="extra-2",
            expected_revision=await revision(play, cid),
        ),
        principal_id="b",
    )
    play.rng = RecordedDice((1,))
    parts = await ArmouryService(play).execute(
        cid,
        AssessRepairParts(
            id="parts",
            actor_id="b",
            item_id="repair-target",
            repair_start_command_id="repair",
            expected_revision=await revision(play, cid),
        ),
        principal_id="b",
    )
    assert play.rng.exhausted()
    play.rng = RecordedDice(())
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    task = tasks(play._load(await play.store.read(cid)).resources)[0]
    assert (
        task.skill == 6 and task.parts_quantity == parts.quantity and task.due - task.start == 3600
    )
    await wait(play, cid, 3600)
    play.rng = RecordedDice((1, 1, 1))
    await CombatService(play).execute(
        cid,
        begin(await revision(play, cid)).model_copy(
            update={"id": "finish", "stage": "finish", "task_id": "repair"}
        ),
        principal_id="b",
    )
    assert tasks(play._load(await play.store.read(cid)).resources)[0].restored_hp == 3


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_best_requires_explicit_physical_tl_and_matching_training(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend, "armor", "attribute:iq")
    await declare(play, cid)
    for tl in (None, 1, 7):
        saved = await play.store.read(cid)
        with pytest.raises(ValidationError):
            await ArmouryService(play).execute(
                cid,
                DeclareRepairTools(
                    id=f"bad-{tl}",
                    actor_id="gm",
                    performer_id="b",
                    tool_id="tool-b",
                    quality="best",
                    tool_technology_level=tl,
                    expected_revision=await revision(play, cid),
                ),
                principal_id="gm",
            )
        assert await play.store.read(cid) == saved
    await observe(play, cid, "best")
    await choose(play, cid)
    await select(play, cid, "attribute:iq")
    play.rng = RecordedDice((1,))
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    assert tasks(play._load(await play.store.read(cid)).resources)[0].skill == 6


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_missing_and_severe_damage_refuse_before_parts_rng(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend, "armor", "skill:armoury-melee-weapons")
    await declare(play, cid)
    await observe(play, cid, "basic", 1, "severe")
    await choose(play, cid)
    saved = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="at least 3"):
        await select(play, cid, "skill:armoury-melee-weapons")
    assert await play.store.read(cid) == saved and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_selected_quality_with_purchased_skill_uses_linked_assessment(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.character.compiler import Purchase

    cid, play = await fixture(
        tmp_path,
        backend,
        "armor",
        "attribute:iq",
        additional_purchases=(
            Purchase(definition_id="skill:armoury-body-armor", amount=4, technology_level=4),
        ),
    )
    await observe(play, cid, "good")
    await choose(play, cid)
    play.rng = RecordedDice((1,))
    parts = await ArmouryService(play).execute(
        cid,
        AssessRepairParts(
            id="parts",
            actor_id="b",
            item_id="repair-target",
            repair_start_command_id="repair",
            expected_revision=await revision(play, cid),
        ),
        principal_id="b",
    )
    play.rng = RecordedDice(())
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    task = tasks(play._load(await play.store.read(cid)).resources)[0]
    assert task.skill == 11 and task.parts_quantity == parts.quantity
    await wait(play, cid, 1800)
    play.rng = RecordedDice((2, 2, 2))
    await CombatService(play).execute(
        cid,
        begin(await revision(play, cid)).model_copy(
            update={"id": "finish", "stage": "finish", "task_id": "repair"}
        ),
        principal_id="b",
    )
    assert tasks(play._load(await play.store.read(cid)).resources)[0].restored_hp == 5


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("damaged", [False, True])
async def test_fine_quality_rescues_default_one_but_damage_refuses_two(
    tmp_path: Path, backend: str, damaged: bool
) -> None:
    cid, play = await fixture(tmp_path, backend, "armor", "attribute:iq", tl=5, hp=1)
    await declare(play, cid)
    await observe(play, cid, "fine", damage="minor" if damaged else "none")
    await choose(play, cid)
    saved = await play.store.read(cid)
    play.rng = RecordedDice(())
    if damaged:
        with pytest.raises(ValidationError, match="at least 3"):
            await select(play, cid, "attribute:iq")
        assert await play.store.read(cid) == saved and play.rng.exhausted()
        return
    await select(play, cid, "attribute:iq")
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    task = tasks(play._load(await play.store.read(cid)).resources)[0]
    assert task.skill == 3 and task.parts_quantity == 0 and task.technology_level_penalty == -5
    await wait(play, cid, 1800)
    play.rng = RecordedDice((1, 1, 1))
    await CombatService(play).execute(
        cid,
        begin(await revision(play, cid)).model_copy(
            update={"id": "finish", "stage": "finish", "task_id": "repair"}
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    assert tasks(state.resources)[0].restored_hp == 1
    item = next(i for i in state.resources.items if i.id == "repair-target")
    assert item.condition and item.condition.hp == 2
