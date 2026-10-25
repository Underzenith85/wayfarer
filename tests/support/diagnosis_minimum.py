"""Actual approved TL8 Diagnosis, disease and dose fixtures; no scalar skill inputs."""

from dataclasses import replace
from pathlib import Path

from test_actions import campaign, world
from test_statistics import gurps_draft, profile_compiler, profile_package

from scripts.replay_fixtures import FixtureExecutor
from support.runtime import build_play, played, seed_campaign
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    PackagePin,
    RuleDefinition,
    RulesCatalog,
)
from wayfarer.engine.rules.skills.mundane.medicine import definitions
from wayfarer.engine.rules.types.hazard import HazardSchedule, HazardSpec
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup
from wayfarer.engine.simulation.campaign.default_knowledge import AdoptDefaultKnowledge
from wayfarer.engine.simulation.events import document
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import EquipmentSpec, Item, Owner, ResourceState
from wayfarer.orchestration.default_knowledge import DefaultKnowledgeService
from wayfarer.orchestration.hazard_care import HazardCare, HazardCareCommand
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands

PROFILE = "gurps-basic-set-4e-2004"


async def fixture(
    path: Path,
    backend: str,
    *,
    iq: int = 9,
    paid: bool = False,
    known: bool = True,
    no_edge: bool = False,
    patient_ht: int = 10,
    due: int = 86400,
    schedule_id: str = "exposure",
) -> tuple[str, PlayService, Campaign]:
    diagnosis = next(d for d in definitions() if d.id == "skill:diagnosis")
    if no_edge:
        assert diagnosis.skill
        diagnosis = replace(diagnosis, skill=replace(diagnosis.skill, defaults=()))
    base = profile_package(PROFILE)
    dose = RuleDefinition(
        "antibiotic",
        DefinitionKind.EQUIPMENT,
        "Antibiotic dose",
        base.sources[0].id,
        0,
        ImplementationStatus.IMPLEMENTED,
    )
    package = profile_package(PROFILE, diagnosis, dose)
    original = profile_compiler(PROFILE)
    policy = replace(
        original.policy, technology_level=8, allowed_equipment=frozenset({"antibiotic"})
    )
    rules = replace(
        original.rules, packages=(PackagePin(package.id, package.version, package.digest),)
    )
    catalog = RulesCatalog((package,))
    compiler = CharacterCompiler(catalog, rules, policy, statistics_profile=PROFILE)
    engine = ActionEngine(
        PowerReviewer(compiler, PowerPolicy(id="diagnosis", version=1), frozenset({"gm"})),
        ResourceEngine(
            world(),
            catalog,
            rules,
            policy,
            (EquipmentSpec(definition_id="antibiotic", unit_weight=0),),
        ),
        ActionRules(id="diagnosis", version=1, fatigue_cost=0, maximum_wait=10000),
    )
    play = build_play(path, engine, backend=backend, seeds=lambda: f"{270:064x}")
    initial = campaign(engine)
    setups = []
    for actor in ("a", "b"):
        draft = gurps_draft(
            *(
                (Purchase(definition_id="skill:diagnosis", amount=1, technology_level=8),)
                if paid and actor == "a"
                else ()
            )
        )
        draft = draft.model_copy(
            update={
                "purchases": tuple(
                    p.model_copy(update={"amount": iq})
                    if p.definition_id == "attribute:iq" and actor == "a"
                    else p.model_copy(update={"amount": patient_ht})
                    if p.definition_id == "attribute:ht" and actor == "b"
                    else p
                    for p in draft.purchases
                )
            }
        )
        setups.append(ActorSetup(actor_id=actor, proposal=CharacterProposal(draft=draft)))
    state = play.initial_state(
        initial,
        world(),
        ResourceState(
            owners=(Owner(actor_id="a", capacity=100), Owner(actor_id="b", capacity=100)),
            items=(Item(id="dose", definition_id="antibiotic", owner_id="a", quantity=2),),
        ),
        tuple(setups),
    )
    hazard = HazardSchedule(
        id=schedule_id,
        actor_id="b",
        spec=HazardSpec(
            id="illness",
            scene_id="dock",
            kind="disease",
            interval=86400,
            cycles=3,
            reference="B443",
            bacterial=True,
        ),
        started=0,
        due=due,
        remaining=3,
        ht=patient_ht,
        will=10,
        swimming=6,
        stage="cycles",
        symptoms=1,
    )
    state = state.model_copy(
        update={"resources": state.resources.model_copy(update={"hazards": (hazard,)})}
    )
    engine.validate(state)
    play.commit(initial, state)
    await seed_campaign(play.store, initial)
    await DefaultKnowledgeService(play).execute(
        initial["id"],
        AdoptDefaultKnowledge(
            id="diagnosis-fact",
            actor_id="gm",
            expected_revision=0,
            performer_id="a",
            known_skill_ids=("skill:diagnosis",) if known else (),
            reason="GM confirms Diagnosis in this TL8 society",
        ),
        principal_id="gm",
    )
    return initial["id"], play, initial


def context(*_: object) -> HazardCare:
    return HazardCare("diagnosis", "b", "exposure", technology_level=8, known_disease=True)


def unavailable(*_: object) -> HazardCare:
    raise AssertionError("Retry/replay must not invoke an external resolver")


async def command(play: PlayService, cid: str, identifier: str = "diagnose") -> HazardCareCommand:
    return HazardCareCommand(
        id=identifier,
        actor_id="a",
        expected_revision=(await play.store.read(cid))["revision"],
        treatment_id="exam",
    )


async def reexecute(
    play: PlayService, cid: str, initial: Campaign, path: Path, *, after: str | None = None
) -> None:
    records = await played(play.store, cid)
    if after is not None:
        i = next(i for i, r in enumerate(records) if r.command_id == after)
        initial, records = records[i].state_after, records[i + 1 :]
    ids = {r.command_id for r in records}
    final, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, path),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks), [
        (c.command_id, c.reason) for c in checks
    ]
    assert (
        document(final)
        == document(await play.store.read(cid))
        == document(await play.store.replay(cid))
    )
