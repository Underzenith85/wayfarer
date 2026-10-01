"""Purchased Slow Healing changes actual care periods (Characters B155/B80)."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_statistics import gurps_draft, profile_package
from trait_support import approved_build, options

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.traits.physiology import PhysiologyTraits, physiology_traits
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
from wayfarer.engine.simulation.health.medical.commands import (
    BeginRecovery,
    CareContext,
    FinishRecovery,
)
from wayfarer.engine.simulation.health.medical.recovery import apply_recovery
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.errors import ValidationError


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


def traits(level: int) -> PhysiologyTraits:
    purchases = (
        ()
        if level == 0
        else (Purchase(definition_id="trait:disadvantage:slow-healing", amount=level),)
    )
    build, engine = approved_build(compiler(), *purchases)
    return physiology_traits(build, engine.definitions)


def state() -> ResourceState:
    return ResourceState(
        pools=(
            Pool(id="hp:a", current=5, maximum=10, injury=InjuryStatus(profile_id=PROFILE)),
            Pool(id="fp:a", current=10, maximum=10, fatigue=FatigueStatus(profile_id=PROFILE)),
        )
    )


@pytest.mark.parametrize(("level", "days"), [(0, 1), (1, 2), (2, 4), (3, 8)])
def test_approved_slow_healing_gates_natural_roll_and_actual_hp(level: int, days: int) -> None:
    context = CareContext(PROFILE, 10, food=True, physiology=traits(level))
    begin = BeginRecovery(
        id="rest", actor_id="a", target_id="a", expected_revision=0, kind="natural"
    )
    pending, _ = apply_recovery(state(), begin, context, rng=RecordedDice([]), system=True)
    assert pending.recovery_tasks[0].due == days * 86400
    assert pending.pools[0].current == 5
    finish = FinishRecovery(id="finish", actor_id="a", expected_revision=1, task_id="rest")
    with pytest.raises(ValidationError, match="due"):
        apply_recovery(
            pending.model_copy(update={"game_time": days * 86400 - 1}),
            finish,
            context,
            rng=RecordedDice([]),
            system=True,
        )
    due = pending.model_copy(update={"game_time": days * 86400})
    completed, result = apply_recovery(
        due, finish, context, rng=RecordedDice([3, 3, 4]), system=True
    )
    assert completed.pools[0].current == 6 and result.hp_recovered == 1
    assert completed.recovery_tasks[0].status == "completed"
    persisted = ResourceState.model_validate_json(completed.model_dump_json())
    assert apply_recovery(persisted, finish, context, rng=RecordedDice([]), system=True) == (
        persisted,
        result,
    )


@pytest.mark.parametrize(("level", "days"), [(0, 1), (1, 2), (2, 4), (3, 8)])
def test_approved_slow_healing_doubles_physician_roll_period(level: int, days: int) -> None:
    context = CareContext(PROFILE, 10, skill=12, technology_level=8, physiology=traits(level))
    begin = BeginRecovery(
        id="physician", actor_id="doctor", target_id="a", expected_revision=0, kind="physician"
    )
    pending, _ = apply_recovery(state(), begin, context, rng=RecordedDice([]), system=True)
    assert pending.recovery_tasks[0].due == days * 86400
    finish = FinishRecovery(
        id="finish", actor_id="doctor", expected_revision=1, task_id="physician"
    )
    completed, result = apply_recovery(
        pending.model_copy(update={"game_time": days * 86400}),
        finish,
        context,
        rng=RecordedDice([3, 3, 4]),
        system=True,
    )
    assert completed.pools[0].current == 6 and result.hp_recovered == 1


def test_source_maximum_and_regeneration_exclusion_use_actual_mundane_identity() -> None:
    result = compiler().compile(
        gurps_draft(Purchase(definition_id="trait:disadvantage:slow-healing", amount=4))
    )
    assert result.build is None
    result = compiler().compile(
        gurps_draft(
            Purchase(definition_id="trait:disadvantage:slow-healing"),
            Purchase(definition_id="advantage:regeneration", trait=options(rate="fast")),
        )
    )
    assert result.build is None
    assert any(d.code == "trait.exclusion" for d in result.diagnostics)


async def test_live_medical_service_projects_current_approved_slow_healing(tmp_path: Path) -> None:
    from support.runtime import seed_campaign
    from test_actions import campaign, world

    from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
    from wayfarer.engine.simulation.action_engine.engine import ActionEngine
    from wayfarer.engine.simulation.actions import ActionRules, ActorSetup
    from wayfarer.engine.simulation.resource_engine import ResourceEngine
    from wayfarer.engine.simulation.resources import Owner
    from wayfarer.orchestration.medical import CareEnvironment, MedicalService
    from wayfarer.orchestration.play import PlayService
    from wayfarer.persistence.async_sqlite import AsyncSQLiteStore

    compiled = compiler()
    engine = ActionEngine(
        PowerReviewer(compiled, PowerPolicy(id="test", version=1), frozenset({"gm"})),
        ResourceEngine(
            world(), RulesCatalog((rule_package(),)), compiled.rules, compiled.policy, ()
        ),
        ActionRules(id="slow-healing-test", version=1, fatigue_cost=0),
    )
    play = PlayService(
        AsyncSQLiteStore(tmp_path / "slow-healing.sqlite", 10), engine, rng=RecordedDice([])
    )
    initial = campaign(engine)
    checkpoint = play.initial_state(
        initial,
        world(),
        ResourceState(owners=(Owner(actor_id="a", capacity=100),)),
        (
            ActorSetup(
                actor_id="a",
                proposal=CharacterProposal(
                    draft=gurps_draft(Purchase(definition_id="trait:disadvantage:slow-healing"))
                ),
                aware_of=("alley",),
            ),
        ),
    )
    checkpoint = checkpoint.model_copy(
        update={
            "resources": checkpoint.resources.model_copy(
                update={
                    "pools": tuple(
                        pool.model_copy(update={"current": 5}) if pool.id == "hp:a" else pool
                        for pool in checkpoint.resources.pools
                    )
                }
            )
        }
    )
    engine.validate(checkpoint)
    initial["play_json"] = checkpoint.model_dump_json()
    await seed_campaign(play.store, initial)
    service = MedicalService(play, lambda _play, _state, _target: CareEnvironment(food=True))
    result = await service.execute(
        initial["id"],
        BeginRecovery(
            id="natural", actor_id="a", target_id="a", expected_revision=0, kind="natural"
        ),
        principal_id="a",
    )
    assert result.status == "pending"
    pending = play._load(await play.store.read(initial["id"]))
    assert pending.resources.recovery_tasks[0].due == 172800
    assert pending.resources.pools[0].current == 5
