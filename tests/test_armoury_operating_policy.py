"""Fresh operating-tool refusal is producer-bound; old raw free-tool history stays exact."""

import secrets
from copy import copy
from pathlib import Path
from typing import Literal

import pytest
from support.armoury_defaults import revision
from support.runtime import build_play, played, seed_campaign
from test_issue_818_armoury_acceptance import begin, workshop

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.electronics import ElectronicsSuite, SensorSpec
from wayfarer.engine.rules.types.general_equipment import GeneralEquipmentFeature
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.equipment.repair_parts import AssessRepairParts
from wayfarer.engine.simulation.equipment.repair_time import SelectRepairTime
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.engine.simulation.equipment.tool_context import DeclareRepairTools, SelectRepairTools
from wayfarer.errors import ValidationError
from wayfarer.orchestration import armoury_generations
from wayfarer.orchestration.armoury import ArmouryService
from wayfarer.orchestration.combat import CombatService, generations
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands


async def required_kit(
    path: Path, backend: str, skill: str, external: bool
) -> tuple[str, PlayService]:
    feature = GeneralEquipmentFeature(
        kind="tool",
        skill_id=skill,
        consumable_definition_id="equipment:spare-parts" if external else None,
        consumable_units=1 if external else 0,
        duration_seconds=None if external else 30,
    )
    cid, donor = await workshop(path / "donor", "armor", tool_features=(feature,))
    initial = await donor.store.read(cid)
    play = build_play(path / "actual", donor.engine, backend=backend, seeds=lambda: f"{11:064x}")
    await seed_campaign(play.store, initial)
    return cid, play


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("skill", ["skill:armoury", "skill:carpentry", "skill:armoury-body-armor"])
@pytest.mark.parametrize("external", [False, True])
async def test_fresh_required_operating_kit_never_uses_generic_or_exact_free_tool_path(
    tmp_path: Path, backend: str, skill: str, external: bool
) -> None:
    cid, play = await required_kit(tmp_path, backend, skill, external)
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="explicit supported selection"):
        await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    assert await play.store.read(cid) == before and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("external", [False, True])
async def test_absent_feature_raw_start_and_fresh_finish_preserve_legacy_seeded_history(
    tmp_path: Path, backend: str, external: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await required_kit(tmp_path, backend, "skill:armoury", external)
    initial = (await play.store.history(cid))[0].state_after
    active = generations.ACTIVE
    monkeypatch.setattr(generations, "ACTIVE", frozenset())
    play.rng = secrets
    command = begin(await revision(play, cid))
    await CombatService(play).execute(cid, command, principal_id="b")
    record = await play.store.command_input(cid, command.id)
    assert record and record.text and generations.KEY not in record.text
    # This accepted historical behavior is retained, not marked source compliant.
    state = play._load(await play.store.read(cid))
    task = tasks(state.resources)[0]
    assert task.skill == 10 and task.due - task.start == 1800
    assert (
        next(i.quantity for i in state.resources.items if i.id == "parts-b")
        == 30 - task.parts_quantity
    )
    monkeypatch.setattr(generations, "ACTIVE", active)
    await play.execute(
        cid,
        Wait(id="work", actor_id="b", ticks=1800, expected_revision=await revision(play, cid)),
        principal_id="b",
    )
    finish = command.model_copy(
        update={
            "id": "finish",
            "stage": "finish",
            "task_id": "repair",
            "expected_revision": await revision(play, cid),
        }
    )
    await CombatService(play).execute(cid, finish, principal_id="b")
    done = tasks(play._load(await play.store.read(cid)).resources)[0]
    assert done.check and done.check.effective_target == 10 and done.check.total == 5
    assert done.restored_hp == 5
    assert await CombatService(play).execute(cid, finish, principal_id="b")
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "legacy-seed-replay"),
    )
    assert checks and all(check.folded and check.reexecuted for check in checks)
    assert replayed == await play.store.read(cid) == await play.store.replay(cid)


async def unsupported_required_kit(
    path: Path, backend: str, form: Literal["light", "electronics", "cell"] = "light"
) -> tuple[str, PlayService]:
    cid, donor = await required_kit(path / "source", "sqlite", "skill:armoury", False)
    initial = await donor.store.read(cid)
    state = donor._load(initial)
    combat = donor.engine.rules.combat
    assert combat and combat.gurps_equipment
    equipment = combat.gurps_equipment
    original = next(
        entry for entry in equipment.entries if entry.definition_id == "equipment:armoury-tools"
    )
    if form == "light":
        changed = original.model_copy(
            update={"general": (GeneralEquipmentFeature(kind="light", duration_seconds=60),)}
        )
    else:
        changed = original.model_copy(
            update={
                "general": (
                    GeneralEquipmentFeature(kind="tool", skill_id="skill:armoury-melee-weapons"),
                ),
                **(
                    {
                        "electronics": ElectronicsSuite(
                            sensor=SensorSpec(form="hands-free", sense="vision"),
                            power_capacity_seconds=60,
                        )
                    }
                    if form == "electronics"
                    else {"ammunition": True, "power_cell_capacity": 60}
                ),
            }
        )
    resources = copy(donor.engine.resources)
    resources.specs = {**resources.specs, changed.definition_id: changed.inventory_spec()}
    catalog = equipment.model_copy(
        update={
            "entries": tuple(
                changed if entry.definition_id == changed.definition_id else entry
                for entry in equipment.entries
            )
        }
    )
    engine = ActionEngine(
        donor.engine.reviewer,
        resources,
        donor.engine.rules.model_copy(
            update={"combat": combat.model_copy(update={"gurps_equipment": catalog})}
        ),
    )
    play = build_play(path / "actual", engine, backend=backend, seeds=lambda: f"{11:064x}")
    inventory = state.resources.model_copy(
        update={
            "items": tuple(
                item.model_copy(update={"charges": 60}) if item.id == "tool-b" else item
                for item in state.resources.items
            )
        }
    )
    initial["play_json"] = state.model_copy(
        update={"configuration_digest": engine.digest, "resources": inventory}
    ).model_dump_json()
    await seed_campaign(play.store, initial)
    return cid, play


async def choose_light_kit(play: PlayService, cid: str) -> None:
    await ArmouryService(play).execute(
        cid,
        DeclareRepairTools(
            id="light-facts",
            actor_id="gm",
            performer_id="b",
            tool_id="tool-b",
            quality="fine",
            expected_revision=await revision(play, cid),
        ),
        principal_id="gm",
    )
    await ArmouryService(play).execute(
        cid,
        SelectRepairTools(
            id="light-choice",
            actor_id="b",
            item_id="repair-target",
            tool_id="tool-b",
            start_command_id="repair",
            expected_revision=await revision(play, cid),
        ),
        principal_id="b",
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("phase", ["start", "assessment", "time"])
@pytest.mark.parametrize("form", ["light", "electronics", "cell"])
async def test_selected_unsupported_operating_profile_cannot_grant_free_repair(
    tmp_path: Path, backend: str, phase: str, form: Literal["light", "electronics", "cell"]
) -> None:
    cid, play = await unsupported_required_kit(tmp_path, backend, form)
    await choose_light_kit(play, cid)
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="operating limits"):
        if phase == "start":
            await CombatService(play).execute(
                cid, begin(await revision(play, cid)), principal_id="b"
            )
        else:
            command = (
                AssessRepairParts(
                    id="assess-light",
                    actor_id="b",
                    item_id="repair-target",
                    repair_start_command_id="repair",
                    expected_revision=await revision(play, cid),
                )
                if phase == "assessment"
                else SelectRepairTime(
                    id="time-light",
                    actor_id="b",
                    item_id="repair-target",
                    start_command_id="repair",
                    method="ordinary",
                    expected_revision=await revision(play, cid),
                )
            )
            await ArmouryService(play).execute(cid, command, principal_id="b")
    assert await play.store.read(cid) == before and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_legacy_selected_non_tool_operating_history_is_retained_without_promotion(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await unsupported_required_kit(tmp_path, backend)
    initial = (await play.store.history(cid))[0].state_after
    active = generations.ACTIVE
    monkeypatch.setattr(armoury_generations, "ACTIVE", False)
    await choose_light_kit(play, cid)
    monkeypatch.setattr(generations, "ACTIVE", frozenset())
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    monkeypatch.setattr(armoury_generations, "ACTIVE", True)
    monkeypatch.setattr(generations, "ACTIVE", active)
    await play.execute(
        cid,
        Wait(id="work", actor_id="b", ticks=1800, expected_revision=await revision(play, cid)),
        principal_id="b",
    )
    await CombatService(play).execute(
        cid,
        begin(await revision(play, cid)).model_copy(
            update={"id": "finish", "stage": "finish", "task_id": "repair"}
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    assert tasks(state.resources)[0].restored_hp == 7
    assert next(item.charges for item in state.resources.items if item.id == "tool-b") == 60
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "legacy-non-tool-seed-replay"),
    )
    assert checks and all(check.folded and check.reexecuted for check in checks)
    assert replayed == await play.store.read(cid)
