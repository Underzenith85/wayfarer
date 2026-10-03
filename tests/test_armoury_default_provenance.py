"""B168/B173/B178 defaults resolve into actual B484 durability transactions."""

from dataclasses import replace
from pathlib import Path

import pytest
from support.armoury_defaults import declare, fixture, revision, select, wait
from support.runtime import build_play
from test_issue_818_armoury_acceptance import begin

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.orchestration.combat import CombatService, EndEncounter


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_genuinely_purchased_source_with_default_credit_is_not_default_only(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(
        tmp_path,
        backend,
        "firearm",
        "skill:engineer-small-arms",
        additional_purchases=(
            Purchase(definition_id="skill:engineer-artillery", amount=40, technology_level=4),
        ),
    )
    await declare(play, cid)
    plan = await select(play, cid, "skill:engineer-small-arms")
    # B173 purchased native14 gains credit from learned Engineer Artillery19−4;
    # 20 paid +24 default credit yields learned Engineer SmallArms20, then Armoury−4.
    assert plan.source_level == 20 and plan.skill_level == 16
    play.rng = RecordedDice((1,))
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    assert tasks(play._load(await play.store.read(cid)).resources)[0].skill == 15
    await wait(play, cid, 1800)
    play.rng = RecordedDice((4, 4, 4))
    await CombatService(play).execute(
        cid,
        begin(await revision(play, cid)).model_copy(
            update={"id": "finish", "stage": "finish", "task_id": "repair"}
        ),
        principal_id="b",
    )
    task = tasks(play._load(await play.store.read(cid)).resources)[0]
    assert task.restored_hp == 3 and task.check and task.check.effective_target == 15


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_rule_of_twenty_caps_actual_iq_default_consumer(tmp_path: Path, backend: str) -> None:
    from support.runtime import seed_play
    from test_actions import campaign
    from test_statistics import profile_package

    from wayfarer.engine.character import statistics
    from wayfarer.engine.character.compiler import CharacterCompiler
    from wayfarer.engine.character.power import PowerReviewer
    from wayfarer.engine.rules.catalog import RulesCatalog
    from wayfarer.engine.simulation.action_engine.engine import ActionEngine
    from wayfarer.engine.simulation.actions import ActorSetup
    from wayfarer.engine.simulation.resource_engine import ResourceEngine

    original_cid, original = await fixture(tmp_path / "original", backend, "armor", "attribute:iq")
    # Reconstruct the identical pinned package, then explicitly configure a campaign
    # permitting IQ21; no altered skill definitions or authored skill numbers.
    state = original._load(await original.store.read(original_cid))
    old = original.engine.reviewer.compiler
    base_ids = {d.id for d in statistics.definitions("gurps-basic-set-4e-2004")}
    package = profile_package(
        "gurps-basic-set-4e-2004", *(d for d in old.definitions.values() if d.id not in base_ids)
    )
    assert package.digest == old.rules.packages[0].digest
    catalog = RulesCatalog((package,))
    policy = replace(old.policy, attribute_ceiling=25, point_budget=500)
    compiler = CharacterCompiler(
        catalog, old.rules, policy, statistics_profile="gurps-basic-set-4e-2004"
    )
    reviewer = PowerReviewer(
        compiler, original.engine.reviewer.policy, original.engine.reviewer.gm_ids
    )
    resources = ResourceEngine(
        state.world, catalog, old.rules, policy, tuple(original.engine.resources.specs.values())
    )
    engine = ActionEngine(reviewer, resources, original.engine.rules)
    play = build_play(tmp_path / "high-iq", engine, backend=backend, rng=RecordedDice(()))
    actors = tuple(
        ActorSetup(
            actor_id=a.actor_id,
            proposal=a.proposal.model_copy(
                update={
                    "draft": a.proposal.draft.model_copy(
                        update={
                            "purchases": tuple(
                                p.model_copy(update={"amount": 21})
                                if p.definition_id == "attribute:iq"
                                else p
                                for p in a.proposal.draft.purchases
                            )
                        }
                    )
                }
            )
            if a.actor_id == "b"
            else a.proposal,
        )
        for a in state.actors
    )
    seeded = await seed_play(
        play,
        campaign(engine),
        state.world,
        state.resources.model_copy(update={"revision": 0}),
        actors,
    )
    cid = seeded.campaign_id
    from wayfarer.engine.simulation.combat.battlefield import GridPoint
    from wayfarer.engine.simulation.combat.spatial import Placement
    from wayfarer.orchestration.combat import StartEncounter

    await CombatService(play).execute(
        cid,
        StartEncounter(
            id="start",
            actor_id="gm",
            encounter_id="fight",
            battlefield_id="dock",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=1, y=1)),
                Placement(actor_id="b", position=GridPoint(x=1, y=2)),
            ),
            expected_revision=0,
        ),
        principal_id="gm",
    )
    await CombatService(play).execute(
        cid,
        EndEncounter(
            id="end", actor_id="gm", encounter_id="fight", reason="workshop", expected_revision=1
        ),
        principal_id="gm",
    )
    await declare(play, cid)
    plan = await select(play, cid, "attribute:iq")
    assert plan.source_level == 20 and plan.skill_level == 15
    play.rng = RecordedDice((1,))
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    task = tasks(play._load(await play.store.read(cid)).resources)[0]
    assert task.skill == 14
    await wait(play, cid, 1800)
    play.rng = RecordedDice((4, 4, 4))
    await CombatService(play).execute(
        cid,
        begin(await revision(play, cid)).model_copy(
            update={"id": "finish", "stage": "finish", "task_id": "repair"}
        ),
        principal_id="b",
    )
    assert tasks(play._load(await play.store.read(cid)).resources)[0].restored_hp == 2
