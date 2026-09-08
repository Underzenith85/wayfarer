"""Server-only social command commits using the existing resource receipt ledger."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
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
from wayfarer.world import EntityKind, World


class SocialCommand(Command):
    kind: Literal["reaction", "influence", "fright", "self-control", "fright-recovery"]
    subject_id: str
    trigger_id: str


class SocialOutcome(Record):
    """Explicit player projection: no raw roll, threshold, modifier or secret ID."""

    kind: Literal["reaction", "influence", "fright", "self-control", "fright-recovery"]
    outcome: str
    requires_adjudication: bool = False
    adjudication: tuple[str, ...] = ()


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
    digest = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
    # Preserve legacy IDs for simple components; colon-bearing IDs must not alias
    # another subject/trigger pair. The hash namespace cannot overlap legacy IDs.
    identity = (command.kind, command.subject_id, command.trigger_id)
    event_id = (
        "social-key:" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        if any(":" in part for part in identity)
        else "social:" + ":".join(identity)
    )
    if command.kind == "fright-recovery":
        event_id = "social-recovery:" + hashlib.sha256(command.id.encode()).hexdigest()
    previous = next((r for r in state.receipts if r.command_id == command.id), None)
    if previous:
        if previous.digest != digest:
            raise ConflictError("Social command ID reused")
        legacy_id = "social:" + ":".join(identity)
        event = next(
            e
            for e in state.events
            if e.id in (event_id, legacy_id) and e.target_id == command.subject_id
        )
        return state, SocialOutcome.model_validate_json(json.loads(event.kind)["public"])
    actors = {e.id for e in world.entities if e.kind is EntityKind.ACTOR}
    if not {command.actor_id, command.subject_id} <= actors:
        raise ValidationError("Social checks require world actors")
    known = {f.id for f in world.perspective(command.subject_id).facts}
    if not set(context.required_fact_ids) <= known:
        raise ValidationError("Subject lacks the evidence required for this social trigger")
    if state.revision != command.expected_revision:
        raise ConflictError("Resource revision changed")
    if any(
        e.id in (event_id, "social:" + ":".join(identity)) and e.target_id == command.subject_id
        for e in state.events
    ):
        raise ConflictError("Social trigger already resolved")
    details: object
    if command.kind == "fright-recovery":
        from wayfarer.simulation.fright import recover

        state, passed = recover(
            state,
            actor_id=command.subject_id,
            trigger_id=command.trigger_id,
            command_id=command.id,
            rng=rng,
        )
        outcome = SocialOutcome(kind=command.kind, outcome="recovered" if passed else "recovering")
        details = {}
    elif command.kind == "reaction":
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
        if fright.effect is not None:
            effect = fright.effect
            choices = []
            if effect.trait_choice != "none":
                choices.append(f"{effect.trait_choice}:{effect.trait_points}")
            if effect.condition == "panic":
                choices.append("panic-action")
            if effect.permanent_ht_loss:
                choices.append(f"permanent-ht-loss:{effect.permanent_ht_loss}")
            if effect.permanent_iq_loss:
                choices.append(f"permanent-iq-loss:{effect.permanent_iq_loss}")
            if effect.neglect_progression:
                choices.append("catatonia-medical-care")
            if effect.aftermath_seconds:
                choices.append("aftermath-penalty")
            outcome = outcome.model_copy(
                update={
                    "requires_adjudication": bool(choices),
                    "adjudication": tuple(choices),
                }
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


@dataclass(frozen=True)
class SocialDisclosure:
    """A bounded server-authored NPC response, never a player/model fact request."""

    fact_ids: tuple[str, ...] = ()
    outcomes: tuple[str, ...] = ("good", "very-good", "excellent")


def apply_interaction(
    state: ResourceState,
    world: World,
    command: SocialCommand,
    context: SocialContext,
    disclosure: SocialDisclosure,
    *,
    rng: RandomSource,
    system: bool = False,
) -> tuple[ResourceState, World, SocialOutcome]:
    """Resolve once and disclose only a configured fact the subject knows.

    The caller must atomically persist both returned states. No player behavior,
    approved character build, or NPC belief is changed by an outcome.
    """
    if not system:
        raise ValidationError("Social interactions require authoritative trigger context")
    replay = any(r.command_id == command.id for r in state.receipts)
    if not replay and disclosure.fact_ids:
        if command.kind not in ("reaction", "influence"):
            raise ValidationError("Only NPC interactions can disclose facts")
        known = {f.id for f in world.perspective(command.subject_id).facts}
        if not set(disclosure.fact_ids) <= known:
            raise ValidationError("NPC cannot communicate unknown facts")
        if len(set(disclosure.fact_ids)) != len(disclosure.fact_ids):
            raise ValidationError("Duplicate social disclosure fact")
        if not disclosure.outcomes or not set(disclosure.outcomes) <= {
            "disastrous",
            "very-bad",
            "bad",
            "poor",
            "neutral",
            "good",
            "very-good",
            "excellent",
        }:
            raise ValidationError("Unsupported disclosure outcome")
    updated, outcome = apply_social(state, world, command, context, rng=rng, system=True)
    if not replay and outcome.outcome in disclosure.outcomes:
        for fact_id in disclosure.fact_ids:
            world = world.learn(command.actor_id, fact_id)
    return updated, world, outcome
