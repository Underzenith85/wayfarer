"""Independent B248 Awaken state and deadline expectations."""

from pathlib import Path

import pytest
from test_healing_spell_effects import fixture

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.affliction import AfflictionEffect
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter
from wayfarer.engine.simulation.magic.awaken import AwakenSubject, alerts, awaken
from wayfarer.engine.simulation.magic.spell_transitions import approved_context
from wayfarer.engine.simulation.magic.spells import SpellCommand, apply_spell, latest
from wayfarer.engine.simulation.resources import Advance, ResourceState
from wayfarer.errors import ConflictError, ValidationError


def approved_area(tmp_path: Path) -> tuple[ResourceState, SpellCommand, object]:
    runtime, state = fixture(tmp_path, "awaken")
    encounter = Encounter(
        id="fight",
        legacy_battlefield_id="room",
        turn_order=("a", "b"),
        participants=tuple(
            Combatant(
                actor_id=actor,
                initiative=5,
                position=GridPoint(x=x, y=1),
                reach=1,
                movement_allowance=5,
            )
            for actor, x in (("a", 1), ("b", 2))
        ),
    )
    state = state.model_copy(update={"encounters": (encounter,)})
    command = SpellCommand(
        id="start",
        actor_id="a",
        expected_revision=0,
        kind="start",
        spell_id="awaken",
        cast_id="cast",
        channel_id="awaken",
        radius=2,
    )
    context = approved_context(runtime, state, command)
    assert tuple(s.actor_id for s in context.awaken_subjects) == ("a", "b")
    assert tuple(s.ht for s in context.awaken_subjects) == (10, 10)
    return state.resources, command, context


def test_approved_area_awaken_clears_actual_stun_and_sleep(tmp_path: Path) -> None:
    from wayfarer.engine.simulation.magic.spells import SpellContext

    initial, command, raw_context = approved_area(tmp_path)
    assert isinstance(raw_context, SpellContext)
    hp = next(p for p in initial.pools if p.id == "hp:b")
    assert hp.injury
    sleep = AfflictionEffect(
        id="sleep",
        actor_id="b",
        source_id="source",
        condition="sleep",
        started_at=0,
        expires_at=100,
    )
    initial = initial.model_copy(
        update={
            "pools": tuple(
                hp.model_copy(update={"injury": hp.injury.model_copy(update={"stunned": True})})
                if p.id == hp.id
                else p
                for p in initial.pools
            ),
            "afflictions": (sleep,),
            "active_effect_ids": ("sleep",),
        }
    )
    started, _ = apply_spell(initial, command, raw_context, rng=RecordedDice([]), system=True)
    complete = command.model_copy(
        update={"id": "complete", "kind": "complete", "expected_revision": 1}
    )
    due = started.model_copy(update={"game_time": latest(started)["cast"].ready_at})
    changed, result = apply_spell(
        due, complete, raw_context, rng=RecordedDice([3, 3, 3, 3, 3, 3]), system=True
    )
    # Source base cost 1 multiplied by the selected radius 2.
    assert result.energy_spent == 2 and len(result.checks) == 2
    assert not next(p for p in changed.pools if p.id == "hp:b").injury.stunned  # type: ignore[union-attr]
    assert "sleep" not in changed.active_effect_ids
    assert latest(changed)["cast"].phase == "ended"
    assert apply_spell(changed, complete, raw_context, rng=RecordedDice([]), system=True) == (
        changed,
        result,
    )


@pytest.mark.parametrize(
    "fp,injured,dice,wakes",
    [(0, False, (), False), (10, True, (4, 4, 4), False), (10, True, (2, 2, 2), True)],
)
def test_awaken_eligibility_and_injury_modifier(
    tmp_path: Path, fp: int, injured: bool, dice: tuple[int, ...], wakes: bool
) -> None:
    _, state = fixture(tmp_path, "awaken")
    patient = next(p for p in state.resources.pools if p.id == "hp:b")
    assert patient.injury
    resources = state.resources.model_copy(
        update={
            "pools": tuple(
                p.model_copy(update={"current": fp})
                if p.id == "fp:b"
                else patient.model_copy(
                    update={
                        "current": 1 if injured else 10,
                        "injury": patient.injury.model_copy(
                            update={"unconscious": True, "stunned": True}
                        ),
                    }
                )
                if p.id == patient.id
                else p
                for p in state.resources.pools
            )
        }
    )
    if injured:
        from wayfarer.engine.simulation.health.injury import Wound, apply_injury

        healthy = resources.model_copy(
            update={
                "pools": tuple(
                    p.model_copy(update={"current": 10, "injury": patient.injury})
                    if p.id == "hp:b"
                    else p
                    for p in resources.pools
                )
            }
        )
        resources, _ = apply_injury(
            healthy,
            Wound(
                id="injury",
                actor_id="b",
                expected_revision=0,
                basic_damage=6,
                resistance=0,
                damage_type="cr",
            ),
            ht=10,
            rng=RecordedDice([6, 6, 6]),
            system=True,
        )
    changed, traces = awaken(
        resources,
        (AwakenSubject(actor_id="b", ht=10),),
        margin=2,
        command_id="awake",
        rng=RecordedDice(list(dice)),
    )
    status = next(p for p in changed.pools if p.id == "hp:b").injury
    assert status and status.unconscious is not wakes
    assert status.stunned is (fp <= 0)
    if traces:
        assert traces[0].effective_target == 9


def test_alert_cost_is_exactly_one_fp_at_hour_and_cannot_be_skipped(tmp_path: Path) -> None:
    runtime, state = fixture(tmp_path, "awaken")
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "pools": tuple(
                        p.model_copy(update={"current": 2}) if p.id == "fp:b" else p
                        for p in state.resources.pools
                    )
                }
            )
        }
    )
    awake, traces = awaken(
        state.resources,
        (AwakenSubject(actor_id="b", ht=10),),
        margin=2,
        command_id="awake",
        rng=RecordedDice([]),
    )
    assert not traces and alerts(awake)[0].due == 3600
    with pytest.raises(ConflictError, match="Awaken"):
        runtime.resources.apply(
            awake,
            Advance(id="skip", actor_id="a", expected_revision=0, to=3601),
            rng=RecordedDice([]),
            system=True,
        )
    changed = runtime.resources.apply(
        awake,
        Advance(id="hour", actor_id="a", expected_revision=0, to=3600),
        rng=RecordedDice([]),
        system=True,
    )
    assert next(p.current for p in changed.pools if p.id == "fp:b") == 1
    assert alerts(changed)[0].paid
    assert (
        runtime.resources.apply(
            changed,
            Advance(id="later", actor_id="a", expected_revision=changed.revision, to=3601),
            rng=RecordedDice([]),
            system=True,
        ).pools
        == changed.pools
    )


def test_awaken_requires_spatial_authority(tmp_path: Path) -> None:
    runtime, state = fixture(tmp_path, "awaken")
    command = SpellCommand(
        id="start",
        actor_id="a",
        expected_revision=0,
        kind="start",
        spell_id="awaken",
        cast_id="cast",
        channel_id="awaken",
    )
    with pytest.raises(ValidationError, match="placements"):
        approved_context(runtime, state, command)


@pytest.mark.parametrize("trained,legal", [((), False), (("lend-energy", "lend-vitality"), True)])
def test_awaken_construction_requires_lend_vitality(trained: tuple[str, ...], legal: bool) -> None:
    from test_spell_construction import compile_spells

    from wayfarer.engine.rules.magic.healing import package

    result = compile_spells(
        (package(),), tuple(("spell:" + spell, 4) for spell in (*trained, "awaken"))
    )
    assert result.legal is legal


def test_awaken_critical_failure_records_patient_harm_for_actual_area(tmp_path: Path) -> None:
    from wayfarer.engine.simulation.magic.backfires import backfires
    from wayfarer.engine.simulation.magic.spells import SpellContext

    initial, command, context = approved_area(tmp_path)
    assert isinstance(context, SpellContext)
    started, _ = apply_spell(initial, command, context, rng=RecordedDice([]), system=True)
    complete = command.model_copy(update={"id": "bad", "kind": "complete", "expected_revision": 1})
    due = started.model_copy(update={"game_time": latest(started)["cast"].ready_at})
    failed, result = apply_spell(due, complete, context, rng=RecordedDice([6, 6, 6]), system=True)
    assert result.outcome == "critical-failure" and result.energy_spent == 2
    assert {b.target_id for b in backfires(failed) if b.pending} == {"a", "b"}
    assert all(b.row == 0 for b in backfires(failed))
    assert failed.pools[0].current == initial.pools[0].current
