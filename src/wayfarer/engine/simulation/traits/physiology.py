"""Atomic physiology intervals on the existing HP pool and event ledger."""

import hashlib
from collections.abc import Mapping
from typing import Literal

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits.physiology import PhysiologyTraits, physiology_traits
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.checks import RandomSource, draw_dice
from wayfarer.engine.simulation.health.healing import restore_hp
from wayfarer.engine.simulation.health.injury import InjuryResult, Wound, apply_injury
from wayfarer.engine.simulation.resources import Command, Pool, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record

PREFIX = "physiology:"


class PhysiologyInterval(Record):
    id: str
    actor_id: str
    kind: Literal["regeneration", "dependency", "weakness", "extra-life"]
    due: int = Field(ge=0)
    amount: int = Field(default=1, ge=1, le=1000)
    active: bool = True
    started: int = Field(default=0, ge=0, exclude_if=lambda value: value == 0)


class PhysiologyCommand(Command):
    interval_id: str


class PhysiologyOutcome(Record):
    actor_id: str
    kind: Literal["regenerated", "injured", "revived", "unavailable"]
    hp_before: int
    hp_after: int
    interval_id: str
    injury: InjuryResult | None = None
    damage_dice: tuple[int, ...] = ()


class PhysiologyEvent(Record):
    command_id: str
    outcome: PhysiologyOutcome
    interval: PhysiologyInterval | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    request_digest: str | None = Field(default=None, exclude_if=lambda value: value is None)


def _event_id(command_id: str) -> str:
    return PREFIX + hashlib.sha256(command_id.encode()).hexdigest()


def history(resources: ResourceState) -> tuple[PhysiologyEvent, ...]:
    return tuple(
        PhysiologyEvent.model_validate_json(event.kind)
        for event in resources.events
        if event.id.startswith(PREFIX)
    )


def _request_digest(command: PhysiologyCommand, interval: PhysiologyInterval) -> str:
    return hashlib.sha256(
        (command.model_dump_json() + "\n" + interval.model_dump_json()).encode()
    ).hexdigest()


def _require_regeneration_due(
    resources: ResourceState, interval: PhysiologyInterval, period: int
) -> None:
    if interval.due < interval.started + period or (interval.due - interval.started) % period:
        raise ValidationError("Regeneration interval differs from the approved rate")
    consumed: list[int] = []
    for raw in resources.events:
        if not raw.id.startswith(PREFIX):
            continue
        entry = PhysiologyEvent.model_validate_json(raw.kind)
        if entry.outcome.actor_id != interval.actor_id or entry.outcome.kind != "regenerated":
            continue
        if entry.outcome.interval_id == interval.id:
            raise ConflictError("Regeneration interval was already consumed")
        # Old checkpoints did not record a due tick. Their settlement tick is a
        # conservative lower bound for scheduling the next legal interval.
        consumed.append(raw.at if entry.interval is None else entry.interval.due)
    if consumed and interval.due < max(consumed) + period:
        raise ConflictError("Regeneration interval was already consumed or is too early")


def _extra_life_outcome(
    resources: ResourceState,
    interval: PhysiologyInterval,
    hp: Pool,
    traits: PhysiologyTraits,
) -> tuple[Literal["revived", "unavailable"], int]:
    used = sum(
        1
        for entry in history(resources)
        if entry.outcome.kind == "revived" and entry.outcome.actor_id == interval.actor_id
    )
    if hp.injury is None or hp.injury.profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Extra Life requires canonical Basic Set HP")
    purchase = traits.purchase("advantage:extra-life")
    assert purchase is not None
    if purchase.modifiers:
        raise ValidationError("Extra Life modifiers require a separately supported revival")
    if any(
        entry.outcome.kind == "revived"
        and entry.outcome.actor_id == interval.actor_id
        and entry.outcome.interval_id == interval.id
        for entry in history(resources)
    ):
        raise ConflictError("Extra Life interval was already consumed")
    if not hp.injury.dead or used >= traits.level("advantage:extra-life"):
        return "unavailable", hp.current
    return "revived", hp.maximum


def _settled_hp(hp: Pool, after: int, *, revived: bool) -> Pool:
    updated_hp = hp.model_copy(update={"current": after})
    if not revived:
        return updated_hp
    assert hp.injury is not None
    # Revival is distinct from healing: remove fatal and transient injury
    # conditions while retaining the actor's anatomy and durable injuries.
    return updated_hp.model_copy(
        update={
            "injury": hp.injury.model_copy(
                update={
                    "dead": False,
                    "unconscious": False,
                    "mortal_wound": False,
                    "mortal_wound_due": None,
                    "mortal_wound_started": 0,
                    "shock": 0,
                    "shock_expires": 0,
                    "stunned": False,
                    "electrical_stun": None,
                }
            )
        }
    )


def _harmful_interval(
    resources: ResourceState,
    command: PhysiologyCommand,
    interval: PhysiologyInterval,
    traits: PhysiologyTraits,
    required: str,
    build: ValidatedBuild,
    rng: RandomSource | None,
    hp: Pool,
) -> tuple[ResourceState, Pool, InjuryResult, tuple[int, ...]]:
    purchase = traits.purchase(required)
    assert purchase is not None
    if purchase.modifiers:
        raise ValidationError("Harmful physiology modifiers require a supported interval")
    frequency = traits.parameter(required, "interval")
    periods = (
        {"minute": 60, "five-minutes": 300, "thirty-minutes": 1800}
        if interval.kind == "weakness"
        else {"minute": 60, "hour": 3600, "day": 86400, "week": 604800}
    )
    period = periods.get(str(frequency))
    if period is None:
        raise ValidationError("Harmful physiology requires an approved frequency")
    first = interval.started + (
        period
        if interval.kind == "weakness" or frequency == "minute"
        else period + {"hour": 600, "day": 3600, "week": 21600}[str(frequency)]
    )
    cadence = (
        period
        if interval.kind == "weakness"
        else {"minute": 60, "hour": 600, "day": 3600, "week": 21600}[str(frequency)]
    )
    if interval.due < first or (interval.due - first) % cadence:
        raise ValidationError("Physiology interval differs from the approved frequency")
    previous = [
        entry.interval.due
        for entry in history(resources)
        if entry.interval is not None
        and entry.interval.actor_id == interval.actor_id
        and entry.interval.kind == interval.kind
        and entry.interval.started == interval.started
        and entry.outcome.kind == "injured"
    ]
    if previous and interval.due <= max(previous):
        raise ConflictError("Physiology interval was already consumed")
    if rng is None or build.statistics is None:
        raise ValidationError("Harmful physiology requires canonical injury randomness and HT")
    damage_dice = draw_dice(rng, 1) if interval.kind == "weakness" else ()
    damage = sum(damage_dice) if damage_dice else 1
    resources, injury_result = apply_injury(
        resources,
        Wound(
            id="physiology-injury:" + command.id,
            actor_id=command.actor_id,
            expected_revision=resources.revision,
            basic_damage=damage,
            resistance=0,
            damage_type="tox",
            injury_source="internal",
        ),
        ht=build.statistics.ht,
        rng=rng,
        system=True,
    )
    hp = next(pool for pool in resources.pools if pool.id == hp.id)
    return resources, hp, injury_result, damage_dice


def apply_physiology_interval(
    resources: ResourceState,
    command: PhysiologyCommand,
    interval: PhysiologyInterval,
    build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
    *,
    authorized_actor_id: str,
    system: bool = False,
    rng: RandomSource | None = None,
) -> tuple[ResourceState, PhysiologyOutcome]:
    if (
        not system
        or authorized_actor_id != command.actor_id
        or interval.actor_id != command.actor_id
    ):
        raise ValidationError("Physiology execution requires actor authority")
    digest = _request_digest(command, interval)
    prior = next((entry for entry in history(resources) if entry.command_id == command.id), None)
    if prior is not None:
        if (
            prior.outcome.actor_id == command.actor_id
            and prior.outcome.interval_id == command.interval_id
            and (prior.request_digest is None or prior.request_digest == digest)
        ):
            return resources, prior.outcome
        raise ConflictError("Physiology command ID was already used")
    if resources.revision != command.expected_revision:
        raise ConflictError("Physiology revision changed")
    if interval.id != command.interval_id or resources.game_time < interval.due:
        raise ValidationError("Physiology interval is not due")
    traits = physiology_traits(build, definitions)
    required = {
        "regeneration": "advantage:regeneration",
        "dependency": "disadvantage:dependency",
        "weakness": "disadvantage:weakness",
        "extra-life": "advantage:extra-life",
    }[interval.kind]
    hp = next((pool for pool in resources.pools if pool.id == "hp:" + command.actor_id), None)
    if hp is None:
        raise ValidationError("Physiology HP pool is unavailable")
    before = hp.current
    injury_result: InjuryResult | None = None
    damage_dice: tuple[int, ...] = ()
    original = resources
    if not traits.has(required) or not interval.active:
        kind: Literal["regenerated", "injured", "revived", "unavailable"] = "unavailable"
        after = before
    elif interval.kind == "regeneration":
        period = traits.regeneration_interval()
        if period is None:
            raise ValidationError("Regeneration requires an approved rate")
        purchase = traits.purchase(required)
        assert purchase is not None
        if "radiation-only" in purchase.modifiers:
            raise ValidationError("Radiation-only regeneration cannot restore HP")
        _require_regeneration_due(resources, interval, period)
        if hp.injury is None or hp.injury.profile_id != "gurps-basic-set-4e-2004":
            raise ValidationError("Regeneration requires canonical Basic Set HP")
        # B424 explicitly scales Regeneration along with other HP healing.
        healed_hp, _ = restore_hp(
            resources, hp, traits.regeneration_amount() * max(1, hp.maximum // 10), kind="natural"
        )
        kind, after = "regenerated", healed_hp.current
    elif interval.kind in {"dependency", "weakness"}:
        resources, hp, injury_result, damage_dice = _harmful_interval(
            resources, command, interval, traits, required, build, rng, hp
        )
        kind, after = "injured", hp.current
    else:
        kind, after = _extra_life_outcome(resources, interval, hp, traits)
    outcome = PhysiologyOutcome(
        actor_id=command.actor_id,
        kind=kind,
        hp_before=before,
        hp_after=after,
        interval_id=interval.id,
        injury=injury_result,
        damage_dice=damage_dice,
    )
    updated_hp = _settled_hp(hp, after, revived=kind == "revived")
    pools = tuple(pool if pool.id != hp.id else updated_hp for pool in resources.pools)
    event = PhysiologyEvent(
        command_id=command.id, outcome=outcome, interval=interval, request_digest=digest
    )
    return resources.model_copy(
        update={
            "revision": original.revision + 1,
            "pools": pools,
            "events": resources.events
            + (
                ResourceEvent(
                    id=_event_id(command.id),
                    at=resources.game_time,
                    target_id=command.actor_id,
                    kind=event.model_dump_json(),
                ),
            ),
        }
    ), outcome
