"""Typed, event-backed execution for authored mental and spirit trait channels."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Literal

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits.mental_spirit import MentalSpiritTraits, mental_spirit_traits
from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.checks import Outcome, RandomSource, RecordedDice, draw_dice
from wayfarer.engine.rules.gurps_checks import (
    Contestant,
    ResistanceTrace,
    resistance_roll,
    success_roll,
)
from wayfarer.engine.rules.traits.mental_spirit import PROFILE
from wayfarer.engine.simulation.resources import (
    Command,
    Pool,
    ResourceEvent,
    ResourceState,
    Scheduled,
)
from wayfarer.engine.simulation.traits.neutralization import (
    COOLDOWN_PREFIX,
    PSI_FAMILIES,
    Neutralization,
    NeutralizeCooldown,
    neutralize_crippled,
    power_suppressed,
    suppressions,
)
from wayfarer.engine.simulation.traits.neutralization import PREFIX as NEUTRALIZATION_PREFIX
from wayfarer.engine.world import World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record

PREFIX = "mental-spirit-use:"
MentalKind = Literal[
    "influence", "probe", "read", "possession", "terror", "neutralize", "foresight", "spirit"
]

KINDS: Mapping[str, frozenset[MentalKind]] = {
    "advantage:dominance": frozenset({"influence"}),
    "advantage:mind-control": frozenset({"influence"}),
    "advantage:mind-probe": frozenset({"probe"}),
    "advantage:mind-reading": frozenset({"read"}),
    "advantage:neutralize": frozenset({"neutralize"}),
    "advantage:possession": frozenset({"possession"}),
    "advantage:precognition": frozenset({"foresight"}),
    "advantage:oracle": frozenset({"foresight"}),
    "advantage:psychometry": frozenset({"probe"}),
    "advantage:racial-memory": frozenset({"probe"}),
    "advantage:terror": frozenset({"terror"}),
    "advantage:true-faith": frozenset({"terror"}),
    "advantage:channeling": frozenset({"spirit"}),
    "advantage:medium": frozenset({"spirit"}),
    "advantage:spirit-empathy": frozenset({"spirit"}),
}


class MentalChannel(Record):
    id: str
    definition_id: str
    actor_id: str
    target_id: str
    location_id: str
    kind: MentalKind
    fact_ids: tuple[str, ...] = ()
    actor_score: int = Field(default=10, ge=1)
    resistance_score: int | None = Field(default=None, ge=1)
    actor_roll: int = Field(default=10, ge=3, le=18)
    resistance_roll: int | None = Field(default=None, ge=3, le=18)
    duration_seconds: int = Field(default=1, ge=1)
    fatigue_cost: int = Field(default=0, ge=0)
    interruptible: bool = True
    blocked: bool = False
    power_family: str = "psi"
    # Trusted authors declare the B349 scope; technological attacks and
    # nonliving, nonsapient targets do not use the supernatural resistance cap.
    supernatural_attack: bool = True
    target_living_or_sapient: bool = True
    touching_target: bool = False


class MentalCommand(Command):
    definition_id: str
    channel_id: str
    kind: Literal["activate", "interrupt"]


class MentalOutcome(Record):
    outcome: Literal["successful", "resisted", "blocked", "interrupted"]
    actor_id: str
    target_id: str
    definition_id: str
    channel_id: str
    resistance: ResistanceTrace | None = None
    revealed_fact_ids: tuple[str, ...] = ()
    effect_id: str | None = None
    expires_at: int | None = Field(default=None, ge=0)


class MentalEvent(Record):
    command_id: str
    command_kind: Literal["activate", "interrupt"]
    outcome: MentalOutcome


def _event_id(command_id: str) -> str:
    return PREFIX + hashlib.sha256(command_id.encode()).hexdigest()


def _effect_id(channel_id: str) -> str:
    return PREFIX + "effect:" + hashlib.sha256(channel_id.encode()).hexdigest()


def history(resources: ResourceState) -> tuple[MentalEvent, ...]:
    return tuple(
        MentalEvent.model_validate_json(event.kind)
        for event in resources.events
        if event.id.startswith(PREFIX)
    )


def _spent_fatigue(resources: ResourceState, actor_id: str, amount: int) -> tuple[Pool, ...]:
    if amount == 0:
        return resources.pools
    pool_id = "fp:" + actor_id
    pool = next((value for value in resources.pools if value.id == pool_id), None)
    if pool is None or pool.current < amount:
        raise ValidationError("Mental/spirit use requires available fatigue")
    return tuple(
        value.model_copy(update={"current": value.current - amount})
        if value.id == pool_id
        else value
        for value in resources.pools
    )


def _recorded_total(total: int) -> tuple[int, int, int]:
    """Adapt authored 3d6 totals to the shared scorer without drawing new dice.

    Channels predate individual die receipts. This canonical decomposition is
    scoring input only, not a claim about the original physical dice.
    """
    if not 3 <= total <= 18:
        raise ValidationError("Mental/spirit roll total must be in 3-18")
    first = min(6, total - 2)
    second = min(6, total - first - 1)
    return first, second, total - first - second


def _resistance(channel: MentalChannel) -> ResistanceTrace | None:
    if channel.resistance_score is None:
        if channel.resistance_roll is not None:
            raise ValidationError("Resistance roll requires a resistance score")
        return None
    if channel.resistance_roll is None:
        raise ValidationError("Resistance score requires a resistance roll")
    # B68-76 specify resisted Quick Contests for these attack advantages.
    capped = channel.definition_id in {
        "advantage:mind-control",
        "advantage:mind-probe",
        "advantage:mind-reading",
        "advantage:neutralize",
        "advantage:possession",
    }
    return resistance_roll(
        PROFILE,
        Contestant("attacker", channel.actor_score),
        Contestant("resister", channel.resistance_score),
        rule_of_16=capped and channel.supernatural_attack and channel.target_living_or_sapient,
        rng=RecordedDice(
            _recorded_total(channel.actor_roll) + _recorded_total(channel.resistance_roll)
        ),
    )


def _validate_neutralize(
    resources: ResourceState,
    command: MentalCommand,
    channel: MentalChannel,
    traits: MentalSpiritTraits,
) -> str | None:
    if channel.kind != "neutralize":
        return None
    if command.kind == "interrupt":
        raise ValidationError("Neutralize lasts for its source duration and cannot be interrupted")
    if (
        not channel.touching_target
        or channel.resistance_score is None
        or channel.fatigue_cost
        or channel.power_family not in PSI_FAMILIES | {"psi"}
    ):
        raise ValidationError(
            "Neutralize requires touch and a Will contest without authored FP cost"
        )
    purchase = traits.purchase("advantage:neutralize")
    assert purchase is not None
    if "power-theft" in purchase.modifiers:
        raise ValidationError(
            "Neutralize Power Theft requires a separately supported power transfer"
        )
    if neutralize_crippled(resources, command.actor_id):
        raise ValidationError("Neutralize is crippled after a critical failure")
    if any(
        effect.actor_id == command.actor_id
        and effect.target_id == channel.target_id
        and effect.effect_id in resources.active_effect_ids
        and resources.game_time < effect.expires_at
        for effect in suppressions(resources)
    ):
        raise ConflictError("Neutralize cannot affect this subject again until power recovery")
    selected = next(
        (
            modifier.removeprefix("one-power-")
            for modifier in purchase.modifiers
            if modifier.startswith("one-power-")
        ),
        None,
    )
    return None if selected is None else "power:" + selected


def _neutralize_facts(
    resources: ResourceState,
    command: MentalCommand,
    channel: MentalChannel,
    resistance: ResistanceTrace | None,
    result: str,
    effect_id: str,
    power_id: str | None,
    rng: RandomSource | None,
) -> tuple[int | None, tuple[ResourceEvent, ...]]:
    if channel.kind != "neutralize":
        return (
            resources.game_time + channel.duration_seconds if result == "successful" else None,
            (),
        )
    if result == "successful":
        assert resistance is not None
        expires_at = resources.game_time + resistance.contest.victory_margin * 60
        fact = Neutralization(
            actor_id=command.actor_id,
            target_id=channel.target_id,
            power_id=power_id,
            effect_id=effect_id,
            expires_at=expires_at,
        )
        return expires_at, (
            ResourceEvent(
                id=NEUTRALIZATION_PREFIX + command.id,
                at=resources.game_time,
                target_id=channel.target_id,
                kind=fact.model_dump_json(),
            ),
        )
    if resistance is not None and resistance.attacker.outcome is Outcome.CRITICAL_FAILURE:
        if rng is None:
            raise ValidationError("Neutralize critical failure requires authoritative randomness")
        duration = sum(draw_dice(rng, 1)) * 3600
        fact_failure = NeutralizeCooldown(
            actor_id=command.actor_id, expires_at=resources.game_time + duration
        )
        return None, (
            ResourceEvent(
                id=COOLDOWN_PREFIX + command.id,
                at=resources.game_time,
                target_id=command.actor_id,
                kind=fact_failure.model_dump_json(),
            ),
        )
    return None, ()


def apply_mental_use(
    resources: ResourceState,
    world: World,
    command: MentalCommand,
    build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
    channels: tuple[MentalChannel, ...],
    *,
    authorized_actor_id: str,
    system: bool = False,
    rng: RandomSource | None = None,
) -> tuple[ResourceState, World, MentalOutcome]:
    if not system or authorized_actor_id != command.actor_id:
        raise ValidationError("Mental/spirit execution requires actor authority")
    prior = next((event for event in history(resources) if event.command_id == command.id), None)
    if prior is not None:
        if (
            prior.outcome.actor_id == command.actor_id
            and prior.command_kind == command.kind
            and prior.outcome.channel_id == command.channel_id
            and prior.outcome.definition_id == command.definition_id
        ):
            return resources, world, prior.outcome
        raise ConflictError("Mental/spirit command ID was already used")
    if resources.revision != command.expected_revision:
        raise ConflictError("Mental/spirit revision changed")
    channel = next((value for value in channels if value.id == command.channel_id), None)
    if (
        channel is None
        or channel.actor_id != command.actor_id
        or channel.definition_id != command.definition_id
        or channel.kind not in KINDS.get(command.definition_id, frozenset())
    ):
        raise ValidationError("Authored mental/spirit channel is unavailable")
    traits = mental_spirit_traits(build, definitions)
    if not traits.has(command.definition_id):
        raise ValidationError("Mental/spirit trait is not in the approved build")
    entities = {entity.id: entity for entity in world.entities}
    facts = {fact.id for fact in world.facts}
    if (
        command.actor_id not in entities
        or channel.target_id not in entities
        or channel.location_id not in entities
        or entities[command.actor_id].location_id != channel.location_id
        or entities[channel.target_id].location_id != channel.location_id
        or not set(channel.fact_ids) <= facts
    ):
        raise ValidationError("Mental/spirit channel context changed")

    selected_power = _validate_neutralize(resources, command, channel, traits)
    channel = channel.model_copy(
        update={
            "blocked": channel.blocked
            or power_suppressed(
                resources, command.actor_id, channel.power_family, command.definition_id
            )
        }
    )
    extra_events: tuple[ResourceEvent, ...] = ()
    effect_id = _effect_id(channel.id)
    updated_world = world
    pools = resources.pools
    active = resources.active_effect_ids
    scheduled = resources.scheduled
    if command.kind == "interrupt":
        activation = next(
            (
                event.outcome
                for event in reversed(history(resources))
                if event.outcome.effect_id == effect_id and event.outcome.outcome == "successful"
            ),
            None,
        )
        if (
            activation is None
            or effect_id not in active
            or not channel.interruptible
            or activation.expires_at is None
            or resources.game_time >= activation.expires_at
        ):
            raise ConflictError("Mental/spirit effect cannot be interrupted")
        outcome = MentalOutcome(
            outcome="interrupted",
            actor_id=command.actor_id,
            target_id=channel.target_id,
            definition_id=command.definition_id,
            channel_id=channel.id,
            effect_id=effect_id,
            expires_at=resources.game_time,
        )
        active = tuple(value for value in active if value != effect_id)
        scheduled = tuple(value for value in scheduled if value.target_id != effect_id)
    else:
        pools = _spent_fatigue(
            resources, command.actor_id, 0 if channel.blocked else channel.fatigue_cost
        )
        result: Literal["successful", "resisted", "blocked"]
        resistance = None if channel.blocked else _resistance(channel)
        if channel.blocked:
            result = "blocked"
        elif resistance is not None and not resistance.affected:
            result = "resisted"
        elif (
            resistance is None
            and not success_roll(
                PROFILE, channel.actor_score, rng=RecordedDice(_recorded_total(channel.actor_roll))
            ).outcome.succeeded
        ):
            result = "resisted"
        else:
            result = "successful"
        persistent = channel.kind in {"influence", "possession", "neutralize"}
        expires_at, extra_events = _neutralize_facts(
            resources, command, channel, resistance, result, effect_id, selected_power, rng
        )
        revealed = (
            channel.fact_ids
            if result == "successful"
            and channel.kind
            in {
                "probe",
                "read",
                "foresight",
                "spirit",
            }
            else ()
        )
        outcome = MentalOutcome(
            outcome=result,
            actor_id=command.actor_id,
            target_id=channel.target_id,
            definition_id=command.definition_id,
            channel_id=channel.id,
            revealed_fact_ids=revealed,
            resistance=resistance,
            effect_id=effect_id if result == "successful" and persistent else None,
            expires_at=expires_at,
        )
        if outcome.effect_id is not None:
            assert expires_at is not None
            active = tuple(sorted(set(active) | {outcome.effect_id}))
            scheduled = scheduled + (
                Scheduled(
                    id=PREFIX + "expiry:" + hashlib.sha256(channel.id.encode()).hexdigest(),
                    due=expires_at,
                    kind="expire",
                    target_id=outcome.effect_id,
                ),
            )
        for fact_id in revealed:
            updated_world = updated_world.learn(command.actor_id, fact_id)

    event = MentalEvent(command_id=command.id, command_kind=command.kind, outcome=outcome)
    updated = resources.model_copy(
        update={
            "revision": resources.revision + 1,
            "pools": pools,
            "active_effect_ids": active,
            "scheduled": scheduled,
            "events": resources.events
            + extra_events
            + (
                ResourceEvent(
                    id=_event_id(command.id),
                    at=resources.game_time,
                    target_id=channel.target_id,
                    kind=event.model_dump_json(),
                ),
            ),
        }
    )
    return updated, updated_world, outcome
