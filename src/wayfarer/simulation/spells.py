"""Provisional Basic Set spell lifecycle on the shared resource ledger.

Intended source: Characters first printing B235-241, B246-247, B249-250,
with 2007-01-26 errata. Numeric rules reconstructed under project policy;
source audit and full play adapters remain certification blockers.
"""

import hashlib
from typing import Literal

from pydantic import Field

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.checks import CheckTrace, Outcome, RandomSource
from wayfarer.rules.gurps_checks import success_roll
from wayfarer.rules.hazard_types import require_hazards_settled
from wayfarer.rules.recovery_types import interrupt_tasks, require_settled
from wayfarer.simulation.concentration import require_idle_concentration
from wayfarer.simulation.fatigue import FatigueCost, apply_fatigue
from wayfarer.simulation.resources import Command, Id, Receipt, Record, ResourceEvent, ResourceState

PROFILE: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
PREFIX = "spell:"
SpellId = Literal["light", "daze", "fireball", "create-fire"]


class SpellSpec(Record):
    id: SpellId
    kind: Literal["regular", "resisted", "missile", "area"]
    cost: int
    maintenance: int
    seconds: int
    duration: int | None
    magery: int = 0
    prerequisites: tuple[str, ...] = ()
    reference: str


SPELLS: dict[str, SpellSpec] = {
    "light": SpellSpec(
        id="light", kind="regular", cost=1, maintenance=1, seconds=1, duration=60, reference="B249"
    ),
    "daze": SpellSpec(
        id="daze",
        kind="resisted",
        cost=3,
        maintenance=2,
        seconds=2,
        duration=60,
        prerequisites=("foolishness",),
        reference="B250",
    ),
    "fireball": SpellSpec(
        id="fireball",
        kind="missile",
        cost=1,
        maintenance=0,
        seconds=1,
        duration=None,
        magery=1,
        prerequisites=("create-fire", "shape-fire"),
        reference="B247",
    ),
    "create-fire": SpellSpec(
        id="create-fire",
        kind="area",
        cost=2,
        maintenance=1,
        seconds=1,
        duration=60,
        prerequisites=("ignite-fire",),
        reference="B246",
    ),
}


class SpellContext(Record):
    """Trusted adapter input; never a player payload or inferred LLM ruling.

    Skill is the compiled trained spell skill, including Magery, before mana,
    range, shock and spells-on penalties. Magery -1 means no Magery advantage.
    """

    profile_id: str
    build_revision: Id
    skill: int = Field(ge=1, le=100)
    magery: int = Field(default=0, ge=-1, le=100)
    learned: tuple[str, ...] = ()
    mana: Literal["none", "low", "normal", "high", "very-high"] = "normal"
    ht: int = Field(default=10, ge=1)
    will: int = Field(default=10, ge=1)
    target_ht: int = Field(default=10, ge=1)
    distance: int = Field(default=0, ge=0, le=10000)
    target_id: Id
    radius: int = Field(default=1, ge=1, le=100)
    energy: int = Field(default=1, ge=1, le=100)
    distracted: bool = False
    unavailable: bool = False


class SpellCommand(Command):
    kind: Literal["start", "complete", "maintain", "cancel"]
    spell_id: SpellId
    cast_id: Id


class SpellEffect(Record):
    cast_id: Id
    actor_id: Id
    target_id: Id
    spell_id: SpellId
    build_revision: Id
    phase: Literal["casting", "active", "ended"]
    started_at: int
    ready_at: int
    expires_at: int | None = None
    skill: int
    cost: int
    maintenance: int
    hp_at_start: int
    radius: int = 1
    energy: int = 1
    distracted: bool = False


class SpellResult(Record):
    outcome: Literal[
        "casting", "active", "failed", "resisted", "cancelled", "interrupted", "critical-failure"
    ]
    energy_spent: int = 0
    checks: tuple[CheckTrace, ...] = ()


class SpellEvent(Record):
    effect: SpellEffect
    result: SpellResult


def event_id(command_id: str) -> str:
    return PREFIX + hashlib.sha256(command_id.encode()).hexdigest()


def latest(state: ResourceState) -> dict[str, SpellEffect]:
    found: dict[str, SpellEffect] = {}
    for event in state.events:
        if event.id.startswith(PREFIX):
            effect = SpellEvent.model_validate_json(event.kind).effect
            found[effect.cast_id] = effect
    return found


def active_spells(state: ResourceState) -> tuple[SpellEffect, ...]:
    return tuple(
        e
        for e in latest(state).values()
        if e.phase == "active" and (e.expires_at is None or state.game_time < e.expires_at)
    )


def interrupt_spells(
    state: ResourceState, actor_id: str, command_id: str, *, distraction: bool = False
) -> ResourceState:
    events = []
    for effect in latest(state).values():
        if effect.actor_id != actor_id or effect.phase != "casting":
            continue
        effect = effect.model_copy(
            update={"distracted": True} if distraction else {"phase": "ended"}
        )
        record = SpellEvent(
            effect=effect, result=SpellResult(outcome="casting" if distraction else "interrupted")
        )
        events.append(
            ResourceEvent(
                id=event_id(command_id + ":interrupt:" + effect.cast_id),
                at=state.game_time,
                target_id=actor_id,
                kind=record.model_dump_json(),
            )
        )
    return state.model_copy(update={"events": state.events + tuple(events)})


def cost_reduction(skill: int) -> int:
    return 0 if skill < 15 else 1 + (skill - 15) // 5


def casting_seconds(seconds: int, skill: int, *, missile: bool = False) -> int:
    if missile or skill < 20:
        return seconds
    divisor = 1 << (1 + (skill - 20) // 5)
    return max(1, (seconds + divisor - 1) // divisor)


def apply_spell(
    state: ResourceState,
    command: SpellCommand,
    context: SpellContext,
    *,
    rng: RandomSource,
    system: bool = False,
) -> tuple[ResourceState, SpellResult]:
    """Commit the returned checkpoint atomically using the existing store CAS.

    Completion must occur at the scheduled time; the turn adapter must call the
    interruption hook for other maneuvers. Late time cannot bank concentration.
    Critical failures persist a terminal result, never an invented table effect.
    """
    if not system or context.profile_id != PROFILE:
        raise ValidationError("Spell execution requires exact Basic Set authority")
    digest = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
    receipt = next((r for r in state.receipts if r.command_id == command.id), None)
    if receipt:
        if receipt.digest != digest:
            raise ConflictError("Spell command ID reused")
        previous_event = next(e for e in state.events if e.id == event_id(command.id))
        return state, SpellEvent.model_validate_json(previous_event.kind).result
    if state.revision != command.expected_revision:
        raise ConflictError("Spell revision changed")
    require_settled(
        state.recovery_tasks, frozenset({command.actor_id, context.target_id}), state.game_time
    )
    require_hazards_settled(
        state.hazards, frozenset({command.actor_id, context.target_id}), state.game_time
    )
    spec = SPELLS[command.spell_id]
    effect = latest(state).get(command.cast_id)
    fp = next((p for p in state.pools if p.id == "fp:" + command.actor_id), None)
    hp = next((p for p in state.pools if p.id == "hp:" + command.actor_id), None)
    if (
        fp is None
        or hp is None
        or fp.fatigue is None
        or hp.injury is None
        or fp.fatigue.profile_id != PROFILE
        or hp.injury.profile_id != PROFILE
    ):
        raise ValidationError("Spellcasting requires matching GURPS HP and FP pools")
    if effect and (effect.actor_id != command.actor_id or effect.spell_id != command.spell_id):
        raise ConflictError("Cast identity belongs to another actor or spell")
    if command.kind != "cancel":
        if context.mana in ("none", "very-high"):
            raise ValidationError("No-mana casting and unaudited very-high mana are unavailable")
        if (
            context.unavailable
            or hp.injury.incapacitated
            or hp.injury.stunned
            or fp.fatigue.collapsed
            or fp.fatigue.unconscious
            or fp.fatigue.heart_attack
        ):
            raise ValidationError("Caster cannot concentrate")
    checks: list[CheckTrace] = []
    spent = 0
    outcome: Literal[
        "casting", "active", "failed", "resisted", "cancelled", "interrupted", "critical-failure"
    ]
    if command.kind == "start":
        if effect is not None:
            raise ConflictError("Cast identity already used")
        require_idle_concentration(state, command.actor_id)
        if command.spell_id not in context.learned or not set(spec.prerequisites) <= set(
            context.learned
        ):
            raise ValidationError("Spell and prerequisites must be learned")
        if context.magery < spec.magery and not (spec.magery == 0 and context.mana == "high"):
            raise ValidationError("Required Magery unavailable")
        if (
            spec.kind != "area"
            and context.radius != 1
            or spec.kind != "missile"
            and context.energy != 1
        ):
            raise ValidationError("Spell does not accept this area or energy")
        if spec.kind == "missile" and context.energy > context.magery:
            raise ValidationError("Initial missile energy exceeds Magery")
        reduction = cost_reduction(context.skill)
        scale = (
            context.radius
            if spec.kind == "area"
            else context.energy
            if spec.kind == "missile"
            else 1
        )
        cost = max(0, spec.cost * scale - reduction)
        if fp.current < max(1, cost):
            raise ValidationError("Insufficient FP; HP-powered casting is unavailable")
        penalty = sum(1 for e in active_spells(state) if e.actor_id == command.actor_id)
        skill = context.skill - (5 if context.mana == "low" else 0) - hp.injury.shock - penalty
        if spec.kind != "missile":
            skill -= context.distance
        if skill < 1:
            raise ValidationError("Effective spell skill is below one")
        effect = SpellEffect(
            cast_id=command.cast_id,
            actor_id=command.actor_id,
            target_id=context.target_id,
            spell_id=command.spell_id,
            build_revision=context.build_revision,
            phase="casting",
            started_at=state.game_time,
            ready_at=state.game_time
            + casting_seconds(spec.seconds, context.skill, missile=spec.kind == "missile"),
            skill=skill,
            cost=cost,
            maintenance=max(
                0, spec.maintenance * (context.radius if spec.kind == "area" else 1) - reduction
            ),
            hp_at_start=hp.current,
            radius=context.radius,
            energy=context.energy,
        )
        outcome = "casting"
    else:
        if effect is None or effect.phase == "ended":
            raise ConflictError("Cast is not available")
        if command.kind == "cancel":
            if effect.phase == "active" and spec.kind == "missile":
                raise ValidationError("Held missile disposal requires the missile adapter")
            if (
                effect.phase == "active"
                and effect.expires_at is not None
                and state.game_time < effect.expires_at
            ):
                spent = 1  # B237: ending a running spell early is not free.
            effect = effect.model_copy(update={"phase": "ended"})
            outcome = "cancelled"
        else:
            if (
                effect.build_revision != context.build_revision
                or effect.target_id != context.target_id
            ):
                raise ConflictError("Spell binding changed")
            if command.kind == "maintain":
                if (
                    effect.phase != "active"
                    or effect.expires_at != state.game_time
                    or spec.duration is None
                    or spec.kind == "missile"
                ):
                    raise ConflictError("Maintenance is only available at expiry")
                spent = effect.maintenance
                effect = effect.model_copy(update={"expires_at": state.game_time + spec.duration})
                outcome = "active"
            else:
                if effect.phase != "casting" or state.game_time != effect.ready_at:
                    raise ConflictError("Complete concentration at its shared-clock deadline")
                if fp.current < effect.cost:
                    raise ConflictError("Caster no longer has casting energy")
                interrupted = False
                if effect.distracted or context.distracted or hp.current < effect.hp_at_start:
                    check = success_roll(PROFILE, context.will - 3, rng=rng)
                    checks.append(check)
                    interrupted = not check.outcome.succeeded
                if interrupted:
                    outcome = "interrupted"
                else:
                    check = success_roll(PROFILE, effect.skill, rng=rng)
                    checks.append(check)
                    if check.outcome is Outcome.CRITICAL_FAILURE:
                        outcome, spent = "critical-failure", effect.cost
                    elif not check.outcome.succeeded:
                        outcome, spent = "failed", min(1, effect.cost)
                    else:
                        outcome = "active"
                        spent = 0 if check.outcome is Outcome.CRITICAL_SUCCESS else effect.cost
                        if (
                            spec.kind == "resisted"
                            and check.outcome is not Outcome.CRITICAL_SUCCESS
                        ):
                            resistance = success_roll(PROFILE, context.target_ht, rng=rng)
                            checks.append(resistance)
                            # One casting roll serves as the attack roll. Rule of 16
                            # caps its contest margin; failed casting never affects.
                            margin = min(effect.skill, max(16, context.target_ht)) - sum(check.dice)
                            if resistance.outcome.succeeded and resistance.margin >= margin:
                                outcome = "resisted"
                effect = effect.model_copy(
                    update={
                        "phase": "active" if outcome == "active" else "ended",
                        "expires_at": state.game_time + spec.duration
                        if outcome == "active" and spec.duration
                        else None,
                    }
                )
    assert effect is not None
    if spent:
        if fp.current < spent:
            raise ConflictError("Caster no longer has reserved casting energy")
        state, _ = apply_fatigue(
            state,
            FatigueCost(
                id=event_id(command.id) + ":energy",
                actor_id=command.actor_id,
                expected_revision=state.revision,
                amount=spent,
            ),
            ht=context.ht,
            rng=rng,
            system=True,
        )
    state = state.model_copy(
        update={
            "recovery_tasks": interrupt_tasks(
                state.recovery_tasks, frozenset({command.actor_id}), state.game_time
            )
        }
    )
    result = SpellResult(outcome=outcome, energy_spent=spent, checks=tuple(checks))
    event = SpellEvent(effect=effect, result=result)
    return state.model_copy(
        update={
            "revision": command.expected_revision + 1,
            "receipts": state.receipts + (Receipt(command_id=command.id, digest=digest),),
            "events": state.events
            + (
                ResourceEvent(
                    id=event_id(command.id),
                    at=state.game_time,
                    target_id=command.actor_id,
                    kind=event.model_dump_json(),
                ),
            ),
        }
    ), result
