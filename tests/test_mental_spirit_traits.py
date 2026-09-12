"""Independent mental/spirit expectations, Characters 4e B40-161."""

from dataclasses import replace

import pytest
from test_statistics import gurps_draft, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase, ValidatedBuild
from wayfarer.engine.character.mental_spirit_traits import mental_spirit_traits
from wayfarer.engine.rules.catalog import CampaignPolicy, CampaignRules, PackagePin, RulesCatalog
from wayfarer.engine.rules.mental_spirit_traits import BINDINGS, PROFILE, RUNTIME_HOOKS
from wayfarer.engine.rules.mental_spirit_traits import package as mental_spirit_package
from wayfarer.engine.rules.supernatural import inventory
from wayfarer.engine.rules.traits import TraitOptions
from wayfarer.engine.simulation.mental_spirit_traits import (
    MentalChannel,
    MentalCommand,
    apply_mental_use,
    history,
)
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.engine.world import Entity, EntityKind, Fact, World
from wayfarer.errors import ConflictError, ValidationError

EXPECTED = {
    "advantage:blessed": 10,
    "advantage:channeling": 10,
    "advantage:compartmentalized-mind": 50,
    "advantage:destiny": 5,
    "advantage:dominance": 20,
    "advantage:higher-purpose": 5,
    "advantage:illuminated": 15,
    "advantage:medium": 10,
    "advantage:mind-control": 50,
    "advantage:mind-probe": 20,
    "advantage:mind-reading": 30,
    "advantage:mind-shield": 4,
    "advantage:mindlink": 5,
    "advantage:modular-abilities": 10,
    "advantage:neutralize": 50,
    "advantage:oracle": 15,
    "advantage:possession": 100,
    "advantage:precognition": 25,
    "advantage:psi-static": 30,
    "advantage:psychometry": 20,
    "advantage:puppet": 5,
    "advantage:racial-memory": 15,
    "advantage:reawakened": 10,
    "advantage:special-rapport": 5,
    "advantage:spirit-empathy": 10,
    "advantage:super-luck": 100,
    "advantage:temporal-inertia": 15,
    "advantage:terror": 30,
    "advantage:true-faith": 15,
    "advantage:visualization": 10,
    "advantage:wild-talent": 20,
    "disadvantage:cursed": -75,
    "disadvantage:destiny": -5,
    "disadvantage:divine-curse": -5,
    "disadvantage:draining": -5,
    "disadvantage:dread": -10,
    "disadvantage:frightens-animals": -10,
    "disadvantage:infectious-attack": -5,
    "disadvantage:lifebane": -10,
    "disadvantage:revulsion": -5,
    "disadvantage:supernatural-features": -1,
    "disadvantage:supersensitive": -15,
    "disadvantage:uncontrollable-appetite": -15,
    "disadvantage:unique": -10,
    "disadvantage:weirdness-magnet": -15,
}


def options(**values: str | int | bool) -> TraitOptions:
    return TraitOptions(parameters=tuple(values.items()))


def compiler() -> CharacterCompiler:
    base, traits = profile_package(PROFILE), mental_spirit_package()
    combined = replace(
        base,
        id="package:test-mental-spirit-traits",
        definitions=base.definitions + traits.definitions,
    )
    policy = CampaignPolicy(
        "policy:mental-spirit-traits",
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


def test_registry_and_inventory_account_for_all_45_distinct_entries() -> None:
    assert {binding.id: binding.point_cost for binding in BINDINGS} == EXPECTED
    rows = {row.id: row for row in inventory().entries if row.id in EXPECTED}
    assert set(rows) == set(EXPECTED)
    assert all(row.blockers == (191,) for row in rows.values())
    assert all(row.evidence == ("tests/test_mental_spirit_traits.py",) for row in rows.values())


@pytest.mark.parametrize(
    ("purchase", "expected"),
    [
        (Purchase(definition_id="advantage:blessed", trait=options(kind="very-blessed")), 20),
        (Purchase(definition_id="advantage:mindlink", trait=options(**{"group-size": 99})), 20),
        (
            Purchase(
                definition_id="advantage:modular-abilities",
                trait=options(framework="cosmic-power", capacity=5, slots=1),
            ),
            50,
        ),
        (Purchase(definition_id="advantage:racial-memory", trait=options(active=True)), 40),
        (Purchase(definition_id="disadvantage:divine-curse", trait=options(value=30)), -30),
        (Purchase(definition_id="disadvantage:dread", trait=options(rarity="common")), -15),
        (
            Purchase(
                definition_id="disadvantage:uncontrollable-appetite",
                trait=options(**{"self-control": 6}),
            ),
            -30,
        ),
    ],
)
def test_variable_costs_are_selected_by_trusted_parameters(
    purchase: Purchase, expected: int
) -> None:
    build, _ = approved(purchase)
    assert (
        next(
            value.cost for value in build.purchases if value.definition_id == purchase.definition_id
        )
        == expected
    )


def test_missing_unknown_and_mutually_exclusive_options_fail_closed() -> None:
    engine = compiler()
    for purchase in (
        Purchase(definition_id="advantage:blessed"),
        Purchase(definition_id="advantage:mindlink", trait=options(**{"group-size": 4})),
        Purchase(
            definition_id="advantage:mind-control",
            trait=TraitOptions(modifiers=("conditioning", "conditioning-only")),
        ),
    ):
        result = engine.compile(gurps_draft(purchase))
        assert result.build is None and "trait.invalid" in {
            error.code for error in result.diagnostics
        }


def test_projection_supplies_concrete_mental_and_spirit_capabilities() -> None:
    build, engine = approved(
        Purchase(definition_id="advantage:mind-shield", amount=4),
        Purchase(definition_id="advantage:compartmentalized-mind", amount=2),
        Purchase(definition_id="advantage:higher-purpose", amount=3),
        Purchase(definition_id="advantage:terror", trait=options(penalty=2)),
        Purchase(definition_id="advantage:medium"),
        Purchase(definition_id="advantage:precognition"),
        Purchase(definition_id="advantage:psi-static"),
    )
    traits = mental_spirit_traits(build, engine.definitions)
    assert traits.mind_shield_bonus() == 4
    assert traits.simultaneous_concentrations() == 3
    assert traits.higher_purpose_bonus(applies=True) == 3
    assert traits.higher_purpose_bonus(applies=False) == 0
    assert traits.terror_penalty() == -2
    assert traits.can_contact("spirits") and traits.can_contact("future")
    assert traits.resists("psi") and not traits.resists("magic")


def world() -> World:
    return World(
        entities=(
            Entity("room", EntityKind.LOCATION, "Room"),
            Entity("a", EntityKind.ACTOR, "Reader", "room"),
            Entity("b", EntityKind.ACTOR, "Subject", "room"),
        ),
        facts=(Fact("surface", "b", "thought", "door"), Fact("secret", "b", "thought", "key")),
        knowledge=(("b", "surface"), ("b", "secret")),
    )


def command(kind: str = "activate", revision: int = 0, identifier: str = "read") -> MentalCommand:
    return MentalCommand.model_validate(
        {
            "id": f"mental-{kind}-{revision}",
            "actor_id": "a",
            "expected_revision": revision,
            "definition_id": "advantage:mind-reading",
            "channel_id": identifier,
            "kind": kind,
        }
    )


def channel(**changes: object) -> MentalChannel:
    return MentalChannel(
        id="read",
        definition_id="advantage:mind-reading",
        actor_id="a",
        target_id="b",
        location_id="room",
        kind="read",
        fact_ids=("surface",),
        actor_score=14,
        actor_roll=10,
        resistance_score=12,
        resistance_roll=11,
        duration_seconds=60,
        fatigue_cost=2,
    ).model_copy(update=changes)


def test_success_spends_cost_reveals_only_authored_facts_and_restarts() -> None:
    build, engine = approved(Purchase(definition_id="advantage:mind-reading"))
    resources = ResourceState(pools=(Pool(id="fp:a", current=10, maximum=10),))
    state, learned, result = apply_mental_use(
        resources,
        world(),
        command(),
        build,
        engine.definitions,
        (channel(),),
        authorized_actor_id="a",
        system=True,
    )
    assert result.outcome == "successful" and result.revealed_fact_ids == ("surface",)
    assert state.pools[0].current == 8
    assert ("a", "surface") in learned.knowledge and ("a", "secret") not in learned.knowledge
    restarted = ResourceState.model_validate_json(state.model_dump_json())
    assert history(restarted)[0].outcome == result
    assert apply_mental_use(
        restarted,
        learned,
        command(),
        build,
        engine.definitions,
        (channel(),),
        authorized_actor_id="a",
        system=True,
    ) == (restarted, learned, result)


def test_resistance_blocking_authority_and_compare_and_set_fail_closed() -> None:
    build, engine = approved(Purchase(definition_id="advantage:mind-reading"))
    resources = ResourceState(pools=(Pool(id="fp:a", current=10, maximum=10),))
    _, _, resisted = apply_mental_use(
        resources,
        world(),
        command(),
        build,
        engine.definitions,
        (channel(actor_roll=13, resistance_roll=8),),
        authorized_actor_id="a",
        system=True,
    )
    assert resisted.outcome == "resisted"
    _, _, blocked = apply_mental_use(
        resources,
        world(),
        command(),
        build,
        engine.definitions,
        (channel(blocked=True),),
        authorized_actor_id="a",
        system=True,
    )
    assert blocked.outcome == "blocked"
    with pytest.raises(ValidationError, match="authority"):
        apply_mental_use(
            resources,
            world(),
            command(),
            build,
            engine.definitions,
            (channel(),),
            authorized_actor_id="b",
            system=True,
        )
    with pytest.raises(ConflictError, match="revision"):
        apply_mental_use(
            resources.model_copy(update={"revision": 1}),
            world(),
            command(),
            build,
            engine.definitions,
            (channel(),),
            authorized_actor_id="a",
            system=True,
        )


def test_persistent_control_can_be_interrupted_before_expiry_once() -> None:
    build, engine = approved(Purchase(definition_id="advantage:mind-control"))
    control = channel(
        id="control",
        definition_id="advantage:mind-control",
        kind="influence",
        fact_ids=(),
        fatigue_cost=0,
    )
    activate = command(identifier="control").model_copy(
        update={"definition_id": "advantage:mind-control"}
    )
    state, current_world, result = apply_mental_use(
        ResourceState(),
        world(),
        activate,
        build,
        engine.definitions,
        (control,),
        authorized_actor_id="a",
        system=True,
    )
    assert result.effect_id in state.active_effect_ids
    assert state.scheduled[0].target_id == result.effect_id and state.scheduled[0].due == 60
    stop = MentalCommand(
        id="stop-control",
        actor_id="a",
        expected_revision=1,
        definition_id="advantage:mind-control",
        channel_id="control",
        kind="interrupt",
    )
    stopped, _, outcome = apply_mental_use(
        state,
        current_world,
        stop,
        build,
        engine.definitions,
        (control,),
        authorized_actor_id="a",
        system=True,
    )
    assert outcome.outcome == "interrupted" and result.effect_id not in stopped.active_effect_ids
    assert stopped.scheduled == ()
