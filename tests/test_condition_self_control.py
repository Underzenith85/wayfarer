"""B35/B121/B428: actual condition producers change approved self-control rolls."""

import json
import secrets
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, played, seed_play
from test_actions import campaign
from test_haste_manufacture_power_composition import _canonical_campaign
from test_mundane_trait_runtime import prepare as trait_prepare
from test_mundane_traits import combined_package

from scripts.replay_fixtures import FixtureExecutor
from wayfarer import validation
from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import PowerReviewer
from wayfarer.engine.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    PackagePin,
    RuleDefinition,
    RulesCatalog,
)
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.base import TraitOptions
from wayfarer.engine.rules.types.skill import ControllingAttribute, Difficulty, SkillSpec
from wayfarer.engine.rules.types.toxin import DeliveryEvidence, ToxinProfile
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, CheckRule, Inspect, PlayState, Wait
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.campaign.npcs import (
    NPCSocialAction,
    NPCSocialPlan,
    NPCSocialRules,
    NPCSocialTrigger,
)
from wayfarer.engine.simulation.campaign.party import PartyRules
from wayfarer.engine.simulation.campaign.scenes import Scene, SceneRules
from wayfarer.engine.simulation.campaign.social_policy import SocialActionRules
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.condition_generations import condition_generation
from wayfarer.engine.simulation.health.survival import BeginSurvival, SettleSurvival
from wayfarer.engine.simulation.health.toxins import DrinkCommand, ToxinCommand
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import EquipmentSpec, Item
from wayfarer.engine.simulation.social.social import SocialCommand, SocialContext
from wayfarer.errors import ValidationError
from wayfarer.orchestration import social_generations
from wayfarer.orchestration.drinking import DrinkingEnvironment, DrinkingService
from wayfarer.orchestration.drowsy_toxins import DrowsyExposure, DrowsyToxinService
from wayfarer.orchestration.entropy import commit_command
from wayfarer.orchestration.npcs import NPCProposal, NPCService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.social import InteractionResolver, ResolvedInteraction, SocialService
from wayfarer.orchestration.survival import SurvivalEnvironment, SurvivalService
from wayfarer.orchestration.symptom_generations import symptom_generation
from wayfarer.orchestration.task_records import BeginTaskCheck, ChooseTaskCheck, SetRealPlayClock
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.command_inputs import replay_payload, stamp
from wayfarer.persistence.replay import verify_commands

PROFILE: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
BAD_TEMPER = "trait:bad-temper"
COWARDICE = "trait:disadvantage:cowardice"


async def fixture(
    path: Path, backend: str, trait_id: str, *, npc: bool = False
) -> tuple[str, PlayService]:
    observation = RuleDefinition(
        "skill:observation",
        DefinitionKind.SKILL,
        "Observation",
        "sjg:basic-set-characters-4e-2004",
        None,
        ImplementationStatus.IMPLEMENTED,
        hooks=("character.gurps-skill", "check.target"),
        skill=SkillSpec(ControllingAttribute.PER, Difficulty.AVERAGE, "B211"),
    )
    old_id, original = await trait_prepare(
        path / "template",
        Purchase(definition_id=trait_id, trait=TraitOptions(self_control=12)),
        Purchase(definition_id="secondary:will", amount=20),
        Purchase(definition_id=observation.id, amount=4),
        extra_definitions=(observation,)
        + tuple(
            RuleDefinition(
                key,
                DefinitionKind.EQUIPMENT,
                key,
                observation.source_id,
                1,
                ImplementationStatus.IMPLEMENTED,
            )
            for key in ("equipment:conditions-rations", "equipment:conditions-water")
        ),
    )
    source = original._load(await original.store.read(old_id))
    rules = SocialActionRules.model_validate(original.engine.rules.model_dump()).model_copy(
        update={
            "maximum_wait": 10000,
            "party": PartyRules(id="conditions-party", version=1) if npc else None,
            "scenes": SceneRules(
                id="conditions-scenes",
                version=1,
                scenes=(Scene(id="dock-scene", version=1, location_id="dock", title="Dock"),),
            )
            if npc
            else None,
            "checks": (
                CheckRule(
                    id="look",
                    action="inspect",
                    target_id="npc",
                    definition_id=observation.id,
                    package_id=original.engine.reviewer.compiler.definition_packages[
                        observation.id
                    ][0],
                    package_version=original.engine.reviewer.compiler.definition_packages[
                        observation.id
                    ][1],
                    duration=1,
                    reveal_fact_ids=("secret",),
                ),
            ),
            "npcs": NPCSocialRules(
                id="temptation",
                plans=(
                    NPCSocialPlan(
                        id="temptation",
                        actor_id="npc",
                        goal="Provoke the purchased disadvantage",
                        first_due=1,
                        interval=1,
                        action_budget=1,
                        actions=(
                            NPCSocialAction(
                                id="provoke",
                                kind="communicate",
                                social=NPCSocialTrigger(
                                    kind="self-control",
                                    subject_id="a",
                                    trait_id=trait_id,
                                ),
                            ),
                        ),
                    ),
                ),
            )
            if npc
            else None,
        }
    )
    original_compiler = original.engine.reviewer.compiler
    policy = replace(
        original_compiler.policy,
        allowed_equipment=frozenset({"equipment:conditions-rations", "equipment:conditions-water"}),
    )
    package = replace(combined_package(), definitions=tuple(original_compiler.definitions.values()))
    catalog = RulesCatalog((package,))
    pinned = replace(
        original_compiler.rules, packages=(PackagePin(package.id, package.version, package.digest),)
    )
    compiler = CharacterCompiler(
        catalog,
        pinned,
        policy,
        statistics_profile=PROFILE,
        trait_runtime_hooks=original_compiler.trait_runtime_hooks,
    )
    resources_engine = ResourceEngine(
        source.world,
        catalog,
        compiler.rules,
        policy,
        tuple(EquipmentSpec(definition_id=key, unit_weight=1) for key in policy.allowed_equipment),
    )
    engine = ActionEngine(
        PowerReviewer(compiler, original.engine.reviewer.policy, original.engine.reviewer.gm_ids),
        resources_engine,
        rules,
    )
    play = build_play(path / "source", engine, backend=backend, rng=secrets)
    initial = campaign(engine)
    resources = source.resources.model_copy(
        update={
            "items": (
                Item(
                    id="rations",
                    definition_id="equipment:conditions-rations",
                    owner_id="a",
                    quantity=20,
                ),
                Item(
                    id="water",
                    definition_id="equipment:conditions-water",
                    owner_id="a",
                    quantity=20,
                ),
            )
        }
    )
    await seed_play(
        play,
        initial,
        source.world,
        resources,
        (ActorSetup(actor_id="a", proposal=source.actors[0].proposal, aware_of=("npc",)),),
        members=source.members
        + (CampaignMember(principal_id="a", role="player", actor_ids=("a",)),),
    )
    return initial["id"], play


async def dose(play: PlayService, cid: str) -> None:
    environment = DrowsyExposure(
        profile=ToxinProfile(
            id="scenario-sedative",
            vector="digestive",
            condition="drowsy",
            condition_seconds=60,
            reference="B438-B440",
        ),
        evidence=DeliveryEvidence(swallowed=True),
    )
    service = DrowsyToxinService(play, lambda *_: environment)
    for kind in ("expose", "resolve"):
        await service.execute(
            cid,
            ToxinCommand(
                id=f"dose-{kind}",
                actor_id="a",
                expected_revision=await revision(play, cid),
                kind=kind,
                exposure_id="sedative",
            ),
            principal_id="gm",
        )


async def revision(play: PlayService, cid: str) -> int:
    return (await play.store.read(cid))["revision"]


def source(trait_id: str, modifier: int = 0) -> InteractionResolver:
    def resolve(play: PlayService, state: PlayState, command: SocialCommand) -> ResolvedInteraction:
        definition = play.engine.reviewer.compiler.definitions[trait_id]
        actor = next(a for a in state.actors if a.actor_id == command.subject_id)
        purchase = next(p for p in actor.proposal.draft.purchases if p.definition_id == trait_id)
        assert definition.point_cost is not None
        return ResolvedInteraction(
            SocialContext(
                PROFILE,
                20,
                trait_base=definition.point_cost,
                trait_levels=purchase.amount,
                trait_options=purchase.trait,
                trait_rules=definition.trait_rules,
                self_control_trait_id=trait_id,
                self_control_modifier=modifier,
            )
        )

    return resolve


async def drink(play: PlayService, cid: str) -> None:
    service = DrinkingService(play, lambda *_: DrinkingEnvironment())
    play.rng = RecordedDice((5, 5, 5, 5, 5, 5, 3, 3, 3))
    for number, count in enumerate((4, 1)):
        await service.execute(
            cid,
            DrinkCommand(
                id=f"drink-{number}",
                actor_id="a",
                expected_revision=await revision(play, cid),
                kind="drink",
                drinks=count,
            ),
            principal_id="a",
        )


def private(state: PlayState, identifier: str) -> dict[str, object]:
    event = next(
        e
        for e in state.resources.events
        if e.id.startswith(("social:", "social-key:")) and identifier in e.id
    )
    return validation.mapping(validation.mapping(validation.decode(event.kind))["private"])


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "trait_id,modifier,target,triggered",
    [
        (BAD_TEMPER, 0, 8, True),
        (COWARDICE, 0, 12, False),
        (COWARDICE, -5, 7, True),
    ],
)
async def test_actual_drinking_bound_self_control_and_cowardice_exception(
    tmp_path: Path, backend: str, trait_id: str, modifier: int, target: int, triggered: bool
) -> None:
    cid, play = await fixture(tmp_path, backend, trait_id)
    await drink(play, cid)
    before = await play.store.read(cid)
    command = SocialCommand(
        id="control",
        actor_id="npc",
        subject_id="a",
        kind="self-control",
        trigger_id="provocation",
        expected_revision=before["revision"],
    )
    service = SocialService(play, source(trait_id, modifier))
    with pytest.raises(ValidationError):
        await service.execute(cid, command, principal_id="alice")
    play.rng = RecordedDice((3, 3, 3))
    result = await service.execute(cid, command, principal_id="gm")
    assert result.outcome == ("triggered" if triggered else "resisted")
    state = play._load(await play.store.read(cid))
    trace = private(state, "provocation")
    assert (
        trace["base_target"] == 12 and trace["effective_target"] == target and trace["total"] == 9
    )
    record = (await play.store.history(cid))[-1]
    payload = validation.mapping(replay_payload(record.command_input or "{}"))
    captured = validation.mapping(validation.mapping(payload["social_source"])["context"])
    assert captured["self_control_trait_id"] == trait_id
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )

    def forbidden(*_: object) -> ResolvedInteraction:
        raise AssertionError("Retry must retain its approved trait identity")

    assert (
        await SocialService(restarted, forbidden).execute(cid, command, principal_id="gm") == result
    )
    assert state.actors == play._load(before).actors


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("identity", ["trait:unknown", COWARDICE])
async def test_unpurchased_trait_identity_refuses_before_dice_or_persistence(
    tmp_path: Path, backend: str, identity: str
) -> None:
    cid, play = await fixture(tmp_path, backend, BAD_TEMPER)
    before = await play.store.read(cid)
    resolver = source(BAD_TEMPER)

    def invalid(play: PlayService, state: PlayState, command: SocialCommand) -> ResolvedInteraction:
        interaction = resolver(play, state, command)
        interaction.context.self_control_trait_id = identity
        return interaction

    # An empty die source makes accidental resolution an immediate failure.
    play.rng = RecordedDice(())
    command = SocialCommand(
        id="invalid-control",
        actor_id="npc",
        subject_id="a",
        kind="self-control",
        trigger_id="provocation",
        expected_revision=before["revision"],
    )
    with pytest.raises(ValidationError, match="implemented approved disadvantage"):
        await SocialService(play, invalid).execute(cid, command, principal_id="gm")
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_sobering_reduces_then_removes_self_control_penalty(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend, BAD_TEMPER)
    await drink(play, cid)
    drinks = DrinkingService(play, lambda *_: DrinkingEnvironment())
    play.rng = RecordedDice((2, 2, 2))
    await drinks.execute(
        cid,
        DrinkCommand(
            id="stop",
            actor_id="a",
            expected_revision=await revision(play, cid),
            kind="stop-drinking",
        ),
        principal_id="a",
    )
    for number, target in enumerate((10, 12)):
        state = play._load(await play.store.read(cid))
        due = state.resources.intoxications[0].sober_due
        assert due is not None
        await play.execute(
            cid,
            Wait(
                id=f"wait-{number}",
                actor_id="a",
                expected_revision=state.revision,
                ticks=due - state.resources.game_time,
            ),
            principal_id="a",
        )
        play.rng = RecordedDice((3, 3, 3))
        await drinks.execute(
            cid,
            DrinkCommand(
                id=f"sober-{number}",
                actor_id="a",
                expected_revision=await revision(play, cid),
                kind="sober",
            ),
            principal_id="a",
        )
        play.rng = RecordedDice((3, 3, 3))
        await SocialService(play, source(BAD_TEMPER)).execute(
            cid,
            SocialCommand(
                id=f"control-{number}",
                actor_id="npc",
                subject_id="a",
                kind="self-control",
                trigger_id=f"provocation-{number}",
                expected_revision=await revision(play, cid),
            ),
            principal_id="gm",
        )
        assert (
            private(play._load(await play.store.read(cid)), f"provocation-{number}")[
                "effective_target"
            ]
            == target
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("route", ["wait", "task"])
@pytest.mark.parametrize("version", [0, 1, 2, 3, 4])
async def test_npc_and_task_checkpoint_self_control_reexecute_all_producers(
    tmp_path: Path, backend: str, route: str, version: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(social_generations, "CURRENT_VERSION", version)
    cid, play = await fixture(tmp_path, backend, BAD_TEMPER, npc=True)
    initial = await play.store.read(cid)
    drinks = DrinkingService(play, lambda *_: DrinkingEnvironment(empty_stomach=True))
    play.seeds = lambda: (
        f"{5:064x}"
    )  # total12 fails two drinking HT checks; laterpinkuses remainingdice.
    await drinks.execute(
        cid,
        DrinkCommand(id="drink", actor_id="a", expected_revision=0, kind="drink", drinks=4),
        principal_id="a",
    )
    # Tipsy alone gives self-control12−2=10; choose NPC roll11 to trigger.
    if route == "task":
        await TaskService(play).execute(
            cid,
            SetRealPlayClock(
                id="clock", actor_id="gm", expected_revision=await revision(play, cid), running=True
            ),
            principal_id="gm",
        )
    await NPCService(play).propose(
        cid,
        NPCProposal(
            id="propose",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            plan_id="temptation",
            action_id="provoke",
        ),
        principal_id="gm",
    )
    play.seeds = lambda: f"{3:064x}"  # NPC self-control total11.
    if route == "wait":
        await play.execute(
            cid,
            Wait(id="time", actor_id="a", expected_revision=await revision(play, cid), ticks=1),
            principal_id="a",
        )
    else:
        opened = await TaskService(play).execute(
            cid,
            BeginTaskCheck(
                id="time",
                actor_id="a",
                expected_revision=await revision(play, cid),
                check_id="look",
            ),
            principal_id="alice",
        )
        assert opened.pending_id is not None
        await TaskService(play).execute(
            cid,
            ChooseTaskCheck(
                id="accept",
                actor_id="a",
                expected_revision=await revision(play, cid),
                pending_id=opened.pending_id,
                kind="accept-check",
            ),
            principal_id="alice",
        )
    final = await play.store.read(cid)
    state = play._load(final)
    event = next(e for e in state.resources.events if e.id.startswith(("social:", "social-key:")))
    trace = validation.mapping(validation.mapping(validation.decode(event.kind))["private"])
    assert trace["effective_target"] == (10 if version >= 3 else 12) and trace["total"] == 11
    assert json.loads(json.loads(event.kind)["public"])["outcome"] == (
        "triggered" if version >= 3 else "resisted"
    )
    monkeypatch.setattr(social_generations, "CURRENT_VERSION", 1 if version >= 3 else 4)
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert _canonical_campaign(replayed) == _canonical_campaign(final)
    assert final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("trait_id", [BAD_TEMPER, COWARDICE])
@pytest.mark.parametrize("alcohol", [False, True])
async def test_awake_drowsiness_self_control_expiry_and_distinct_alcohol_condition(
    tmp_path: Path, backend: str, trait_id: str, alcohol: bool
) -> None:
    cid, play = await fixture(tmp_path, backend, trait_id)
    if alcohol:
        await drink(play, cid)
    play.rng = RecordedDice((4, 4, 4))
    await dose(play, cid)
    for ended in (False, True):
        if ended:
            await play.execute(
                cid,
                Wait(
                    id="expiry", actor_id="a", expected_revision=await revision(play, cid), ticks=60
                ),
                principal_id="a",
            )
        play.rng = RecordedDice((3, 4, 4))
        target = (8 if alcohol and trait_id != COWARDICE else 12) - (0 if ended else 2)
        result = await SocialService(play, source(trait_id)).execute(
            cid,
            SocialCommand(
                id=f"control-{ended}",
                actor_id="npc",
                subject_id="a",
                kind="self-control",
                trigger_id=f"temptation-{ended}",
                expected_revision=await revision(play, cid),
            ),
            principal_id="gm",
        )
        trace = private(play._load(await play.store.read(cid)), f"temptation-{ended}")
        assert trace["effective_target"] == target and trace["total"] == 11
        assert result.outcome == ("resisted" if target >= 11 else "triggered")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("version", [2, 3])
async def test_actual_survival_and_dose_sustain_one_drowsy_condition_with_captured_history(
    tmp_path: Path, backend: str, version: int
) -> None:
    cid, play = await fixture(tmp_path, backend, BAD_TEMPER)
    initial = await play.store.read(cid)
    survival = SurvivalService(
        play, lambda *_: SurvivalEnvironment(meal_item_ids=("rations",), water_item_ids=("water",))
    )
    play.rng = secrets
    play.seeds = lambda: f"{1:064x}"
    await survival.execute(
        cid,
        BeginSurvival(id="survival", actor_id="a", expected_revision=await revision(play, cid)),
        principal_id="a",
    )
    for _ in range(20):
        state = play._load(await play.store.read(cid))
        if state.resources.survival[0].drowsy_until is not None:
            break
        due = state.resources.survival[0].next_due
        while state.resources.game_time < due:
            await play.execute(
                cid,
                Wait(
                    id=f"idle-{state.revision}",
                    actor_id="a",
                    expected_revision=state.revision,
                    ticks=min(10000, due - state.resources.game_time),
                ),
                principal_id="a",
            )
            state = play._load(await play.store.read(cid))
        await survival.execute(
            cid,
            SettleSurvival(
                id=f"deprivation-{state.revision}",
                actor_id="a",
                expected_revision=state.revision,
            ),
            principal_id="a",
        )
    state = play._load(await play.store.read(cid))
    assert state.resources.survival[0].drowsy_until == 129600
    onset = state.resources.game_time
    play.seeds = lambda: f"{5:064x}"
    await dose(play, cid)
    before = await play.store.read(cid)
    state = play._load(before)
    with condition_generation(True, deduplicate=version >= 3):
        assert tuple(
            sum(m.value for m in check_modifiers(state.resources, "a", a))
            for a in ("dx", "iq", "per", "will")
        ) == ((-2,) * 4 if version == 3 else (-4,) * 4)
        assert check_modifiers(state.resources, "a", "dx", defensive=True) == ()
    command = Inspect(id="observe", actor_id="a", target_id="npc", expected_revision=state.revision)
    play.seeds = lambda: f"{10:064x}"
    if version == 3:
        result = await play.execute(cid, command, principal_id="a")
    else:
        plan = play.plan(command, before)
        with symptom_generation(True, condition_attributes=True):
            await commit_command(
                play,
                cid,
                command.id,
                command.expected_revision,
                stamp(plan.payload, version=2),
                plan.resolve,
                actor_id="a",
                rng=play.rng,
            )
        result = await plan.outcome(await play.store.read(cid))
    assert result.check and result.check.effective_target == (9 if version == 3 else 7)
    assert result.check.total == 8 and result.check.outcome.succeeded == (version == 3)
    state = play._load(await play.store.read(cid))
    assert (("a", "secret") in state.world.knowledge) == (version == 3)
    play.seeds = lambda: f"{3:064x}"
    controlled = await SocialService(play, source(BAD_TEMPER)).execute(
        cid,
        SocialCommand(
            id="drowsy-control",
            actor_id="npc",
            subject_id="a",
            kind="self-control",
            trigger_id="drowsy-provocation",
            expected_revision=state.revision,
        ),
        principal_id="gm",
    )
    assert controlled.outcome == "triggered"
    state = play._load(await play.store.read(cid))
    trace = private(state, "drowsy-provocation")
    assert trace["effective_target"] == 10 and trace["total"] == 11
    await play.execute(
        cid,
        Wait(
            id="toxin-expiry",
            actor_id="a",
            expected_revision=state.revision,
            ticks=onset + 60 - state.resources.game_time,
        ),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    assert check_modifiers(state.resources, "a", "iq")[0].value == -2
    assert state.resources.survival[0].drowsy_until == 129600
    final = await play.store.read(cid)
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert _canonical_campaign(replayed) == _canonical_campaign(final)
    assert final == await play.store.replay(cid)
