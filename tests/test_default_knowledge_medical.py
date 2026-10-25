"""First Aid reads the same adopted default projection as ordinary skill consumers."""

from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, seed_campaign
from test_actions import campaign, world
from test_default_knowledge_hosts import adopt
from test_statistics import gurps_draft, profile_compiler, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import PackagePin, RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.skills.mundane.medicine import definitions
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup
from wayfarer.engine.simulation.health.medical.commands import BeginRecovery
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Owner, ResourceState
from wayfarer.errors import ValidationError
from wayfarer.orchestration.medical import CareEnvironment, MedicalService, _build, care_skill

PROFILE = "gurps-basic-set-4e-2004"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("trained", [False, True])
@pytest.mark.parametrize("adoption", [False, True])
async def test_actual_first_aid_unknown_default_refuses_before_environment_but_training_remains(
    tmp_path: Path, backend: str, trained: bool, adoption: bool
) -> None:
    first_aid = next(d for d in definitions() if d.id == "skill:first-aid")
    package = profile_package(PROFILE, first_aid)
    base = profile_compiler(PROFILE)
    catalog = RulesCatalog((package,))
    compiler = CharacterCompiler(
        catalog,
        replace(base.rules, packages=(PackagePin(package.id, package.version, package.digest),)),
        replace(base.policy, technology_level=8),
        statistics_profile=PROFILE,
    )
    engine = ActionEngine(
        PowerReviewer(compiler, PowerPolicy(id="medical", version=1), frozenset({"gm"})),
        ResourceEngine(world(), catalog, compiler.rules, compiler.policy, ()),
        ActionRules(id="medical-defaults", version=1),
    )
    play = build_play(tmp_path, engine, backend=backend)
    initial = campaign(engine)
    purchases = (
        (Purchase(definition_id="skill:first-aid", amount=1, technology_level=8),)
        if trained
        else ()
    )
    state = play.initial_state(
        initial,
        world(),
        ResourceState(owners=(Owner(actor_id="a", capacity=100),)),
        (ActorSetup(actor_id="a", proposal=CharacterProposal(draft=gurps_draft(*purchases))),),
    )
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    cid = initial["id"]
    actor = state.actors[0]
    raw, _ = play.engine.reviewer.activate(
        actor.proposal, actor.approval, campaign_id=cid, actor_id="a"
    )
    # Construction arithmetic is retained; the live current actor join is stricter.
    assert care_skill(raw, "first-aid", CareEnvironment())[0] == (10 if trained else 6)
    if adoption:
        await adopt(play, cid)
    state = play._load(await play.store.read(cid))
    if trained:
        assert care_skill(_build(play, state, "a"), "first-aid", CareEnvironment())[0] == 10
        return
    before = await play.store.read(cid)
    play.rng = RecordedDice(())

    def forbidden(*_: object) -> CareEnvironment:
        raise AssertionError("Unknown default must refuse before external environment capture")

    with pytest.raises(ValidationError, match="skill or attribute is unavailable"):
        await MedicalService(play, forbidden).execute(
            cid,
            BeginRecovery(
                id="treat",
                actor_id="a",
                expected_revision=state.revision,
                kind="first-aid",
                target_id="a",
                wound_id="injury",
            ),
            principal_id="a",
        )
    assert await play.store.read(cid) == before and play.rng.exhausted()
