"""B61/B103-104/B378/B442-443: composed Cyclic delivery and persisted consequences."""

import json
from dataclasses import replace
from decimal import Decimal
from typing import Literal

import pytest
from pydantic import ValidationError as ModelValidationError
from test_attack_defense_traits import approved, channel, command, resources, world
from test_composed_attacks import attacker, hp, pick, resolve
from test_fatigue_innate_attack import fatigue_resources
from test_resources import engine

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.modifiers import (
    EnhancementParameters,
    LimitationParameters,
    ModifierSelection,
)
from wayfarer.engine.rules.types.cyclic import CyclicAttack, ZeroDamageCyclicAttack
from wayfarer.engine.rules.types.disease import ContactExposure
from wayfarer.engine.rules.types.hazard import blocked_fp
from wayfarer.engine.simulation.campaign.scenario_document import InitialResources
from wayfarer.engine.simulation.health.cyclic import StopCyclic, stop
from wayfarer.engine.simulation.health.cyclic_contagion import ExposeCyclic, expose
from wayfarer.engine.simulation.resources import Advance, ResourceState
from wayfarer.engine.simulation.traits.attack_defense import apply_trait_attack
from wayfarer.engine.simulation.traits.composed_attacks import (
    AttackCompositionContext,
    apply_composed_attack,
)
from wayfarer.engine.world import Entity, EntityKind
from wayfarer.errors import ConflictError, ValidationError


@pytest.mark.parametrize(("divisor", "dr", "lost_fp"), [("2", 5, 3), ("3", 5, 4), ("2", 1, 5)])
def test_b61_b102_b378_composed_fatigue_divides_dr_then_spends_fp_and_retries(
    divisor: str, dr: int, lost_fp: int
) -> None:
    # B102/B378: divide DR and round down, minimum zero. B61 spends FP.
    build = attacker(pick("enhancement:armor-divisor", option=divisor), kind="fat")
    state, outcome = resolve(build, state=fatigue_resources(), dr=dr, dice=[3, 3, 3, 2, 3])
    assert hp(state) == 10
    assert next(p.current for p in state.pools if p.id == "fp:b") == 10 - lost_fp
    assert outcome.fatigue and (outcome.fatigue.fp_lost, outcome.fatigue.hp_lost) == (lost_fp, 0)
    assert outcome.damage_dice == (2, 3)
    assert outcome.modifier_profile and outcome.modifier_profile.armor_divisor == int(divisor)
    restarted = ResourceState.model_validate_json(state.model_dump_json())
    target, compiler = approved(Purchase(definition_id="advantage:damage-resistance", amount=dr))
    context = AttackCompositionContext(
        target_id="b", location_id="room", distance_yards=2, defense="none"
    )
    assert apply_composed_attack(
        restarted,
        world(),
        command(),
        build,
        target,
        compiler.definitions,
        context,
        rng=RecordedDice([]),
        authorized_actor_id="a",
        system=True,
    ) == (restarted, outcome)


def test_b103_fatigue_cyclic_repeats_keep_the_purchased_armor_divisor() -> None:
    # DR 3 / divisor 2 -> DR 1 on each occurrence. Rolls 3, 2, 4 lose 2+1+3 FP.
    build = attacker(
        cyclic("fat"), pick("enhancement:armor-divisor", option="2"), kind="fat", levels=1
    )
    state, _ = resolve(build, state=fatigue_resources(), dr=3, dice=[3, 3, 3, 3])
    occurrence = state.cyclic_attacks[0]
    assert (occurrence.resistance, occurrence.armor_divisor, occurrence.fp_debt) == (
        3,
        Decimal(2),
        2,
    )
    assert blocked_fp(state.illnesses, "b") == 2
    assert next(p.current for p in state.pools if p.id == "fp:b") == 8
    state = engine().apply(
        state,
        Advance(id="before-cycle", actor_id="a", expected_revision=state.revision, to=9),
        rng=RecordedDice([]),
        system=True,
    )
    assert next(p.current for p in state.pools if p.id == "fp:b") == 8
    clock = Advance(id="repeat", actor_id="a", expected_revision=state.revision, to=20)
    state = engine().apply(state, clock, rng=RecordedDice([2, 4]), system=True)
    assert hp(state) == 10 and next(p.current for p in state.pools if p.id == "fp:b") == 4
    assert state.cyclic_attacks[0].fp_debt == 6 and not state.cyclic_attacks[0].active
    assert blocked_fp(state.illnesses, "b") == 0
    restarted = ResourceState.model_validate_json(state.model_dump_json())
    assert engine().apply(restarted, clock, rng=RecordedDice([]), system=True) == restarted


def test_b61_machine_immunity_prevents_divisor_fatigue_and_cyclic_schedule() -> None:
    build = attacker(
        cyclic("fat"), pick("enhancement:armor-divisor", option="2"), kind="fat", levels=1
    )
    initial = fatigue_resources(machine=True)
    state, outcome = resolve(build, state=initial, dr=3, dice=[3, 3, 3, 3])
    assert state.pools == initial.pools
    assert outcome.outcome == "unaffected" and outcome.fatigue is None
    assert not state.cyclic_attacks and not state.illnesses


@pytest.mark.parametrize("composed", [False, True])
def test_fatigue_armor_divisor_still_requires_the_approved_composed_profile(composed: bool) -> None:
    build = attacker(kind="fat", levels=1)
    target, compiler = approved()
    initial = fatigue_resources()
    with pytest.raises(ValidationError, match="modifier-free"):
        apply_trait_attack(
            initial,
            world(),
            command(),
            build,
            target,
            compiler.definitions,
            (channel(damage_type="fat", armor_divisor=Decimal(2), composed=composed),),
            target_ht=10,
            rng=RecordedDice([3]),
            authorized_actor_id="a",
            system=True,
        )
    assert initial == fatigue_resources()


def cyclic(kind: str, *, contagious: Literal["none", "mild", "high"] = "none") -> ModifierSelection:
    return ModifierSelection(
        definition_id="modifier:enhancement:cyclic",
        option="persistent-attack",
        parameters=EnhancementParameters.model_validate(
            {
                "interval_seconds": 86400 if contagious != "none" else 10,
                "cycles": 3,
                "stop_condition": "treatment" if contagious != "none" else "wash",
                "contagious": contagious,
                "damage_kind": {
                    "burn": "burning",
                    "cor": "corrosion",
                    "fat": "fatigue",
                    "tox": "toxic",
                }[kind],
            }
        ),
    )


@pytest.mark.parametrize("kind", ["burn", "cor", "fat", "tox"])
@pytest.mark.parametrize("distance", [10, 11])
def test_b103_b378_zero_initial_damage_preserves_exposure_and_rerolled_cycles_after_retry(
    kind: str, distance: int
) -> None:
    # One purchased die rolls 1: B378 halves to zero, including at exactly 1/2D.
    build = attacker(cyclic(kind), kind=kind, levels=1, dx=20)
    initial = fatigue_resources()
    context = AttackCompositionContext(
        target_id="b", location_id="room", distance_yards=distance, defense="none"
    )
    state, outcome = resolve(build, state=initial, context=context, dice=[2, 2, 2, 1])
    assert state.pools == initial.pools
    assert outcome.outcome == "unaffected" and outcome.damage_dice == (1,)
    occurrence = state.cyclic_attacks[0]
    assert (occurrence.basic_damage, occurrence.damage_dice) == (0, 1)
    assert (occurrence.remaining, occurrence.due) == (2, 10)
    assert occurrence.hp_debt == occurrence.fp_debt == 0
    assert state.illnesses[0].hp_debt == state.illnesses[0].fp_debt == 0
    assert state.revision == initial.revision + 1
    restarted = ResourceState.model_validate_json(state.model_dump_json())
    target, compiler = approved()
    assert apply_composed_attack(
        restarted,
        world(),
        command(),
        build,
        target,
        compiler.definitions,
        context,
        rng=RecordedDice([]),
        authorized_actor_id="a",
        system=True,
    ) == (restarted, outcome)
    with pytest.raises(ConflictError, match="revision"):
        apply_composed_attack(
            restarted,
            world(),
            command().model_copy(update={"id": "stale"}),
            build,
            target,
            compiler.definitions,
            context,
            rng=RecordedDice([]),
            authorized_actor_id="a",
            system=True,
        )
    advance = Advance(id="clock", actor_id="a", expected_revision=restarted.revision, to=20)
    advanced = engine().apply(
        restarted,
        advance,
        rng=RecordedDice([2, 3]),
        system=True,
    )
    pools = {p.id: p for p in advanced.pools}
    assert pools["hp:b"].current == (10 if kind == "fat" else 5)
    assert pools["fp:b"].current == (5 if kind == "fat" else 10)
    occurrence = advanced.cyclic_attacks[0]
    assert occurrence.remaining == 0 and not occurrence.active
    assert occurrence.hp_debt == (0 if kind == "fat" else 5)
    assert occurrence.fp_debt == (5 if kind == "fat" else 0)
    restarted = ResourceState.model_validate_json(advanced.model_dump_json())
    assert engine().apply(restarted, advance, rng=RecordedDice([]), system=True) == restarted


def test_b103_zero_initial_exposure_still_requires_a_positive_future_damage_expression() -> None:
    state, _ = resolve(attacker(cyclic("burn"), levels=1, dx=20), distance=10, dice=[2, 2, 2, 1])
    occurrence = state.cyclic_attacks[0]
    payload = occurrence.model_dump()
    assert ZeroDamageCyclicAttack.model_validate_json(occurrence.model_dump_json()) == occurrence
    with pytest.raises(ModelValidationError, match="basic_damage"):
        CyclicAttack.model_validate_json(occurrence.model_dump_json())
    with pytest.raises(ModelValidationError, match="basic_damage"):
        InitialResources(cyclic_attacks=(occurrence,))
    with pytest.raises(ModelValidationError, match="basic_damage"):
        InitialResources.model_validate_json(
            json.dumps({"cyclic_attacks": [occurrence.model_dump(mode="json")]})
        )
    for changes in ({"damage_dice": 0}, {"basic_damage": -1}):
        with pytest.raises(ModelValidationError):
            ZeroDamageCyclicAttack.model_validate({**payload, **changes})
    del payload["damage_dice"]
    with pytest.raises(ModelValidationError, match="damage_dice"):
        ZeroDamageCyclicAttack.model_validate(payload)


def test_b104_stopping_zero_initial_exposure_prevents_later_damage() -> None:
    state, _ = resolve(attacker(cyclic("burn"), levels=1, dx=20), distance=10, dice=[2, 2, 2, 1])
    state = stop(
        state,
        StopCyclic(
            id="wash",
            actor_id="b",
            expected_revision=state.revision,
            attack_id=state.cyclic_attacks[0].id,
            condition="wash",
        ),
        system=True,
    )
    state = engine().apply(
        state,
        Advance(id="clock", actor_id="a", expected_revision=state.revision, to=20),
        rng=RecordedDice([]),
        system=True,
    )
    assert hp(state) == 10 and not state.cyclic_attacks[0].active
    assert state.cyclic_attacks[0].hp_debt == 0


def test_b104_resistance_after_zero_initial_exposure_ends_future_cycles() -> None:
    resistible = ModifierSelection(
        definition_id="modifier:limitation:resistible",
        option="ht+0",
        limitation=LimitationParameters(resistance_modifier=0),
    )
    build = attacker(cyclic("burn"), resistible, levels=1, dx=20)
    state, outcome = resolve(build, distance=10, dice=[2, 2, 2, 6, 6, 6, 1])
    assert outcome.outcome == "unaffected" and state.cyclic_attacks[0].active
    state = engine().apply(
        state,
        Advance(id="clock", actor_id="a", expected_revision=state.revision, to=20),
        rng=RecordedDice([3, 3, 3]),
        system=True,
    )
    assert hp(state) == 10 and not state.cyclic_attacks[0].active
    assert state.cyclic_attacks[0].hp_debt == 0


def test_b61_immune_fatigue_target_gets_no_cyclic_exposure_after_zero_roll() -> None:
    build = attacker(cyclic("fat"), kind="fat", levels=1, dx=20)
    initial = fatigue_resources(machine=True)
    state, outcome = resolve(build, state=initial, distance=10, dice=[2, 2, 2, 1])
    assert outcome.outcome == "unaffected" and outcome.fatigue is None
    assert state.pools == initial.pools and not state.cyclic_attacks


@pytest.mark.parametrize(("distance", "rolled"), [(9, 1), (10, 2)])
def test_b103_positive_composed_damage_keeps_remaining_cycles(distance: int, rolled: int) -> None:
    build = attacker(cyclic("burn"), levels=1, dx=20)
    state, outcome = resolve(build, distance=distance, dice=[2, 2, 2, rolled])
    assert hp(state) == 9 and outcome.outcome == "injured"
    occurrence = state.cyclic_attacks[0]
    assert (occurrence.basic_damage, occurrence.remaining, occurrence.due) == (1, 2, 10)
    assert occurrence.hp_debt == 1
    # Existing positive checkpoints and frozen authoring inputs retain their models.
    assert CyclicAttack.model_validate_json(occurrence.model_dump_json()) == occurrence
    assert ResourceState.model_validate_json(state.model_dump_json()) == state
    authored = InitialResources(cyclic_attacks=(occurrence,))
    assert InitialResources.model_validate_json(authored.model_dump_json()) == authored
    state = engine().apply(
        state,
        Advance(id="clock", actor_id="a", expected_revision=state.revision, to=20),
        rng=RecordedDice([2, 3]),
        system=True,
    )
    assert hp(state) == 4 and not state.cyclic_attacks[0].active
    assert state.cyclic_attacks[0].hp_debt == 6


@pytest.mark.parametrize("contagious", ["mild", "high"])
@pytest.mark.parametrize(("distance", "initial_roll", "initial_hp"), [(0, 2, 8), (10, 1, 10)])
def test_b104_composed_contagion_retains_vector_incubation_and_independent_infection(
    contagious: Literal["mild", "high"],
    distance: int,
    initial_roll: int,
    initial_hp: int,
) -> None:
    # B442-443: an authored respiratory exposure is checked at day end; its
    # first damage waits for the authored incubation period, independently.
    build = attacker(cyclic("tox", contagious=contagious), kind="tox", levels=1, dx=20)
    context = AttackCompositionContext(
        target_id="b",
        location_id="room",
        distance_yards=distance,
        defense="none",
        contagion_vector="respiratory",
        incubation_seconds=3600,
    )
    state, outcome = resolve(build, context=context, dice=[3, 3, 3, initial_roll])
    assert hp(state) == initial_hp and outcome.damage_dice == (initial_roll,)
    source = state.cyclic_attacks[0]
    assert (source.contagion_vector, source.incubation_seconds) == ("respiratory", 3600)
    assert source.contagious == contagious
    restarted = ResourceState.model_validate_json(state.model_dump_json())
    target, compiler = approved()
    assert apply_composed_attack(
        restarted,
        world(),
        command(),
        build,
        target,
        compiler.definitions,
        context,
        rng=RecordedDice([]),
        authorized_actor_id="a",
        system=True,
    ) == (restarted, outcome)
    subject = next(p for p in resources().pools if p.id == "hp:b").model_copy(update={"id": "hp:c"})
    state = restarted.model_copy(update={"pools": restarted.pools + (subject,)})
    scene = replace(
        world(),
        entities=world().entities
        + (Entity("c", EntityKind.ACTOR, "Secondary", location_id="room"),),
    )
    relationship = ContactExposure(
        id="contact",
        actor_id="c",
        disease_id=source.attack_id,
        vector="respiratory",
        contact="close-conversation",
        occurred_at=state.game_time,
        carrier_id="b",
    )
    exposure = ExposeCyclic(
        id="exposure",
        actor_id="c",
        expected_revision=state.revision,
        source_attack_id=source.id,
        relationship_id=relationship.id,
    )
    with pytest.raises(ValidationError, match="relationship"):
        expose(
            state,
            scene,
            exposure,
            relationship.model_copy(update={"vector": "blood"}),
            ht=10,
            system=True,
        )
    state = expose(state, scene, exposure, relationship, ht=10, system=True)
    restarted = ResourceState.model_validate_json(state.model_dump_json())
    assert restarted.cyclic_exposures[0].source.basic_damage == source.basic_damage
    if initial_hp == 10:
        with pytest.raises(ModelValidationError, match="basic_damage"):
            InitialResources(cyclic_exposures=restarted.cyclic_exposures)
        with pytest.raises(ModelValidationError, match="basic_damage"):
            InitialResources.model_validate_json(
                json.dumps(
                    {"cyclic_exposures": [restarted.cyclic_exposures[0].model_dump(mode="json")]}
                )
            )
    assert expose(restarted, scene, exposure, relationship, ht=10, system=True) == restarted
    reducer = engine().for_world(scene)
    check_command = Advance(id="day-end", actor_id="a", expected_revision=state.revision, to=86400)
    state = reducer.apply(restarted, check_command, rng=RecordedDice([6, 6, 6, 2]), system=True)
    infection = next(a for a in state.cyclic_attacks if a.actor_id == "c")
    assert (infection.cycle, infection.remaining, infection.due) == (0, 3, 90000)
    assert infection.hp_debt == 0 and infection.resistance == 0
    assert next(p.current for p in state.pools if p.id == "hp:c") == 10
    assert hp(state) == initial_hp - 2
    assert state.cyclic_exposures[0].check is not None
    assert state.cyclic_exposures[0].check.effective_target == 12
    restarted = ResourceState.model_validate_json(state.model_dump_json())
    assert reducer.apply(restarted, check_command, rng=RecordedDice([]), system=True) == restarted
    state = reducer.apply(
        restarted,
        Advance(id="incubating", actor_id="a", expected_revision=state.revision, to=89999),
        rng=RecordedDice([]),
        system=True,
    )
    assert next(p.current for p in state.pools if p.id == "hp:c") == 10
    state = reducer.apply(
        state,
        Advance(id="first-symptoms", actor_id="a", expected_revision=state.revision, to=90000),
        rng=RecordedDice([3]),
        system=True,
    )
    assert next(p.current for p in state.pools if p.id == "hp:c") == 7
    infection = next(a for a in state.cyclic_attacks if a.actor_id == "c")
    assert (infection.cycle, infection.remaining, infection.hp_debt) == (1, 2, 3)
    assert hp(state) == initial_hp - 2 and len(state.cyclic_attacks) == 2


def test_b104_contagious_composition_still_requires_an_authored_vector() -> None:
    build = attacker(cyclic("tox", contagious="mild"), kind="tox", levels=1)
    with pytest.raises(ValidationError, match="authored illness vector"):
        resolve(build, dice=[3, 3, 3])
