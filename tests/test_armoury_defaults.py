"""B168/B173/B178 defaults resolve into actual B484 durability transactions."""

from pathlib import Path

import pytest
from support.armoury_defaults import TARGETS, Kind, declare, fixture, revision, select, wait
from support.runtime import build_play
from test_issue_818_armoury_acceptance import begin

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.equipment.repair_defaults import (
    DeclareArmouryTraining,
    SelectRepairDefault,
)
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.errors import AuthorizationError, ValidationError
from wayfarer.orchestration.armoury import ArmouryService
from wayfarer.orchestration.combat import CombatService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "kind,source,skill,spent",
    [
        ("armor", "attribute:iq", 4, 5),
        ("armor", "skill:armoury-melee-weapons", 6, 5),
        ("firearm", "skill:engineer-small-arms", 9, 2),
    ],
)
@pytest.mark.parametrize("success", [True, False])
async def test_actual_defaulted_major_repair(
    tmp_path: Path, backend: str, kind: Kind, source: str, skill: int, spent: int, success: bool
) -> None:
    cid, play = await fixture(tmp_path, backend, kind, source)
    await declare(play, cid)
    plan = await select(play, cid, source)
    assert plan.default_modifier == (-5 if source == "attribute:iq" else -4)
    assert plan.tl_penalty == 0 and plan.training_tl == plan.equipment_tl == 4
    command = begin(await revision(play, cid))
    play.rng = RecordedDice((1,))
    receipt = await CombatService(play).execute(cid, command, principal_id="b")
    pending = await play.store.read(cid)
    task = tasks(play._load(pending).resources)[0]
    assert task.skill == skill and task.parts_quantity == spent and task.parts_die == 1
    assert task.due - task.start == 1800
    assert task.effect == ("restore-small-arm" if kind == "firearm" else "restore-body-armor")
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await CombatService(restarted).execute(cid, command, principal_id="b") == receipt
    assert await restarted.store.read(cid) == pending
    await wait(restarted, cid, 1800)
    restarted.rng = RecordedDice((1, 1, 1) if success else (6, 6, 6))
    await CombatService(restarted).execute(
        cid,
        command.model_copy(
            update={
                "id": "finish",
                "stage": "finish",
                "task_id": "repair",
                "expected_revision": await revision(restarted, cid),
            }
        ),
        principal_id="b",
    )
    state = restarted._load(await restarted.store.read(cid))
    task = tasks(state.resources)[0]
    restored = skill - 3 if success else 0
    item = next(i for i in state.resources.items if i.id == "repair-target")
    assert (
        item.condition
        and item.condition.hp == restored
        and item.condition.disabled is (not success)
    )
    assert task.restored_hp == restored and task.status == "completed"
    assert next(i for i in state.resources.items if i.id == "parts-b").quantity == 30 - spent
    assert await restarted.store.read(cid) == await restarted.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "source,tl,message",
    [
        ("skill:engineer-small-arms", 4, "wrong specialty"),
        ("skill:engineer-body-armor", 4, "exact source"),
        ("skill:armoury-melee-weapons", 5, "at least 3"),
        ("attribute:iq", 8, "impossible"),
        ("attribute:iq", 7, "at least 3"),
    ],
)
async def test_invalid_source_or_tl_refuses_before_rng_and_material(
    tmp_path: Path, backend: str, source: str, tl: int, message: str
) -> None:
    purchased = "attribute:iq" if source == "skill:engineer-body-armor" else source
    cid, play = await fixture(tmp_path, backend, "armor", purchased, tl=tl)
    await declare(play, cid)
    saved = await play.store.read(cid)
    with pytest.raises(ValidationError, match=message):
        await select(play, cid, source)
    assert (
        await play.store.read(cid) == saved
        and isinstance(play.rng, RecordedDice)
        and play.rng.exhausted()
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_default_only_source_and_player_training_refuse(tmp_path: Path, backend: str) -> None:
    cid, play = await fixture(tmp_path, backend, "armor", "attribute:iq")
    saved = await play.store.read(cid)
    state = play._load(saved)
    with pytest.raises((AuthorizationError, ValidationError)):
        await ArmouryService(play).execute(
            cid,
            DeclareArmouryTraining(
                id="forged",
                actor_id="b",
                performer_id="b",
                build_revision=build(play.rules_context, state, "b").revision,
                personal_technology_level=4,
                society_known_skills=TARGETS,
                expected_revision=state.revision,
            ),
            principal_id="b",
        )
    assert await play.store.read(cid) == saved
    await declare(play, cid)
    saved = await play.store.read(cid)
    with pytest.raises(ValidationError, match="untrained"):
        await select(play, cid, "skill:armoury-melee-weapons")
    assert await play.store.read(cid) == saved


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "kind,source,quantity,skill",
    [("armor", "attribute:iq", 5, 5), ("firearm", "skill:engineer-small-arms", 2, 10)],
)
async def test_default_linked_assessment_and_selected_time_are_real_consumers(
    tmp_path: Path, backend: str, kind: Kind, source: str, quantity: int, skill: int
) -> None:
    from wayfarer.engine.simulation.equipment.repair_parts import AssessRepairParts
    from wayfarer.engine.simulation.equipment.repair_time import SelectRepairTime

    cid, play = await fixture(tmp_path, backend, kind, source)
    await declare(play, cid)
    await select(play, cid, source)
    play.rng = RecordedDice((1,))
    command = AssessRepairParts(
        id="assess",
        actor_id="b",
        item_id="repair-target",
        repair_start_command_id="repair",
        expected_revision=await revision(play, cid),
    )
    assessment = await ArmouryService(play).execute(cid, command, principal_id="b")
    assert assessment.quantity == quantity and assessment.die == 1 and play.rng.exhausted()
    saved = await play.store.read(cid)
    assert await ArmouryService(play).execute(cid, command, principal_id="b") == assessment
    assert await play.store.read(cid) == saved
    play.rng = RecordedDice(())
    method = await ArmouryService(play).execute(
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
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    task = tasks(play._load(await play.store.read(cid)).resources)[0]
    assert task.skill == skill and task.parts_quantity == quantity and task.parts_die == 1
    assert task.time_plan == method and task.due - task.start == 3600 and play.rng.exhausted()
    await wait(play, cid, 3600)
    play.rng = RecordedDice((1, 1, 1))
    await CombatService(play).execute(
        cid,
        begin(await revision(play, cid)).model_copy(
            update={"id": "finish", "stage": "finish", "task_id": "repair"}
        ),
        principal_id="b",
    )
    final = play._load(await play.store.read(cid))
    task = tasks(final.resources)[0]
    maximum = 12 if kind == "armor" else 6
    assert task.restored_hp == min(maximum, skill - 3)
    assert next(i for i in final.resources.items if i.id == "parts-b").quantity == 30 - quantity
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_source_time_composition_rescues_and_refuses_without_free_parts(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend, "armor", "attribute:iq", tl=2)
    await declare(play, cid)
    saved = await play.store.read(cid)
    command = SelectRepairDefault(
        id="choice",
        actor_id="b",
        item_id="repair-target",
        start_command_id="repair",
        source_id="attribute:iq",
        repair_time_method="extra-2",
        expected_revision=await revision(play, cid),
    )
    with pytest.raises(ValidationError, match="at least 3"):
        await ArmouryService(play).execute(cid, command, principal_id="b")  # 5−3−1+1 = 2
    assert (
        saved == await play.store.read(cid)
        and isinstance(play.rng, RecordedDice)
        and play.rng.exhausted()
    )
    command = command.model_copy(update={"id": "rescued", "repair_time_method": "extra-4"})
    result = await ArmouryService(play).execute(cid, command, principal_id="b")  # 1+2 = 3
    saved = await play.store.read(cid)
    assert await ArmouryService(play).execute(cid, command, principal_id="b") == result
    assert saved == await play.store.read(cid)
    from wayfarer.engine.simulation.equipment.repair_parts import AssessRepairParts

    play.rng = RecordedDice((1,))
    await ArmouryService(play).execute(
        cid,
        AssessRepairParts(
            id="assess",
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
    assert task.skill == 3 and task.due - task.start == 7200 and task.parts_quantity == 5
    await wait(play, cid, 7200)
    play.rng = RecordedDice((1, 1, 1))
    await CombatService(play).execute(
        cid,
        begin(await revision(play, cid)).model_copy(
            update={"id": "finish", "stage": "finish", "task_id": "repair"}
        ),
        principal_id="b",
    )
    assert tasks(play._load(await play.store.read(cid)).resources)[0].restored_hp == 1


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_familiarity_separate_from_training_and_extra_time_rescue(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.simulation.equipment.armoury_context import DeclareArmouryFamiliarity
    from wayfarer.engine.simulation.equipment.repair_time import SelectRepairTime

    cid, play = await fixture(tmp_path, backend, "armor", "attribute:iq")
    await declare(play, cid)
    saved = await play.store.read(cid)
    familiarity = DeclareArmouryFamiliarity(
        id="unfamiliar",
        actor_id="gm",
        performer_id="b",
        item_id="repair-target",
        basis="unfamiliar-model",
        repair_start_command_id="repair",
        expected_revision=await revision(play, cid),
    )
    with pytest.raises(ValidationError, match="explicit selected"):
        await ArmouryService(play).execute(cid, familiarity, principal_id="gm")
    assert saved == await play.store.read(cid)
    plan = await select(play, cid, "attribute:iq")
    await ArmouryService(play).execute(
        cid,
        familiarity.model_copy(update={"expected_revision": await revision(play, cid)}),
        principal_id="gm",
    )
    saved = await play.store.read(cid)
    with pytest.raises(ValidationError, match="at least 3"):
        await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    assert saved == await play.store.read(cid)
    await ArmouryService(play).execute(
        cid,
        SelectRepairTime(
            id="slow",
            actor_id="b",
            item_id="repair-target",
            start_command_id="repair",
            method="extra-2",
            expected_revision=await revision(play, cid),
        ),
        principal_id="b",
    )
    play.rng = RecordedDice((1,))
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    task = tasks(play._load(await play.store.read(cid)).resources)[0]
    assert plan.skill_level == 5 and plan.tl_penalty == 0
    assert (
        task.skill == 3 and task.due - task.start == 3600
    )  # IQ−5 + price1 − major2 − unfamiliar2 + time1
    await wait(play, cid, 3600)
    play.rng = RecordedDice((1, 1, 1))
    await CombatService(play).execute(
        cid,
        begin(await revision(play, cid)).model_copy(
            update={"id": "finish", "stage": "finish", "task_id": "repair"}
        ),
        principal_id="b",
    )
    assert tasks(play._load(await play.store.read(cid)).resources)[0].restored_hp == 1
