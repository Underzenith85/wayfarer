"""Approved food and water purchases consume supplies at B80/B139 rates."""

from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import seed_campaign
from test_actions import campaign, world
from test_statistics import gurps_draft, profile_package
from trait_support import approved_build

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.character.traits.physiology import physiology_traits
from wayfarer.engine.rules.catalog import (
    CampaignPolicy,
    CampaignRules,
    PackagePin,
    RulesCatalog,
    RulesPackage,
)
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.mundane import candidate_package
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.rules.traits.physiology import PROFILE, RUNTIME_HOOKS, package
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.recovery import FatigueStatus
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup
from wayfarer.engine.simulation.health.survival import (
    BeginSurvival,
    SettleSurvival,
    SurvivalContext,
    begin_survival,
    settle_survival,
)
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Advance, Owner, Pool, ResourceState
from wayfarer.errors import ConflictError, NotFoundError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.survival import SurvivalEnvironment, SurvivalService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


def rule_package() -> RulesPackage:
    base, mundane, physiology = profile_package(PROFILE), candidate_package(), package()
    combined = replace(
        base,
        id="package:consumption",
        sources=base.sources + mundane.sources,
        definitions=base.definitions + mundane.definitions + physiology.definitions,
    )
    return combined


def compiler() -> CharacterCompiler:
    combined = rule_package()
    policy = CampaignPolicy(
        "policy:consumption",
        1,
        10000,
        10000,
        20,
        20,
        frozenset(source.id for source in combined.sources),
        allow_supernatural=True,
    )
    rules = CampaignRules(
        combined.edition,
        (PackagePin(combined.id, combined.version, combined.digest),),
        policy.id,
        policy.version,
    )
    return CharacterCompiler(
        RulesCatalog((combined,)),
        rules,
        policy,
        statistics_profile=PROFILE,
        trait_runtime_hooks=SUPPORTED_HOOKS | RUNTIME_HOOKS,
    )


async def prepared_service(tmp_path: Path, *purchases: Purchase) -> tuple[PlayService, Campaign]:
    compiled = compiler()
    engine = ActionEngine(
        PowerReviewer(compiled, PowerPolicy(id="test", version=1), frozenset({"gm"})),
        ResourceEngine(
            world(), RulesCatalog((rule_package(),)), compiled.rules, compiled.policy, ()
        ),
        ActionRules(id="survival-test", version=1, fatigue_cost=0),
    )
    play = PlayService(
        AsyncSQLiteStore(tmp_path / "survival.sqlite", 10), engine, rng=RecordedDice([])
    )
    initial = campaign(engine)
    state = play.initial_state(
        initial,
        world(),
        ResourceState(owners=(Owner(actor_id="a", capacity=100),)),
        (
            ActorSetup(
                actor_id="a",
                proposal=CharacterProposal(draft=gurps_draft(*purchases)),
                aware_of=("alley",),
            ),
        ),
    )
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    return play, initial


@pytest.mark.parametrize(
    ("identifier", "level", "period"),
    [
        ("trait:advantage:reduced-consumption", 1, 43200),
        ("trait:advantage:reduced-consumption", 2, 86400),
        ("trait:advantage:reduced-consumption", 3, 604800),
        ("trait:advantage:reduced-consumption", 4, 2592000),
        ("trait:disadvantage:increased-consumption", 1, 14400),
        ("trait:disadvantage:increased-consumption", 2, 7200),
        ("trait:disadvantage:increased-consumption", 3, 3600),
    ],
)
async def test_approved_consumption_reaches_persisted_service(
    tmp_path: Path, identifier: str, level: int, period: int
) -> None:
    play, initial = await prepared_service(
        tmp_path, Purchase(definition_id=identifier, amount=level)
    )
    service = SurvivalService(play, lambda *_: SurvivalEnvironment())
    command = BeginSurvival(id="begin", actor_id="a", expected_revision=0)
    with pytest.raises(NotFoundError):
        await service.execute(initial["id"], command, principal_id="other")
    result = await service.execute(initial["id"], command, principal_id="a")
    persisted = play._load(await play.store.read(initial["id"]))
    assert persisted.resources.survival[0].next_meal_due == period
    assert persisted.resources.survival[0].meal_period == period
    assert await service.execute(initial["id"], command, principal_id="a") == result
    with pytest.raises(ConflictError):
        await service.execute(
            initial["id"], command.model_copy(update={"id": "stale"}), principal_id="a"
        )


async def test_approved_doesnt_sleep_prevents_unrelated_sleep_fatigue(tmp_path: Path) -> None:
    play, initial = await prepared_service(
        tmp_path,
        Purchase(definition_id="trait:advantage:reduced-consumption", amount=3),
        Purchase(definition_id="advantage:doesnt-sleep"),
    )
    service = SurvivalService(play, lambda *_: SurvivalEnvironment())
    await service.execute(
        initial["id"],
        BeginSurvival(id="begin", actor_id="a", expected_revision=0),
        principal_id="a",
    )
    state = play._load(await play.store.read(initial["id"]))
    assert state.resources.survival[0].does_not_sleep
    assert state.resources.survival[0].next_due == 604800

    def advance(campaign: Campaign) -> CommandReceipt:
        before = play._load(campaign)
        resources = play.engine.resources.apply(
            before.resources,
            Advance(id="week", actor_id="a", expected_revision=1, to=604800),
            system=True,
        )
        play.commit(campaign, before.model_copy(update={"revision": 2, "resources": resources}))
        return CommandReceipt(action="resource", outcome="week")

    await play.store.commit_turn(initial["id"], "week", 1, "week", advance)
    await service.execute(
        initial["id"], SettleSurvival(id="due", actor_id="a", expected_revision=2), principal_id="a"
    )
    after = play._load(await play.store.read(initial["id"]))
    fp = next(pool for pool in after.resources.pools if pool.id == "fp:a")
    assert fp.fatigue is not None and fp.fatigue.sleep == 0
    assert fp.fatigue.starvation == 1


@pytest.mark.parametrize(
    ("identifier", "period", "starvation"),
    [
        ("trait:advantage:reduced-consumption", 43200, 0),
        ("trait:disadvantage:increased-consumption", 14400, 1),
    ],
)
def test_purchased_consumption_changes_actual_starvation(
    identifier: str, period: int, starvation: int
) -> None:
    build, compiled = approved_build(compiler(), Purchase(definition_id=identifier))
    context = SurvivalContext(
        PROFILE, 10, 10, physiology=physiology_traits(build, compiled.definitions)
    )
    state = ResourceState(
        pools=(
            Pool(id="hp:a", current=10, maximum=10, injury=InjuryStatus(profile_id=PROFILE)),
            Pool(id="fp:a", current=10, maximum=10, fatigue=FatigueStatus(profile_id=PROFILE)),
        )
    )
    pending, _ = begin_survival(
        state, BeginSurvival(id="begin", actor_id="a", expected_revision=0), context, system=True
    )
    assert pending.survival[0].next_meal_due == period
    due = pending.survival[0].next_due
    command = SettleSurvival(id="settle", actor_id="a", expected_revision=1)
    updated, result = settle_survival(
        pending.model_copy(update={"game_time": due}),
        command,
        context,
        rng=RecordedDice([]),
        system=True,
    )
    fp = next(pool for pool in updated.pools if pool.id == "fp:a")
    assert fp.fatigue is not None and fp.fatigue.starvation == starvation
    assert settle_survival(
        ResourceState.model_validate_json(updated.model_dump_json()),
        command,
        context,
        rng=RecordedDice([]),
        system=True,
    ) == (updated, result)
