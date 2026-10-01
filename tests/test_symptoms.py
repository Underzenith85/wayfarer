"""Characters third printing B35-36/B109 independent Symptoms consequence oracles."""

import pytest
from test_attack_defense_traits import approved, channel, command, resources, world
from trait_support import options

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.modifiers import ModifierSelection
from wayfarer.engine.rules.traits.symptoms import symptom_percentage, symptom_spec
from wayfarer.engine.simulation.health.condition_checks import (
    check_modifiers,
    require_hazard_capacity,
)
from wayfarer.engine.simulation.health.healing import restore_hp
from wayfarer.engine.simulation.health.symptom_state import projected_build
from wayfarer.engine.simulation.health.symptoms import reconcile_recovery, track_damage
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.traits.attack_defense import apply_trait_attack
from wayfarer.errors import ValidationError


def selection(effect: str = "blindness", threshold: str = "one-half") -> ModifierSelection:
    return ModifierSelection.model_validate(
        {
            "definition_id": "modifier:enhancement:symptoms",
            "option": "source-test",
            "parameters": {
                "symptom": effect,
                "symptom_threshold": threshold,
                "damage_kind": "burning",
            },
        }
    )


def hit(
    state: ResourceState | None = None,
    *,
    damage: int = 3,
    effect: str = "blindness",
    channel_id: str = "attack",
    identifier: str = "hit",
) -> ResourceState:
    state = state or resources()
    attacker, compiler = approved(
        Purchase(
            definition_id="advantage:innate-attack",
            trait=options(**{"damage-type": "burn"}).model_copy(
                update={"attack_modifiers": (selection(effect),)}
            ),
        )
    )
    target, _ = approved()
    result, _ = apply_trait_attack(
        state,
        world(),
        command().model_copy(
            update={"id": identifier, "expected_revision": state.revision, "channel_id": channel_id}
        ),
        attacker,
        target,
        compiler.definitions,
        (channel(basic_damage=damage).model_copy(update={"id": channel_id}),),
        target_ht=12,
        rng=RecordedDice([2] * 60),
        authorized_actor_id="a",
        system=True,
    )
    return result


def heal(state: ResourceState, amount: int) -> ResourceState:
    hp = next(p for p in state.pools if p.id == "hp:b")
    restored, _ = restore_hp(state, hp, amount, kind="magic")
    result = state.model_copy(
        update={"pools": tuple(restored if p.id == hp.id else p for p in state.pools)}
    )
    return reconcile_recovery(state, result)


@pytest.mark.parametrize(
    ("effect", "threshold", "percent"),
    [
        ("blindness", "one-third", 150),
        ("blindness", "one-half", 100),
        ("blindness", "two-thirds", 50),
        ("coughing", "one-half", 40),
        ("attribute-penalty:st:2", "one-half", 20),
        ("attribute-penalty:iq:2", "one-half", 40),
    ],
)
def test_printed_affliction_cost_multiplier(effect: str, threshold: str, percent: int) -> None:
    assert symptom_percentage(symptom_spec(selection(effect, threshold))) == percent


def test_threshold_hysteresis_cumulative_attacks_and_recovery() -> None:
    state = hit(damage=5)
    assert not state.symptom_effects[0].active  # exactly half never activates
    state = hit(state, damage=1, identifier="second")
    assert state.symptom_effects[0].active
    with pytest.raises(ValidationError, match="blindness"):
        require_hazard_capacity(state, "b", "vision")
    state = heal(state, 1)
    assert state.symptom_effects[0].active  # heal past, not merely to, threshold
    state = heal(state, 1)
    assert not state.symptom_effects[0].active


def test_other_attack_damage_does_not_activate_specific_symptoms() -> None:
    state = hit(damage=3)
    hp = next(p for p in state.pools if p.id == "hp:b")
    state = state.model_copy(
        update={
            "pools": tuple(
                p.model_copy(update={"current": 3}) if p.id == hp.id else p for p in state.pools
            )
        }
    )
    state = track_damage(state, pool_id="hp:b", injury_id="other", amount=4)
    assert not state.symptom_effects[0].active
    state = hit(state, damage=3, identifier="again")
    assert state.symptom_effects[0].active
    assert sum(d.remaining for d in state.symptom_debts if d.source_id is not None) == 6


def test_independent_channel_sources_never_combine_threshold_damage() -> None:
    state = hit(damage=3)
    state = hit(state, damage=3, channel_id="other", identifier="other")
    assert len(state.symptom_effects) == 2
    assert not any(e.active for e in state.symptom_effects)


def test_attribute_effect_changes_approved_runtime_view_without_mutating_purchase() -> None:
    state = hit(damage=6, effect="attribute-penalty:iq:2")
    target, compiler = approved()
    updated = projected_build(state, "b", target, compiler.definitions)
    assert target.statistics and updated.statistics
    assert updated.statistics.iq == target.statistics.iq - 2
    assert updated.statistics.will == target.statistics.will - 2
    assert updated.statistics.per == target.statistics.per - 2
    assert updated.statistics.hp == target.statistics.hp
    assert updated.statistics.fp == target.statistics.fp


def test_st_penalty_recalculates_lift_damage_but_keeps_hp() -> None:
    state = hit(damage=6, effect="attribute-penalty:st:2")
    target, compiler = approved()
    updated = projected_build(state, "b", target, compiler.definitions)
    assert target.statistics and updated.statistics
    assert updated.statistics.st == 8 and updated.statistics.basic_lift == 13
    assert updated.statistics.hp == target.statistics.hp
    assert updated.statistics.thrust != target.statistics.thrust


def test_coughing_changes_checks_and_prevents_stealth() -> None:
    state = hit(damage=6, effect="coughing")
    assert sum(m.value for m in check_modifiers(state, "b", "dx")) == -3
    assert sum(m.value for m in check_modifiers(state, "b", "iq")) == -1
    assert not check_modifiers(state, "b", "dx", defensive=True)
    with pytest.raises(ValidationError, match="Stealth"):
        require_hazard_capacity(state, "b", "stealth")


def test_unexecutable_effects_are_rejected_in_approved_construction() -> None:
    with pytest.raises(AssertionError, match="consumer"):
        approved(
            Purchase(
                definition_id="advantage:innate-attack",
                trait=options(**{"damage-type": "burn"}).model_copy(
                    update={"attack_modifiers": (selection("untyped-effect"),)}
                ),
            )
        )
