"""B66 real-play intervals retain fractions, exclude pauses and never bank uses."""

import pytest
from pydantic import ValidationError as RecordValidationError

from wayfarer.errors import ValidationError
from wayfarer.orchestration.clock import CommandInstant
from wayfarer.orchestration.real_play_clock import (
    RealPlayClock,
    RealPlayCooldown,
    settle_real_play,
    spend_real_play_cooldown,
)


def test_only_running_intervals_count_and_subseconds_survive_restart() -> None:
    initial = RealPlayClock()
    clock = settle_real_play(initial, CommandInstant(100_000_000))
    assert clock.elapsed_microseconds == 0 and not clock.running
    clock = settle_real_play(clock, CommandInstant(200_000_000), running=True)
    assert clock.elapsed_microseconds == 0
    clock = settle_real_play(clock, CommandInstant(200_400_001), running=False)
    assert clock.elapsed_microseconds == 400_001 and clock.elapsed_seconds == 0
    # Loading a paused checkpoint excludes downtime without losing its remainder.
    clock = RealPlayClock.model_validate_json(clock.model_dump_json())
    clock = settle_real_play(clock, CommandInstant(900_000_000))
    clock = settle_real_play(clock, CommandInstant(999_000_000), running=True)
    clock = settle_real_play(clock, CommandInstant(999_599_999))
    assert clock.elapsed_microseconds == 1_000_000 and clock.elapsed_seconds == 1
    assert initial == RealPlayClock()


@pytest.mark.parametrize("running", [False, True])
def test_equal_instants_are_valid_but_reversed_instants_never_reset_anchor(running: bool) -> None:
    clock = settle_real_play(RealPlayClock(), CommandInstant(100), running=running)
    assert settle_real_play(clock, CommandInstant(100)) == clock
    with pytest.raises(ValidationError, match="cannot go backwards"):
        settle_real_play(clock, CommandInstant(99), running=not running)
    assert clock.observed_at_us == 100
    assert settle_real_play(clock, CommandInstant(110)).elapsed_microseconds == (
        10 if running else 0
    )


def test_repeated_resume_and_pause_keep_the_previous_interval_mode() -> None:
    clock = settle_real_play(RealPlayClock(), CommandInstant(0), running=True)
    clock = settle_real_play(clock, CommandInstant(10), running=True)
    clock = settle_real_play(clock, CommandInstant(20), running=False)
    clock = settle_real_play(clock, CommandInstant(30), running=False)
    assert clock.elapsed_microseconds == 20
    clock = settle_real_play(clock, CommandInstant(40), running=True)
    assert clock.elapsed_microseconds == 20
    assert settle_real_play(clock, CommandInstant(45)).elapsed_microseconds == 25


def test_integer_arithmetic_preserves_microseconds_above_float_exact_range() -> None:
    anchor = 2**53 + 1
    clock = settle_real_play(RealPlayClock(), CommandInstant(anchor), running=True)
    clock = settle_real_play(clock, CommandInstant(anchor + 1))
    assert clock.elapsed_microseconds == 1
    clock = settle_real_play(clock, CommandInstant(anchor + 1_000_000))
    assert clock.elapsed_microseconds == 1_000_000 and clock.elapsed_seconds == 1


@pytest.mark.parametrize("seconds", [3600, 1800, 600])
def test_fractional_use_unlocks_at_exact_elapsed_deadline_not_rounded_seconds(seconds: int) -> None:
    clock = settle_real_play(RealPlayClock(), CommandInstant(0), running=True)
    clock = settle_real_play(clock, CommandInstant(900_001))
    spent = spend_real_play_cooldown(clock, actor_id="worker", seconds=seconds)
    deadline = seconds * 1_000_000 + 900_001
    assert spent.elapsed_seconds == 0
    assert spent.cooldowns[0].available_at_microseconds == deadline
    early = settle_real_play(spent, CommandInstant(deadline - 1))
    assert early.elapsed_seconds == seconds
    with pytest.raises(ValidationError, match="cooling down"):
        spend_real_play_cooldown(early, actor_id="worker", seconds=seconds)
    ready = settle_real_play(early, CommandInstant(deadline))
    again = spend_real_play_cooldown(ready, actor_id="worker", seconds=seconds)
    assert again.cooldowns[0].available_at_microseconds == deadline + seconds * 1_000_000
    assert clock.cooldowns == ()


def test_cooldowns_are_actor_local_and_pausing_cannot_unlock_or_bank_uses() -> None:
    clock = settle_real_play(RealPlayClock(), CommandInstant(0), running=True)
    clock = spend_real_play_cooldown(clock, actor_id="worker", seconds=600)
    clock = spend_real_play_cooldown(clock, actor_id="other", seconds=3600)
    assert len(clock.cooldowns) == 2
    clock = settle_real_play(clock, CommandInstant(1_000_000), running=False)
    clock = settle_real_play(clock, CommandInstant(10_000_000_000), running=True)
    with pytest.raises(ValidationError, match="cooling down"):
        spend_real_play_cooldown(clock, actor_id="worker", seconds=600)
    clock = settle_real_play(clock, CommandInstant(99_000_000_000))
    clock = spend_real_play_cooldown(clock, actor_id="worker", seconds=600)
    assert len(clock.cooldowns) == 2
    with pytest.raises(ValidationError, match="cooling down"):
        spend_real_play_cooldown(clock, actor_id="worker", seconds=600)
    assert RealPlayClock.model_validate_json(clock.model_dump_json()) == clock


def test_invalid_clock_records_and_cooldown_inputs_fail_closed() -> None:
    with pytest.raises(RecordValidationError, match="unstarted"):
        RealPlayClock(running=True)
    with pytest.raises(RecordValidationError, match="Duplicate"):
        RealPlayClock(
            observed_at_us=0,
            cooldowns=(
                RealPlayCooldown(actor_id="a", available_at_microseconds=1),
                RealPlayCooldown(actor_id="a", available_at_microseconds=2),
            ),
        )
    with pytest.raises(RecordValidationError):
        RealPlayClock(elapsed_microseconds=True)
    with pytest.raises(ValidationError, match="recorded command instant"):
        spend_real_play_cooldown(RealPlayClock(), actor_id="a", seconds=600)
    clock = settle_real_play(RealPlayClock(), CommandInstant(0))
    for seconds in (0, -1, True):
        with pytest.raises(ValidationError, match="positive integer"):
            spend_real_play_cooldown(clock, actor_id="a", seconds=seconds)
    with pytest.raises(ValidationError, match="requires an actor"):
        spend_real_play_cooldown(clock, actor_id="", seconds=600)
