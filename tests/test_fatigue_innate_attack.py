"""Fatigue Innate Attack regression cases for audit A06, B61 and B426.

Expectations follow issue #760 and the existing selected-source fatigue review.
Reopening the supplied printings is pending; these cases do not certify variants.
"""

from dataclasses import replace

import pytest
from test_attack_defense_traits import (
    approved,
    channel,
    command,
    resources,
    tolerance_options,
    world,
)
from trait_support import options

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.attack_defense import PROFILE
from wayfarer.engine.rules.traits.physical import PhysicalTraits
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.recovery import FatigueStatus
from wayfarer.engine.simulation.health.fatigue import (
    ContinueExertion,
    apply_fatigue,
    fatigue_value,
)
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.engine.simulation.traits.attack_defense import (
    AttackChannel,
    TraitAttackCommand,
    TraitAttackOutcome,
    apply_trait_attack,
)
from wayfarer.errors import ConflictError, ValidationError


def fatigue_resources(fp: int = 10, *, machine: bool = False, fitness: int = 0) -> ResourceState:
    state = resources()
    return state.model_copy(
        update={
            "pools": tuple(
                pool.model_copy(
                    update={
                        "injury": InjuryStatus(
                            profile_id=PROFILE,
                            anatomy="creature" if machine else "human",
                            machine=machine,
                            physical_traits=PhysicalTraits(fitness=fitness),
                        )
                    }
                )
                if pool.id == "hp:b"
                else pool
                for pool in state.pools
            )
            + (Pool(id="fp:b", current=fp, maximum=10, fatigue=FatigueStatus(profile_id=PROFILE)),)
        }
    )


def attack(
    state: ResourceState,
    *,
    damage: int = 6,
    dr: int = 0,
    attack_channel: AttackChannel | None = None,
    attack_command: TraitAttackCommand | None = None,
    authorized_actor_id: str = "a",
    system: bool = True,
) -> tuple[ResourceState, TraitAttackOutcome]:
    attacker, engine = approved(
        Purchase(definition_id="advantage:innate-attack", trait=options(**{"damage-type": "fat"}))
    )
    purchases = (Purchase(definition_id="advantage:damage-resistance", amount=dr),) if dr else ()
    target, _ = approved(*purchases)
    return apply_trait_attack(
        state,
        world(),
        attack_command or command(),
        attacker,
        target,
        engine.definitions,
        (attack_channel or channel(damage_type="fat", basic_damage=damage),),
        target_ht=12,
        rng=RecordedDice([3] * 60),
        authorized_actor_id=authorized_actor_id,
        system=system,
    )


@pytest.mark.parametrize("levels", [1, 3, 10])
def test_fatigue_innate_attack_costs_ten_points_per_level(levels: int) -> None:
    build, _ = approved(
        Purchase(
            definition_id="advantage:innate-attack",
            amount=levels,
            trait=options(**{"damage-type": "fat"}),
        )
    )
    assert (
        next(
            purchase.cost
            for purchase in build.purchases
            if purchase.definition_id == "advantage:innate-attack"
        )
        == 10 * levels
    )


def test_fatigue_damage_reduces_fp_after_dr_without_direct_hp_injury() -> None:
    state, outcome = attack(fatigue_resources(), dr=2)
    pools = {pool.id: pool for pool in state.pools}
    assert pools["fp:b"].current == 6
    assert pools["hp:b"].current == 10
    assert pools["hp:b"].injury is not None
    assert pools["hp:b"].injury.shock == 0
    assert outcome.fatigue is not None
    assert (outcome.fatigue.fp_lost, outcome.fatigue.hp_lost) == (4, 0)
    assert outcome.injury is None
    assert state.revision == 1


def test_fatigue_damage_applies_exhaustion_to_st_move_and_dodge() -> None:
    state, _ = attack(fatigue_resources(4), damage=1)
    fp = next(pool for pool in state.pools if pool.id == "fp:b")
    assert fp.current == 3
    assert tuple(fatigue_value(fp, value) for value in (11, 5, 9)) == (6, 3, 5)


def test_negative_fp_uses_canonical_hp_overflow_and_continued_exertion_check() -> None:
    state, outcome = attack(fatigue_resources(2), damage=4)
    pools = {pool.id: pool for pool in state.pools}
    assert (pools["fp:b"].current, pools["hp:b"].current) == (-2, 8)
    assert outcome.fatigue is not None
    assert (outcome.fatigue.fp_lost, outcome.fatigue.hp_lost) == (4, 2)
    continued, exertion = apply_fatigue(
        state,
        ContinueExertion(id="try-to-act", actor_id="b", expected_revision=state.revision),
        ht=12,
        will=10,
        rng=RecordedDice([5, 5, 5]),
        system=True,
    )
    assert not exertion.allowed
    fp = next(pool for pool in continued.pools if pool.id == "fp:b")
    assert fp.fatigue is not None and fp.fatigue.collapsed


def test_negative_maximum_fp_causes_unconsciousness_and_denies_action() -> None:
    state, _ = attack(fatigue_resources(1), damage=11)
    fp = next(pool for pool in state.pools if pool.id == "fp:b")
    assert fp.current == -10
    assert fp.fatigue is not None and fp.fatigue.unconscious
    _, exertion = apply_fatigue(
        state,
        ContinueExertion(id="cannot-act", actor_id="b", expected_revision=state.revision),
        ht=12,
        will=10,
        rng=RecordedDice([]),
        system=True,
    )
    assert not exertion.allowed and exertion.checks == ()


def test_very_fit_does_not_halve_attack_damage_or_reclassify_it_as_power_cost() -> None:
    state, outcome = attack(fatigue_resources(fitness=2), damage=3)
    fp = next(pool for pool in state.pools if pool.id == "fp:b")
    assert fp.current == 7
    assert fp.fatigue is not None
    assert fp.fatigue.power == 0 and not fp.fatigue.half_paid
    assert outcome.fatigue is not None and outcome.fatigue.fp_lost == 3


def test_machine_immunity_is_read_from_canonical_physiology() -> None:
    initial = fatigue_resources(machine=True)
    initial = initial.model_copy(
        update={"pools": tuple(pool for pool in initial.pools if pool.id != "fp:b")}
    )
    state, outcome = attack(initial)
    assert state.pools == initial.pools
    assert outcome.outcome == "unaffected" and outcome.fatigue is None
    restored = ResourceState.model_validate_json(state.model_dump_json())
    retried, replay = attack(restored)
    assert retried == restored and replay == outcome


def test_unliving_injury_tolerance_does_not_imply_machine_immunity() -> None:
    attacker, engine = approved(
        Purchase(definition_id="advantage:innate-attack", trait=options(**{"damage-type": "fat"}))
    )
    target, _ = approved(
        Purchase(definition_id="advantage:injury-tolerance", trait=tolerance_options("unliving"))
    )
    state, _ = apply_trait_attack(
        fatigue_resources(),
        world(),
        command(),
        attacker,
        target,
        engine.definitions,
        (channel(damage_type="fat"),),
        target_ht=12,
        rng=RecordedDice([]),
        authorized_actor_id="a",
        system=True,
    )
    assert next(pool for pool in state.pools if pool.id == "fp:b").current == 4


def test_fatigue_attack_miss_and_fully_resisted_damage_do_not_change_pools() -> None:
    initial = fatigue_resources()
    missed, outcome = attack(initial, attack_channel=channel(damage_type="fat", attack_roll=18))
    assert missed.pools == initial.pools and outcome.outcome == "missed"
    blocked, outcome = attack(initial, dr=6)
    assert blocked.pools == initial.pools and outcome.outcome == "unaffected"


def test_fatigue_attack_restart_retry_and_changed_payload_rejection() -> None:
    state, outcome = attack(fatigue_resources())
    restored = ResourceState.model_validate_json(state.model_dump_json())
    retried, replay = attack(restored)
    assert retried == restored and replay == outcome
    for changed in (
        command().model_copy(update={"definition_id": "advantage:teeth"}),
        command(revision=1),
    ):
        with pytest.raises(ConflictError):
            attack(restored, attack_command=changed)


def test_fatigue_attack_preserves_authority_revision_context_and_purchase_checks() -> None:
    initial = fatigue_resources()
    with pytest.raises(ValidationError):
        attack(initial, system=False)
    with pytest.raises(ValidationError):
        attack(initial, authorized_actor_id="b")
    with pytest.raises(ConflictError):
        attack(initial.model_copy(update={"revision": 1}))
    with pytest.raises(ValidationError):
        attack(initial, attack_channel=channel(damage_type="burn"))
    attacker, engine = approved(
        Purchase(definition_id="advantage:innate-attack", trait=options(**{"damage-type": "fat"}))
    )
    target, _ = approved()
    remote = replace(
        world(),
        entities=tuple(
            replace(entity, location_id=None) if entity.id == "b" else entity
            for entity in world().entities
        ),
    )
    with pytest.raises(ValidationError):
        apply_trait_attack(
            initial,
            remote,
            command(),
            attacker,
            target,
            engine.definitions,
            (channel(damage_type="fat"),),
            target_ht=12,
            rng=RecordedDice([]),
            authorized_actor_id="a",
            system=True,
        )
    assert initial == fatigue_resources()


def test_nonmachine_requires_explicit_canonical_fp_pool() -> None:
    with pytest.raises(ValidationError, match="FP and HP"):
        attack(resources())
