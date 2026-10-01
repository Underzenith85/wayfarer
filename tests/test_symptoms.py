"""Characters third printing B35-36/B109 independent Symptoms consequence oracles."""

import pytest
from test_attack_defense_traits import approved, channel, command, resources, world
from test_resources import engine
from trait_support import options

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.modifiers import EnhancementParameters, ModifierSelection
from wayfarer.engine.rules.traits.symptoms import symptom_percentage, symptom_spec
from wayfarer.engine.rules.types.recovery import FatigueStatus
from wayfarer.engine.simulation.health.condition_checks import (
    check_modifiers,
    require_hazard_capacity,
)
from wayfarer.engine.simulation.health.healing import restore_hp
from wayfarer.engine.simulation.health.medical.commands import (
    BeginRecovery,
    CareContext,
    FinishRecovery,
)
from wayfarer.engine.simulation.health.medical.recovery import apply_recovery
from wayfarer.engine.simulation.health.symptom_state import projected_build
from wayfarer.engine.simulation.health.symptoms import reconcile_recovery, track_damage
from wayfarer.engine.simulation.resources import Advance, Pool, ResourceState
from wayfarer.engine.simulation.traits.attack_defense import apply_trait_attack
from wayfarer.errors import ConflictError, ValidationError


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


def test_natural_medical_recovery_removes_real_symptoms_after_healing_past_threshold() -> None:
    state = hit(damage=6)
    context = CareContext("gurps-basic-set-4e-2004", 12, food=True)
    for day in (1, 2):
        state, _ = apply_recovery(
            state,
            BeginRecovery(
                id=f"day:{day}",
                actor_id="b",
                expected_revision=state.revision,
                kind="natural",
                target_id="b",
            ),
            context,
            rng=RecordedDice([]),
            system=True,
        )
        task = state.recovery_tasks[-1]
        state = state.model_copy(update={"game_time": task.due})
        state, result = apply_recovery(
            state,
            FinishRecovery(
                id=f"finish:{day}", actor_id="b", expected_revision=state.revision, task_id=task.id
            ),
            context,
            rng=RecordedDice([2, 2, 2]),
            system=True,
        )
        assert result.hp_recovered == 1
        assert state.symptom_effects[0].active == (day == 1)


def test_fatigue_attack_symptoms_clear_on_authoritative_rest() -> None:
    initial = resources().model_copy(
        update={
            "pools": resources().pools
            + (
                Pool(
                    id="fp:b",
                    current=10,
                    maximum=10,
                    fatigue=FatigueStatus(profile_id="gurps-basic-set-4e-2004"),
                ),
            )
        }
    )
    chosen = selection("coughing").model_copy(
        update={
            "parameters": EnhancementParameters(
                symptom="coughing", symptom_threshold="one-half", damage_kind="fatigue"
            )
        }
    )
    attacker, compiler = approved(
        Purchase(
            definition_id="advantage:innate-attack",
            trait=options(**{"damage-type": "fat"}).model_copy(
                update={"attack_modifiers": (chosen,)}
            ),
        )
    )
    target, _ = approved()
    state, _ = apply_trait_attack(
        initial,
        world(),
        command(),
        attacker,
        target,
        compiler.definitions,
        (channel(basic_damage=6, damage_type="fat"),),
        target_ht=12,
        rng=RecordedDice([2] * 30),
        authorized_actor_id="a",
        system=True,
    )
    assert state.symptom_effects[0].active
    context = CareContext("gurps-basic-set-4e-2004", 12)
    state, _ = apply_recovery(
        state,
        BeginRecovery(
            id="rest",
            actor_id="b",
            expected_revision=state.revision,
            kind="rest",
            target_id="b",
            seconds=1200,
        ),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    state = engine().apply(
        state,
        Advance(id="clock", actor_id="b", expected_revision=state.revision, to=1200),
        rng=RecordedDice([]),
        system=True,
    )
    assert next(p.current for p in state.pools if p.id == "fp:b") == 6
    assert not state.symptom_effects[0].active


def test_cyclic_repeat_registers_same_source_without_double_counting_debt() -> None:
    cyclic = ModifierSelection(
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
    attacker, compiler = approved(
        Purchase(
            definition_id="advantage:innate-attack",
            trait=options(**{"damage-type": "burn"}).model_copy(
                update={"attack_modifiers": (cyclic, selection())}
            ),
        )
    )
    target, _ = approved()
    state, _ = apply_trait_attack(
        resources(),
        world(),
        command(),
        attacker,
        target,
        compiler.definitions,
        (channel(basic_damage=2),),
        target_ht=12,
        rng=RecordedDice([2] * 30),
        authorized_actor_id="a",
        system=True,
    )
    state = engine().apply(
        state,
        Advance(id="repeat", actor_id="a", expected_revision=state.revision, to=20),
        rng=RecordedDice([2, 2]),
        system=True,
    )
    assert len(state.symptom_effects) == 1
    assert sum(d.remaining for d in state.symptom_debts) == 6
    assert state.symptom_effects[0].active


def test_receipt_retries_stale_revision_and_authority_do_not_repeat_damage() -> None:
    attacker, compiler = approved(
        Purchase(
            definition_id="advantage:innate-attack",
            trait=options(**{"damage-type": "burn"}).model_copy(
                update={"attack_modifiers": (selection(),)}
            ),
        )
    )
    target, _ = approved()
    initial = resources()
    args = (world(), command(), attacker, target, compiler.definitions, (channel(),))
    state, result = apply_trait_attack(
        initial,
        *args,
        target_ht=12,
        rng=RecordedDice([2, 2, 2]),
        authorized_actor_id="a",
        system=True,
    )
    assert apply_trait_attack(
        state, *args, target_ht=12, rng=RecordedDice([]), authorized_actor_id="a", system=True
    ) == (state, result)
    with pytest.raises(ConflictError):
        apply_trait_attack(
            state,
            world(),
            command().model_copy(update={"id": "new"}),
            attacker,
            target,
            compiler.definitions,
            (channel(),),
            target_ht=12,
            rng=RecordedDice([]),
            authorized_actor_id="a",
            system=True,
        )
    with pytest.raises(ValidationError):
        apply_trait_attack(
            initial,
            *args,
            target_ht=12,
            rng=RecordedDice([]),
            authorized_actor_id="b",
            system=False,
        )
