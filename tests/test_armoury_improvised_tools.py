"""#977: B345 technological improvisation reaches actual B484 repair checks."""

import secrets
from pathlib import Path

import pytest
from pydantic import ValidationError as SchemaError
from support.armoury_defaults import declare, fixture, revision, select, wait
from support.runtime import build_play, played
from test_armoury_alternative_tools import alternative
from test_issue_818_armoury_acceptance import begin

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.engine.simulation.equipment.tool_context import (
    Damage,
    DeclareRepairTools,
    RepairToolObservation,
    SelectRepairTools,
)
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.armoury import ArmouryService
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands


async def workshop(tmp_path: Path, backend: str = "sqlite") -> tuple[str, PlayService]:
    # IQ 10, twenty points in IQ/A Armoury = 15. B484's $500 major-repair
    # adjustment is +1-2, giving 14 before the observed equipment penalty.
    return await fixture(
        tmp_path,
        backend,
        "armor",
        "attribute:iq",
        additional_purchases=(
            Purchase(definition_id="skill:armoury-body-armor", amount=20, technology_level=4),
        ),
        alternative_tools=(alternative("skill:armoury-body-armor"),),
    )


async def observe(
    play: PlayService, cid: str, missing: int = 0, damage: Damage = "none"
) -> RepairToolObservation:
    return await ArmouryService(play).execute(
        cid,
        DeclareRepairTools(
            id="improvised-facts",
            actor_id="gm",
            performer_id="b",
            tool_id="alternative-tool-0",
            quality="improvised",
            missing_important_components=missing,
            damage=damage,
            expected_revision=await revision(play, cid),
        ),
        principal_id="gm",
    )


async def choose(play: PlayService, cid: str) -> SelectRepairTools:
    command = SelectRepairTools(
        id="improvised-choice",
        actor_id="b",
        item_id="repair-target",
        tool_id="alternative-tool-0",
        start_command_id="repair",
        expected_revision=await revision(play, cid),
    )
    await ArmouryService(play).execute(cid, command, principal_id="b")
    return command


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "missing,damage,modifier,target,restored",
    [(0, "none", -5, 9, 2), (1, "minor", -5, 9, 2), (2, "severe", -5, 9, 2)],
)
async def test_improvised_physical_tools_change_actual_margin_and_hp(
    tmp_path: Path,
    backend: str,
    missing: int,
    damage: Damage,
    modifier: int,
    target: int,
    restored: int,
) -> None:
    # B345 limits extra missing-item/damage penalties to basic-or-better kits.
    # Improvised Armoury equipment remains -5 even with recorded deficiencies.
    cid, play = await workshop(tmp_path, backend)
    facts = await observe(play, cid, missing, damage)
    assert facts.modifier == modifier
    await choose(play, cid)
    play.rng = RecordedDice((1,))
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    state = play._load(await play.store.read(cid))
    task = tasks(state.resources)[0]
    assert task.tool_id == "alternative-tool-0" and task.skill == target
    assert task.parts_quantity == 5 and task.due - task.start == 1800
    assert next(i.quantity for i in state.resources.items if i.id == "parts-b") == 25
    await wait(play, cid, 1800)
    command = begin(await revision(play, cid)).model_copy(
        update={"id": "finish", "stage": "finish", "task_id": "repair"}
    )
    play.rng = RecordedDice((3, 2, 2))
    result = await CombatService(play).execute(cid, command, principal_id="b")
    state = play._load(await play.store.read(cid))
    done = tasks(state.resources)[0]
    assert done.check and done.check.effective_target == target and done.check.total == 7
    assert done.restored_hp == restored
    item = next(i for i in state.resources.items if i.id == "repair-target")
    assert item.condition and item.condition.hp == restored
    assert item.condition.disabled == (restored == 0)
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await CombatService(restarted).execute(cid, command, principal_id="b") == result
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_improvised_observation_retains_gm_authority_and_exact_retry(tmp_path: Path) -> None:
    cid, play = await workshop(tmp_path)
    command = DeclareRepairTools(
        id="improvised-facts",
        actor_id="gm",
        performer_id="b",
        tool_id="alternative-tool-0",
        quality="improvised",
        expected_revision=await revision(play, cid),
    )
    before = await play.store.read(cid)
    for principal in ("a", "b"):
        with pytest.raises((AuthorizationError, ValidationError)):
            await ArmouryService(play).execute(cid, command, principal_id=principal)
    with pytest.raises(ConflictError):
        await ArmouryService(play).execute(
            cid, command.model_copy(update={"expected_revision": 0}), principal_id="gm"
        )
    assert await play.store.read(cid) == before
    result = await ArmouryService(play).execute(cid, command, principal_id="gm")
    assert result.modifier == -5
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await ArmouryService(restarted).execute(cid, command, principal_id="gm") == result
    with pytest.raises(ConflictError):
        await ArmouryService(play).execute(
            cid, command.model_copy(update={"quality": "basic"}), principal_id="gm"
        )
    with pytest.raises(SchemaError, match="exact source-derived"):
        RepairToolObservation.model_validate(result.model_dump() | {"modifier": -2})


@pytest.mark.parametrize("stage", ["start", "finish"])
async def test_improvised_choice_still_requires_its_current_physical_observation(
    tmp_path: Path, stage: str
) -> None:
    cid, play = await workshop(tmp_path)
    await observe(play, cid)
    await choose(play, cid)
    if stage == "finish":
        play.rng = RecordedDice((1,))
        await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
        await wait(play, cid, 1800)
    await ArmouryService(play).execute(
        cid,
        DeclareRepairTools(
            id="changed-improvisation",
            actor_id="gm",
            performer_id="b",
            tool_id="alternative-tool-0",
            quality="improvised",
            missing_important_components=1,
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


async def test_improvised_minimum_skill_refuses_before_parts_or_dice(tmp_path: Path) -> None:
    cid, play = await fixture(
        tmp_path,
        "sqlite",
        "armor",
        "skill:armoury-melee-weapons",
        alternative_tools=(alternative("skill:armoury-body-armor"),),
    )
    await declare(play, cid)
    await observe(play, cid)
    await choose(play, cid)
    before = await play.store.read(cid)
    recorded = RecordedDice(())
    play.rng = recorded
    # Cross-specialty default 7, major-repair +1-2, improvised -5 => 1.
    with pytest.raises(ValidationError, match="at least 3"):
        await select(play, cid, "skill:armoury-melee-weapons")
    assert await play.store.read(cid) == before and recorded.exhausted()


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_improvised_selection_and_repair_reexecute_from_original_genesis(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await workshop(tmp_path, backend)
    initial = await play.store.read(cid)
    play.rng = secrets
    play.seeds = lambda: f"{1:064x}"
    await observe(play, cid)
    await choose(play, cid)
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    await wait(play, cid, 1800)
    await CombatService(play).execute(
        cid,
        begin(await revision(play, cid)).model_copy(
            update={"id": "finish", "stage": "finish", "task_id": "repair"}
        ),
        principal_id="b",
    )
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == await play.store.read(cid)
