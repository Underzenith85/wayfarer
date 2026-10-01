"""Independent B237/B241-242/B251/B253 lock object-state expectations."""

import pytest
from test_private_spell_vocabulary import private_record
from test_spells import context, state

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.magic.lock_state import (
    LockFixture,
    LockState,
    opening_allowed,
    passage_blocked,
    save,
)
from wayfarer.engine.simulation.magic.lock_state import (
    latest as locks,
)
from wayfarer.engine.simulation.magic.spell_state import (
    RuntimeSpellEvent,
    RuntimeSpellId,
    SpellResult,
    event_id,
    latest,
)
from wayfarer.engine.simulation.magic.spells import (
    RuntimeSpellCommand,
    SpellContext,
    apply_spell,
)
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError


def fixture(*, modifier: int = 0, size: int = 0) -> ResourceState:
    return save(
        state(),
        LockState(
            fixture=LockFixture(
                object_id="door",
                location_id="hall",
                kind="door",
                difficulty_modifier=modifier,
                size_modifier=size,
                passage=("hall", "room"),
            ),
            locked=True,
            closed=True,
        ),
        "fixture",
    )


def bound(*, skill: int = 14) -> SpellContext:
    return context().model_copy(
        update={
            "target_id": "door",
            "skill": skill,
            "magery": 2,
            "learned": ("lockmaster", "magelock", "apportation"),
            "execution_version": 2,
            "execute_effects": True,
        }
    )


def command(
    resources: ResourceState, spell_id: RuntimeSpellId, kind: str, identifier: str
) -> RuntimeSpellCommand:
    return RuntimeSpellCommand.model_validate(
        dict(
            id=identifier,
            actor_id="a",
            expected_revision=resources.revision,
            spell_id=spell_id,
            cast_id=spell_id,
            kind=kind,
        )
    )


def cast(
    resources: ResourceState,
    spell_id: RuntimeSpellId,
    *,
    dice: tuple[int, ...] = (3, 3, 3),
    skill: int = 14,
    final_context: SpellContext | None = None,
) -> tuple[ResourceState, SpellResult, RuntimeSpellCommand]:
    resources, _ = apply_spell(
        resources,
        command(resources, spell_id, "start", spell_id + ":start"),
        bound(skill=skill),
        rng=RecordedDice(()),
        system=True,
    )
    effect = latest(resources)[spell_id]
    for second in range(effect.started_at + 1, effect.ready_at + 1):
        resources = resources.model_copy(update={"game_time": second})
        if second < effect.ready_at:
            resources, _ = apply_spell(
                resources,
                command(resources, spell_id, "concentrate", f"{spell_id}:concentrate:{second}"),
                bound(skill=skill),
                rng=RecordedDice(()),
                system=True,
            )
    complete = command(resources, spell_id, "complete", spell_id + ":complete")
    resources, result = apply_spell(
        resources,
        complete,
        final_context or bound(skill=skill),
        rng=RecordedDice(dice),
        system=True,
    )
    return resources, result, complete


def ward(resources: ResourceState, skill: int = 14) -> ResourceState:
    effect = private_record(phase="active").model_copy(
        update={"cast_id": "ward", "actor_id": "other-mage", "skill": skill, "expires_at": 21600}
    )
    return resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=event_id("ward", "magelock"),
                    at=0,
                    target_id="door",
                    kind=RuntimeSpellEvent(
                        effect=effect, result=SpellResult(outcome="active")
                    ).model_dump_json(),
                ),
            )
        }
    )


def test_lockmaster_opens_the_lock_persistently_but_door_requires_physical_opening() -> None:
    after, result, complete = cast(fixture(), "lockmaster")
    assert after.game_time == 10 and result.energy_spent == 3
    assert next(p.current for p in after.pools if p.id == "fp:a") == 7
    value = locks(after)["door"]
    assert not value.locked and value.closed and opening_allowed(after, value)
    assert passage_blocked(after, "hall", "room")
    opened = save(after, value.model_copy(update={"closed": False}), "physical-open")
    assert not passage_blocked(opened, "hall", "room")
    assert latest(after)["lockmaster"].phase == "ended"
    restored = ResourceState.model_validate_json(after.model_dump_json())
    assert apply_spell(restored, complete, bound(), rng=RecordedDice(()), system=True) == (
        restored,
        result,
    )
    with pytest.raises(ConflictError, match="not available"):
        apply_spell(
            after,
            command(after, "lockmaster", "maintain", "maintain"),
            bound(),
            rng=RecordedDice(()),
            system=True,
        )


def test_lockpicking_difficulty_changes_the_actual_cast_and_failure_preserves_lock() -> None:
    after, result, _ = cast(fixture(modifier=-4), "lockmaster", dice=(3, 4, 4))
    assert result.checks[0].effective_target == 10
    assert result.outcome == "failed" and result.energy_spent == 1
    assert locks(after)["door"].locked and not opening_allowed(after, locks(after)["door"])


@pytest.mark.parametrize(
    "skill,ward_skill,dice,expected",
    [
        (14, 14, (3, 3, 3, 3, 3, 3), "resisted"),
        (14, 14, (3, 3, 3, 4, 4, 4), "active"),
        (20, 18, (3, 3, 3, 3, 3, 3), "active"),
        (20, 30, (3, 3, 3, 6, 6, 6), "active"),
        (14, 30, (1, 1, 1), "active"),
    ],
)
def test_magelock_contest_ties_critical_success_and_no_rule_of_sixteen(
    skill: int, ward_skill: int, dice: tuple[int, ...], expected: str
) -> None:
    after, result, _ = cast(ward(fixture(), ward_skill), "lockmaster", dice=dice, skill=skill)
    assert result.outcome == expected
    assert opening_allowed(after, locks(after)["door"]) == (expected == "active")
    assert (latest(after)["ward"].phase == "ended") == (expected == "active")
    if dice == (1, 1, 1):
        assert len(result.checks) == 1 and result.energy_spent == 0
    elif skill == 14:
        assert result.energy_spent == 3


def test_magelock_duration_maintenance_and_cancellation_govern_real_open_eligibility() -> None:
    before = fixture()
    before = save(before, locks(before)["door"].model_copy(update={"locked": False}), "unlocked")
    after, result, _ = cast(before, "magelock")
    assert after.game_time == 4 and result.energy_spent == 3
    assert not opening_allowed(after, locks(after)["door"])
    assert latest(after)["magelock"].expires_at == 21604
    due = after.model_copy(update={"game_time": 21604})
    assert opening_allowed(due, locks(due)["door"])
    maintained, result = apply_spell(
        due,
        command(due, "magelock", "maintain", "maintain"),
        bound(),
        rng=RecordedDice(()),
        system=True,
    )
    assert result.energy_spent == 2 and latest(maintained)["magelock"].expires_at == 43204
    assert not opening_allowed(maintained, locks(maintained)["door"])
    cancelled, result = apply_spell(
        maintained,
        command(maintained, "magelock", "cancel", "cancel"),
        bound(),
        rng=RecordedDice(()),
        system=True,
    )
    assert result.energy_spent == 1 and opening_allowed(cancelled, locks(cancelled)["door"])


def test_magelock_cannot_bind_a_non_door_or_open_door() -> None:
    for change in ("lock", "open"):
        before = fixture()
        value = locks(before)["door"]
        if change == "lock":
            value = value.model_copy(
                update={
                    "fixture": value.fixture.model_copy(update={"kind": "lock", "passage": None})
                }
            )
        else:
            value = value.model_copy(update={"locked": False, "closed": False})
        before = save(before, value, "changed")
        with pytest.raises(ValidationError, match="closed door"):
            apply_spell(
                before,
                command(before, "magelock", "start", "start"),
                bound(),
                rng=RecordedDice(()),
                system=True,
            )


@pytest.mark.parametrize("size,cost,maintenance", [(-3, 3, 2), (0, 3, 2), (2, 9, 6)])
def test_regular_lock_spell_size_scales_energy_before_skill_reductions(
    size: int, cost: int, maintenance: int
) -> None:
    after, result, _ = cast(fixture(size=size), "magelock")
    assert result.energy_spent == cost
    assert latest(after)["magelock"].maintenance == maintenance
    after, result, _ = cast(fixture(size=size), "magelock", skill=20)
    assert result.energy_spent == cost - 2
    assert latest(after)["magelock"].maintenance == maintenance - 2


@pytest.mark.parametrize("unseen,target", [(False, 10), (True, 5)])
def test_regular_spell_range_is_measured_when_rolling_not_when_concentration_started(
    unseen: bool, target: int
) -> None:
    resources = fixture()
    starting = bound().model_copy(update={"distance": 1})
    resources, _ = apply_spell(
        resources,
        command(resources, "lockmaster", "start", "moving:start"),
        starting,
        rng=RecordedDice(()),
        system=True,
    )
    assert latest(resources)["lockmaster"].skill == 13
    for second in range(1, 10):
        resources = resources.model_copy(update={"game_time": second})
        resources, _ = apply_spell(
            resources,
            command(resources, "lockmaster", "concentrate", f"moving:{second}"),
            starting,
            rng=RecordedDice(()),
            system=True,
        )
    resources = resources.model_copy(update={"game_time": 10})
    after, result = apply_spell(
        resources,
        command(resources, "lockmaster", "complete", "moving:complete"),
        bound().model_copy(update={"distance": 4, "unseen": unseen}),
        rng=RecordedDice((1, 2, 2)),
        system=True,
    )
    assert result.checks[0].effective_target == target
    assert not locks(after)["door"].locked


def test_canonical_door_destruction_removes_the_access_barrier() -> None:
    from test_objects import fixture as object_fixture

    from wayfarer.engine.simulation.equipment.objects import DamageObject, apply_object

    engine, objects = object_fixture()
    before = fixture()
    value = locks(before)["door"]
    value = value.model_copy(
        update={"fixture": value.fixture.model_copy(update={"item_id": "sword"})}
    )
    before = save(
        before.model_copy(update={"items": objects.items, "owners": objects.owners}),
        value,
        "durable",
    )
    after, _, _ = cast(before, "magelock")
    assert not opening_allowed(after, locks(after)["door"])
    after, broken = apply_object(
        engine,
        after,
        DamageObject(
            id="destroy",
            actor_id="a",
            expected_revision=after.revision,
            item_id="sword",
            basic_damage=100,
            damage_type="cr",
        ),
        system=True,
    )
    assert broken.condition.destroyed
    assert opening_allowed(after, locks(after)["door"])
    assert not passage_blocked(after, "hall", "room")


def test_ordinary_operation_requires_one_canonical_free_hand() -> None:
    from wayfarer.engine.simulation.magic.lock_ready import require_free_hand

    require_free_hand(state(), "a", (("held", "left-hand"),))
    with pytest.raises(ValidationError, match="usable free hand"):
        require_free_hand(state(), "a", (("left", "left-hand"), ("right", "right-hand")))


def test_critical_failed_ward_remembers_actual_modified_resistance_skill() -> None:
    from wayfarer.engine.rules.types.toxin import Intoxication

    before = fixture().model_copy(
        update={"intoxications": (Intoxication(actor_id="a", window_started=0, level="tipsy"),)}
    )
    after, result, _ = cast(before, "magelock", dice=(6, 6, 6, 2, 2, 3))
    assert result.outcome == "critical-failure"
    assert result.checks[0].effective_target == 13
    assert latest(after)["magelock"].skill == 13


@pytest.mark.parametrize("distance,tipsy", [(12, False), (11, True)])
def test_b345_impossible_lock_roll_is_rejected_before_dice(distance: int, tipsy: bool) -> None:
    from wayfarer.engine.rules.types.toxin import Intoxication

    before = fixture().model_copy(
        update={
            "intoxications": (Intoxication(actor_id="a", window_started=0, level="tipsy"),)
            if tipsy
            else ()
        }
    )
    with pytest.raises(ValidationError, match="at least 3"):
        cast(
            before,
            "lockmaster",
            dice=(),
            final_context=bound().model_copy(update={"distance": distance}),
        )
    assert locks(before)["door"].locked
    # The boundary itself is legal; critical3 can succeed at effective3.
    after, result, _ = cast(
        fixture(),
        "lockmaster",
        dice=(1, 1, 1),
        final_context=bound().model_copy(update={"distance": 11}),
    )
    assert result.checks[0].effective_target == 3 and not locks(after)["door"].locked
