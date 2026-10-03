"""B168/B173/B178 defaults resolve into actual B484 durability transactions."""

from pathlib import Path

import pytest
from support.armoury_defaults import Kind, declare, fixture, revision, select, wait
from test_issue_818_armoury_acceptance import begin

from wayfarer.orchestration.combat import CombatService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "kind,source", [("armor", "attribute:iq"), ("firearm", "skill:engineer-small-arms")]
)
@pytest.mark.parametrize("route", ["ordinary", "composite", "familiarity"])
async def test_whole_default_family_seeded_reexecution(
    tmp_path: Path, backend: str, kind: Kind, source: str, route: str
) -> None:
    import secrets

    from support.runtime import played

    from scripts.replay_fixtures import FixtureExecutor
    from wayfarer.persistence.replay import verify_commands

    cid, play = await fixture(tmp_path, backend, kind, source)
    initial = await play.store.read(cid)
    play.rng = secrets
    play.seeds = lambda: f"{1:064x}"
    await declare(play, cid)
    from wayfarer.engine.simulation.equipment.armoury_context import DeclareArmouryFamiliarity
    from wayfarer.engine.simulation.equipment.repair_defaults import SelectRepairDefault
    from wayfarer.engine.simulation.equipment.repair_parts import AssessRepairParts
    from wayfarer.engine.simulation.equipment.repair_time import SelectRepairTime
    from wayfarer.orchestration.armoury import ArmouryService

    if route == "ordinary":
        await select(play, cid, source)
    else:
        await ArmouryService(play).execute(
            cid,
            SelectRepairDefault(
                id="default",
                actor_id="b",
                item_id="repair-target",
                start_command_id="repair",
                source_id=source,
                repair_time_method="extra-2" if route == "composite" else None,
                expected_revision=await revision(play, cid),
            ),
            principal_id="b",
        )
        if route == "familiarity":
            await ArmouryService(play).execute(
                cid,
                DeclareArmouryFamiliarity(
                    id="model",
                    actor_id="gm",
                    performer_id="b",
                    item_id="repair-target",
                    basis="unfamiliar-model",
                    repair_start_command_id="repair",
                    expected_revision=await revision(play, cid),
                ),
                principal_id="gm",
            )
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
    await wait(play, cid, 1800 if route == "ordinary" else 3600)
    await CombatService(play).execute(
        cid,
        begin(await revision(play, cid)).model_copy(
            update={"id": "finish", "stage": "finish", "task_id": "repair"}
        ),
        principal_id="b",
    )
    committed = await play.store.read(cid)
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == committed
