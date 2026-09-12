"""Independent attack/defense trait expectations, Characters 4e B35-161."""

from dataclasses import replace
from decimal import Decimal

import pytest
from test_statistics import gurps_draft, profile_package

from wayfarer.character.attack_defense_traits import attack_defense_traits
from wayfarer.character.compiler import CharacterCompiler, Purchase, ValidatedBuild
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.attack_defense_traits import BINDINGS, PROFILE, RUNTIME_HOOKS
from wayfarer.rules.attack_defense_traits import package as attack_defense_package
from wayfarer.rules.catalog import CampaignPolicy, CampaignRules, PackagePin, RulesCatalog
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.injury_types import InjuryStatus
from wayfarer.rules.supernatural import inventory
from wayfarer.rules.traits import TraitOptions
from wayfarer.simulation.attack_defense_traits import (
    AttackChannel,
    TraitAttackCommand,
    apply_trait_attack,
    history,
)
from wayfarer.simulation.resources import Pool, ResourceState
from wayfarer.world import Entity, EntityKind, World

EXPECTED = {
    "advantage:affliction": 10,
    "advantage:binding": 2,
    "advantage:claws": 3,
    "advantage:constriction-attack": 15,
    "advantage:damage-resistance": 5,
    "advantage:injury-tolerance": 20,
    "advantage:innate-attack": 5,
    "advantage:nictitating-membrane": 1,
    "advantage:spines": 1,
    "advantage:striker": 5,
    "advantage:striking-st": 5,
    "advantage:supernatural-durability": 150,
    "advantage:teeth": 0,
    "advantage:unkillable": 50,
    "advantage:vampiric-bite": 30,
    "disadvantage:fragile": -5,
    "disadvantage:vulnerability": -10,
    "disadvantage:weak-bite": -2,
}


def options(**values: str | int | bool) -> TraitOptions:
    return TraitOptions(parameters=tuple(values.items()))


def compiler() -> CharacterCompiler:
    base, traits = profile_package(PROFILE), attack_defense_package()
    combined = replace(
        base,
        id="package:test-attack-defense-traits",
        definitions=base.definitions + traits.definitions,
    )
    policy = CampaignPolicy(
        "policy:attack-defense-traits",
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


def test_registry_and_inventory_account_for_all_18_entries() -> None:
    assert {binding.id: binding.point_cost for binding in BINDINGS} == EXPECTED
    rows = {row.id: row for row in inventory().entries if row.id in EXPECTED}
    assert set(rows) == set(EXPECTED)
    assert all(191 in row.blockers and 237 not in row.blockers for row in rows.values())
    assert all(row.evidence == ("tests/test_attack_defense_traits.py",) for row in rows.values())


@pytest.mark.parametrize(
    ("purchase", "expected"),
    [
        (Purchase(definition_id="advantage:claws", trait=options(kind="long-talons")), 11),
        (Purchase(definition_id="advantage:injury-tolerance", trait=options(kind="diffuse")), 100),
        (
            Purchase(
                definition_id="advantage:innate-attack",
                amount=3,
                trait=options(**{"damage-type": "imp"}),
            ),
            24,
        ),
        (Purchase(definition_id="advantage:spines", trait=options(kind="long")), 3),
        (Purchase(definition_id="advantage:striker", trait=options(**{"damage-type": "cut"})), 7),
        (Purchase(definition_id="advantage:teeth", trait=options(kind="fangs")), 2),
        (Purchase(definition_id="disadvantage:fragile", trait=options(kind="unnatural")), -50),
        (
            Purchase(
                definition_id="disadvantage:vulnerability",
                trait=options(rarity="very-common", multiplier=4),
            ),
            -80,
        ),
    ],
)
def test_variable_costs_are_trusted(purchase: Purchase, expected: int) -> None:
    build, _ = approved(purchase)
    assert (
        next(
            value.cost for value in build.purchases if value.definition_id == purchase.definition_id
        )
        == expected
    )


def test_invalid_parameters_and_modifiers_fail_closed() -> None:
    engine = compiler()
    for purchase in (
        Purchase(definition_id="advantage:claws"),
        Purchase(definition_id="advantage:teeth", trait=options(kind="venomous")),
        Purchase(
            definition_id="advantage:damage-resistance",
            trait=TraitOptions(modifiers=("invented",)),
        ),
    ):
        result = engine.compile(gurps_draft(purchase))
        assert result.build is None and "trait.invalid" in {
            error.code for error in result.diagnostics
        }


def test_projection_feeds_defense_damage_and_survival_values() -> None:
    build, engine = approved(
        Purchase(definition_id="advantage:damage-resistance", amount=4),
        Purchase(definition_id="advantage:nictitating-membrane", amount=2),
        Purchase(definition_id="advantage:striking-st", amount=3),
        Purchase(definition_id="advantage:injury-tolerance", trait=options(kind="unliving")),
        Purchase(definition_id="advantage:unkillable", amount=2),
        Purchase(
            definition_id="disadvantage:vulnerability",
            trait=options(rarity="very-common", multiplier=3),
        ),
    )
    traits = attack_defense_traits(build, engine.definitions)
    assert traits.damage_resistance() == 4 and traits.damage_resistance(eyes=True) == 2
    assert traits.striking_st(11) == 14
    assert traits.injury_tolerance() == "unliving"
    assert traits.injury_tolerance_profile() is not None
    assert traits.injury_multiplier("very-common") == 3
    assert traits.death_thresholds_ignored() == 2


def world() -> World:
    return World(
        entities=(
            Entity("room", EntityKind.LOCATION, "Room"),
            Entity("a", EntityKind.ACTOR, "Attacker", "room"),
            Entity("b", EntityKind.ACTOR, "Target", "room"),
        )
    )


def resources(*, attacker_hp: int = 10) -> ResourceState:
    return ResourceState(
        pools=(
            Pool(
                id="hp:a",
                current=attacker_hp,
                maximum=10,
                injury=InjuryStatus(profile_id=PROFILE),
            ),
            Pool(
                id="hp:b",
                current=10,
                maximum=10,
                injury=InjuryStatus(profile_id=PROFILE),
            ),
        )
    )


def command(
    definition_id: str = "advantage:innate-attack", revision: int = 0
) -> TraitAttackCommand:
    return TraitAttackCommand(
        id="trait-hit",
        actor_id="a",
        expected_revision=revision,
        definition_id=definition_id,
        channel_id="attack",
    )


def channel(definition_id: str = "advantage:innate-attack", **changes: object) -> AttackChannel:
    return AttackChannel(
        id="attack",
        definition_id=definition_id,
        attacker_id="a",
        target_id="b",
        location_id="room",
        kind="damage",
        basic_damage=6,
        damage_type="burn",
        armor_divisor=Decimal(1),
    ).model_copy(update=changes)


def test_damage_uses_target_build_dr_and_vulnerability_in_shared_injury_reducer() -> None:
    attacker, engine = approved(
        Purchase(
            definition_id="advantage:innate-attack",
            trait=options(**{"damage-type": "burn"}),
        )
    )
    target, _ = approved(
        Purchase(definition_id="advantage:damage-resistance", amount=2),
        Purchase(
            definition_id="disadvantage:vulnerability",
            trait=options(rarity="very-common", multiplier=2),
        ),
    )
    state, result = apply_trait_attack(
        resources(),
        world(),
        command(),
        attacker,
        target,
        engine.definitions,
        (channel(),),
        target_ht=12,
        rng=RecordedDice([3, 3, 3]),
        authorized_actor_id="a",
        system=True,
    )
    assert result.injury is not None and result.injury.injury == 10
    assert next(pool for pool in state.pools if pool.id == "hp:b").current == 0
    restarted = ResourceState.model_validate_json(state.model_dump_json())
    assert apply_trait_attack(
        restarted,
        world(),
        command(),
        attacker,
        target,
        engine.definitions,
        (channel(),),
        target_ht=12,
        rng=RecordedDice([]),
        authorized_actor_id="a",
        system=True,
    ) == (restarted, result)


def test_affliction_resistance_duration_authority_and_cas() -> None:
    attacker, engine = approved(
        Purchase(definition_id="advantage:affliction", trait=options(effect="stun"))
    )
    target, _ = approved()
    affliction = channel(
        definition_id="advantage:affliction",
        kind="affliction",
        basic_damage=0,
        attack_score=14,
        attack_roll=10,
        resistance_score=12,
        resistance_roll=11,
        duration_seconds=60,
    )
    state, result = apply_trait_attack(
        resources(),
        world(),
        command("advantage:affliction"),
        attacker,
        target,
        engine.definitions,
        (affliction,),
        target_ht=12,
        rng=RecordedDice([]),
        authorized_actor_id="a",
        system=True,
    )
    assert result.outcome == "applied" and result.effect_id in state.active_effect_ids
    assert state.scheduled[0].due == 60 and history(state)[0].outcome == result
    with pytest.raises(ValidationError, match="authority"):
        apply_trait_attack(
            resources(),
            world(),
            command("advantage:affliction"),
            attacker,
            target,
            engine.definitions,
            (affliction,),
            target_ht=12,
            rng=RecordedDice([]),
            authorized_actor_id="b",
            system=True,
        )
    with pytest.raises(ConflictError, match="revision"):
        apply_trait_attack(
            resources().model_copy(update={"revision": 1}),
            world(),
            command("advantage:affliction"),
            attacker,
            target,
            engine.definitions,
            (affliction,),
            target_ht=12,
            rng=RecordedDice([]),
            authorized_actor_id="a",
            system=True,
        )


def test_unkillable_changes_the_shared_injury_death_state() -> None:
    attacker, engine = approved(
        Purchase(
            definition_id="advantage:innate-attack",
            trait=options(**{"damage-type": "burn"}),
        )
    )
    target, _ = approved(Purchase(definition_id="advantage:unkillable"))
    state, result = apply_trait_attack(
        resources(),
        world(),
        command(),
        attacker,
        target,
        engine.definitions,
        (channel(basic_damage=60),),
        target_ht=12,
        rng=RecordedDice([]),
        authorized_actor_id="a",
        system=True,
    )
    status = next(pool for pool in state.pools if pool.id == "hp:b").injury
    assert result.injury is not None and status is not None
    assert not status.dead and status.unconscious
