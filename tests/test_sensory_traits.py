"""Independent senses/communication expectations, Characters 4e B41-96."""

from dataclasses import replace

import pytest
from test_statistics import gurps_draft, profile_package

from wayfarer.character.compiler import CharacterCompiler, Purchase, ValidatedBuild
from wayfarer.character.sensory_traits import sensory_traits
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.catalog import CampaignPolicy, CampaignRules, PackagePin, RulesCatalog
from wayfarer.rules.sensory_traits import BINDINGS, PROFILE, RUNTIME_HOOKS
from wayfarer.rules.sensory_traits import package as sensory_package
from wayfarer.rules.supernatural import inventory
from wayfarer.rules.traits import TraitOptions
from wayfarer.simulation.resources import ResourceState
from wayfarer.simulation.sensory_traits import (
    SensoryChannel,
    SensoryCommand,
    apply_sensory_use,
    history,
)
from wayfarer.world import Entity, EntityKind, Fact, World

EXPECTED = {
    "advantage:detect": 10,
    "advantage:digital-mind": 5,
    "advantage:discriminatory-hearing": 15,
    "advantage:discriminatory-smell": 15,
    "advantage:discriminatory-taste": 10,
    "advantage:chameleon": 5,
    "advantage:clairsentience": 50,
    "advantage:hyperspectral-vision": 25,
    "advantage:dark-vision": 25,
    "advantage:infravision": 10,
    "advantage:scanning-sense": 20,
    "advantage:see-invisible": 15,
    "advantage:invisibility": 40,
    "advantage:sensitive-touch": 10,
    "advantage:silence": 5,
    "advantage:speak-underwater": 5,
    "advantage:speak-with-animals": 25,
    "advantage:speak-with-plants": 15,
    "advantage:microscopic-vision": 5,
    "advantage:mimicry": 10,
    "advantage:subsonic-hearing": 5,
    "advantage:subsonic-speech": 10,
    "advantage:obscure": 2,
    "advantage:telecommunication": 10,
    "advantage:telescopic-vision": 5,
    "advantage:parabolic-hearing": 4,
    "advantage:penetrating-vision": 10,
    "advantage:ultrahearing": 5,
    "advantage:ultrasonic-speech": 10,
    "advantage:ultravision": 10,
    "advantage:protected-sense": 5,
    "advantage:vibration-sense": 10,
}


def options(**values: str | int | bool) -> TraitOptions:
    return TraitOptions(parameters=tuple(values.items()))


def compiler() -> CharacterCompiler:
    base, senses = profile_package(PROFILE), sensory_package()
    combined = replace(
        base,
        id="package:test-sensory-traits",
        definitions=base.definitions + senses.definitions,
    )
    policy = CampaignPolicy(
        "policy:sensory-traits",
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


def test_registry_and_inventory_account_for_all_32_entries() -> None:
    assert {binding.id: binding.point_cost for binding in BINDINGS} == EXPECTED
    rows = {row.id: row for row in inventory().entries if row.id in EXPECTED}
    assert set(rows) == set(EXPECTED)
    assert all(row.blockers == (191,) for row in rows.values())
    assert all(row.evidence == ("tests/test_sensory_traits.py",) for row in rows.values())


@pytest.mark.parametrize(
    ("purchase", "expected"),
    [
        (Purchase(definition_id="advantage:detect", trait=options(rarity="rare")), 5),
        (
            Purchase(
                definition_id="advantage:detect",
                trait=TraitOptions(parameters=(("rarity", "common"),), modifiers=("precise",)),
            ),
            40,
        ),
        (Purchase(definition_id="advantage:scanning-sense", trait=options(kind="para-radar")), 40),
        (Purchase(definition_id="advantage:telecommunication", trait=options(kind="telesend")), 30),
        (
            Purchase(definition_id="advantage:speak-with-animals", trait=options(scope="species")),
            10,
        ),
        (Purchase(definition_id="advantage:infravision", trait=options(native=True)), 0),
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


def test_missing_or_unknown_options_fail_closed() -> None:
    engine = compiler()
    for purchase in (
        Purchase(definition_id="advantage:detect"),
        Purchase(definition_id="advantage:telecommunication", trait=options(kind="magic")),
        Purchase(
            definition_id="advantage:invisibility", trait=TraitOptions(modifiers=("invented",))
        ),
    ):
        result = engine.compile(gurps_draft(purchase))
        assert result.build is None and "trait.invalid" in {
            error.code for error in result.diagnostics
        }


def test_projection_supplies_concrete_shared_sense_and_communication_effects() -> None:
    build, engine = approved(
        Purchase(definition_id="advantage:dark-vision"),
        Purchase(definition_id="advantage:telescopic-vision", amount=3),
        Purchase(definition_id="advantage:parabolic-hearing", amount=2),
        Purchase(definition_id="advantage:chameleon", amount=2),
        Purchase(definition_id="advantage:silence", amount=3),
        Purchase(definition_id="advantage:protected-sense", trait=options(sense="vision")),
        Purchase(definition_id="advantage:telecommunication", trait=options(kind="radio")),
    )
    traits = sensory_traits(build, engine.definitions)
    assert traits.visual_darkness(-9) == 0
    assert traits.aimed_vision_bonus() == 6
    assert traits.hearing_range_multiplier() == 4
    assert traits.camouflage_bonus(moving=False) == 4
    assert traits.camouflage_bonus(moving=True) == 2
    assert traits.silence_penalty() == -3 and traits.protected("vision")
    assert traits.can_communicate("radio") and not traits.can_communicate("laser")


def world() -> World:
    return World(
        entities=(
            Entity("room", EntityKind.LOCATION, "Room"),
            Entity("a", EntityKind.ACTOR, "Observer", "room"),
            Entity("b", EntityKind.ACTOR, "Subject", "room"),
        ),
        facts=(Fact("presence", "b", "presence", "there"), Fact("secret", "b", "secret", "hidden")),
        knowledge=(("b", "secret"),),
    )


def command(kind: str = "observe", revision: int = 0) -> SensoryCommand:
    return SensoryCommand.model_validate(
        {
            "id": f"sense-{kind}-{revision}",
            "actor_id": "a",
            "expected_revision": revision,
            "definition_id": "advantage:detect",
            "channel_id": "detect",
            "kind": kind,
        }
    )


def channel(**changes: object) -> SensoryChannel:
    return SensoryChannel(
        id="detect",
        definition_id="advantage:detect",
        actor_id="a",
        target_id="b",
        location_id="room",
        fact_ids=("presence",),
    ).model_copy(update=changes)


def test_authored_detection_is_private_idempotent_and_restart_safe() -> None:
    build, engine = approved(
        Purchase(definition_id="advantage:detect", trait=options(rarity="rare"))
    )
    state, learned, result = apply_sensory_use(
        ResourceState(),
        world(),
        command(),
        build,
        engine.definitions,
        (channel(),),
        authorized_actor_id="a",
        system=True,
    )
    assert result.outcome == "observed" and result.revealed_fact_ids == ("presence",)
    assert ("a", "presence") in learned.knowledge and ("a", "secret") not in learned.knowledge
    restarted = ResourceState.model_validate_json(state.model_dump_json())
    assert history(restarted)[0].outcome == result
    assert apply_sensory_use(
        restarted,
        learned,
        command(),
        build,
        engine.definitions,
        (channel(),),
        authorized_actor_id="a",
        system=True,
    ) == (restarted, learned, result)


def test_channels_enforce_authority_context_resistance_and_compare_and_set() -> None:
    build, engine = approved(
        Purchase(definition_id="advantage:detect", trait=options(rarity="rare"))
    )
    with pytest.raises(ValidationError, match="authority"):
        apply_sensory_use(
            ResourceState(),
            world(),
            command(),
            build,
            engine.definitions,
            (channel(),),
            authorized_actor_id="b",
            system=True,
        )
    with pytest.raises(ConflictError):
        apply_sensory_use(
            ResourceState(revision=1),
            world(),
            command(),
            build,
            engine.definitions,
            (channel(),),
            authorized_actor_id="a",
            system=True,
        )
    _, _, resisted = apply_sensory_use(
        ResourceState(),
        world(),
        command(),
        build,
        engine.definitions,
        (channel(resistant=True),),
        authorized_actor_id="a",
        system=True,
    )
    assert resisted.outcome == "resisted" and not resisted.revealed_fact_ids
    moved = replace(
        world(),
        entities=(
            world().entities[0],
            replace(world().entities[1], location_id=None),
            world().entities[2],
        ),
    )
    with pytest.raises(ValidationError, match="context changed"):
        apply_sensory_use(
            ResourceState(),
            moved,
            command(),
            build,
            engine.definitions,
            (channel(),),
            authorized_actor_id="a",
            system=True,
        )
