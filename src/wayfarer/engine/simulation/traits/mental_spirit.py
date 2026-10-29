"""Typed, event-backed execution for authored mental and spirit trait channels."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Literal

from pydantic import Field
from pydantic import ValidationError as SchemaError

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
from wayfarer.engine.rules.resistance_attempts import (
    FailedResistanceAttempt,
    ResistanceAttempt,
    ResistancePreparation,
    prepare_resistance_attempt,
    resolve_resistance_attempt,
    validate_current_resistance_attempt,
)
from wayfarer.engine.rules.traits.mental_spirit import PROFILE
from wayfarer.engine.simulation.resources import (
    Command,
    Pool,
    ResourceEvent,
    ResourceState,
    Scheduled,
)
from wayfarer.engine.simulation.traits.mental_control import PREFIX as CONTROL_PREFIX
from wayfarer.engine.simulation.traits.mental_control import MentalControl, control_grants
from wayfarer.engine.simulation.traits.mental_resistance_origin import (
    MentalControlSource,
    MentalResistanceOrigin,
    capture,
    control_source,
    source_digest,
    validate_context_json,
    validate_digest,
    validate_event_json,
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
from wayfarer.engine.simulation.traits.psi_protection import protects
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
    target_visible: bool = True
    concentration_seconds: int = Field(default=1, ge=1)
    target_wary: bool = False
    target_psi_static: bool | None = None


class CurrentMentalChannel(MentalChannel):
    """Explicit trusted current provider context; mandatory scores have no floor."""

    actor_score: int = 10
    resistance_score: int | None = None


class MentalCommand(Command):
    definition_id: str
    channel_id: str
    kind: Literal["activate", "interrupt", "stop-concentrating"]


class MentalOutcome(Record):
    outcome: Literal["successful", "resisted", "blocked", "interrupted", "lingering"]
    actor_id: str
    target_id: str
    definition_id: str
    channel_id: str
    resistance: ResistanceAttempt | None = None
    revealed_fact_ids: tuple[str, ...] = ()
    effect_id: str | None = None
    expires_at: int | None = Field(default=None, ge=0)


class MentalEvent(Record):
    command_id: str
    command_kind: Literal["activate", "interrupt", "stop-concentrating"]
    outcome: MentalOutcome
    resistance_origin: MentalResistanceOrigin | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


def _event_id(command_id: str) -> str:
    return PREFIX + hashlib.sha256(command_id.encode()).hexdigest()


def _effect_id(channel_id: str) -> str:
    return PREFIX + "effect:" + hashlib.sha256(channel_id.encode()).hexdigest()


def _origin_channel(event: MentalEvent) -> CurrentMentalChannel:
    assert event.resistance_origin is not None
    origin = event.resistance_origin
    validate_digest(origin)
    validate_context_json(origin.command_json)
    validate_context_json(origin.channel_json)
    try:
        command = MentalCommand.model_validate_json(origin.command_json)
        channel = CurrentMentalChannel.model_validate_json(origin.channel_json)
    except SchemaError as exc:
        raise ValidationError("Invalid recorded mental provider context") from exc
    if (
        command.id != event.command_id
        or command.kind != event.command_kind
        or command.actor_id != event.outcome.actor_id
        or command.definition_id != event.outcome.definition_id
        or command.channel_id != event.outcome.channel_id
        or channel.id != command.channel_id
        or channel.actor_id != command.actor_id
        or channel.definition_id != command.definition_id
        or channel.target_id != event.outcome.target_id
        or channel.kind not in KINDS.get(command.definition_id, frozenset())
    ):
        raise ValidationError("Recorded mental provider identity changed")
    return channel


def _capture_control_sources(
    resources: ResourceState, channel: MentalChannel
) -> tuple[MentalControlSource, ...]:
    if channel.definition_id != "advantage:mind-control":
        return ()
    for existing_source in resources.events:
        if existing_source.id.startswith(CONTROL_PREFIX):
            validate_context_json(existing_source.kind)
    result = []
    for grant in control_grants(resources):
        if grant.controller_id != channel.actor_id or grant.kind != "influence":
            continue
        source = next(
            (
                e
                for e in reversed(resources.events)
                if e.id.startswith(CONTROL_PREFIX)
                and MentalControl.model_validate_json(e.kind) == grant
            ),
            None,
        )
        if source is None:
            raise ValidationError("Mind Control penalty lacks its actual native source")
        result.append(control_source(source))
    return tuple(result)


def _control_penalty(
    event: MentalEvent, authored: CurrentMentalChannel, source_events: tuple[ResourceEvent, ...]
) -> int:
    assert event.resistance_origin is not None
    origin = event.resistance_origin
    effects: set[str] = set()
    native_indices = tuple(
        i for i, e in enumerate(source_events) if e.id == _event_id(event.command_id)
    )
    before_index = native_indices[0] if native_indices else len(source_events)
    try:
        for reference in origin.control_sources:
            matching = tuple(e for e in source_events if e.id == reference.event_id)
            if len(matching) != 1:
                raise ValidationError("Mind Control penalty lacks a unique native source")
            source = matching[0]
            validate_context_json(source.kind)
            grant = MentalControl.model_validate_json(source.kind)
            if (
                source_digest(source) != reference.event_digest
                or not source.id.startswith(CONTROL_PREFIX)
                or source.id == CONTROL_PREFIX + event.command_id
                or next(i for i, e in enumerate(source_events) if e.id == source.id) >= before_index
                or not 0 <= source.at <= origin.at
                or source.target_id != grant.target_id
                or grant.controller_id != authored.actor_id
                or grant.kind != "influence"
                or grant.released
                or grant.expires_at is not None
                and grant.expires_at <= origin.at
                or grant.effect_id in effects
            ):
                raise ValidationError("Mind Control penalty source identity changed")
            effects.add(grant.effect_id)
    except SchemaError as exc:
        raise ValidationError("Invalid Mind Control penalty source") from exc
    return len(effects)


def _prepared_channel(
    event: MentalEvent, authored: CurrentMentalChannel, source_events: tuple[ResourceEvent, ...]
) -> CurrentMentalChannel:
    assert event.resistance_origin is not None
    encoded = event.resistance_origin.prepared_channel_json
    if encoded is None:
        raise ValidationError("Current mental provider lacks its actual derived context")
    validate_context_json(encoded)
    try:
        prepared = CurrentMentalChannel.model_validate_json(encoded)
    except SchemaError as exc:
        raise ValidationError("Invalid derived mental provider context") from exc
    mutable = {"actor_score", "resistance_score", "blocked"}
    if (
        authored.model_dump(exclude=mutable) != prepared.model_dump(exclude=mutable)
        or authored.blocked
        and not prepared.blocked
    ):
        raise ValidationError("Recorded mental provider changed its authored context")
    if authored.definition_id == "advantage:mind-control":
        bonus = (
            4
            if authored.concentration_seconds >= 3600
            else 2
            if authored.concentration_seconds >= 60
            else 0
        )
        scores_valid = (
            prepared.actor_score
            == authored.actor_score + bonus - _control_penalty(event, authored, source_events)
            and prepared.resistance_score == authored.resistance_score
        )
    elif authored.definition_id == "advantage:possession":
        scores_valid = (
            prepared.actor_score == authored.actor_score
            and authored.resistance_score is not None
            and prepared.resistance_score
            == authored.resistance_score + (5 if authored.target_wary else 0)
        )
    else:
        scores_valid = (prepared.actor_score, prepared.resistance_score) == (
            authored.actor_score,
            authored.resistance_score,
        )
    if (
        authored.definition_id != "advantage:mind-control"
        and event.resistance_origin.control_sources
    ):
        raise ValidationError("Only Mind Control derives a controller penalty")
    if not scores_valid:
        raise ValidationError("Recorded mental control/wary adjustments changed")
    return prepared


def _read_event(
    event: ResourceEvent,
    *,
    at: int,
    revision: int | None = None,
    source_events: tuple[ResourceEvent, ...] = (),
) -> MentalEvent:
    origin_present = validate_event_json(event.kind)
    try:
        value = MentalEvent.model_validate_json(event.kind)
    except SchemaError as exc:
        raise ValidationError("Invalid mental provider event") from exc
    origin = value.resistance_origin
    if origin_present and origin is None:
        raise ValidationError("Explicit mental provider origin cannot be null")
    if origin is not None:
        channel = _origin_channel(value)
        command = MentalCommand.model_validate_json(origin.command_json)
        if (
            event.id != _event_id(value.command_id)
            or event.target_id != value.outcome.target_id
            or event.at != origin.at
            or not 0 <= event.at <= at
            or revision is not None
            and command.expected_revision >= revision
        ):
            raise ValidationError("Recorded mental provider event envelope changed")
        actual = _prepared_channel(value, channel, source_events)
        if value.command_kind == "activate":
            expected = _current_attempt(actual)
            if value.outcome.resistance is not None:
                prepared = _current_preparation(actual)
                if prepared is None:
                    raise ValidationError("Unresisted mental event contains a resistance attempt")
                validate_current_resistance_attempt(
                    prepared,
                    value.outcome.resistance,
                    allow_optional_subject=channel.resistance_roll is not None,
                )
            if (
                expected != value.outcome.resistance
                or _activation_result(actual, expected) != value.outcome.outcome
            ):
                raise ValidationError("Mental resistance disagrees with its actual consequence")
        elif value.outcome.resistance is not None or value.outcome.outcome not in {
            "interrupted",
            "lingering",
        }:
            raise ValidationError("Stopped mental effect cannot contain a resistance attempt")
    elif isinstance(value.outcome.resistance, FailedResistanceAttempt):
        raise ValidationError("One-check mental resistance lacks current producer provenance")
    return value


def history(resources: ResourceState) -> tuple[MentalEvent, ...]:
    events = tuple(event for event in resources.events if event.id.startswith(PREFIX))
    if any(sum(other.id == event.id for other in events) != 1 for event in events):
        raise ValidationError("Duplicate mental provider event")
    return tuple(
        _read_event(
            event,
            at=resources.game_time,
            revision=resources.revision,
            source_events=resources.events,
        )
        for event in events
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
    if command.kind != "activate":
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


def _control_context(
    resources: ResourceState,
    channel: MentalChannel,
    traits: MentalSpiritTraits,
    *,
    current: bool = False,
) -> MentalChannel:
    if channel.definition_id not in {"advantage:mind-control", "advantage:possession"}:
        return channel
    purchase = traits.purchase(channel.definition_id)
    assert purchase is not None
    if purchase.modifiers:
        raise ValidationError("Control modifiers require their separately supported procedure")
    if channel.fatigue_cost or channel.resistance_score is None:
        raise ValidationError("Control requires its source contest without authored FP cost")
    if channel.kind == "possession":
        if not channel.touching_target:
            raise ValidationError("Possession requires physical touch")
        return channel.model_copy(
            update={
                "resistance_score": channel.resistance_score + (5 if channel.target_wary else 0)
            }
        )
    if not (channel.target_visible or channel.touching_target):
        raise ValidationError("Mind Control requires sight or touch")
    slaves = sum(
        grant.controller_id == channel.actor_id and grant.kind == "influence"
        for grant in control_grants(resources)
    )
    bonus = (
        4
        if channel.concentration_seconds >= 3600
        else 2
        if channel.concentration_seconds >= 60
        else 0
    )
    score = channel.actor_score + bonus - slaves
    return channel.model_copy(update={"actor_score": score if current else max(1, score)})


def _control_facts(
    resources: ResourceState,
    command: MentalCommand,
    channel: MentalChannel,
    resistance: ResistanceAttempt | None,
    result: str,
    effect_id: str,
) -> tuple[int | None, tuple[ResourceEvent, ...]] | None:
    if channel.definition_id not in {"advantage:mind-control", "advantage:possession"}:
        return None
    if result != "successful":
        return None, ()
    assert isinstance(resistance, ResistanceTrace)
    grant = MentalControl(
        controller_id=command.actor_id,
        target_id=channel.target_id,
        kind="possession" if channel.kind == "possession" else "influence",
        effect_id=effect_id,
        victory_margin=resistance.contest.victory_margin,
    )
    return None, (
        ResourceEvent(
            id=CONTROL_PREFIX + command.id,
            at=resources.game_time,
            target_id=channel.target_id,
            kind=grant.model_dump_json(),
        ),
    )


def _stop_control(
    resources: ResourceState,
    command: MentalCommand,
    channel: MentalChannel,
    outcome: MentalOutcome,
    active: tuple[str, ...],
    scheduled: tuple[Scheduled, ...],
) -> tuple[MentalOutcome, tuple[str, ...], tuple[Scheduled, ...], tuple[ResourceEvent, ...]]:
    grant = next((g for g in control_grants(resources) if g.effect_id == outcome.effect_id), None)
    if grant is None:
        if command.kind == "stop-concentrating":
            raise ValidationError("Only Mind Control can linger after concentration")
        return outcome, active, scheduled, ()
    if grant.kind == "possession":
        raise ValidationError("Base Possession requires transfer to another living host")
    if command.kind == "stop-concentrating":
        if grant.expires_at is not None:
            raise ConflictError("Mind Control concentration has already stopped")
        due = resources.game_time + grant.victory_margin * 60
        grant = grant.model_copy(update={"expires_at": due})
        outcome = outcome.model_copy(update={"outcome": "lingering", "expires_at": due})
        assert outcome.effect_id is not None
        active = tuple(sorted(set(active) | {outcome.effect_id}))
        scheduled += (
            Scheduled(
                id=CONTROL_PREFIX + command.id, due=due, kind="expire", target_id=outcome.effect_id
            ),
        )
    else:
        grant = grant.model_copy(update={"released": True, "expires_at": resources.game_time})
    return (
        outcome,
        active,
        scheduled,
        (
            ResourceEvent(
                id=CONTROL_PREFIX + command.id,
                at=resources.game_time,
                target_id=channel.target_id,
                kind=grant.model_dump_json(),
            ),
        ),
    )


def _neutralize_facts(
    resources: ResourceState,
    command: MentalCommand,
    channel: MentalChannel,
    resistance: ResistanceAttempt | None,
    result: str,
    effect_id: str,
    power_id: str | None,
    rng: RandomSource | None,
) -> tuple[int | None, tuple[ResourceEvent, ...]]:
    controlled = _control_facts(resources, command, channel, resistance, result, effect_id)
    if controlled is not None:
        return controlled
    if channel.kind != "neutralize":
        return (
            resources.game_time + channel.duration_seconds if result == "successful" else None,
            (),
        )
    if result == "successful":
        assert isinstance(resistance, ResistanceTrace)
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


def _apply_mental_use(
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
    current: bool,
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
            and (
                prior.resistance_origin is None
                or MentalCommand.model_validate_json(prior.resistance_origin.command_json)
                == command
            )
        ):
            return resources, world, prior.outcome
        raise ConflictError("Mental/spirit command ID was already used")
    if resources.revision != command.expected_revision:
        raise ConflictError("Mental/spirit revision changed")
    channel = _selected_channel(channels, command, current=current)
    origin = capture(command, channel, build.revision, resources.game_time) if current else None
    origin = (
        origin.model_copy(update={"control_sources": _capture_control_sources(resources, channel)})
        if origin is not None
        else None
    )
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

    channel = _control_context(resources, channel, traits, current=current)
    selected_power = _validate_neutralize(resources, command, channel, traits)
    channel = channel.model_copy(
        update={
            "blocked": channel.blocked
            or power_suppressed(
                resources, command.actor_id, channel.power_family, command.definition_id
            )
            or (channel.power_family == "psi" or channel.power_family in PSI_FAMILIES)
            and protects(resources, channel.target_id, purchased=channel.target_psi_static)
        }
    )
    origin = (
        origin.model_copy(update={"prepared_channel_json": channel.model_dump_json()})
        if origin is not None
        else None
    )
    extra_events: tuple[ResourceEvent, ...] = ()
    effect_id = _effect_id(channel.id)
    updated_world = world
    pools = resources.pools
    active = resources.active_effect_ids
    scheduled = resources.scheduled
    if command.kind in {"interrupt", "stop-concentrating"}:
        activation = next(
            (
                event.outcome
                for event in reversed(history(resources))
                if event.outcome.effect_id == effect_id
                and event.outcome.outcome in {"successful", "lingering"}
            ),
            None,
        )
        if (
            activation is None
            or effect_id not in active
            or not channel.interruptible
            or (activation.expires_at is not None and resources.game_time >= activation.expires_at)
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
        outcome, active, scheduled, extra_events = _stop_control(
            resources, command, channel, outcome, active, scheduled
        )
    else:
        pools, resistance = _activation_roll(resources, channel, current=current)
        result = _activation_result(channel, resistance)
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
            active = tuple(sorted(set(active) | {outcome.effect_id}))
            scheduled = scheduled + (
                ()
                if expires_at is None
                else (
                    Scheduled(
                        id=PREFIX + "expiry:" + hashlib.sha256(channel.id.encode()).hexdigest(),
                        due=expires_at,
                        kind="expire",
                        target_id=outcome.effect_id,
                    ),
                )
            )
        for fact_id in revealed:
            updated_world = updated_world.learn(command.actor_id, fact_id)

    event = MentalEvent(
        command_id=command.id,
        command_kind=command.kind,
        outcome=outcome,
        resistance_origin=origin,
    )
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


def _selected_channel(
    channels: tuple[MentalChannel, ...], command: MentalCommand, *, current: bool
) -> MentalChannel:
    channel = next((value for value in channels if value.id == command.channel_id), None)
    if (
        channel is None
        or channel.actor_id != command.actor_id
        or channel.definition_id != command.definition_id
        or channel.kind not in KINDS.get(command.definition_id, frozenset())
    ):
        raise ValidationError("Authored mental/spirit channel is unavailable")
    if current:
        if sum(value.id == command.channel_id for value in channels) != 1:
            raise ValidationError("Ambiguous current mental provider channel")
        if not isinstance(channel, CurrentMentalChannel):
            raise ValidationError("Current mental provider requires its explicit current context")
        try:
            return CurrentMentalChannel.model_validate(channel.model_dump())
        except SchemaError as exc:
            raise ValidationError("Invalid current mental provider context") from exc
    if isinstance(channel, CurrentMentalChannel):
        raise ValidationError("Historical mental provider cannot accept a current context")
    return channel


def _current_rule16(channel: MentalChannel) -> bool:
    return (
        channel.resistance_score is not None
        and channel.supernatural_attack
        and channel.target_living_or_sapient
    )


def _current_preparation(channel: MentalChannel) -> ResistancePreparation | None:
    if channel.resistance_score is None:
        if channel.resistance_roll is not None:
            raise ValidationError("Resistance roll requires a resistance score")
        if channel.actor_score < 3:
            raise ValidationError("Voluntary mental attempt requires effective skill at least 3")
        return None
    return prepare_resistance_attempt(
        PROFILE,
        Contestant("attacker", channel.actor_score),
        Contestant("resister", channel.resistance_score),
        rule_of_16=_current_rule16(channel),
    )


def _current_attempt(channel: MentalChannel) -> ResistanceAttempt | None:
    prepared = _current_preparation(channel)
    if prepared is None or channel.blocked:
        return None
    return resolve_resistance_attempt(
        prepared,
        attacker_dice=_recorded_total(channel.actor_roll),
        resister_dice=_recorded_total(channel.resistance_roll)
        if channel.resistance_roll is not None
        else None,
    )


def _activation_result(
    channel: MentalChannel, resistance: ResistanceAttempt | None
) -> Literal["successful", "resisted", "blocked"]:
    if channel.blocked:
        return "blocked"
    if resistance is not None:
        return "successful" if resistance.affected else "resisted"
    return (
        "successful"
        if success_roll(
            PROFILE, channel.actor_score, rng=RecordedDice(_recorded_total(channel.actor_roll))
        ).outcome.succeeded
        else "resisted"
    )


def _activation_roll(
    resources: ResourceState, channel: MentalChannel, *, current: bool
) -> tuple[tuple[Pool, ...], ResistanceAttempt | None]:
    if current:
        resistance = _current_attempt(channel)
        # Source-current score admission and supplied-roll completeness precede FP.
        return (
            _spent_fatigue(
                resources, channel.actor_id, 0 if channel.blocked else channel.fatigue_cost
            ),
            resistance,
        )
    pools = _spent_fatigue(
        resources, channel.actor_id, 0 if channel.blocked else channel.fatigue_cost
    )
    return pools, None if channel.blocked else _resistance(channel)


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
    """Historical provider: unchanged two-supplied-roll contract and event bytes."""
    return _apply_mental_use(
        resources,
        world,
        command,
        build,
        definitions,
        channels,
        authorized_actor_id=authorized_actor_id,
        system=system,
        rng=rng,
        current=False,
    )


def apply_current_mental_use(
    resources: ResourceState,
    world: World,
    command: MentalCommand,
    build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
    channels: tuple[CurrentMentalChannel, ...],
    *,
    authorized_actor_id: str,
    system: bool = False,
    rng: RandomSource | None = None,
) -> tuple[ResourceState, World, MentalOutcome]:
    """Explicit trusted-system provider; supplied totals are scoring inputs only."""
    return _apply_mental_use(
        resources,
        world,
        command,
        build,
        definitions,
        channels,
        authorized_actor_id=authorized_actor_id,
        system=system,
        rng=rng,
        current=True,
    )


def replay_mental_use(
    resources: ResourceState,
    world: World,
    command: MentalCommand,
    build: ValidatedBuild,
    definitions: Mapping[str, RuleDefinition],
    recorded_event: ResourceEvent,
    *,
    historical_channels: tuple[MentalChannel, ...] | None = None,
    rng: RandomSource | None = None,
) -> tuple[ResourceState, World, MentalOutcome]:
    """Replay a captured native producer, never infer omitted historical context."""
    if any(e.id == recorded_event.id for e in resources.events):
        raise ValidationError("Mental replay requires its pre-command state")
    event = _read_event(recorded_event, at=resources.game_time, source_events=resources.events)
    if (
        recorded_event.id != _event_id(command.id)
        or event.command_id != command.id
        or event.command_kind != command.kind
        or event.outcome.actor_id != command.actor_id
        or event.outcome.definition_id != command.definition_id
        or event.outcome.channel_id != command.channel_id
    ):
        raise ValidationError("Recorded mental replay command identity changed")
    if event.resistance_origin is not None:
        origin = event.resistance_origin
        if (
            origin.build_revision != build.revision
            or MentalCommand.model_validate_json(origin.command_json) != command
        ):
            raise ValidationError("Recorded mental replay build or command changed")
        result = apply_current_mental_use(
            resources,
            world,
            command,
            build,
            definitions,
            (_origin_channel(event),),
            authorized_actor_id=command.actor_id,
            system=True,
            rng=rng,
        )
    else:
        if historical_channels is None:
            raise ValidationError("Historical mental replay requires its authored context")
        result = apply_mental_use(
            resources,
            world,
            command,
            build,
            definitions,
            historical_channels,
            authorized_actor_id=command.actor_id,
            system=True,
            rng=rng,
        )
    regenerated = next(e for e in result[0].events if e.id == recorded_event.id)
    if regenerated != recorded_event or result[2] != event.outcome:
        raise ValidationError("Recorded mental replay result changed")
    return result
