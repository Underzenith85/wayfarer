"""Actual approved alcohol traits and every source drinking HT check."""

from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import seed_campaign
from test_actions import campaign, world
from test_statistics import gurps_draft, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
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
from wayfarer.engine.rules.types.toxin import Intoxication
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup
from wayfarer.engine.simulation.health.toxins import DrinkCommand, apply_drinking
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Owner, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.drinking import DrinkingEnvironment, DrinkingService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


def rule_package() -> RulesPackage:
    base, mundane, physiology = profile_package(PROFILE), candidate_package(), package()
    combined = replace(
        base,
        id="package:slow-healing",
        sources=base.sources + mundane.sources,
        definitions=base.definitions + mundane.definitions + physiology.definitions,
    )
    return combined


def compiler() -> CharacterCompiler:
    combined = rule_package()
    policy = CampaignPolicy(
        "policy:slow-healing",
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


@pytest.mark.parametrize(
    ("identifier", "target", "level"),
    [
        ("trait:advantage:alcohol-tolerance", 11, "sober"),
        ("trait:disadvantage:alcohol-intolerance", 7, "tipsy"),
    ],
)
async def test_current_approved_purchase_controls_persisted_drinking(
    tmp_path: Path, identifier: str, target: int, level: str
) -> None:
    compiled = compiler()
    engine = ActionEngine(
        PowerReviewer(compiled, PowerPolicy(id="test", version=1), frozenset({"gm"})),
        ResourceEngine(
            world(), RulesCatalog((rule_package(),)), compiled.rules, compiled.policy, ()
        ),
        ActionRules(id="drink-test", version=1, fatigue_cost=0),
    )
    play = PlayService(
        AsyncSQLiteStore(tmp_path / "drink.sqlite", 10), engine, rng=RecordedDice([3, 3, 4])
    )
    initial = campaign(engine)
    state = play.initial_state(
        initial,
        world(),
        ResourceState(owners=(Owner(actor_id="a", capacity=100),)),
        (
            ActorSetup(
                actor_id="a",
                proposal=CharacterProposal(draft=gurps_draft(Purchase(definition_id=identifier))),
                aware_of=("alley",),
            ),
        ),
    )
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    service = DrinkingService(play, lambda *_: DrinkingEnvironment())
    command = DrinkCommand(id="third", actor_id="a", expected_revision=0, kind="drink", drinks=3)
    with pytest.raises(ValidationError):
        await service.execute(initial["id"], command, principal_id="other")
    result = await service.execute(initial["id"], command, principal_id="a")
    assert result.check is not None and result.check.effective_target == target
    assert result.level == level
    persisted = play._load(await play.store.read(initial["id"]))
    assert persisted.resources.intoxications[0].level == level
    play.rng = RecordedDice([])
    assert await service.execute(initial["id"], command, principal_id="a") == result
    with pytest.raises(ConflictError):
        await service.execute(
            initial["id"], command.model_copy(update={"drinks": 4}), principal_id="a"
        )
    with pytest.raises(ConflictError):
        await service.execute(
            initial["id"], command.model_copy(update={"id": "stale"}), principal_id="a"
        )


@pytest.mark.parametrize("tolerance", [-2, 2])
def test_tolerance_applies_to_sobering_ht(tolerance: int) -> None:
    state = ResourceState(
        game_time=3600,
        intoxications=(
            Intoxication(
                actor_id="a",
                window_started=0,
                total_session_drinks=3,
                level="tipsy",
                stopped_at=0,
                sober_due=3600,
            ),
        ),
    )
    command = DrinkCommand(id="sober", actor_id="a", expected_revision=0, kind="sober")
    updated, result = apply_drinking(
        state, command, rng=RecordedDice([3, 3, 4]), system=True, st=10, ht=10, tolerance=tolerance
    )
    assert result.check is not None and result.check.effective_target == 10 + tolerance
    assert result.level == ("sober" if tolerance == 2 else "tipsy")
    assert apply_drinking(
        ResourceState.model_validate_json(updated.model_dump_json()),
        command,
        rng=RecordedDice([]),
        system=True,
        st=10,
        ht=10,
        tolerance=tolerance,
    ) == (updated, result)


@pytest.mark.parametrize("tolerance", [-2, 2])
def test_tolerance_applies_to_hangover_ht(tolerance: int) -> None:
    state = ResourceState(
        intoxications=(
            Intoxication(actor_id="a", window_started=0, total_session_drinks=3, level="sober"),
        )
    )
    updated, _ = apply_drinking(
        state,
        DrinkCommand(id="stop", actor_id="a", expected_revision=0, kind="stop-drinking"),
        rng=RecordedDice([3, 3, 4] + ([1] if tolerance == -2 else [])),
        system=True,
        st=10,
        ht=10,
        tolerance=tolerance,
    )
    assert (updated.intoxications[0].hangover_due is None) == (tolerance == 2)


@pytest.mark.parametrize("tolerance", [-2, 2])
def test_tolerance_applies_to_pink_elephants_ht(tolerance: int) -> None:
    state = ResourceState(
        intoxications=(Intoxication(actor_id="a", window_started=0, level="tipsy"),)
    )
    _, result = apply_drinking(
        state,
        DrinkCommand(id="pink", actor_id="a", expected_revision=0, kind="drink", drinks=3),
        rng=RecordedDice([5, 5, 5, 4, 4, 5]),
        system=True,
        st=10,
        ht=10,
        tolerance=tolerance,
    )
    assert result.level == "drunk"
    assert result.hallucinating == (tolerance == -2)


@pytest.mark.parametrize("tolerance", [-2, 2])
def test_tolerance_applies_to_vomiting_ht(tolerance: int) -> None:
    state = ResourceState(
        intoxications=(Intoxication(actor_id="a", window_started=0, level="drunk"),)
    )
    _, result = apply_drinking(
        state,
        DrinkCommand(id="purge", actor_id="a", expected_revision=0, kind="drink", drinks=3),
        rng=RecordedDice([5, 5, 5, 3, 3, 4]),
        system=True,
        st=10,
        ht=10,
        tolerance=tolerance,
    )
    assert result.retching == (tolerance == 2)
    assert result.level == ("drunk" if tolerance == 2 else "unconscious")
