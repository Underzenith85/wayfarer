"""Characters third printing B61/B102/B106/B109: approved composed attack oracles."""

import pytest
from test_attack_defense_traits import approved, command, compiler, resources, world
from test_statistics import gurps_draft
from trait_support import options

from wayfarer.engine.character.compiler import Purchase, ValidatedBuild
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.modifiers import LimitationParameters, ModifierSelection
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.traits.attack_defense import TraitAttackOutcome
from wayfarer.engine.simulation.traits.composed_attacks import (
    AttackCompositionContext,
    apply_composed_attack,
)
from wayfarer.errors import ConflictError, ValidationError


def attacker(*modifiers: ModifierSelection, dx: int = 14, kind: str = "burn") -> ValidatedBuild:
    engine = compiler()
    purchase = Purchase(
        definition_id="advantage:innate-attack",
        amount=2,
        trait=options(**{"damage-type": kind}).model_copy(update={"attack_modifiers": modifiers}),
    )
    draft = gurps_draft(purchase)
    draft = draft.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": dx}) if p.definition_id == "attribute:dx" else p
                for p in draft.purchases
            )
        }
    )
    result = engine.compile(draft)
    assert result.build, result.diagnostics
    return result.build


def pick(identifier: str, *, option: str | None = None, level: int = 1) -> ModifierSelection:
    return ModifierSelection(definition_id="modifier:" + identifier, option=option, level=level)


def resolve(
    build: ValidatedBuild,
    *,
    dice: list[int] | None = None,
    dr: int = 0,
    distance: int = 2,
    aim: int = 0,
    defense: str = "none",
    context: AttackCompositionContext | None = None,
    state: ResourceState | None = None,
) -> tuple[ResourceState, TraitAttackOutcome]:
    target, engine = approved(
        *((Purchase(definition_id="advantage:damage-resistance", amount=dr),) if dr else ())
    )
    initial = state or resources()
    context = context or AttackCompositionContext.model_validate(
        {
            "target_id": "b",
            "location_id": "room",
            "distance_yards": distance,
            "aim_seconds": aim,
            "defense": defense,
        }
    )
    return apply_composed_attack(
        initial,
        world(),
        command(revision=initial.revision),
        build,
        target,
        engine.definitions,
        context,
        rng=RecordedDice(dice if dice is not None else [3] * 40),
        authorized_actor_id="a",
        system=True,
    )


def hp(state: ResourceState) -> int:
    return next(p.current for p in state.pools if p.id == "hp:b")


def test_approved_armor_divisor_changes_actual_penetration_and_records_damage_dice() -> None:
    build = attacker(pick("enhancement:armor-divisor", option="2"))
    state, result = resolve(build, dr=6, dice=[3, 3, 3, 3, 3])
    assert hp(state) == 7 and result.injury and result.injury.penetration == 3
    assert result.damage_dice == (3, 3)
    assert result.modifier_profile and result.modifier_profile.armor_divisor == 2
    plain, _ = resolve(attacker(), dr=6, dice=[3, 3, 3, 3, 3])
    assert hp(plain) == 10


def test_increased_range_changes_both_printed_ranges_and_half_damage_boundary() -> None:
    build = attacker(pick("enhancement:increased-range"), dx=20)
    at, full = resolve(build, distance=20, aim=3, dice=[3, 3, 3, 3, 3, 2, 2, 2])
    beyond, half = resolve(build, distance=21, aim=3, dice=[3, 3, 3, 3, 3])
    assert hp(at) == 4 and hp(beyond) == 7
    assert full.modifier_profile and (
        full.modifier_profile.half_damage_range,
        full.modifier_profile.max_range,
    ) == (20, 200)
    assert half.injury and half.injury.penetration == 3
    resolve(build, distance=200, aim=3, dice=[2, 2, 2, 3, 3])
    with pytest.raises(ValidationError, match="range"):
        resolve(build, distance=201, aim=3, dice=[])
    with pytest.raises(ValidationError, match="range"):
        resolve(attacker(dx=20), distance=101, aim=3, dice=[])


def test_accuracy_changes_actual_aimed_attack_target_and_miss_consequence() -> None:
    plain, ordinary = resolve(attacker(dx=10), aim=1, dice=[3, 3, 3, 3, 3, 2, 2, 2])
    accurate, improved = resolve(
        attacker(pick("enhancement:accurate", level=2), dx=10), aim=1, dice=[3, 3, 3, 3, 3, 2, 2, 2]
    )
    assert ordinary.checks[0].effective_target == 9
    assert improved.checks[0].effective_target == 11
    assert hp(plain) == hp(accurate) == 4
    _, missed = resolve(attacker(dx=10), aim=0, dice=[3, 3, 3])
    assert missed.outcome == "missed" and not missed.damage_dice


def test_defense_roll_prevents_damage_and_malediction_bypasses_that_defense_and_dr() -> None:
    state, defended = resolve(attacker(), defense="dodge", dice=[3, 3, 3, 2, 2, 2])
    assert hp(state) == 10 and defended.outcome == "defended" and len(defended.checks) == 2
    build = attacker(pick("enhancement:malediction", option="1"), kind="tox")
    context = AttackCompositionContext(
        target_id="b", location_id="room", distance_yards=0, maneuver="concentrate", defense="dodge"
    )
    state, malediction = resolve(build, context=context, dr=20, dice=[2, 2, 2, 4, 4, 4, 2, 2])
    assert hp(state) == 6 and malediction.outcome == "injured"
    assert [c.effective_target for c in malediction.checks] == [10, 10]
    assert malediction.injury and malediction.injury.effective_resistance == 0


def test_vision_based_malediction_uses_creature_modifier_adapters_and_fails_without_contact() -> (
    None
):
    sense = pick("limitation:sense-based", option="vision").model_copy(
        update={"limitation": LimitationParameters(sense="vision")}
    )
    build = attacker(pick("enhancement:malediction", option="1"), sense, kind="tox")
    context = AttackCompositionContext(
        target_id="b",
        location_id="room",
        distance_yards=0,
        maneuver="concentrate",
        vision_contact=False,
    )
    with pytest.raises(ValidationError, match="vision"):
        resolve(build, context=context, dice=[])
    with pytest.raises(ValidationError, match="Concentrate"):
        resolve(build, dice=[])
    with pytest.raises(AssertionError, match="consumer"):
        attacker(pick("enhancement:rapid-fire", level=2))


def test_exact_retry_authority_and_stale_revision_do_not_reroll() -> None:
    build = attacker(pick("enhancement:armor-divisor", option="2"))
    target, engine = approved()
    context = AttackCompositionContext(
        target_id="b", location_id="room", distance_yards=2, defense="none"
    )
    original = command()
    state, outcome = apply_composed_attack(
        resources(),
        world(),
        original,
        build,
        target,
        engine.definitions,
        context,
        rng=RecordedDice([3, 3, 3, 2, 2]),
        authorized_actor_id="a",
        system=True,
    )
    assert apply_composed_attack(
        state,
        world(),
        original,
        build,
        target,
        engine.definitions,
        context,
        rng=RecordedDice([]),
        authorized_actor_id="a",
        system=True,
    ) == (state, outcome)
    with pytest.raises(ConflictError):
        apply_composed_attack(
            state,
            world(),
            original.model_copy(update={"id": "stale"}),
            build,
            target,
            engine.definitions,
            context,
            rng=RecordedDice([]),
            authorized_actor_id="a",
            system=True,
        )
    with pytest.raises(ValidationError, match="authority"):
        apply_composed_attack(
            resources(),
            world(),
            original,
            build,
            target,
            engine.definitions,
            context,
            rng=RecordedDice([]),
            authorized_actor_id="b",
            system=True,
        )


def test_composed_source_costs_match_selected_printed_modifiers() -> None:
    for modifiers, expected in [
        ((pick("enhancement:armor-divisor", option="2"),), 15),
        ((pick("enhancement:increased-range"),), 11),
        ((pick("limitation:reduced-range"),), 9),
        ((pick("enhancement:accurate", level=2),), 11),
        ((pick("enhancement:armor-divisor", option="2"), pick("limitation:reduced-range")), 14),
    ]:
        build = attacker(*modifiers)
        assert (
            next(p.cost for p in build.purchases if p.definition_id == "advantage:innate-attack")
            == expected
        )


def test_reduced_range_at_and_beyond_its_damage_and_max_boundaries() -> None:
    build = attacker(pick("limitation:reduced-range"), dx=20)
    at, _ = resolve(build, distance=5, aim=3, dice=[3, 3, 3, 3, 3, 2, 2, 2])
    beyond, _ = resolve(build, distance=6, aim=3, dice=[3, 3, 3, 3, 3])
    assert hp(at) == 4 and hp(beyond) == 7
    resolve(build, distance=50, aim=3, dice=[3, 3, 3, 3, 3])
    with pytest.raises(ValidationError, match="range"):
        resolve(build, distance=51, aim=3, dice=[])


def test_protected_vision_changes_actual_malediction_resistance_and_tie_consequence() -> None:
    from trait_support import approved_build, trait_compiler

    from wayfarer.engine.rules.traits.attack_defense import RUNTIME_HOOKS as attack_hooks
    from wayfarer.engine.rules.traits.attack_defense import package as attack_package
    from wayfarer.engine.rules.traits.sensory import RUNTIME_HOOKS as sensory_hooks
    from wayfarer.engine.rules.traits.sensory import package as sensory_package

    combined = trait_compiler(
        "composed-protected",
        "gurps-basic-set-4e-2004",
        attack_package(),
        sensory_package(),
        hooks=attack_hooks | sensory_hooks,
    )
    target, _ = approved_build(
        combined, Purchase(definition_id="advantage:protected-sense", trait=options(sense="vision"))
    )
    sense = pick("limitation:sense-based", option="vision").model_copy(
        update={"limitation": LimitationParameters(sense="vision")}
    )
    build = attacker(pick("enhancement:malediction", option="1"), sense, kind="tox")
    context = AttackCompositionContext(
        target_id="b", location_id="room", distance_yards=0, maneuver="concentrate"
    )
    state, outcome = apply_composed_attack(
        resources(),
        world(),
        command(),
        build,
        target,
        combined.definitions,
        context,
        rng=RecordedDice([2, 2, 2, 3, 4, 4]),
        authorized_actor_id="a",
        system=True,
    )
    assert hp(state) == 10 and outcome.checks[1].effective_target == 15
    assert outcome.checks[1].margin == outcome.checks[0].margin == 4
    assert not outcome.damage_dice
    assert (
        next(p.cost for p in build.purchases if p.definition_id == "advantage:innate-attack") == 15
    )


def test_command_cannot_forge_a_new_channel_to_reset_damage_source_identity() -> None:
    build = attacker()
    target, engine = approved()
    context = AttackCompositionContext(target_id="b", location_id="room", distance_yards=2)
    with pytest.raises(ValidationError, match="channel"):
        apply_composed_attack(
            resources(),
            world(),
            command().model_copy(update={"channel_id": "forged"}),
            build,
            target,
            engine.definitions,
            context,
            rng=RecordedDice([]),
            authorized_actor_id="a",
            system=True,
        )
