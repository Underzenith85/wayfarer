"""Authoritative campaign administration and trap procedures (Basic Set B494-B503)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict
from typing import Annotated, Literal, Protocol

from pydantic import Field, TypeAdapter, model_validator

from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.social.gurps_social import ReactionModifier, ReactionTrace, reaction_roll
from wayfarer.engine.simulation.campaign.advancement import AdvancementEntry
from wayfarer.engine.simulation.resources import Receipt, ResourceEvent, ResourceState
from wayfarer.engine.world import World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record


class ReactionContext(Record):
    id: Id
    npc_id: Id
    modifiers: tuple[ReactionModifier, ...] = ()


class KnowledgeSource(Record):
    id: Id
    fact_ids: tuple[Id, ...] = Field(min_length=1)
    audience: Literal["actor", "campaign", "gm"] = "actor"
    provenance: str = Field(min_length=1, max_length=1000)


class AwardRule(Record):
    id: Id
    recipient_id: Id
    points: int = Field(ge=0, le=10000)
    reason: str = Field(min_length=1, max_length=2000)


class ActivityRule(Record):
    id: Id
    kind: Literal["study", "job", "healing", "maintenance", "long-task", "rest"]
    credit_kind: Literal["study", "job", "healing", "maintenance", "task", "rest"]
    load: int = Field(default=100, ge=1, le=100)
    credit_numerator: int = Field(default=1, ge=0, le=100)
    credit_denominator: int = Field(default=1, ge=1, le=100)


class TrapConsequence(Record):
    kind: Literal["injury", "hazard", "effect", "alarm", "capture"]
    target: str = Field(min_length=1, max_length=200)
    amount: int = Field(default=0, ge=0)


class TrapRule(Record):
    id: Id
    trigger_id: Id
    detection_target: int
    disarm_target: int
    avoidance_target: int | None = None
    resets: bool = False
    consequence: TrapConsequence


class AdministrationRules(Record):
    id: Id
    version: int = Field(ge=1)
    profile_id: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
    reactions: tuple[ReactionContext, ...] = ()
    knowledge: tuple[KnowledgeSource, ...] = ()
    awards: tuple[AwardRule, ...] = ()
    activities: tuple[ActivityRule, ...] = ()
    traps: tuple[TrapRule, ...] = ()

    @model_validator(mode="after")
    def unique_rules(self) -> AdministrationRules:
        for values in (self.reactions, self.knowledge, self.awards, self.activities, self.traps):
            if len({value.id for value in values}) != len(values):
                raise ValueError("Duplicate campaign administration rule ID")
        return self


class KnowledgeAcquisition(Record):
    id: Id
    source_id: Id
    fact_ids: tuple[Id, ...]
    actor_ids: tuple[Id, ...]
    audience: Literal["actor", "campaign", "gm"]
    provenance: str
    at: int = Field(ge=0)


class ActivityAllocation(Record):
    activity_id: Id
    start: int = Field(ge=0)
    end: int = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self) -> ActivityAllocation:
        if self.end <= self.start:
            raise ValueError("Time Use interval must have positive duration")
        return self


class TimeCredit(Record):
    activity_id: Id
    kind: Literal["study", "job", "healing", "maintenance", "task", "rest"]
    seconds: int = Field(ge=0)


class TimeUseEntry(Record):
    id: Id
    actor_id: Id
    allocations: tuple[ActivityAllocation, ...] = Field(min_length=1)
    credits: tuple[TimeCredit, ...]
    settled_at: int = Field(ge=0)


class TrapState(Record):
    trap_id: Id
    discovered_by: tuple[Id, ...] = ()
    disarmed: bool = False
    triggered: int = Field(default=0, ge=0)


class AdministrationState(Record):
    knowledge: tuple[KnowledgeAcquisition, ...] = ()
    time_use: tuple[TimeUseEntry, ...] = ()
    traps: tuple[TrapState, ...] = ()


class AdministrationCommand(Record):
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)


class ResolveReaction(AdministrationCommand):
    kind: Literal["reaction"] = "reaction"
    context_id: Id


class AcquireKnowledge(AdministrationCommand):
    kind: Literal["knowledge"] = "knowledge"
    source_id: Id


class AwardPoints(AdministrationCommand):
    kind: Literal["award"] = "award"
    award_id: Id


class SettleTimeUse(AdministrationCommand):
    kind: Literal["time-use"] = "time-use"
    allocations: tuple[ActivityAllocation, ...] = Field(min_length=1)


class ResolveTrap(AdministrationCommand):
    kind: Literal["trap"] = "trap"
    trap_id: Id
    action: Literal["detect", "disarm", "trigger"]
    trigger_id: Id | None = None


CampaignAdministrationCommand = Annotated[
    ResolveReaction | AcquireKnowledge | AwardPoints | SettleTimeUse | ResolveTrap,
    Field(discriminator="kind"),
]
COMMAND_ADAPTER: TypeAdapter[CampaignAdministrationCommand] = TypeAdapter(
    CampaignAdministrationCommand
)


class AdministrationOutcome(Record):
    kind: Literal["reaction", "knowledge", "award", "time-use", "trap"]
    status: str
    private: str = ""
    consequence: TrapConsequence | None = None


class AuthoredRule(Protocol):
    id: str


def _rule[RuleT: AuthoredRule](values: tuple[RuleT, ...], identifier: str, label: str) -> RuleT:
    result = next((value for value in values if value.id == identifier), None)
    if result is None:
        raise ValidationError(f"Unknown authored {label}")
    return result


def _digest(command: AdministrationCommand) -> str:
    return hashlib.sha256(command.model_dump_json().encode()).hexdigest()


def _prior(
    resources: ResourceState, command: AdministrationCommand
) -> AdministrationOutcome | None:
    receipt = next((item for item in resources.receipts if item.command_id == command.id), None)
    if receipt is None:
        return None
    if receipt.digest != _digest(command):
        raise ConflictError("Campaign administration command ID reused")
    event = next(item for item in resources.events if item.id == "campaign-admin:" + command.id)
    return AdministrationOutcome.model_validate_json(event.kind)


def _finish(
    resources: ResourceState,
    command: AdministrationCommand,
    outcome: AdministrationOutcome,
    *,
    revision: int,
) -> ResourceState:
    return resources.model_copy(
        update={
            "revision": revision,
            "receipts": resources.receipts
            + (Receipt(command_id=command.id, digest=_digest(command)),),
            "events": resources.events
            + (
                ResourceEvent(
                    id="campaign-admin:" + command.id,
                    at=resources.game_time,
                    kind=outcome.model_dump_json(),
                    target_id=command.actor_id,
                ),
            ),
        }
    )


def _time_credits(
    rules: AdministrationRules, allocations: tuple[ActivityAllocation, ...]
) -> tuple[TimeCredit, ...]:
    indexed = {rule.id: rule for rule in rules.activities}
    points = sorted({point for item in allocations for point in (item.start, item.end)})
    for start, end in zip(points, points[1:], strict=False):
        active = [item for item in allocations if item.start < end and item.end > start]
        if sum(indexed[item.activity_id].load for item in active) > 100:
            raise ValidationError("Time Use activities exceed the interval's available time")
    return tuple(
        TimeCredit(
            activity_id=item.activity_id,
            kind=indexed[item.activity_id].credit_kind,
            seconds=(item.end - item.start)
            * indexed[item.activity_id].credit_numerator
            // indexed[item.activity_id].credit_denominator,
        )
        for item in allocations
    )


def _resolve_trap(
    state: AdministrationState,
    command: ResolveTrap,
    rules: AdministrationRules,
    rng: RandomSource,
) -> tuple[AdministrationState, AdministrationOutcome]:
    trap = _rule(rules.traps, command.trap_id, "trap")
    assert isinstance(trap, TrapRule)
    current = next(
        (item for item in state.traps if item.trap_id == trap.id), TrapState(trap_id=trap.id)
    )
    if command.action == "detect":
        detect_check = success_roll(rules.profile_id, trap.detection_target, rng=rng)
        if detect_check.outcome.succeeded:
            current = current.model_copy(
                update={
                    "discovered_by": tuple(sorted(set(current.discovered_by) | {command.actor_id}))
                }
            )
        outcome = AdministrationOutcome(
            kind="trap",
            status="discovered" if detect_check.outcome.succeeded else "hidden",
            private=json.dumps(asdict(detect_check), default=str),
        )
    elif command.action == "disarm":
        if command.actor_id not in current.discovered_by or current.disarmed:
            raise ConflictError("Trap must be discovered and armed before disarming")
        disarm_check = success_roll(rules.profile_id, trap.disarm_target, rng=rng)
        current = current.model_copy(update={"disarmed": disarm_check.outcome.succeeded})
        outcome = AdministrationOutcome(
            kind="trap",
            status="disarmed" if disarm_check.outcome.succeeded else "armed",
            private=json.dumps(asdict(disarm_check), default=str),
        )
    else:
        if command.trigger_id != trap.trigger_id:
            raise ValidationError("Trap trigger is not the authored trigger")
        if current.disarmed:
            outcome = AdministrationOutcome(kind="trap", status="safe")
        else:
            avoidance_check = (
                success_roll(rules.profile_id, trap.avoidance_target, rng=rng)
                if trap.avoidance_target is not None
                else None
            )
            avoided = avoidance_check is not None and avoidance_check.outcome.succeeded
            current = current.model_copy(update={"triggered": current.triggered + 1})
            outcome = AdministrationOutcome(
                kind="trap",
                status="avoided" if avoided else "triggered",
                private=(
                    json.dumps(asdict(avoidance_check), default=str) if avoidance_check else ""
                ),
                consequence=None if avoided else trap.consequence,
            )
    traps = tuple(item for item in state.traps if item.trap_id != trap.id) + (current,)
    return state.model_copy(update={"traps": traps}), outcome


def apply_administration(
    state: AdministrationState,
    resources: ResourceState,
    world: World,
    advancement: tuple[AdvancementEntry, ...],
    command: CampaignAdministrationCommand,
    rules: AdministrationRules,
    *,
    rng: RandomSource,
    build_revision: str = "unchanged",
    advance: Callable[[ResourceState, int, str], ResourceState] | None = None,
    system: bool = False,
) -> tuple[
    AdministrationState, ResourceState, World, tuple[AdvancementEntry, ...], AdministrationOutcome
]:
    """Apply one trusted command. ``advance`` is the existing shared-clock reducer."""
    if not system:
        raise ValidationError("Campaign administration requires engine authority")
    prior = _prior(resources, command)
    if prior is not None:
        return state, resources, world, advancement, prior
    if command.expected_revision != resources.revision:
        raise ConflictError("Campaign administration revision changed")
    actor_ids = tuple(entity.id for entity in world.entities if entity.kind.value == "actor")
    if command.actor_id not in actor_ids:
        raise ValidationError("Campaign administration actor is not in the world")
    revision = resources.revision + 1
    if isinstance(command, ResolveReaction):
        context = _rule(rules.reactions, command.context_id, "reaction context")
        assert isinstance(context, ReactionContext)
        trace: ReactionTrace = reaction_roll(rules.profile_id, context.modifiers, rng=rng)
        outcome = AdministrationOutcome(
            kind="reaction", status=trace.outcome, private=json.dumps(asdict(trace), default=str)
        )
    elif isinstance(command, AcquireKnowledge):
        source = _rule(rules.knowledge, command.source_id, "knowledge source")
        assert isinstance(source, KnowledgeSource)
        recipients = (
            actor_ids
            if source.audience == "campaign"
            else (command.actor_id,)
            if source.audience == "actor"
            else ()
        )
        if not set(source.fact_ids) <= {fact.id for fact in world.facts}:
            raise ValidationError("Authored knowledge source references an unknown world fact")
        for recipient in recipients:
            for fact_id in source.fact_ids:
                world = world.learn(recipient, fact_id)
        record = KnowledgeAcquisition(
            id=command.id,
            source_id=source.id,
            fact_ids=source.fact_ids,
            actor_ids=recipients,
            audience=source.audience,
            provenance=source.provenance,
            at=resources.game_time,
        )
        state = state.model_copy(update={"knowledge": state.knowledge + (record,)})
        outcome = AdministrationOutcome(kind="knowledge", status="acquired")
    elif isinstance(command, AwardPoints):
        award = _rule(rules.awards, command.award_id, "award")
        assert isinstance(award, AwardRule)
        if award.recipient_id not in actor_ids:
            raise ValidationError("Award recipient is not in the campaign")
        entry = AdvancementEntry(
            id=command.id,
            actor_id=award.recipient_id,
            kind="earned",
            points=award.points,
            revision=revision,
            build_before=build_revision,
            build_after=build_revision,
            reason=award.reason,
        )
        advancement = advancement + (entry,)
        outcome = AdministrationOutcome(kind="award", status="awarded")
    elif isinstance(command, SettleTimeUse):
        if advance is None:
            raise ValidationError("Time Use requires the shared clock reducer")
        if min(item.start for item in command.allocations) < resources.game_time:
            raise ValidationError("Time Use cannot settle an interval in the past")
        known = {rule.id for rule in rules.activities}
        if not {item.activity_id for item in command.allocations} <= known:
            raise ValidationError("Unknown authored Time Use activity")
        credits = _time_credits(rules, command.allocations)
        settled_at = max(item.end for item in command.allocations)
        resources = advance(resources, settled_at, command.id)
        time_entry = TimeUseEntry(
            id=command.id,
            actor_id=command.actor_id,
            allocations=command.allocations,
            credits=credits,
            settled_at=settled_at,
        )
        state = state.model_copy(update={"time_use": state.time_use + (time_entry,)})
        outcome = AdministrationOutcome(kind="time-use", status="settled")
    else:
        state, outcome = _resolve_trap(state, command, rules, rng)
    resources = _finish(resources, command, outcome, revision=revision)
    return state, resources, world, advancement, outcome


def validate_administration(
    rules: AdministrationRules | None,
    state: AdministrationState,
    world: World,
    advancement: tuple[AdvancementEntry, ...],
) -> None:
    if rules is None:
        if state != AdministrationState():
            raise ValidationError("Campaign administration state requires authored rules")
        return
    actor_ids = {entity.id for entity in world.entities if entity.kind.value == "actor"}
    fact_ids = {fact.id for fact in world.facts}
    if any(not set(source.fact_ids) <= fact_ids for source in rules.knowledge):
        raise ValidationError("Campaign knowledge rules reference unknown facts")
    if any(rule.recipient_id not in actor_ids for rule in rules.awards):
        raise ValidationError("Campaign award rules reference unknown actors")
    if len({item.id for item in state.knowledge}) != len(state.knowledge):
        raise ValidationError("Duplicate knowledge acquisition")
    if len({item.id for item in state.time_use}) != len(state.time_use):
        raise ValidationError("Duplicate Time Use settlement")
    if len({item.trap_id for item in state.traps}) != len(state.traps):
        raise ValidationError("Duplicate trap state")
    if not {item.trap_id for item in state.traps} <= {item.id for item in rules.traps}:
        raise ValidationError("Trap state lacks an authored rule")
    if any(
        not set(item.actor_ids) <= actor_ids or not set(item.fact_ids) <= fact_ids
        for item in state.knowledge
    ):
        raise ValidationError("Knowledge acquisition references unknown campaign state")
    if any(entry.actor_id not in actor_ids for entry in advancement):
        raise ValidationError("Advancement ledger references an unknown actor")
