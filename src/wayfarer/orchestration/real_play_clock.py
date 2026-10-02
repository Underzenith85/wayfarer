"""Private elapsed play time, settled atomically from captured command instants.

The host persists this record under its revision lock. Only a trusted session
command may change ``running``; campaign advancement never changes this clock.
Microseconds survive pause/resume and command boundaries so a fractional-second
Luck use cannot become available early through integer-second rounding.
"""

from pydantic import Field, model_validator

from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record
from wayfarer.orchestration.clock import CommandInstant

MICROSECONDS_PER_SECOND = 1_000_000


class RealPlayCooldown(Record):
    actor_id: Id
    available_at_microseconds: int = Field(ge=0)


class RealPlayClock(Record):
    elapsed_microseconds: int = Field(default=0, ge=0)
    observed_at_us: int | None = Field(default=None, ge=0)
    running: bool = False
    cooldowns: tuple[RealPlayCooldown, ...] = ()

    @model_validator(mode="after")
    def valid_clock(self) -> RealPlayClock:
        if self.observed_at_us is None and (
            self.running or self.elapsed_microseconds or self.cooldowns
        ):
            raise ValueError("An unstarted play clock must be empty and paused")
        if len({entry.actor_id for entry in self.cooldowns}) != len(self.cooldowns):
            raise ValueError("Duplicate real-play cooldown actor")
        return self

    @property
    def elapsed_seconds(self) -> int:
        """Compatibility time for LuckState; microsecond deadlines remain authoritative."""
        return self.elapsed_microseconds // MICROSECONDS_PER_SECOND


def settle_real_play(
    clock: RealPlayClock,
    instant: CommandInstant,
    *,
    running: bool | None = None,
) -> RealPlayClock:
    """Settle the previous running interval, then optionally pause or resume.

    The first observation only establishes an anchor, even when resuming. Equal
    instants accrue no time. Reversed instants fail closed rather than moving an
    anchor backwards and later counting the same interval twice. The caller must
    supply the command boundary's captured/replayed instant, never capture anew.
    """
    if running is not None and type(running) is not bool:
        raise ValidationError("Real-play running state must be a boolean")
    now = instant.unix_microseconds
    previous = clock.observed_at_us
    if previous is not None and now < previous:
        raise ValidationError("Recorded real-play command instant cannot go backwards")
    elapsed = clock.elapsed_microseconds
    if previous is not None and clock.running:
        elapsed += now - previous
    return clock.model_copy(
        update={
            "elapsed_microseconds": elapsed,
            "observed_at_us": now,
            "running": clock.running if running is None else running,
        }
    )


def spend_real_play_cooldown(
    clock: RealPlayClock,
    *,
    actor_id: str,
    seconds: int,
) -> RealPlayClock:
    """Set one actor's next deadline from a new use, never bank unused intervals.

    Call after settling the command instant and checking exact retries. The host
    commits this returned clock together with the Luck choice and consequences;
    a failed transaction must discard it. Authority and Luck ownership stay with
    that host and the canonical Luck reducer.
    """
    if clock.observed_at_us is None:
        raise ValidationError("Real-play cooldown requires a recorded command instant")
    if type(seconds) is not int or seconds <= 0:
        raise ValidationError("Real-play cooldown must be positive integer seconds")
    if not actor_id:
        raise ValidationError("Real-play cooldown requires an actor")
    prior = next((entry for entry in clock.cooldowns if entry.actor_id == actor_id), None)
    if prior is not None and clock.elapsed_microseconds < prior.available_at_microseconds:
        raise ValidationError("Luck is cooling down in real play time")
    updated = RealPlayCooldown(
        actor_id=actor_id,
        available_at_microseconds=clock.elapsed_microseconds + seconds * MICROSECONDS_PER_SECOND,
    )
    cooldowns = tuple(entry for entry in clock.cooldowns if entry.actor_id != actor_id) + (updated,)
    return clock.model_copy(update={"cooldowns": cooldowns})
