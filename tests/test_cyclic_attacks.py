"""Characters third printing B103-104: initial plus repeats, stopping and healing debt."""

import pytest
from test_attack_defense_traits import approved, channel, command, resources, world
from test_resources import engine
from trait_support import options

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.modifiers import (
    EnhancementParameters,
    LimitationParameters,
    ModifierSelection,
)
from wayfarer.engine.rules.types.hazard import blocked_hp
from wayfarer.engine.simulation.health.cyclic import StopCyclic, stop
from wayfarer.engine.simulation.health.healing import restore_hp
from wayfarer.engine.simulation.resources import Advance, ResourceState
from wayfarer.engine.simulation.traits.attack_defense import apply_trait_attack
from wayfarer.errors import ConflictError, ValidationError


def attack(
    state: ResourceState | None = None,
    *,
    resistible: bool = False,
    dice: list[int] | None = None,
    damage: int = 2,
    dr: int = 0,
    attack_id: str = "first",
    interval: int = 10,
    cycles: int = 3,
    kind: str = "burn",
) -> ResourceState:
    selections: tuple[ModifierSelection, ...] = (
        ModifierSelection(
            definition_id="modifier:enhancement:cyclic",
            option="test-cyclic",
            parameters=EnhancementParameters(
                interval_seconds=interval,
                cycles=cycles,
                stop_condition="wash",
                contagious="none",
                damage_kind="burning" if kind == "burn" else "toxic",
            ),
        ),
    )
    if resistible:
        selections += (
            ModifierSelection(
                definition_id="modifier:limitation:resistible",
                option="ht+0",
                limitation=LimitationParameters(resistance_modifier=0),
            ),
        )
    attacker, compiler = approved(
        Purchase(
            definition_id="advantage:innate-attack",
            trait=options(**{"damage-type": kind}).model_copy(
                update={"attack_modifiers": selections}
            ),
        )
    )
    target, _ = approved(
        *((Purchase(definition_id="advantage:damage-resistance", amount=dr),) if dr else ())
    )
    initial = state or resources()
    result, _ = apply_trait_attack(
        initial,
        world(),
        command().model_copy(update={"id": attack_id, "expected_revision": initial.revision}),
        attacker,
        target,
        compiler.definitions,
        (channel(damage_type=kind, basic_damage=damage),),
        target_ht=12,
        rng=RecordedDice(dice or [3] * 60),
        authorized_actor_id="a",
        system=True,
    )
    return result


def hp(state: ResourceState) -> int:
    return next(p.current for p in state.pools if p.id == "hp:b")


def advance(state: ResourceState, to: int, *, dice: list[int] | None = None) -> ResourceState:
    return engine().apply(
        state,
        Advance(id=f"clock:{to}", actor_id="a", expected_revision=state.revision, to=to),
        system=True,
        rng=RecordedDice(dice or [2] * 100),
    )


def test_initial_and_repeat_damage_keep_dr_and_complete_on_last_cycle() -> None:
    state = attack(damage=3, dr=1)
    assert hp(state) == 8 and blocked_hp(state.illnesses, "b", "natural") == 2
    state = advance(state, 9)
    assert hp(state) == 8
    state = advance(state, 10, dice=[3] * 100)
    assert hp(state) == 6 and state.cyclic_attacks[0].remaining == 1
    assert blocked_hp(state.illnesses, "b", "physician") == 4
    state = advance(state, 20, dice=[3] * 100)
    assert hp(state) == 4 and not state.cyclic_attacks[0].active
    assert blocked_hp(state.illnesses, "b", "natural") == 0
    assert advance(state, 100).cyclic_attacks == state.cyclic_attacks


def test_resistance_ends_every_future_cycle() -> None:
    state = attack(resistible=True, dice=[6, 6, 6])
    assert hp(state) == 8
    state = advance(state, 100, dice=[3, 3, 3])
    assert hp(state) == 8 and not state.cyclic_attacks[0].active
    assert blocked_hp(state.illnesses, "b", "natural") == 0


def test_initial_resistance_prevents_schedule() -> None:
    state = attack(resistible=True)
    assert hp(state) == 10 and not state.cyclic_attacks


def test_stop_requires_trusted_matching_condition_and_retries_without_new_damage() -> None:
    state = attack()
    cmd = StopCyclic(
        id="stop",
        actor_id="b",
        expected_revision=state.revision,
        attack_id=state.cyclic_attacks[0].id,
        condition="wash",
    )
    with pytest.raises(ValidationError):
        stop(state, cmd)
    with pytest.raises(ValidationError):
        stop(state, cmd.model_copy(update={"condition": "unmatched"}), system=True)
    with pytest.raises(ConflictError):
        stop(state, cmd.model_copy(update={"expected_revision": 0}), system=True)
    settled = stop(state, cmd, system=True)
    assert stop(settled, cmd, system=True) == settled
    assert hp(advance(settled, 100)) == 8
    with pytest.raises(ConflictError):
        stop(settled, cmd.model_copy(update={"condition": "other"}), system=True)


def test_clock_retry_is_exact_and_crossing_without_rng_is_rejected() -> None:
    state = attack()
    cmd = Advance(id="repeat", actor_id="a", expected_revision=state.revision, to=20)
    with pytest.raises(ConflictError):
        engine().apply(state, cmd, system=True)
    result = engine().apply(state, cmd, system=True, rng=RecordedDice([2] * 100))
    restarted = ResourceState.model_validate_json(result.model_dump_json())
    assert engine().apply(restarted, cmd, system=True, rng=RecordedDice([])) == restarted
    assert hp(result) == 4


def test_independent_attacks_have_independent_cycles_and_recovery_debt() -> None:
    state = attack()
    state = attack(state, attack_id="second", interval=1, cycles=2)
    assert len(state.cyclic_attacks) == 2 and blocked_hp(state.illnesses, "b", "natural") == 4
    state = advance(state, 1)
    assert hp(state) == 4 and blocked_hp(state.illnesses, "b", "natural") == 2


def test_source_construction_cost_halves_repeat_percentage_for_resistible() -> None:
    selections = (
        ModifierSelection(
            definition_id="modifier:enhancement:cyclic",
            option="daily",
            parameters=EnhancementParameters(
                interval_seconds=86400,
                cycles=31,
                stop_condition="treatment",
                damage_kind="toxic",
                contagious="none",
            ),
        ),
        ModifierSelection(
            definition_id="modifier:limitation:resistible",
            option="ht+0",
            limitation=LimitationParameters(resistance_modifier=0),
        ),
    )
    build, _ = approved(
        Purchase(
            definition_id="advantage:innate-attack",
            trait=options(**{"damage-type": "tox"}).model_copy(
                update={"attack_modifiers": selections}
            ),
        )
    )
    # 4 points +150% Cyclic -30% Resistible = 8.8, round up once.
    assert (
        next(p.cost for p in build.purchases if p.definition_id == "advantage:innate-attack") == 9
    )


def test_recovery_restores_only_unrelated_damage_until_cycles_stop() -> None:
    state = attack()
    pool = next(p for p in state.pools if p.id == "hp:b")
    assert restore_hp(state, pool, 10, kind="magic")[1] == 0
    pool = pool.model_copy(update={"current": pool.current - 1})
    assert restore_hp(state, pool, 10, kind="magic")[1] == 1
    stopped = stop(
        state,
        StopCyclic(
            id="end",
            actor_id="b",
            expected_revision=state.revision,
            attack_id=state.cyclic_attacks[0].id,
            condition="wash",
        ),
        system=True,
    )
    assert restore_hp(stopped, pool, 10, kind="magic")[1] == 3


def test_fatigue_cycles_use_fp_and_release_recovery_restriction() -> None:
    from test_fatigue_innate_attack import fatigue_resources

    from wayfarer.engine.rules.types.hazard import blocked_fp

    selections = (
        ModifierSelection(
            definition_id="modifier:enhancement:cyclic",
            option="fatigue",
            parameters=EnhancementParameters(
                interval_seconds=10,
                cycles=3,
                stop_condition="treatment",
                contagious="none",
                damage_kind="fatigue",
            ),
        ),
    )
    attacker, compiler = approved(
        Purchase(
            definition_id="advantage:innate-attack",
            trait=options(**{"damage-type": "fat"}).model_copy(
                update={"attack_modifiers": selections}
            ),
        )
    )
    target, _ = approved()
    state, _ = apply_trait_attack(
        fatigue_resources(),
        world(),
        command(),
        attacker,
        target,
        compiler.definitions,
        (channel(damage_type="fat", basic_damage=2),),
        target_ht=12,
        rng=RecordedDice([3] * 60),
        authorized_actor_id="a",
        system=True,
    )
    assert next(p.current for p in state.pools if p.id == "fp:b") == 8
    assert blocked_fp(state.illnesses, "b") == 2 and hp(state) == 10
    state = advance(state, 20)
    assert next(p.current for p in state.pools if p.id == "fp:b") == 4
    assert hp(state) == 10 and blocked_fp(state.illnesses, "b") == 0


def test_repeat_damage_rolls_each_purchased_die_and_persists_roll_evidence() -> None:
    from wayfarer.engine.rules.types.cyclic import CyclicOccurrence

    state = attack()
    state = advance(state, 20, dice=[1, 6] + [3] * 60)
    occurrences = tuple(
        CyclicOccurrence.model_validate_json(e.kind)
        for e in state.events
        if e.id.startswith("cyclic:attack-defense-use:")
    )
    assert tuple(o.damage_dice for o in occurrences) == ((1,), (6,))
    assert tuple(o.hp_lost for o in occurrences) == (1, 6)
    assert hp(state) == 1


def test_successful_resistance_preserves_exact_secret_check_trace() -> None:
    from wayfarer.engine.rules.types.cyclic import CyclicOccurrence

    state = attack(resistible=True, dice=[6, 6, 6])
    state = advance(state, 20, dice=[2, 3, 4])
    occurrence = next(
        CyclicOccurrence.model_validate_json(e.kind)
        for e in state.events
        if e.id.startswith("cyclic:attack-defense-use:")
    )
    assert occurrence.check is not None
    assert occurrence.check.dice == (2, 3, 4) and occurrence.check.effective_target == 12
    assert occurrence.check.outcome.succeeded and occurrence.damage_dice == ()
    assert occurrence.hp_lost == occurrence.fp_lost == 0
