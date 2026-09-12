"""Independent physiology expectations, Characters 4e B41-160."""

from dataclasses import replace

import pytest
from test_statistics import gurps_draft, profile_package

from wayfarer.character.compiler import CharacterCompiler, Purchase, ValidatedBuild
from wayfarer.character.physiology_traits import physiology_traits
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.catalog import CampaignPolicy, CampaignRules, PackagePin, RulesCatalog
from wayfarer.rules.injury_types import InjuryStatus
from wayfarer.rules.physiology_traits import BINDINGS, PROFILE, RUNTIME_HOOKS
from wayfarer.rules.physiology_traits import package as physiology_package
from wayfarer.rules.supernatural import inventory
from wayfarer.rules.traits import TraitOptions
from wayfarer.simulation.physiology_traits import (
    PhysiologyCommand,
    PhysiologyInterval,
    apply_physiology_interval,
    history,
)
from wayfarer.simulation.resources import Pool, ResourceState


def options(**values: str | int | bool) -> TraitOptions:
    return TraitOptions(parameters=tuple(values.items()))


def compiler() -> CharacterCompiler:
    base, physiology = profile_package(PROFILE), physiology_package()
    combined = replace(
        base, id="package:test-physiology", definitions=base.definitions + physiology.definitions
    )
    policy = CampaignPolicy(
        "policy:physiology",
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
        trait_runtime_hooks=RUNTIME_HOOKS,
    )


def approved(*purchases: Purchase) -> tuple[ValidatedBuild, CharacterCompiler]:
    engine = compiler()
    result = engine.compile(gurps_draft(*purchases))
    assert result.build is not None, result.diagnostics
    return result.build, engine


def test_registry_and_inventory_account_for_all_37_entries() -> None:
    assert len(BINDINGS) == 37 and len({binding.id for binding in BINDINGS}) == 37
    rows = {
        row.id: row for row in inventory().entries if row.id in {binding.id for binding in BINDINGS}
    }
    assert set(rows) == {binding.id for binding in BINDINGS}
    assert all(
        row.blockers == (191,) and row.evidence == ("tests/test_physiology_traits.py",)
        for row in rows.values()
    )


@pytest.mark.parametrize(
    ("purchase", "expected"),
    [
        (Purchase(definition_id="advantage:doesnt-breathe"), 20),
        (Purchase(definition_id="advantage:radiation-tolerance", trait=options(divisor=100)), 30),
        (Purchase(definition_id="advantage:regeneration", trait=options(rate="fast")), 50),
        (Purchase(definition_id="disadvantage:bestial", trait=options(speech=False)), -15),
        (
            Purchase(
                definition_id="disadvantage:dependency",
                trait=options(rarity="rare", interval="day"),
            ),
            -90,
        ),
        (Purchase(definition_id="disadvantage:unhealing", trait=options(kind="total")), -30),
        (
            Purchase(
                definition_id="disadvantage:weakness",
                trait=options(rarity="common", interval="minute"),
            ),
            -45,
        ),
    ],
)
def test_fixed_and_variable_costs_are_compiler_owned(purchase: Purchase, expected: int) -> None:
    build, _ = approved(purchase)
    assert (
        next(
            entry.cost for entry in build.purchases if entry.definition_id == purchase.definition_id
        )
        == expected
    )


def test_projection_supplies_survival_environment_and_recovery_rules() -> None:
    build, engine = approved(
        Purchase(definition_id="advantage:doesnt-breathe"),
        Purchase(definition_id="advantage:doesnt-eat-or-drink"),
        Purchase(definition_id="advantage:breath-holding", amount=2),
        Purchase(definition_id="advantage:extended-lifespan", amount=3),
        Purchase(definition_id="advantage:radiation-tolerance", trait=options(divisor=20)),
        Purchase(definition_id="advantage:regeneration", trait=options(rate="fast")),
        Purchase(definition_id="advantage:pressure-support", amount=2),
        Purchase(definition_id="advantage:vacuum-support"),
    )
    traits = physiology_traits(build, engine.definitions)
    assert traits.breath_multiplier() is None
    assert traits.survival_requirements() == frozenset({"sleep"})
    assert traits.lifespan_multiplier() == 8 and traits.radiation_divisor() == 20
    assert traits.regeneration_interval() == 60
    assert traits.environmental_protection("pressure") == 20
    assert traits.environmental_protection("vacuum") == 10


def state(current: int = 5, time: int = 60) -> ResourceState:
    return ResourceState(
        game_time=time,
        pools=(
            Pool(id="hp:a", current=current, maximum=10, injury=InjuryStatus(profile_id=PROFILE)),
        ),
    )


def command(revision: int = 0, identifier: str = "regen") -> PhysiologyCommand:
    return PhysiologyCommand(
        id="physiology-" + identifier,
        actor_id="a",
        expected_revision=revision,
        interval_id=identifier,
    )


def test_regeneration_updates_hp_atomically_and_retries_exactly_once_after_restart() -> None:
    build, engine = approved(
        Purchase(definition_id="advantage:regeneration", trait=options(rate="fast"))
    )
    interval = PhysiologyInterval(id="regen", actor_id="a", kind="regeneration", due=60, amount=2)
    updated, outcome = apply_physiology_interval(
        state(),
        command(),
        interval,
        build,
        engine.definitions,
        authorized_actor_id="a",
        system=True,
    )
    assert (outcome.hp_before, outcome.hp_after, outcome.kind) == (5, 7, "regenerated")
    assert updated.pools[0].current == 7
    restarted = ResourceState.model_validate_json(updated.model_dump_json())
    assert history(restarted)[0].outcome == outcome
    assert apply_physiology_interval(
        restarted,
        command(),
        interval,
        build,
        engine.definitions,
        authorized_actor_id="a",
        system=True,
    ) == (restarted, outcome)


def test_weakness_and_extra_life_use_the_same_hp_ledger_and_limits() -> None:
    weak, weak_engine = approved(
        Purchase(
            definition_id="disadvantage:weakness", trait=options(rarity="common", interval="minute")
        )
    )
    injured, result = apply_physiology_interval(
        state(),
        command(identifier="weak"),
        PhysiologyInterval(id="weak", actor_id="a", kind="weakness", due=60, amount=3),
        weak,
        weak_engine.definitions,
        authorized_actor_id="a",
        system=True,
    )
    assert result.kind == "injured" and injured.pools[0].current == 2
    life, life_engine = approved(Purchase(definition_id="advantage:extra-life"))
    revived, result = apply_physiology_interval(
        state(-10),
        command(identifier="life"),
        PhysiologyInterval(id="life", actor_id="a", kind="extra-life", due=60),
        life,
        life_engine.definitions,
        authorized_actor_id="a",
        system=True,
    )
    assert result.kind == "revived" and revived.pools[0].current == 10


def test_interval_rejects_early_unauthorized_and_stale_commands() -> None:
    build, engine = approved(
        Purchase(definition_id="advantage:regeneration", trait=options(rate="regular"))
    )
    interval = PhysiologyInterval(id="regen", actor_id="a", kind="regeneration", due=60)
    with pytest.raises(ValidationError, match="authority"):
        apply_physiology_interval(
            state(),
            command(),
            interval,
            build,
            engine.definitions,
            authorized_actor_id="b",
            system=True,
        )
    with pytest.raises(ConflictError):
        apply_physiology_interval(
            state().model_copy(update={"revision": 1}),
            command(),
            interval,
            build,
            engine.definitions,
            authorized_actor_id="a",
            system=True,
        )
    with pytest.raises(ValidationError, match="not due"):
        apply_physiology_interval(
            state(time=59),
            command(),
            interval,
            build,
            engine.definitions,
            authorized_actor_id="a",
            system=True,
        )
