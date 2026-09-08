"""Durable Basic Set close-combat contracts (B370-371, B397, B405).

Numeric expectations are reconstructed from the selected 2004 rules baseline;
source-artifact review remains outstanding. No Technical Grappling control points.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Self

from pydantic import Field, model_validator

from wayfarer.errors import ValidationError
from wayfarer.rules.checks import CheckTrace, RandomSource
from wayfarer.rules.gurps_checks import Contestant, quick_contest, regular_contest_round
from wayfarer.rules.location_types import Hand
from wayfarer.simulation.resources import Id, Record, ResourceState

if TYPE_CHECKING:
    from wayfarer.simulation.combat import Encounter

BASIC = "gurps-basic-set-4e-2004"
UnarmedAction = Literal[
    "punch",
    "kick",
    "grapple",
    "break_free",
    "takedown",
    "pin",
    "release",
    "arm_lock",
    "lock_damage",
    "strangle",
]
UnarmedSkill = Literal[
    "attribute:dx",
    "skill:brawling",
    "skill:boxing",
    "skill:karate",
    "skill:judo",
    "skill:wrestling",
    "skill:sumo-wrestling",
]
GrappleLocation = Literal["torso", "neck", "left-arm", "right-arm", "left-leg", "right-leg"]


class Grip(Record):
    id: Id
    holder_id: Id
    target_id: Id
    hands: tuple[Hand, ...] = Field(min_length=1, max_length=2)
    location: GrappleLocation = "torso"
    skill: UnarmedSkill = "attribute:dx"
    acquired_round: int = Field(default=1, ge=1)
    arm_lock: bool = False
    escape_penalty: int = Field(default=0, ge=0)
    last_damage_round: int = Field(default=0, ge=0)
    hazard_id: str | None = None
    pinned: bool = False
    escape_after_round: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.holder_id == self.target_id or len(set(self.hands)) != len(self.hands):
            raise ValueError("Grip requires different actors and distinct hands")
        if self.arm_lock and (
            len(self.hands) != 2 or self.location not in ("left-arm", "right-arm")
        ):
            raise ValueError("Arm lock requires two hands and an arm")
        if self.pinned and self.location != "torso":
            raise ValueError("A pin requires a torso grapple")
        return self


class PendingUnarmed(Record):
    id: Id
    actor_id: Id
    target_id: Id
    action: Literal["punch", "kick", "grapple", "arm_lock"]
    grip_id: str | None = None
    skill: UnarmedSkill
    foot: Literal["left-foot", "right-foot"] = "right-foot"
    hands: tuple[Hand, ...] = ()
    location: GrappleLocation = "torso"
    allowed: tuple[Literal["dodge", "parry", "none"], ...]


class UnarmedReaction(Record):
    """An unarmed attack declared in advance as a Wait reaction (B366).

    Every parameter is fixed before the trigger fires, so the reaction cannot be
    re-chosen once an opponent commits. A reaction never includes a step, so
    close-combat entry is not declarable here.
    """

    action: Literal["punch", "kick", "grapple", "arm_lock"]
    skill: UnarmedSkill = "attribute:dx"
    hands: tuple[Hand, ...] = ()
    foot: Literal["left-foot", "right-foot"] = "right-foot"
    location: GrappleLocation = "torso"
    grip_id: str | None = None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if len(set(self.hands)) != len(self.hands):
            raise ValueError("Unarmed reaction repeats a hand")
        if (self.action == "arm_lock") != (self.grip_id is not None):
            raise ValueError("Only an arm-lock reaction names its grapple")
        return self


class UnarmedTrace(Record):
    action: UnarmedAction
    actor_id: Id
    target_id: Id
    intent: PendingUnarmed | None = None
    checks: tuple[CheckTrace, ...] = ()
    won: bool = False
    grip_id: str | None = None
    basic_damage: int = Field(default=0, ge=0)
    injury: int = Field(default=0, ge=0)
    damage_dice: tuple[int, ...] = ()
    blocked_reason: str | None = None
    # Extra table dice must survive even when a consequence is not yet implemented.
    table_dice: tuple[int, ...] = ()
    effect_dice: tuple[int, ...] = Field(default=(), exclude_if=lambda value: not value)
    effect_checks: tuple[CheckTrace, ...] = Field(default=(), exclude_if=lambda value: not value)
    resolved_location: GrappleLocation | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    # Ordered choices include an unused fallback, so a receipt retains full intent.
    defenses: tuple[tuple[Literal["dodge", "parry", "none"], str | None], ...] = Field(
        default=(), exclude_if=lambda value: not value
    )


def require_basic(profile_id: str) -> None:
    if profile_id != BASIC:
        raise ValidationError("Unarmed combat requires the exact Basic Set profile")


def wrestling_bonus(dx: int, skill: int | None) -> int:
    """B228: +1 ST at DX+1, +2 at DX+2 or better."""
    return 0 if skill is None else min(2, max(0, skill - dx))


def striking_bonus(skill_id: str, dx: int, skill: int) -> int:
    """B182/B200/B203, per damage die; no bonus for an untrained DX attack."""
    if skill_id == "skill:brawling":
        return int(skill >= dx + 2)
    if skill_id == "skill:boxing":
        return min(2, max(0, skill - dx))
    if skill_id == "skill:karate":
        return 2 if skill >= dx + 1 else int(skill >= dx)
    return 0


def contest(
    profile_id: str,
    actor_id: str,
    target_id: str,
    first: int,
    second: int,
    *,
    regular: bool = False,
    rng: RandomSource,
) -> tuple[bool, tuple[CheckTrace, ...], bool]:
    """One committed contest round. An undecided pin never rolls ahead in time."""
    require_basic(profile_id)
    if regular:
        a, b = regular_contest_round(
            profile_id,
            Contestant(actor_id, first),
            Contestant(target_id, second),
            rng=rng,
        )
        decided = a.outcome.succeeded != b.outcome.succeeded
        return decided and a.outcome.succeeded, (a, b), decided
    trace = quick_contest(
        profile_id,
        Contestant(actor_id, first),
        Contestant(target_id, second),
        rng=rng,
    )
    return trace.winner == actor_id, (trace.first, trace.second), trace.winner is not None


def validate_control(encounter: Encounter, resources: ResourceState, *, basic: bool) -> None:
    if not basic and (
        encounter.grips
        or encounter.close_pairs
        or encounter.pending_unarmed
        or encounter.unarmed_history
        or any(p.grappled or p.pinned or p.arm_locked for p in encounter.participants)
    ):
        raise ValidationError("Unarmed state requires exact Basic Set dispatch")
    participants = {p.actor_id: p for p in encounter.participants}
    if len({g.id for g in encounter.grips}) != len(encounter.grips):
        raise ValidationError("Duplicate grip ID")
    if len(set(encounter.close_pairs)) != len(encounter.close_pairs):
        raise ValidationError("Duplicate close-combat relationship")
    for pair in encounter.close_pairs:
        if (
            pair != tuple(sorted(pair))
            or pair[0] == pair[1]
            or not set(pair) <= participants.keys()
            or participants[pair[0]].position != participants[pair[1]].position
        ):
            raise ValidationError("Invalid close-combat relationship")
    occupied: set[tuple[str, Hand]] = set()
    for grip in encounter.grips:
        if (
            grip.holder_id not in participants
            or grip.target_id not in participants
            or tuple(sorted((grip.holder_id, grip.target_id))) not in encounter.close_pairs
        ):
            raise ValidationError("Grip requires an explicit shared close-combat position")
        holder = participants[grip.holder_id]
        for hand in grip.hands:
            key = (grip.holder_id, hand)
            if key in occupied or any(h == hand for _, h in holder.hand_bindings):
                raise ValidationError("Grip hand is already occupied")
            occupied.add(key)
    for actor in encounter.participants:
        incoming = tuple(g for g in encounter.grips if g.target_id == actor.actor_id)
        if actor.grappled != any(g.location == "torso" for g in incoming) or actor.pinned != any(
            g.pinned for g in incoming
        ):
            raise ValidationError("Combat control projection disagrees with grips")
        if actor.arm_locked != any(g.arm_lock for g in incoming):
            raise ValidationError("Arm-lock projection disagrees with grips")
    pending = encounter.pending_unarmed
    interrupt = encounter.wait_interrupt
    if pending is not None and (
        encounter.pending_defense is not None
        # A declared unarmed Wait reaction owns its own defense pause.
        or (interrupt is not None and not interrupt.reacting)
        or encounter.status != "active"
        or pending.actor_id != encounter.current_actor_id
        or pending.target_id not in participants
        or pending.target_id == pending.actor_id
        or not pending.allowed
        or "none" not in pending.allowed
        or len(set(pending.allowed)) != len(pending.allowed)
    ):
        raise ValidationError("Invalid unarmed defense pause")
