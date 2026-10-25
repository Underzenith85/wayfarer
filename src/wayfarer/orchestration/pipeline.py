"""The one write path every command family plans into (#638).

A family says what it wants written — the command's identity, the revision it
expects, the payload its retries are matched against, who may submit it and the
pure reduction that produces the receipt. This module runs the named stages
around that plan in one order for every family: authorize the principal, answer
a retry from the log, capture the instant, seed and origin, and commit the
reduction in one transaction. A service therefore holds no commit call, and
states its authorization as registered rules rather than checking for itself.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.errors import AuthorizationError, ValidationError
from wayfarer.orchestration.ability_boundary import AbilityAdmission, require_boundary
from wayfarer.orchestration.clock import CommandInstant
from wayfarer.orchestration.default_knowledge_generations import (
    capture as capture_default_knowledge,
)
from wayfarer.orchestration.default_knowledge_generations import (
    default_knowledge_scope,
    required_from_payload,
)
from wayfarer.orchestration.defense_posture_generations import capture as capture_defense_postures
from wayfarer.orchestration.defense_posture_generations import defense_posture_scope
from wayfarer.orchestration.entropy import CommandBoundary, commit_command
from wayfarer.orchestration.membership import member_for, require_control
from wayfarer.orchestration.ranged_target_posture_generations import (
    capture as capture_ranged_target_postures,
)
from wayfarer.orchestration.ranged_target_posture_generations import ranged_target_posture_scope
from wayfarer.orchestration.social_generations import capture as capture_social
from wayfarer.orchestration.social_generations import (
    extreme_scores_from_payload,
    questions_from_payload,
    self_control_from_payload,
    social_generation,
)
from wayfarer.orchestration.symptom_generations import (
    capture,
    condition_attributes_from_payload,
    drowsiness_from_payload,
    symptom_generation,
)
from wayfarer.orchestration.task_records import has_task_records, require_task_boundary
from wayfarer.persistence.events import CommandOrigin

# Each raises on refusal, so a plan states its rule instead of checking for itself.
Control = Callable[[str], None]


@dataclass(frozen=True)
class ActsAs:
    """The principal may submit only as the actor the command names."""

    actor_id: str
    refusal: str = "Command actor is not authorized"

    def __call__(self, principal_id: str) -> None:
        if self.actor_id != principal_id:
            raise ValidationError(self.refusal)


@dataclass(frozen=True)
class Trusted:
    """The deployment must also trust the principal with the director's seat.

    Campaign membership says a principal plays that part; this says the server
    accepts them in it. Both are needed, so it composes with `ActsAs`.
    """

    ids: frozenset[str]
    refusal: str = "Command requires GM authority"

    def __call__(self, principal_id: str) -> None:
        if principal_id not in self.ids:
            raise ValidationError(self.refusal)


@dataclass(frozen=True)
class Controls:
    """Campaign membership must let this principal act for the named actor."""

    member: CampaignMember
    actor_id: str
    refusal: str = "Principal cannot control this actor"
    state: PlayState | None = None

    def __call__(self, principal_id: str) -> None:
        if principal_id != self.member.principal_id:
            raise AuthorizationError(self.refusal)
        try:
            current = (
                member_for(self.state, principal_id) if self.state is not None else self.member
            )
            require_control(current, self.actor_id, self.state)
        except AuthorizationError as exc:
            raise AuthorizationError(self.refusal) from exc


@dataclass(frozen=True)
class Seats:
    """The campaign's own membership must seat the principal in this role.

    Trust and seating are separate facts: `Trusted` says the deployment accepts a
    principal as a director, this says the campaign does. Director families
    compose both.
    """

    state: PlayState
    role: str = "gm"
    refusal: str = "This command requires trusted director authority"

    def __call__(self, principal_id: str) -> None:
        if member_for(self.state, principal_id).role != self.role:
            raise ValidationError(self.refusal)


@dataclass(frozen=True)
class CommandPlan[T]:
    """One intended write, described rather than performed.

    ``resolve`` is the pure reduction the transaction runs; ``outcome`` reads the
    caller's typed answer back out of the committed campaign. ``replayed`` is the
    same answer for a command that already committed, for the families whose
    result lives in the receipt rather than in the checkpoint.
    """

    command_id: str
    expected_revision: int
    payload: str
    resolve: Callable[[Campaign], CommandReceipt]
    actor_id: str
    outcome: Callable[[Campaign], Awaitable[T]]
    # Applied in order before anything is read or written. An empty tuple means
    # campaign membership already decided this, upstream of the plan.
    control: tuple[Control, ...] = ()
    replayed: Callable[[Campaign], Awaitable[T]] | None = None
    hypothetical: bool = False
    # A feasibility answer that settles the command without a transaction. It runs
    # after the retry lookup, so a resubmitted command still answers from the log.
    assess: Callable[[], T | None] | None = None
    rng: RandomSource | None = None
    instant: CommandInstant | None = None
    origin: CommandOrigin | None = None
    pending_task_id: str | None = None
    task_clock_only: bool = False
    ability_admission: AbilityAdmission | None = None


def authorized(
    plan: CommandPlan[object], principal_id: str, state: PlayState | None = None
) -> None:
    """Every declared rule, then the one shape rule: a preview never writes."""
    for rule in plan.control:
        selected = (
            replace(rule, state=state)
            if isinstance(rule, (Controls, Seats)) and state is not None
            else rule
        )
        selected(principal_id)
    if plan.hypothetical:
        raise ValidationError("A hypothetical command is not authorized to write; preview it")


def _control_state(campaign: Campaign, plan: CommandPlan[object]) -> PlayState | None:
    if not any(isinstance(rule, (Controls, Seats)) for rule in plan.control):
        return None
    raw = campaign.get("play_json")
    return PlayState.model_validate_json(raw) if raw is not None else None


async def submit[T](
    play: CommandBoundary,
    cid: str,
    plan: CommandPlan[T],
    *,
    principal_id: str,
    authorize: Callable[[Campaign], None] | None = None,
) -> T:
    """Run one plan through the fixed stages and return its typed result.

    ``authorize`` is the caller's re-check under the campaign lock, for a
    transport that must confirm its own versions against the row it will write.
    """
    state = (
        _control_state(await play.store.read(cid), plan)
        if any(isinstance(rule, (Controls, Seats)) for rule in plan.control)
        else None
    )
    authorized(plan, principal_id, state)
    social_payload, correct_reactions = await capture_social(
        play.store, cid, plan.command_id, plan.payload
    )
    correct_extremes = extreme_scores_from_payload(social_payload)
    correct_self_control = self_control_from_payload(social_payload)
    correct_questions = questions_from_payload(social_payload)
    payload, correct_attributes = await capture(play.store, cid, plan.command_id, social_payload)
    correct_conditions = condition_attributes_from_payload(payload)
    correct_drowsiness = drowsiness_from_payload(payload)
    payload, correct_postures = await capture_defense_postures(
        play.store, cid, plan.command_id, payload
    )
    payload, correct_ranged_targets = await capture_ranged_target_postures(
        play.store, cid, plan.command_id, payload
    )
    payload, correct_knowledge = await capture_default_knowledge(
        play.store, cid, plan.command_id, payload
    )
    required_knowledge = required_from_payload(payload)
    plan = replace(plan, payload=payload)
    duplicate = await play.store.duplicate(cid, plan.command_id, plan.payload)
    if duplicate is not None:
        authorized(plan, principal_id, _control_state(await play.store.read(cid), plan))
        return await (plan.replayed or plan.outcome)(duplicate)
    require_boundary(
        await play.store.read(cid),
        plan.ability_admission,
        command_id=plan.command_id,
        revision=plan.expected_revision,
    )
    if plan.assess is not None:
        with (
            symptom_generation(
                correct_attributes,
                condition_attributes=correct_conditions,
                deduplicate_drowsiness=correct_drowsiness,
            ),
            social_generation(
                correct_reactions,
                extreme_scores=correct_extremes,
                self_control=correct_self_control,
                source_questions=correct_questions,
            ),
            defense_posture_scope(correct_postures),
            ranged_target_posture_scope(correct_ranged_targets),
            default_knowledge_scope(correct_knowledge, required=required_knowledge),
        ):
            settled = plan.assess()
        if settled is not None:
            return settled

    def resolve(campaign: Campaign) -> CommandReceipt:
        authorized(plan, principal_id, _control_state(campaign, plan))
        if authorize is not None:
            authorize(campaign)
        require_boundary(
            campaign,
            plan.ability_admission,
            command_id=plan.command_id,
            revision=plan.expected_revision,
        )
        raw = campaign.get("play_json")
        if raw is not None and has_task_records(raw):
            require_task_boundary(
                PlayState.model_validate_json(raw),
                plan.pending_task_id,
                clock_only=plan.task_clock_only,
            )
        with (
            symptom_generation(
                correct_attributes,
                condition_attributes=correct_conditions,
                deduplicate_drowsiness=correct_drowsiness,
            ),
            social_generation(
                correct_reactions,
                extreme_scores=correct_extremes,
                self_control=correct_self_control,
                source_questions=correct_questions,
            ),
            defense_posture_scope(correct_postures),
            ranged_target_posture_scope(correct_ranged_targets),
            default_knowledge_scope(correct_knowledge, required=required_knowledge),
        ):
            return plan.resolve(campaign)

    committed = await commit_command(
        play,
        cid,
        plan.command_id,
        plan.expected_revision,
        plan.payload,
        resolve,
        actor_id=plan.actor_id,
        rng=plan.rng,
        instant=plan.instant,
        origin=plan.origin,
    )
    if committed["kind"] == "replayed":
        authorized(plan, principal_id, _control_state(await play.store.read(cid), plan))
        return await (plan.replayed or plan.outcome)(committed["state"])
    return await plan.outcome(committed["state"])
