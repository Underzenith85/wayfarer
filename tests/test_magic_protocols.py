"""Independent numeric examples for Basic Set magic protocol boundaries."""

import pytest

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.protocols import (
    AreaSelection,
    CeremonialContribution,
    CeremonialPlan,
    InformationAttempt,
    blocking_cast,
    ceremonial_skill_bonus,
    effective_item_power,
    hex_area,
    information_attempt_allowed,
    item_energy_cost,
    ready_melee_spell,
    ritual_requirements,
    square_area,
    validate_tradition,
)
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.recovery import FatigueStatus
from wayfarer.engine.simulation.magic.spells import (
    PROFILE,
    SpellCommand,
    SpellContext,
    apply_spell,
    latest,
)
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.errors import ValidationError


def test_ritual_requirements_use_base_skill_boundaries() -> None:
    assert ritual_requirements(9).model_dump() == {
        "speech": "firm",
        "gesture": "full-body",
        "time_multiplier": 2,
    }


def test_blocking_spell_is_one_unreduced_instant_defense() -> None:
    result = blocking_cast(20, 3, active_spell_penalty=2)
    assert (result.effective_skill, result.energy_cost) == (18, 3)
    with pytest.raises(ValidationError, match="Only one"):
        blocking_cast(20, 3, already_cast_this_turn=True)
    with pytest.raises(ValidationError, match="critical hit"):
        blocking_cast(20, 3, defends_critical_hit=True)


def test_melee_spell_requires_a_hand_or_staff_and_can_be_held() -> None:
    assert ready_melee_spell("cast", "a", free_hand=True).held
    staff = ready_melee_spell("cast", "a", free_hand=False, carrier_item_id="staff")
    assert staff.carrier_item_id == "staff"
    with pytest.raises(ValidationError, match="free hand or magic staff"):
        ready_melee_spell("cast", "a", free_hand=False)
    assert ritual_requirements(10).speech == "quiet"
    assert ritual_requirements(15).gesture == "small"
    assert ritual_requirements(20).model_dump() == {
        "speech": "none",
        "gesture": "none",
        "time_multiplier": 1,
    }


def test_ceremonial_energy_opposition_limits_and_bonus_steps() -> None:
    plan = CeremonialPlan(
        leader_id="mage",
        contributions=(
            CeremonialContribution(actor_id="mage", fp=10, role="leader"),
            CeremonialContribution(actor_id="helper", fp=3, role="nonmage"),
            CeremonialContribution(actor_id="crowd", fp=1, role="spectator"),
        ),
        opposing_spectators=("heckler",),
    )
    assert plan.available_energy == 9
    assert [ceremonial_skill_bonus(10, energy) for energy in (11, 12, 14, 16, 20, 30)] == [
        0,
        1,
        2,
        3,
        4,
        5,
    ]
    with pytest.raises(ValueError, match="participant limit"):
        CeremonialContribution(actor_id="crowd", fp=2, role="spectator")


def test_area_boundaries_support_full_circles_and_paid_subsets() -> None:
    hexes = hex_area(AreaSelection(center=(0, 0)), 2)
    assert len(hexes) == 7 and (1, -1) in hexes and (2, 0) not in hexes
    squares = square_area(AreaSelection(center=(3, 3)), 2)
    assert len(squares) == 9
    chosen = hex_area(AreaSelection(center=(0, 0), cells=((0, 0), (1, 0))), 2)
    assert chosen == {(0, 0), (1, 0)}
    with pytest.raises(ValidationError, match="inside the radius"):
        hex_area(AreaSelection(center=(0, 0), cells=((0, 0), (2, 0))), 2)


def test_magic_item_power_and_energy_follow_mana() -> None:
    assert effective_item_power(19, "low") == 14
    assert effective_item_power(15, "none") is None
    assert [item_energy_cost(4, 2, mana) for mana in ("low", "normal", "high")] == [3, 2, 0]


def test_information_attempts_are_once_per_day_for_same_subject() -> None:
    attempt = InformationAttempt(caster_id="a", spell_id="seek", subject_id="b", day=4)
    assert information_attempt_allowed((), attempt)
    assert not information_attempt_allowed((attempt,), attempt)
    assert information_attempt_allowed((attempt,), attempt.model_copy(update={"day": 5}))
    assert information_attempt_allowed(
        (attempt,), attempt.model_copy(update={"subject_id": "another"})
    )


def test_optional_magic_traditions_fail_closed() -> None:
    validate_tradition("standard", enabled_optional_rules=frozenset())
    with pytest.raises(ValidationError, match="not selected"):
        validate_tradition("clerical", enabled_optional_rules=frozenset())
    validate_tradition("ritual", enabled_optional_rules=frozenset({"magic.ritual"}))


def test_ceremonial_casting_spends_every_contribution_even_on_failure() -> None:
    plan = CeremonialPlan(
        leader_id="a",
        contributions=(
            CeremonialContribution(actor_id="a", fp=1, role="leader"),
            CeremonialContribution(actor_id="helper", fp=1, role="spectator"),
        ),
    )
    state = ResourceState(
        pools=tuple(
            pool
            for actor in ("a", "helper")
            for pool in (
                Pool(
                    id="hp:" + actor,
                    current=10,
                    maximum=10,
                    injury=InjuryStatus(profile_id=PROFILE),
                ),
                Pool(
                    id="fp:" + actor,
                    current=10,
                    maximum=10,
                    fatigue=FatigueStatus(profile_id=PROFILE),
                ),
            )
        )
    )
    context = SpellContext(
        profile_id=PROFILE,
        build_revision="approved",
        skill=15,
        magery=1,
        target_id="b",
        learned=("light",),
        ceremonial=plan,
        ceremonial_ht=(("a", 10), ("helper", 10)),
    )
    start = SpellCommand(
        id="start",
        actor_id="a",
        expected_revision=0,
        kind="start",
        spell_id="light",
        cast_id="ritual",
    )
    casting, result = apply_spell(state, start, context, rng=RecordedDice([]), system=True)
    assert result.outcome == "casting" and latest(casting)["ritual"].ready_at == 10
    completed, result = apply_spell(
        casting.model_copy(update={"game_time": 10}),
        start.model_copy(update={"id": "finish", "kind": "complete", "expected_revision": 1}),
        context,
        rng=RecordedDice([6, 5, 5]),
        system=True,
    )
    assert result.outcome == "failed" and result.energy_spent == 2
    assert {pool.id: pool.current for pool in completed.pools if pool.id.startswith("fp:")} == {
        "fp:a": 9,
        "fp:helper": 9,
    }
    replay, repeated = apply_spell(
        completed,
        start.model_copy(update={"id": "finish", "kind": "complete", "expected_revision": 1}),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    assert replay == completed and repeated == result


def test_area_selection_is_bound_into_spell_execution() -> None:
    state = ResourceState(
        pools=(
            Pool(id="hp:a", current=10, maximum=10, injury=InjuryStatus(profile_id=PROFILE)),
            Pool(id="fp:a", current=10, maximum=10, fatigue=FatigueStatus(profile_id=PROFILE)),
        )
    )
    context = SpellContext(
        profile_id=PROFILE,
        build_revision="approved",
        skill=14,
        magery=1,
        target_id="b",
        learned=("create-fire", "ignite-fire"),
        radius=2,
        position=(0, 0),
        geometry="hex",
        area=AreaSelection(center=(0, 0), cells=((0, 0), (1, 0))),
    )
    command = SpellCommand(
        id="area",
        actor_id="a",
        expected_revision=0,
        kind="start",
        spell_id="create-fire",
        cast_id="fire",
        radius=2,
    )
    casting, _ = apply_spell(state, command, context, rng=RecordedDice([]), system=True)
    assert latest(casting)["fire"].area == context.area
