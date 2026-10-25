"""#977: a selected catalog-backed alternative kit reaches actual repair HP."""

import secrets
from pathlib import Path

import pytest
from support.armoury_defaults import Kind, declare, fixture, revision, select, wait
from support.runtime import build_play, played
from test_issue_818_armoury_acceptance import begin

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.general_equipment import GeneralEquipmentFeature
from wayfarer.engine.simulation.equipment.basic.catalog import BASIC_EQUIPMENT
from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.engine.simulation.equipment.tool_context import DeclareRepairTools, SelectRepairTools
from wayfarer.engine.simulation.resources import Transfer
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.armoury import ArmouryService
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands


def alternative(skill: str | None, **changes: object) -> EquipmentProfile:
    source = next(
        e.provenance
        for e in BASIC_EQUIPMENT.entries
        if e.definition_id == "equipment:flintlock-pistol-51"
    )
    features = (
        ()
        if skill is None
        else (GeneralEquipmentFeature.model_validate(dict(kind="tool", skill_id=skill) | changes),)
    )
    return EquipmentProfile(
        definition_id="equipment:alternative-repair-kit",
        provenance=source,
        weight_millipounds=1000,
        price=20,
        technology_level=4,
        general=features,
    )


async def choose_alternative(play: PlayService, cid: str) -> SelectRepairTools:
    service = ArmouryService(play)
    await service.execute(
        cid,
        DeclareRepairTools(
            id="alternative-facts",
            actor_id="gm",
            performer_id="b",
            tool_id="alternative-tool-0",
            quality="fine",
            expected_revision=await revision(play, cid),
        ),
        principal_id="gm",
    )
    choice = SelectRepairTools(
        id="alternative-choice",
        actor_id="b",
        item_id="repair-target",
        tool_id="alternative-tool-0",
        start_command_id="repair",
        expected_revision=await revision(play, cid),
    )
    await service.execute(cid, choice, principal_id="b")
    return choice


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "kind,source,skill,expected,parts",
    [
        ("armor", "skill:armoury-melee-weapons", "skill:armoury-body-armor", 8, 5),
        ("firearm", "skill:engineer-small-arms", "skill:armoury-small-arms", 11, 2),
    ],
)
async def test_selected_alternative_kit_repairs_actual_equipment(
    tmp_path: Path, backend: str, kind: Kind, source: str, skill: str, expected: int, parts: int
) -> None:
    cid, play = await fixture(
        tmp_path, backend, kind, source, alternative_tools=(alternative(skill),)
    )
    await declare(play, cid)
    choice = await choose_alternative(play, cid)
    await select(play, cid, source)
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await ArmouryService(restarted).execute(cid, choice, principal_id="b")
    play.rng = RecordedDice((1,))
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    state = play._load(await play.store.read(cid))
    task = tasks(state.resources)[0]
    assert task.tool_id == "alternative-tool-0" and task.skill == expected
    assert task.parts_quantity == parts and task.due - task.start == 1800
    assert next(i.quantity for i in state.resources.items if i.id == "parts-b") == 30 - parts
    with pytest.raises(ConflictError, match="pending repair"):
        play.engine.resources.apply(
            state.resources,
            Transfer(
                id="reserved-alternative",
                actor_id="b",
                item_id="alternative-tool-0",
                expected_revision=state.resources.revision,
                quantity=1,
                owner_id="a",
            ),
        )
    await wait(play, cid, 1800)
    command = begin(await revision(play, cid)).model_copy(
        update={"id": "finish", "stage": "finish", "task_id": "repair"}
    )
    play.rng = RecordedDice((2, 2, 2))
    await CombatService(play).execute(cid, command, principal_id="b")
    state = play._load(await play.store.read(cid))
    done = tasks(state.resources)[0]
    assert done.check and done.check.effective_target == expected
    assert done.restored_hp == expected - 6
    item = next(i for i in state.resources.items if i.id == "repair-target")
    assert item.condition and item.condition.hp == expected - 6 and not item.condition.disabled
    assert await CombatService(restarted).execute(cid, command, principal_id="b")
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize(
    "skill", [None, "skill:carpentry", "skill:armoury", "skill:armoury-melee-weapons"]
)
async def test_unrelated_or_generic_tool_does_not_qualify_as_an_alternative(
    tmp_path: Path, skill: str | None
) -> None:
    cid, play = await fixture(
        tmp_path, "sqlite", "armor", "attribute:iq", alternative_tools=(alternative(skill),)
    )
    await ArmouryService(play).execute(
        cid,
        DeclareRepairTools(
            id="facts",
            actor_id="gm",
            performer_id="b",
            tool_id="alternative-tool-0",
            quality="fine",
            expected_revision=await revision(play, cid),
        ),
        principal_id="gm",
    )
    before = await play.store.read(cid)
    recorded = RecordedDice(())
    play.rng = recorded
    with pytest.raises(ValidationError, match="pinned item binding"):
        await ArmouryService(play).execute(
            cid,
            SelectRepairTools(
                id="choice",
                actor_id="b",
                item_id="repair-target",
                tool_id="alternative-tool-0",
                start_command_id="repair",
                expected_revision=await revision(play, cid),
            ),
            principal_id="b",
        )
    assert await play.store.read(cid) == before and recorded.exhausted()


async def test_unselected_alternative_does_not_change_historical_tool_lookup(
    tmp_path: Path,
) -> None:
    cid, play = await fixture(
        tmp_path,
        "sqlite",
        "armor",
        "skill:armoury-melee-weapons",
        alternative_tools=(alternative("skill:armoury-body-armor"),),
    )
    await declare(play, cid)
    await ArmouryService(play).execute(
        cid,
        DeclareRepairTools(
            id="unused-alternative",
            actor_id="gm",
            performer_id="b",
            tool_id="alternative-tool-0",
            quality="fine",
            expected_revision=await revision(play, cid),
        ),
        principal_id="gm",
    )
    await select(play, cid, "skill:armoury-melee-weapons")
    play.rng = RecordedDice((1,))
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    task = tasks(play._load(await play.store.read(cid)).resources)[0]
    assert task.tool_id == "tool-b" and task.skill == 6


@pytest.mark.parametrize("problem", ["modifier", "operating", "fuel"])
async def test_operating_requirements_are_not_silently_treated_as_free_tools(
    tmp_path: Path, problem: str
) -> None:
    facts: dict[str, object] = (
        {"modifier": 1}
        if problem == "modifier"
        else {"duration_seconds": 30}
        if problem == "operating"
        else {"consumable_definition_id": "equipment:spare-parts", "consumable_units": 1}
    )
    cid, play = await fixture(
        tmp_path,
        "sqlite",
        "armor",
        "attribute:iq",
        alternative_tools=(alternative("skill:armoury-body-armor", **facts),),
    )
    before = await play.store.read(cid)
    recorded = RecordedDice(())
    play.rng = recorded
    with pytest.raises(ValidationError, match="without operating limits"):
        await choose_alternative(play, cid)
    assert await play.store.read(cid) == before and recorded.exhausted()


@pytest.mark.parametrize("stage", ["start", "finish"])
async def test_selected_alternative_is_revalidated_before_parts_or_dice(
    tmp_path: Path, stage: str
) -> None:
    cid, play = await fixture(
        tmp_path,
        "sqlite",
        "armor",
        "skill:armoury-melee-weapons",
        alternative_tools=(alternative("skill:armoury-body-armor"),),
    )
    await declare(play, cid)
    await choose_alternative(play, cid)
    await select(play, cid, "skill:armoury-melee-weapons")
    if stage == "finish":
        play.rng = RecordedDice((1,))
        await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
        await wait(play, cid, 1800)
    await ArmouryService(play).execute(
        cid,
        DeclareRepairTools(
            id="changed-facts",
            actor_id="gm",
            performer_id="b",
            tool_id="alternative-tool-0",
            quality="basic",
            expected_revision=await revision(play, cid),
        ),
        principal_id="gm",
    )
    command = begin(await revision(play, cid))
    if stage == "finish":
        command = command.model_copy(
            update={"id": "finish", "stage": "finish", "task_id": "repair"}
        )
    before = await play.store.read(cid)
    recorded = RecordedDice(())
    play.rng = recorded
    with pytest.raises(ConflictError, match="observation"):
        await CombatService(play).execute(cid, command, principal_id="b")
    assert await play.store.read(cid) == before and recorded.exhausted()


async def test_alternative_tool_selection_reexecutes_original_seeded_commands(
    tmp_path: Path,
) -> None:
    cid, play = await fixture(
        tmp_path,
        "sqlite",
        "armor",
        "skill:armoury-melee-weapons",
        alternative_tools=(alternative("skill:armoury-body-armor"),),
    )
    initial = await play.store.read(cid)
    play.rng = secrets
    play.seeds = lambda: f"{1:064x}"
    await declare(play, cid)
    await choose_alternative(play, cid)
    await select(play, cid, "skill:armoury-melee-weapons")
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    await wait(play, cid, 1800)
    command = begin(await revision(play, cid)).model_copy(
        update={"id": "finish", "stage": "finish", "task_id": "repair"}
    )
    await CombatService(play).execute(cid, command, principal_id="b")
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == await play.store.read(cid)
