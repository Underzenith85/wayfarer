"""Server-only social command commits using the existing resource receipt ledger."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Literal

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.checks import RandomSource
from wayfarer.rules.gurps_social import (
    InfluenceSkill,
    ReactionModifier,
    fright_roll,
    influence_roll,
    reaction_roll,
    self_control_roll,
)
from wayfarer.rules.traits import TraitOptions, TraitRules
from wayfarer.simulation.resources import Command, Receipt, Record, ResourceEvent, ResourceState
from wayfarer.world import World


class SocialCommand(Command):
    kind: Literal["reaction", "influence", "fright", "self-control"]
    subject_id: str
    trigger_id: str


class SocialOutcome(Record):
    """Explicit player projection: no raw roll, threshold, modifier or secret ID."""

    kind: Literal["reaction", "influence", "fright", "self-control"]
    outcome: str
    requires_adjudication: bool = False


class SocialContext:
    """Trusted server data, deliberately not a serializable player command."""

    def __init__(
        self,
        profile_id: str,
        target: int,
        *,
        will: int = 10,
        ht: int = 10,
        skill: InfluenceSkill = "diplomacy",
        modifiers: tuple[ReactionModifier, ...] = (),
        required_fact_ids: tuple[str, ...] = (),
        trait_base: int = -5,
        trait_levels: int = 1,
        trait_options: TraitOptions | None = None,
        trait_rules: TraitRules | None = None,
    ) -> None:
        self.profile_id, self.target, self.will = profile_id, target, will
        self.ht = ht
        self.skill, self.modifiers, self.required_fact_ids = skill, modifiers, required_fact_ids
        self.trait_base, self.trait_levels = trait_base, trait_levels
        self.trait_options, self.trait_rules = trait_options, trait_rules


def apply_social(
    state: ResourceState,
    world: World,
    command: SocialCommand,
    context: SocialContext,
    *,
    rng: RandomSource,
    system: bool = False,
) -> tuple[ResourceState, SocialOutcome]:
    if not system:
        raise ValidationError("Social checks require authoritative trigger context")
    if not command.trigger_id or not command.subject_id:
        raise ValidationError("Social checks require stable subject and trigger IDs")
    known = {f.id for f in world.perspective(command.subject_id).facts}
    world.perspective(command.actor_id)
    if not set(context.required_fact_ids) <= known:
        raise ValidationError("Subject lacks the evidence required for this social trigger")
    digest = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
    event_id = f"social:{command.kind}:{command.subject_id}:{command.trigger_id}"
    previous = next((r for r in state.receipts if r.command_id == command.id), None)
    if previous:
        if previous.digest != digest:
            raise ConflictError("Social command ID reused")
        event = next(e for e in state.events if e.id == event_id)
        return state, SocialOutcome.model_validate_json(json.loads(event.kind)["public"])
    if state.revision != command.expected_revision:
        raise ConflictError("Resource revision changed")
    if any(e.id == event_id for e in state.events):
        raise ConflictError("Social trigger already resolved")
    details: object
    if command.kind == "reaction":
        trace = reaction_roll(context.profile_id, context.modifiers, rng=rng)
        outcome = SocialOutcome(kind=command.kind, outcome=trace.outcome)
        details = asdict(trace)
    elif command.kind == "influence":
        influence = influence_roll(
            context.profile_id,
            context.skill,
            command.actor_id,
            command.subject_id,
            context.target,
            context.will,
            context.modifiers,
            rng=rng,
        )
        outcome = SocialOutcome(kind=command.kind, outcome=influence.outcome)
        details = asdict(influence)
    elif command.kind == "fright":
        fright = fright_roll(context.profile_id, context.target, rng=rng, ht=context.ht)
        outcome = SocialOutcome(
            kind=command.kind,
            outcome="passed" if fright.check.outcome.succeeded else "failed",
            requires_adjudication=fright.effect is not None
            and (fright.effect.trait_choice != "none" or fright.effect.condition == "panic"),
        )
        details = {
            "check": asdict(fright.check),
            "table_dice": fright.table_dice,
            "table_total": fright.table_total,
            "effect": fright.effect.model_dump(mode="json") if fright.effect else None,
        }
    else:
        if context.trait_rules is None or context.trait_options is None:
            raise ValidationError("Self-control requires approved trait options")
        control = self_control_roll(
            context.profile_id,
            context.trait_base,
            context.trait_levels,
            context.trait_options,
            context.trait_rules,
            rng=rng,
        )
        outcome = SocialOutcome(
            kind=command.kind, outcome="resisted" if control.outcome.succeeded else "triggered"
        )
        details = asdict(control)
    event = ResourceEvent(
        id=event_id,
        at=state.game_time,
        target_id=command.subject_id,
        kind=json.dumps({"public": outcome.model_dump_json(), "private": details}),
    )
    return state.model_copy(
        update={
            "revision": state.revision + 1,
            "receipts": state.receipts + (Receipt(command_id=command.id, digest=digest),),
            "events": state.events + (event,),
        }
    ), outcome
