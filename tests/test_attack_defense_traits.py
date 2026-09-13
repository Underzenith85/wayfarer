"""Independent attack/defense trait expectations, Characters 4e B35-161."""

from decimal import Decimal

import pytest
from test_statistics import gurps_draft
from trait_support import approved_build, options, trait_compiler

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase, ValidatedBuild
from wayfarer.engine.character.traits.attack_defense import attack_defense_traits
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.supernatural import inventory
from wayfarer.engine.rules.traits.attack_defense import BINDINGS, PROFILE, RUNTIME_HOOKS
from wayfarer.engine.rules.traits.attack_defense import package as attack_defense_package
from wayfarer.engine.rules.traits.base import TraitOptions
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.engine.simulation.traits.attack_defense import (
    AttackChannel,
    TraitAttackCommand,
    apply_trait_attack,
    history,
)
from wayfarer.engine.world import Entity, EntityKind, World
from wayfarer.errors import ConflictError, ValidationError

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


def compiler() -> CharacterCompiler:
    return trait_compiler(
        "attack-defense-traits", PROFILE, attack_defense_package(), hooks=RUNTIME_HOOKS
    )


def approved(*purchases: Purchase) -> tuple[ValidatedBuild, CharacterCompiler]:
    return approved_build(compiler(), *purchases)


def tolerance_options(structure: str = "living", **forms: bool) -> TraitOptions:
    values: dict[str, str | bool] = {
        "structure": structure,
        "no-blood": False,
        "no-brain": False,
        "no-eyes": False,
        "no-head": False,
        "no-neck": False,
        "no-vitals": False,
    }
    values.update({name.replace("_", "-"): enabled for name, enabled in forms.items()})
    return options(**values)


def test_registry_and_inventory_account_for_all_18_entries() -> None:
    assert {binding.id: binding.point_cost for binding in BINDINGS} == EXPECTED
    rows = {row.id: row for row in inventory().entries if row.id in EXPECTED}
    assert set(rows) == set(EXPECTED)
    assert all(row.blockers == () for row in rows.values())
    assert "tests/test_attack_defense_traits.py" in rows["advantage:injury-tolerance"].evidence


@pytest.mark.parametrize(
    ("purchase", "expected"),
    [
        (Purchase(definition_id="advantage:claws", trait=options(kind="long-talons")), 11),
        (
            Purchase(
                definition_id="advantage:injury-tolerance",
                trait=tolerance_options("diffuse"),
            ),
            100,
        ),
        (
            Purchase(
                definition_id="advantage:injury-tolerance",
                trait=tolerance_options(no_head=True, no_eyes=True, no_neck=True),
            ),
            17,
        ),
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
        Purchase(
            definition_id="advantage:injury-tolerance",
            trait=tolerance_options("unliving"),
        ),
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


@pytest.mark.parametrize(
    ("structure", "forms", "expected"),
    [
        ("living", {"no_blood": True}, ("living", True, False, False, False, False, False)),
        ("living", {"no_brain": True}, ("living", False, True, False, False, False, False)),
        ("living", {"no_eyes": True}, ("living", False, False, True, False, False, False)),
        ("living", {"no_head": True}, ("living", False, True, False, True, False, False)),
        ("living", {"no_neck": True}, ("living", False, False, False, False, True, False)),
        ("living", {"no_vitals": True}, ("living", False, False, False, False, False, True)),
        ("homogeneous", {}, ("homogenous", False, True, False, False, False, True)),
        ("diffuse", {}, ("diffuse", True, True, False, False, False, True)),
    ],
)
def test_all_injury_tolerance_forms_project_exact_anatomy(
    structure: str, forms: dict[str, bool], expected: tuple[object, ...]
) -> None:
    build, engine = approved(
        Purchase(
            definition_id="advantage:injury-tolerance",
            trait=tolerance_options(structure, **forms),
        )
    )
    profile = attack_defense_traits(build, engine.definitions).injury_tolerance_profile()
    assert profile is not None
    assert (
        profile.structure,
        profile.no_blood,
        profile.no_brain,
        profile.no_eyes,
        profile.no_head,
        profile.no_neck,
        profile.no_vitals,
    ) == expected


def test_redundant_or_empty_injury_tolerance_forms_fail_closed() -> None:
    engine = compiler()
    for selected in (
        tolerance_options(),
        tolerance_options("diffuse", no_blood=True),
        tolerance_options(no_head=True, no_brain=True),
    ):
        result = engine.compile(
            gurps_draft(Purchase(definition_id="advantage:injury-tolerance", trait=selected))
        )
        assert result.build is None and "trait.invalid" in {
            error.code for error in result.diagnostics
        }


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
        resistance_roll=13,
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
    assert state.afflictions[0].condition == "stun"
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
