"""Independent movement/form expectations from Characters 4e B34-97/B129-165."""

from dataclasses import replace

import pytest
from test_statistics import gurps_draft, profile_package

from wayfarer.character.compiler import CharacterCompiler, Purchase, ValidatedBuild
from wayfarer.character.movement_forms import movement_forms
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.catalog import CampaignPolicy, CampaignRules, PackagePin, RulesCatalog
from wayfarer.rules.movement_forms import (
    BINDINGS,
    PROFILE,
    RUNTIME_HOOKS,
)
from wayfarer.rules.movement_forms import (
    package as movement_package,
)
from wayfarer.rules.supernatural import inventory
from wayfarer.rules.traits import TraitOptions
from wayfarer.simulation.movement_forms import (
    MovementFormCommand,
    active_forms,
    apply_movement_form,
    visible_forms,
)
from wayfarer.simulation.resources import ResourceState
from wayfarer.world import Entity, EntityKind, Fact, World

EXPECTED_COSTS = {
    "advantage:360-vision": 25,
    "advantage:duplication": 35,
    "advantage:elastic-skin": 20,
    "advantage:altered-time-rate": 100,
    "advantage:alternate-form": 15,
    "advantage:enhanced-move": 20,
    "advantage:enhanced-time-sense": 45,
    "advantage:amphibious": 10,
    "advantage:enhanced-tracking": 5,
    "advantage:extra-arms": 10,
    "advantage:arm-dx": 12,
    "advantage:extra-head": 15,
    "advantage:arm-st": 3,
    "advantage:extra-legs": 5,
    "advantage:extra-mouth": 5,
    "advantage:brachiator": 5,
    "advantage:catfall": 10,
    "advantage:flight": 40,
    "advantage:growth": 10,
    "advantage:clinging": 20,
    "advantage:hermaphromorph": 5,
    "advantage:insubstantiality": 80,
    "advantage:shadow-form": 50,
    "advantage:shapeshifting": 0,
    "advantage:shrinking": 5,
    "advantage:lifting-st": 3,
    "advantage:slippery": 2,
    "advantage:stretching": 6,
    "advantage:morph": 100,
    "advantage:super-climbing": 3,
    "advantage:super-jump": 10,
    "advantage:telekinesis": 5,
    "advantage:payload": 1,
    "advantage:terrain-adaptation": 5,
    "advantage:permeation": 40,
    "advantage:tunneling": 30,
    "advantage:walk-on-air": 20,
    "advantage:walk-on-liquid": 15,
    "disadvantage:horizontal": -10,
    "disadvantage:invertebrate": -20,
    "disadvantage:decreased-time-rate": -100,
    "disadvantage:shadow-form": -20,
    "disadvantage:no-fine-manipulators": -30,
    "disadvantage:no-legs": 0,
    "disadvantage:no-manipulators": -50,
    "disadvantage:semi-upright": -5,
    "disadvantage:sexless": -1,
}


def compiler() -> CharacterCompiler:
    base = profile_package(PROFILE)
    movement = movement_package()
    combined = replace(
        base,
        id="package:test-movement-forms",
        version="1.0.0",
        definitions=base.definitions + movement.definitions,
    )
    policy = CampaignPolicy(
        "policy:movement-forms",
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


def options(**values: str | int | bool) -> TraitOptions:
    return TraitOptions(parameters=tuple(values.items()))


def test_registry_accounts_for_every_bounded_issue_entry_and_source_cost() -> None:
    assert {binding.id: binding.point_cost for binding in BINDINGS} == EXPECTED_COSTS
    assert len(BINDINGS) == 47
    assert len({binding.id for binding in BINDINGS}) == len(BINDINGS)
    rows = {entry.id: entry for entry in inventory().entries if entry.id in EXPECTED_COSTS}
    assert set(rows) == set(EXPECTED_COSTS)
    assert all(233 not in row.blockers for row in rows.values())
    assert all(
        row.blockers == (191,) and row.evidence == ("tests/test_movement_forms.py",)
        for row in rows.values()
    )


@pytest.mark.parametrize(
    ("purchase", "expected"),
    [
        (Purchase(definition_id="advantage:flight"), 40),
        (Purchase(definition_id="advantage:flight", trait=TraitOptions(modifiers=("winged",))), 30),
        (
            Purchase(
                definition_id="advantage:alternate-form",
                trait=options(**{"native-template-cost": 0, "target-template-cost": 100}),
            ),
            105,
        ),
        (
            Purchase(
                definition_id="advantage:morph",
                trait=options(**{"native-template-cost": 0, "target-template-cost": 200}),
            ),
            180,
        ),
        (Purchase(definition_id="advantage:arm-dx", amount=2, trait=options(scope="all-arms")), 32),
        (Purchase(definition_id="advantage:arm-st", amount=2, trait=options(scope="two-arms")), 10),
        (Purchase(definition_id="advantage:extra-legs", trait=options(legs=6)), 20),
        (Purchase(definition_id="advantage:permeation", trait=options(rarity="rare")), 10),
        (Purchase(definition_id="advantage:tunneling", amount=3), 40),
        (Purchase(definition_id="disadvantage:no-legs", trait=options(form="portable")), -30),
    ],
)
def test_variable_and_modified_costs_are_compiler_owned(purchase: Purchase, expected: int) -> None:
    build, _ = approved(purchase)
    assert (
        next(
            entry.cost for entry in build.purchases if entry.definition_id == purchase.definition_id
        )
        == expected
    )


def test_abstract_heading_and_malformed_special_options_fail_closed() -> None:
    engine = compiler()
    heading = engine.compile(gurps_draft(Purchase(definition_id="advantage:shapeshifting")))
    assert heading.build is None
    assert "definition.not_implemented" in {error.code for error in heading.diagnostics}
    missing = engine.compile(gurps_draft(Purchase(definition_id="advantage:enhanced-move")))
    assert missing.build is None
    assert "trait.invalid" in {error.code for error in missing.diagnostics}
    invented = engine.compile(
        gurps_draft(
            Purchase(
                definition_id="advantage:flight",
                trait=TraitOptions(modifiers=("invented",)),
            )
        )
    )
    assert invented.build is None


def test_projection_drives_shared_movement_lifting_falling_and_body_constraints() -> None:
    build, engine = approved(
        Purchase(definition_id="advantage:altered-time-rate", amount=2),
        Purchase(definition_id="advantage:enhanced-move", amount=2, trait=options(mode="ground")),
        Purchase(definition_id="advantage:catfall"),
        Purchase(definition_id="advantage:lifting-st", amount=3),
        Purchase(definition_id="advantage:super-climbing", amount=2),
        Purchase(definition_id="advantage:super-jump", amount=2),
        Purchase(definition_id="disadvantage:no-fine-manipulators"),
    )
    traits = movement_forms(build, engine.definitions)
    assert traits.actions_per_turn() == 3
    assert traits.top_move(5) == 20
    assert traits.effective_lifting_st(10) == 13
    assert traits.fall_distance(12) == 7
    assert traits.climbing_bonus() == 4 and traits.jump_multiplier() == 4
    assert traits.manipulator_penalty() == -6


def test_projection_rejects_a_definition_whose_pinned_runtime_metadata_changed() -> None:
    build, engine = approved(Purchase(definition_id="advantage:flight"))
    altered = dict(engine.definitions)
    altered["advantage:flight"] = replace(altered["advantage:flight"], point_cost=1)
    # Point cost is part of the definition but runtime identity is its exact metadata and hook.
    assert movement_forms(build, altered).has("advantage:flight")
    altered["advantage:flight"] = replace(altered["advantage:flight"], trait_rules=None)
    assert not movement_forms(build, altered).has("advantage:flight")


def world() -> World:
    return World(
        entities=(
            Entity("room", EntityKind.LOCATION, "Room"),
            Entity("a", EntityKind.ACTOR, "Shifter", "room"),
            Entity("b", EntityKind.ACTOR, "Observer", "room"),
        ),
        facts=(Fact("seen", "a", "visible", "shifter"),),
        knowledge=(("b", "seen"),),
    )


def command(
    kind: str, revision: int, identifier: str = "advantage:alternate-form"
) -> MovementFormCommand:
    return MovementFormCommand.model_validate(
        {
            "id": f"form-{kind}-{revision}",
            "actor_id": "a",
            "expected_revision": revision,
            "kind": kind,
            "definition_id": identifier,
        }
    )


def test_transformation_lifecycle_is_persisted_restart_safe_and_visibility_filtered() -> None:
    build, engine = approved(
        Purchase(
            definition_id="advantage:alternate-form",
            trait=options(**{"native-template-cost": 0, "target-template-cost": 0}),
        )
    )
    started, pending = apply_movement_form(
        ResourceState(),
        world(),
        command("start", 0),
        build,
        engine.definitions,
        authorized_actor_id="a",
        system=True,
    )
    assert pending.outcome == "concentrating" and pending.effect.ready_at == 10
    assert apply_movement_form(
        started,
        world(),
        command("start", 0),
        build,
        engine.definitions,
        authorized_actor_id="a",
        system=True,
    ) == (started, pending)
    with pytest.raises(ValidationError, match="not ready"):
        apply_movement_form(
            started,
            world(),
            command("resolve", 1),
            build,
            engine.definitions,
            authorized_actor_id="a",
            system=True,
        )
    restarted = ResourceState.model_validate_json(started.model_dump_json()).model_copy(
        update={"game_time": 10}
    )
    active, outcome = apply_movement_form(
        restarted,
        world(),
        command("resolve", 1),
        build,
        engine.definitions,
        authorized_actor_id="a",
        system=True,
    )
    assert outcome.outcome == "active" and active_forms(active) == (outcome.effect,)
    assert visible_forms(active, world(), "b") == (outcome.effect,)
    hidden_world = replace(world(), knowledge=())
    assert visible_forms(active, hidden_world, "b") == ()
    cancelled, result = apply_movement_form(
        active,
        world(),
        command("cancel", 2),
        build,
        engine.definitions,
        authorized_actor_id="a",
        system=True,
    )
    assert result.outcome == "cancelled" and active_forms(cancelled) == ()


def test_transformation_requires_authority_build_and_compare_and_set_revision() -> None:
    build, engine = approved(Purchase(definition_id="advantage:growth"))
    value = command("start", 0, "advantage:growth")
    with pytest.raises(ValidationError, match="authority"):
        apply_movement_form(
            ResourceState(),
            world(),
            value,
            build,
            engine.definitions,
            authorized_actor_id="b",
            system=True,
        )
    with pytest.raises(ConflictError):
        apply_movement_form(
            ResourceState(revision=1),
            world(),
            value,
            build,
            engine.definitions,
            authorized_actor_id="a",
            system=True,
        )
    plain, plain_engine = approved()
    with pytest.raises(ValidationError, match="approved build"):
        apply_movement_form(
            ResourceState(),
            world(),
            value,
            plain,
            plain_engine.definitions,
            authorized_actor_id="a",
            system=True,
        )
