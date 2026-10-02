"""Selected Characters third B35–36/B109 and Campaigns fourth B428 oracles."""

import hashlib
from dataclasses import replace

import pytest
from test_attack_defense_traits import approved, command, resources, world
from test_composed_attacks import attacker, pick
from test_innate_criticals import context, fight, initial, miss, table
from test_resources import engine
from test_symptoms import heal, selection

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.cyclic import cyclic_profile
from wayfarer.engine.rules.traits.modifiers import EnhancementParameters, ModifierSelection
from wayfarer.engine.rules.types.cyclic import CyclicAttack
from wayfarer.engine.rules.types.recovery import FatigueStatus
from wayfarer.engine.simulation.health.condition_checks import (
    check_modifiers,
    require_hazard_capacity,
)
from wayfarer.engine.simulation.health.medical.commands import BeginRecovery, CareContext
from wayfarer.engine.simulation.health.medical.recovery import apply_recovery
from wayfarer.engine.simulation.health.symptom_state import acute_blindness, projected_build
from wayfarer.engine.simulation.resources import Advance, Pool, ResourceState
from wayfarer.engine.simulation.traits.composed_attacks import (
    AttackCompositionContext,
    apply_composed_attack,
)
from wayfarer.engine.simulation.traits.innate_criticals import resolve_innate_miss
from wayfarer.errors import ValidationError

EFFECTS = (selection("coughing", "one-third"), selection("blindness", "two-thirds"))


def deliver(
    state: ResourceState,
    damage: int,
    *,
    identifier: str,
    source: str = "attack",
    actor: str = "a",
    selections: tuple[ModifierSelection, ...] = EFFECTS,
) -> ResourceState:
    build = attacker(*selections, levels=1)
    target, definitions = approved(Purchase(definition_id="secondary:hp", amount=12))
    assert target.statistics and target.statistics.hp == 12
    scene = world()
    if actor != "a":
        scene = replace(scene, entities=scene.entities + (replace(scene.entities[1], id=actor),))
    rng = RecordedDice([3, 3, 3, damage])
    result, outcome = apply_composed_attack(
        state,
        scene,
        command(revision=state.revision).model_copy(
            update={"id": identifier, "actor_id": actor, "channel_id": source}
        ),
        build,
        target,
        definitions.definitions,
        AttackCompositionContext(
            channel_id=source, target_id="b", location_id="room", distance_yards=0, defense="none"
        ),
        rng=rng,
        authorized_actor_id=actor,
        system=True,
    )
    assert outcome.injury and outcome.injury.injury == damage
    assert rng.exhausted()  # B109 adds no resistance or activation dice.
    return result


def twelve_hp() -> ResourceState:
    state = resources()
    return state.model_copy(
        update={
            "pools": tuple(
                p.model_copy(update={"current": 12, "maximum": 12}) if p.id == "hp:b" else p
                for p in state.pools
            )
        }
    )


def test_multiple_named_effects_price_each_source_threshold_even_with_same_option() -> None:
    build = attacker(*EFFECTS, levels=1)
    # B36 coughing20 and blindness50; B109 multipliers3 and1; burning1d costs5.
    assert (
        next(p.cost for p in build.purchases if p.definition_id == "advantage:innate-attack") == 11
    )
    profile = cyclic_profile(EFFECTS, "burn")
    assert profile.symptom_spec and profile.symptom_spec.kind == "coughing"
    assert (
        len(profile.additional_symptoms) == 1 and profile.additional_symptoms[0].kind == "blindness"
    )
    with pytest.raises(AssertionError, match="Duplicate Symptoms"):
        attacker(EFFECTS[0], EFFECTS[0].model_copy(update={"option": "another-label"}))
    with pytest.raises(AssertionError, match="Duplicate Symptoms"):
        attacker(selection("attribute-penalty:iq:02"), selection("attribute-penalty:iq:2"))
    with pytest.raises(AssertionError, match="Duplicate ability modifier"):
        attacker(pick("enhancement:accurate"), pick("enhancement:accurate"))


def test_each_threshold_activates_above_and_recovers_below_its_own_boundary() -> None:
    state = twelve_hp()
    for total, damage, coughing, blind in (
        (3, 3, False, False),
        (4, 1, False, False),
        (5, 1, True, False),
        (7, 2, True, False),
        (8, 1, True, False),
        (9, 1, True, True),
    ):
        state = deliver(state, damage, identifier=f"loss-{total}")
        assert acute_blindness(state, "b") is blind
        assert sum(m.value for m in check_modifiers(state, "b", "dx")) == (-3 if coughing else 0)
        assert sum(m.value for m in check_modifiers(state, "b", "iq")) == (-1 if coughing else 0)
        if coughing:
            with pytest.raises(ValidationError, match="Stealth"):
                require_hazard_capacity(state, "b", "stealth")
        else:
            require_hazard_capacity(state, "b", "stealth")
        assert sum(d.remaining for d in state.symptom_debts) == total
        assert len({e.id for e in state.symptom_effects}) == 2
    for outstanding, blind, coughing in (
        (8, True, True),
        (7, False, True),
        (6, False, True),
        (5, False, True),
        (4, False, True),
        (3, False, False),
    ):
        state = heal(state, 1)
        assert sum(d.remaining for d in state.symptom_debts) == outstanding
        assert acute_blindness(state, "b") is blind
        assert sum(m.value for m in check_modifiers(state, "b", "dx")) == (-3 if coughing else 0)
    require_hazard_capacity(state, "b", "stealth")
    require_hazard_capacity(state, "b", "vision")


@pytest.mark.parametrize(
    ("second_source", "second_actor"), [("other-attack", "a"), ("attack", "c")]
)
def test_independent_attacks_share_no_damage_and_recovery_clears_only_one_source(
    second_source: str, second_actor: str
) -> None:
    state = deliver(twelve_hp(), 3, identifier="first")
    state = deliver(state, 3, identifier="second", source=second_source, actor=second_actor)
    assert not any(e.active for e in state.symptom_effects)
    state = deliver(state, 2, identifier="first-again")
    state = deliver(state, 2, identifier="second-again", source=second_source, actor=second_actor)
    assert sum(e.active for e in state.symptom_effects) == 2
    assert not acute_blindness(state, "b")  # Combined10 must never become one source's blindness.
    state = heal(state, 2)
    assert [e.active for e in state.symptom_effects] == [False, False, True, False]
    assert sum(m.value for m in check_modifiers(state, "b", "dx")) == -3


def test_concurrent_attribute_stages_use_worst_effect_and_keep_current_build_after_recovery() -> (
    None
):
    stages = (
        selection("attribute-penalty:iq:1", "one-third"),
        selection("attribute-penalty:iq:3", "two-thirds"),
    )
    state = deliver(twelve_hp(), 5, identifier="lesser", selections=stages)
    old, definitions = approved()
    first = projected_build(state, "b", old, definitions.definitions)
    assert first.statistics and first.statistics.iq == 9
    state = deliver(state, 4, identifier="greater", selections=stages)
    current, _ = approved(Purchase(definition_id="secondary:will", amount=12))
    projected = projected_build(state, "b", current, definitions.definitions)
    assert projected.statistics and (projected.statistics.iq, projected.statistics.will) == (7, 9)
    state = heal(state, 2)
    projected = projected_build(state, "b", current, definitions.definitions)
    assert projected.statistics and (projected.statistics.iq, projected.statistics.will) == (9, 11)
    state = heal(state, 4)
    assert projected_build(state, "b", current, definitions.definitions) == current
    assert current.statistics and current.statistics.will == 12


def cyclic_selection() -> ModifierSelection:
    return ModifierSelection(
        definition_id="modifier:enhancement:cyclic",
        option="test",
        parameters=EnhancementParameters(
            interval_seconds=10,
            cycles=3,
            stop_condition="wash",
            contagious="none",
            damage_kind="burning",
        ),
    )


def test_cyclic_ticks_share_one_debt_per_injury_across_all_symptoms() -> None:
    state = deliver(twelve_hp(), 3, identifier="cyclic", selections=(cyclic_selection(),) + EFFECTS)
    original = state.cyclic_attacks[0]
    assert len(original.additional_symptoms) == 1
    rng = RecordedDice([3, 3])
    state = engine().apply(
        state,
        Advance(id="ticks", actor_id="a", expected_revision=state.revision, to=20),
        rng=rng,
        system=True,
    )
    assert rng.exhausted()
    assert len(state.symptom_effects) == 2 and all(e.active for e in state.symptom_effects)
    assert len(state.symptom_debts) == 3 and sum(d.remaining for d in state.symptom_debts) == 9
    assert next(p.current for p in state.pools if p.id == "hp:b") == 3
    encoded = state.model_dump_json()
    assert ResourceState.model_validate_json(encoded).model_dump_json() == encoded
    state = heal(state, 2)
    assert not acute_blindness(state, "b") and state.symptom_effects[0].active


def test_real_compiled_modifiers_reach_critical_self_hit_and_preserve_roll_chronology() -> None:
    build = attacker(cyclic_selection(), *EFFECTS, levels=1)
    purchase = next(
        p for p in build.trait_purchases if p.definition_id == "advantage:innate-attack"
    )
    assert purchase.trait
    profile = cyclic_profile(purchase.trait.attack_modifiers, "burn")
    source = context().source.model_copy(update={"modifier_profile": profile})
    captured = context(source=source).model_copy(
        update={
            "limb_dr": tuple(
                (limb, 0) for limb in ("left-arm", "right-arm", "left-leg", "right-leg")
            )
        }
    )
    rng = RecordedDice([*table(5), *table(5), 1, 1, 4])
    state, encounter, outcome = resolve_innate_miss(
        initial(), fight(), captured, miss(), rng=rng, system=True
    )
    assert outcome.injury == 4 and rng.exhausted()
    assert len(state.symptom_effects) == 2 and [e.active for e in state.symptom_effects] == [
        True,
        False,
    ]
    assert len(state.symptom_debts) == 1 and state.symptom_debts[0].remaining == 4
    assert state.cyclic_attacks[0].additional_symptoms == profile.additional_symptoms
    assert sum(m.value for m in check_modifiers(state, "a", "dx")) == -3
    assert resolve_innate_miss(
        state, encounter, captured, miss(), rng=RecordedDice([]), system=True
    ) == (state, encounter, outcome)


def test_single_effect_retains_legacy_identity_and_omits_new_empty_fields() -> None:
    profile = cyclic_profile((selection(),), "burn")
    assert "additional_symptoms" not in profile.model_dump()
    state = deliver(twelve_hp(), 3, identifier="legacy", selections=(selection(),))
    assert state.symptom_effects[0].id == "symptoms:" + hashlib.sha256(b"hp:b:a:attack").hexdigest()
    cyclic = CyclicAttack(
        id="old",
        attacker_id="a",
        actor_id="b",
        attack_id="old",
        basic_damage=1,
        damage_dice=1,
        damage_type="burn",
        resistance=0,
        ht=10,
        interval=10,
        remaining=1,
        due=10,
        stop_condition="wash",
        symptom_spec=profile.symptom_spec,
    )
    assert "additional_symptoms" not in cyclic.model_dump()
    assert CyclicAttack.model_validate_json(cyclic.model_dump_json()) == cyclic


@pytest.mark.parametrize(
    "unsupported",
    [
        "advantage:combat-reflexes",
        "disadvantage:deafness",
        "negated-advantage:combat-reflexes",
        "drowsy",
        "tipsy",
        "drunk",
        "moderate-pain",
        "euphoria",
        "nauseated",
        "severe-pain",
        "terrible-pain",
    ],
)
def test_other_source_variants_still_require_executable_consumers(unsupported: str) -> None:
    with pytest.raises(AssertionError, match="consumer"):
        attacker(*EFFECTS, selection(unsupported))


def test_fatigue_symptoms_use_fp_and_clear_individually_on_actual_rest() -> None:
    selected = tuple(
        s.model_copy(
            update={"parameters": s.parameters.model_copy(update={"damage_kind": "fatigue"})}
        )
        for s in EFFECTS
        if s.parameters
    )
    build = attacker(*selected, levels=1, kind="fat")
    target, definitions = approved(Purchase(definition_id="secondary:fp", amount=12))
    state = resources()
    state = state.model_copy(
        update={
            "pools": state.pools
            + (
                Pool(
                    id="fp:b",
                    current=12,
                    maximum=12,
                    fatigue=FatigueStatus(profile_id="gurps-basic-set-4e-2004"),
                ),
            )
        }
    )
    for number, damage in enumerate((4, 1, 3, 1)):
        rng = RecordedDice([3, 3, 3, damage])
        state, result = apply_composed_attack(
            state,
            world(),
            command(revision=state.revision).model_copy(update={"id": f"fatigue-{number}"}),
            build,
            target,
            definitions.definitions,
            AttackCompositionContext(
                target_id="b", location_id="room", distance_yards=0, defense="none"
            ),
            rng=rng,
            authorized_actor_id="a",
            system=True,
        )
        assert result.fatigue and result.fatigue.fp_lost == damage and rng.exhausted()
        assert [e.active for e in state.symptom_effects] == [
            (False, True, True, True)[number],
            number == 3,
        ]
    assert next(p.current for p in state.pools if p.id == "hp:b") == 10
    state, _ = apply_recovery(
        state,
        BeginRecovery(
            id="rest",
            actor_id="b",
            expected_revision=state.revision,
            target_id="b",
            kind="rest",
            seconds=3600,
        ),
        CareContext("gurps-basic-set-4e-2004", 10),
        rng=RecordedDice([]),
        system=True,
    )
    for minute, blind, coughing in (
        (10, True, True),
        (20, False, True),
        (50, False, True),
        (60, False, False),
    ):
        state = engine().apply(
            state,
            Advance(
                id=f"rest-{minute}", actor_id="b", expected_revision=state.revision, to=minute * 60
            ),
            rng=RecordedDice([]),
            system=True,
        )
        assert acute_blindness(state, "b") is blind
        assert sum(m.value for m in check_modifiers(state, "b", "dx")) == (-3 if coughing else 0)
        assert next(p.current for p in state.pools if p.id == "fp:b") == 3 + minute // 10
    assert next(p.current for p in state.pools if p.id == "hp:b") == 10


def test_recovering_one_attribute_source_keeps_other_effect_and_new_build() -> None:
    state = deliver(
        twelve_hp(),
        5,
        identifier="strong",
        selections=(selection("attribute-penalty:iq:3", "one-third"),),
    )
    state = deliver(
        state,
        5,
        identifier="weak",
        source="other",
        selections=(selection("attribute-penalty:iq:1", "one-third"),),
    )
    current, definitions = approved(Purchase(definition_id="secondary:will", amount=12))
    before = projected_build(state, "b", current, definitions.definitions)
    assert before.statistics and (before.statistics.iq, before.statistics.will) == (7, 9)
    state = heal(state, 2)
    after = projected_build(state, "b", current, definitions.definitions)
    assert after.statistics and (after.statistics.iq, after.statistics.will) == (9, 11)
    assert [e.active for e in state.symptom_effects] == [False, True]
    state = heal(state, 5)
    assert projected_build(state, "b", current, definitions.definitions) == current


def test_same_symptom_set_reordered_keeps_effect_identity_and_prior_damage() -> None:
    state = deliver(twelve_hp(), 3, identifier="original-order")
    effects, debts = state.symptom_effects, state.symptom_debts
    state = deliver(state, 2, identifier="reordered", selections=tuple(reversed(EFFECTS)))
    assert tuple((e.id, e.spec) for e in state.symptom_effects) == tuple(
        (e.id, e.spec) for e in effects
    )
    assert state.symptom_debts[: len(debts)] == debts
    assert len(state.symptom_debts) == 2 and sum(d.remaining for d in state.symptom_debts) == 5
    assert [e.active for e in state.symptom_effects] == [True, False]
    assert sum(m.value for m in check_modifiers(state, "b", "dx")) == -3


@pytest.mark.parametrize(
    "changed",
    [
        EFFECTS[:1],
        EFFECTS + (selection("attribute-penalty:iq:2"),),
        (selection("coughing", "one-half"), EFFECTS[1]),
        (selection("attribute-penalty:iq:1", "one-third"), EFFECTS[1]),
    ],
)
def test_reordering_does_not_permit_a_changed_effect_set(
    changed: tuple[ModifierSelection, ...],
) -> None:
    state = deliver(twelve_hp(), 3, identifier="original")
    with pytest.raises(ValidationError, match="source changed its approved effect"):
        deliver(state, 2, identifier="changed", selections=changed)


def test_same_source_cannot_change_an_attribute_penalty_level() -> None:
    original = (selection("attribute-penalty:iq:1", "one-third"), EFFECTS[1])
    state = deliver(twelve_hp(), 3, identifier="original", selections=original)
    changed = (selection("attribute-penalty:iq:2", "one-third"), EFFECTS[1])
    with pytest.raises(ValidationError, match="source changed its approved effect"):
        deliver(state, 2, identifier="changed-level", selections=changed)
