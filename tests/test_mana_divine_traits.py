"""Independent mana/divine expectations, Characters B66-68/B77/B143/B235."""

from dataclasses import replace

import pytest
from test_statistics import gurps_draft, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase, ValidatedBuild
from wayfarer.engine.character.traits.mana_divine import mana_divine_traits
from wayfarer.engine.rules.catalog import CampaignPolicy, CampaignRules, PackagePin, RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.supernatural import inventory
from wayfarer.engine.rules.traits.base import TraitOptions
from wayfarer.engine.rules.traits.mana_divine import BINDINGS, PROFILE, RUNTIME_HOOKS
from wayfarer.engine.rules.traits.mana_divine import package as mana_package
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.recovery import FatigueStatus
from wayfarer.engine.simulation.magic.spells import SpellCommand, SpellContext, apply_spell, latest
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.engine.simulation.traits.mana_divine import (
    ManaField,
    ManaFieldCommand,
    apply_mana_field,
    apply_spell_context,
    effective_mana,
    history,
)
from wayfarer.engine.world import Entity, EntityKind, World
from wayfarer.errors import ConflictError, ValidationError

EXPECTED = {
    "advantage:magery": 10,
    "advantage:magic-resistance": 2,
    "advantage:mana-damper": 10,
    "advantage:mana-enhancer": 50,
    "advantage:power-investiture": 10,
    "disadvantage:magic-susceptibility": -3,
}


def options(
    *,
    parameters: tuple[tuple[str, str | int | bool], ...] = (),
    modifiers: tuple[str, ...] = (),
) -> TraitOptions:
    return TraitOptions(parameters=parameters, modifiers=modifiers)


def magery_options(
    *, zero_only: bool = False, college: str = "all", modifiers: tuple[str, ...] = ()
) -> TraitOptions:
    return options(parameters=(("zero-only", zero_only), ("college", college)), modifiers=modifiers)


def compiler() -> CharacterCompiler:
    base, mana = profile_package(PROFILE), mana_package()
    combined = replace(
        base,
        id="package:test-mana-divine-traits",
        definitions=base.definitions + mana.definitions,
    )
    policy = CampaignPolicy(
        "policy:mana-divine-traits",
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


def test_registry_and_inventory_account_for_exact_family() -> None:
    assert {binding.id: binding.point_cost for binding in BINDINGS} == EXPECTED
    rows = {row.id: row for row in inventory().entries if row.id in EXPECTED}
    assert set(rows) == set(EXPECTED)
    assert all(row.blockers == (191,) for row in rows.values())
    assert all(row.evidence == ("tests/test_mana_divine_traits.py",) for row in rows.values())


def test_pricing_variants_and_build_projection() -> None:
    zero, _ = approved(
        Purchase(definition_id="advantage:magery", trait=magery_options(zero_only=True))
    )
    limited, engine = approved(
        Purchase(
            definition_id="advantage:magery",
            amount=3,
            trait=magery_options(college="fire", modifiers=("one-college",)),
        )
    )
    resistance, _ = approved(
        Purchase(
            definition_id="advantage:magic-resistance",
            amount=3,
            trait=options(modifiers=("improved",)),
        )
    )
    assert next(p.cost for p in zero.purchases if p.definition_id == "advantage:magery") == 5
    assert next(p.cost for p in limited.purchases if p.definition_id == "advantage:magery") == 23
    assert (
        next(
            p.cost for p in resistance.purchases if p.definition_id == "advantage:magic-resistance"
        )
        == 15
    )
    traits = mana_divine_traits(limited, engine.definitions)
    assert traits.magery_bonus(college="fire") == 3
    assert traits.magery_bonus(college="water") == 0


def test_runtime_conditions_resistance_divinity_visibility_and_mana_steps() -> None:
    build, engine = approved(
        Purchase(
            definition_id="advantage:magery",
            amount=2,
            trait=magery_options(modifiers=("solitary",)),
        ),
        Purchase(definition_id="advantage:power-investiture", amount=3),
        Purchase(definition_id="disadvantage:magic-susceptibility", amount=2),
        Purchase(definition_id="advantage:mana-enhancer", amount=2),
    )
    traits = mana_divine_traits(build, engine.definitions)
    assert traits.magery_bonus(college="fire", sapient_nearby=1) == -1
    assert traits.magic_casting_modifier() == 2
    assert traits.magic_resistance_modifier() == -2
    assert traits.power_investiture_bonus(deity_matches=True, pact_kept=True) == 3
    assert traits.power_investiture_bonus(deity_matches=True, pact_kept=False) == 0
    assert traits.effective_mana("normal") == "very-high"
    assert set(traits.visible_traits(viewer_is_mage=True, targeted_by_spell=False)) == {
        "advantage:magery",
        "advantage:mana-enhancer",
        "disadvantage:magic-susceptibility",
    }


@pytest.mark.parametrize(
    "purchases",
    [
        (
            Purchase(
                definition_id="advantage:magery",
                amount=2,
                trait=magery_options(zero_only=True),
            ),
        ),
        (
            Purchase(
                definition_id="advantage:magery",
                trait=magery_options(college="all", modifiers=("one-college",)),
            ),
        ),
        (
            Purchase(
                definition_id="advantage:mana-enhancer",
                trait=options(modifiers=("area-effect-1", "area-effect-2")),
            ),
        ),
        (
            Purchase(definition_id="advantage:magery", trait=magery_options()),
            Purchase(definition_id="advantage:magic-resistance"),
        ),
        (
            Purchase(definition_id="advantage:magic-resistance"),
            Purchase(definition_id="disadvantage:magic-susceptibility"),
        ),
    ],
)
def test_unsupported_levels_options_and_combinations_fail_closed(
    purchases: tuple[Purchase, ...],
) -> None:
    assert compiler().compile(gurps_draft(*purchases)).build is None


def world() -> World:
    return World(
        entities=(
            Entity("room", EntityKind.LOCATION, "Room"),
            Entity("a", EntityKind.ACTOR, "Source", "room"),
            Entity("b", EntityKind.ACTOR, "Subject", "room"),
        )
    )


def test_switchable_area_field_duration_deactivation_retry_and_restart() -> None:
    build, engine = approved(
        Purchase(
            definition_id="advantage:mana-damper",
            trait=options(modifiers=("area-effect-1", "switchable")),
        )
    )
    field = ManaField(
        id="field",
        definition_id="advantage:mana-damper",
        actor_id="a",
        location_id="room",
        duration_seconds=10,
    )
    command = ManaFieldCommand(
        id="activate", actor_id="a", expected_revision=0, field_id="field", action="activate"
    )
    state, outcome = apply_mana_field(
        ResourceState(),
        world(),
        command,
        build,
        engine.definitions,
        (field,),
        authorized_actor_id="a",
        system=True,
    )
    assert outcome.action == "activated" and outcome.expires_at == 10
    assert (
        effective_mana(
            state,
            world(),
            {"a": build},
            engine.definitions,
            (field,),
            actor_id="b",
            location_id="room",
            base="normal",
        )
        == "low"
    )
    restarted = ResourceState.model_validate_json(state.model_dump_json())
    assert apply_mana_field(
        restarted,
        world(),
        command,
        build,
        engine.definitions,
        (field,),
        authorized_actor_id="a",
        system=True,
    ) == (restarted, outcome)
    stopped, stopped_outcome = apply_mana_field(
        restarted,
        world(),
        ManaFieldCommand(
            id="stop", actor_id="a", expected_revision=1, field_id="field", action="deactivate"
        ),
        build,
        engine.definitions,
        (field,),
        authorized_actor_id="a",
        system=True,
    )
    assert stopped_outcome.action == "deactivated" and not stopped.scheduled
    assert history(stopped)[0].outcome == outcome


def test_field_authority_cas_and_non_switchable_commands_fail_closed() -> None:
    build, engine = approved(Purchase(definition_id="advantage:mana-enhancer"))
    field = ManaField(
        id="field",
        definition_id="advantage:mana-enhancer",
        actor_id="a",
        location_id="room",
    )
    command = ManaFieldCommand(
        id="activate", actor_id="a", expected_revision=0, field_id="field", action="activate"
    )
    with pytest.raises(ValidationError, match="authority"):
        apply_mana_field(
            ResourceState(),
            world(),
            command,
            build,
            engine.definitions,
            (field,),
            authorized_actor_id="b",
            system=True,
        )
    with pytest.raises(ConflictError, match="revision"):
        apply_mana_field(
            ResourceState(revision=1),
            world(),
            command,
            build,
            engine.definitions,
            (field,),
            authorized_actor_id="a",
            system=True,
        )
    with pytest.raises(ValidationError, match="not switchable"):
        apply_mana_field(
            ResourceState(),
            world(),
            command,
            build,
            engine.definitions,
            (field,),
            authorized_actor_id="a",
            system=True,
        )


def test_existing_spell_service_consumes_projected_magery_and_mana() -> None:
    build, engine = approved(
        Purchase(definition_id="advantage:magery", amount=2, trait=magery_options())
    )
    traits = mana_divine_traits(build, engine.definitions)
    context = SpellContext(
        profile_id=PROFILE,
        execution_version=2,
        build_revision=build.revision,
        skill=12,
        magery=-1,
        learned=("light",),
        target_id="b",
    )
    adapted = apply_spell_context(context, traits, college="light-darkness", mana="low")
    assert isinstance(adapted, SpellContext)
    state = ResourceState(
        pools=(
            Pool(id="hp:a", current=10, maximum=10, injury=InjuryStatus(profile_id=PROFILE)),
            Pool(id="fp:a", current=10, maximum=10, fatigue=FatigueStatus(profile_id=PROFILE)),
        )
    )
    started, result = apply_spell(
        state,
        SpellCommand(
            id="cast",
            actor_id="a",
            expected_revision=0,
            kind="start",
            spell_id="light",
            cast_id="c",
        ),
        adapted,
        rng=RecordedDice([]),
        system=True,
    )
    assert result.outcome == "casting"
    assert latest(started)["c"].skill == 9
