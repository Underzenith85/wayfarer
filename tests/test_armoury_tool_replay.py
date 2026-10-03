"""Tool observations and choices reexecute from original campaign genesis."""

import secrets
from pathlib import Path

import pytest
from support.armoury_defaults import Kind, declare, fixture, revision, select, wait
from support.runtime import played
from test_armoury_tools import choose, observe
from test_issue_818_armoury_acceptance import begin

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.simulation.equipment.repair_parts import AssessRepairParts
from wayfarer.engine.simulation.equipment.repair_time import SelectRepairTime
from wayfarer.orchestration.armoury import ArmouryService
from wayfarer.orchestration.combat import CombatService
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "kind,source", [("armor", "attribute:iq"), ("firearm", "skill:engineer-small-arms")]
)
async def test_full_tool_quality_default_time_parts_seeded_reexecution(
    tmp_path: Path, backend: str, kind: Kind, source: str
) -> None:
    cid, play = await fixture(tmp_path, backend, kind, source)
    initial = await play.store.read(cid)
    play.rng = secrets
    play.seeds = lambda: f"{1:064x}"
    await declare(play, cid)
    await observe(play, cid, "fine", 1, "minor")
    await choose(play, cid)
    await select(play, cid, source)
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
    await ArmouryService(play).execute(
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
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    await wait(play, cid, 3600)
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
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == await play.store.read(cid)
