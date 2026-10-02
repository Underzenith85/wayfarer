"""B66/B378-381 selected damage resumes canonical consequences exactly once."""

from dataclasses import replace

import pytest
from test_attack_defense_traits import approved, channel, command, resources, world
from test_composed_attacks import attacker, pick
from test_cyclic_host import source_purchase

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.traits.attack_defense import (
    PreparedOwnerDamage,
    TraitAttackConsequences,
    apply_trait_attack,
    prepare_owner_damage,
)
from wayfarer.errors import ConflictError, ValidationError


@pytest.mark.parametrize(("distance", "injury"), [(2, 9), (10, 3)])
def test_selected_faces_change_actual_dr_injury_shock_and_major_wound(
    distance: int, injury: int
) -> None:
    source = attacker(pick("enhancement:armor-divisor", option="2"))
    target, compiler = approved(Purchase(definition_id="advantage:damage-resistance", amount=5))
    initial = resources()
    incoming = channel(composed=True, distance_yards=distance)
    consequences = TraitAttackConsequences(resistance=5)
    original_rng = RecordedDice((1, 1))
    captured = prepare_owner_damage(
        initial,
        world(),
        command(),
        source,
        target,
        compiler.definitions,
        (incoming,),
        target_ht=10,
        rng=original_rng,
        authorized_actor_id="a",
        system=True,
        consequences=consequences,
    )
    assert captured.original == (1, 1) and original_rng.exhausted()
    assert next(p.current for p in initial.pools if p.id == "hp:b") == 10
    captured = PreparedOwnerDamage.model_validate_json(captured.model_dump_json())
    # B378: 11 basic; at 1/2D floor(11/2)=5. DR floor(5/2)=2.
    # Burning has x1 injury. Nine injury is a major wound; three is not.
    consequences_rng = RecordedDice((4, 4, 4) if injury == 9 else ())
    updated, outcome = apply_trait_attack(
        initial,
        world(),
        command(),
        source,
        target,
        compiler.definitions,
        (incoming,),
        target_ht=10,
        rng=consequences_rng,
        authorized_actor_id="a",
        system=True,
        consequences=consequences,
        prepared_damage=captured,
        selected_damage=(6, 5),
    )
    hp = next(p for p in updated.pools if p.id == "hp:b")
    assert outcome.injury and outcome.injury.injury == injury
    assert outcome.injury.effective_resistance == 2
    assert hp.current == 10 - injury and hp.injury and hp.injury.shock == min(4, injury)
    assert hp.injury.stunned is (injury == 9)
    assert hp.injury.prone is (injury == 9)
    assert consequences_rng.exhausted()
    assert apply_trait_attack(
        updated,
        world(),
        command(),
        source,
        target,
        compiler.definitions,
        (incoming,),
        target_ht=10,
        rng=RecordedDice(()),
        authorized_actor_id="a",
        system=True,
        consequences=consequences,
        prepared_damage=captured,
        selected_damage=(6, 5),
    ) == (updated, outcome)
    with pytest.raises(ConflictError, match="reopened"):
        prepare_owner_damage(
            updated,
            world(),
            command(revision=updated.revision),
            source,
            target,
            compiler.definitions,
            (incoming,),
            target_ht=10,
            rng=RecordedDice(()),
            authorized_actor_id="a",
            system=True,
            consequences=consequences,
        )


def test_cyclic_resistance_is_fixed_before_damage_and_not_drawn_again() -> None:
    purchase = source_purchase(resistible=True)
    source, compiler = approved(purchase)
    target, _ = approved()
    incoming = channel(composed=True)
    dice = RecordedDice((4, 4, 4, 1))
    captured = prepare_owner_damage(
        resources(),
        world(),
        command(),
        source,
        target,
        compiler.definitions,
        (incoming,),
        target_ht=10,
        rng=dice,
        authorized_actor_id="a",
        system=True,
    )
    assert captured.check and captured.check.total == 12 and captured.original == (1,)
    assert dice.exhausted()
    after, outcome = apply_trait_attack(
        resources(),
        world(),
        command(),
        source,
        target,
        compiler.definitions,
        (incoming,),
        target_ht=10,
        rng=RecordedDice(()),
        authorized_actor_id="a",
        system=True,
        prepared_damage=captured,
        selected_damage=(5,),
    )
    assert outcome.damage_dice == (5,) and outcome.checks == (captured.check,)
    assert next(p.current for p in after.pools if p.id == "hp:b") == 5
    assert after.cyclic_attacks[0].damage_dice == 1


@pytest.mark.parametrize("secret", [False, True])
def test_resisted_delivery_has_no_damage_choice(secret: bool) -> None:
    source, compiler = approved(source_purchase(resistible=True))
    target, _ = approved()
    incoming = channel(composed=True)
    dice = RecordedDice((2, 2, 2))
    captured = prepare_owner_damage(
        resources(),
        world(),
        command(),
        source,
        target,
        compiler.definitions,
        (incoming,),
        target_ht=10,
        rng=dice,
        authorized_actor_id="a",
        system=True,
        secret=secret,
    )
    assert not captured.rollable and captured.original == () and dice.exhausted()
    after, outcome = apply_trait_attack(
        resources(),
        world(),
        command(),
        source,
        target,
        compiler.definitions,
        (incoming,),
        target_ht=10,
        rng=RecordedDice(()),
        authorized_actor_id="a",
        system=True,
        prepared_damage=captured,
    )
    assert outcome.outcome == "resisted" and not outcome.damage_dice
    assert next(p.current for p in after.pools if p.id == "hp:b") == 10
    assert not after.cyclic_attacks


def test_secret_preparation_draws_no_damage_and_requires_later_faces() -> None:
    source = attacker()
    target, compiler = approved()
    incoming = channel(composed=True)
    captured = prepare_owner_damage(
        resources(),
        world(),
        command(),
        source,
        target,
        compiler.definitions,
        (incoming,),
        target_ht=10,
        rng=RecordedDice(()),
        authorized_actor_id="a",
        system=True,
        secret=True,
    )
    assert captured.rollable and captured.original is None
    with pytest.raises(ValidationError, match="captured dice"):
        apply_trait_attack(
            resources(),
            world(),
            command(),
            source,
            target,
            compiler.definitions,
            (incoming,),
            target_ht=10,
            rng=RecordedDice(()),
            authorized_actor_id="a",
            system=True,
            prepared_damage=captured,
        )


@pytest.mark.parametrize("mutation", ["build", "channel", "consequences", "count"])
def test_changed_capture_rejects_before_any_consequence_entropy(mutation: str) -> None:
    source = attacker()
    target, compiler = approved()
    incoming = channel(composed=True)
    consequences = TraitAttackConsequences(resistance=0)
    captured = prepare_owner_damage(
        resources(),
        world(),
        command(),
        source,
        target,
        compiler.definitions,
        (incoming,),
        target_ht=10,
        rng=RecordedDice((1, 2)),
        authorized_actor_id="a",
        system=True,
        consequences=consequences,
    )
    if mutation == "build":
        source = replace(source, revision="stale")
    elif mutation == "channel":
        incoming = incoming.model_copy(update={"distance_yards": 10.0})
    elif mutation == "consequences":
        consequences = TraitAttackConsequences(resistance=1)
    else:
        captured = captured.model_copy(update={"dice_count": 3})
    with pytest.raises(ConflictError, match="context changed"):
        apply_trait_attack(
            resources(),
            world(),
            command(),
            source,
            target,
            compiler.definitions,
            (incoming,),
            target_ht=10,
            rng=RecordedDice(()),
            authorized_actor_id="a",
            system=True,
            consequences=consequences,
            prepared_damage=captured,
            selected_damage=(6, 6),
        )
