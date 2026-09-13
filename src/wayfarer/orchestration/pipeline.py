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
from dataclasses import dataclass

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.errors import AuthorizationError, ValidationError
from wayfarer.orchestration.clock import CommandInstant
from wayfarer.orchestration.entropy import CommandBoundary, commit_command
from wayfarer.orchestration.membership import member_for, require_control
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

    def __call__(self, principal_id: str) -> None:
        try:
            require_control(self.member, self.actor_id)
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


def authorized(plan: CommandPlan[object], principal_id: str) -> None:
    """Every declared rule, then the one shape rule: a preview never writes."""
    for rule in plan.control:
        rule(principal_id)
    if plan.hypothetical:
        raise ValidationError("A hypothetical command is not authorized to write; preview it")


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
    authorized(plan, principal_id)
    duplicate = await play.store.duplicate(cid, plan.command_id, plan.payload)
    if duplicate is not None:
        return await (plan.replayed or plan.outcome)(duplicate)
    if plan.assess is not None:
        settled = plan.assess()
        if settled is not None:
            return settled

    def resolve(campaign: Campaign) -> CommandReceipt:
        if authorize is not None:
            authorize(campaign)
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
    if committed["kind"] == "replayed" and plan.replayed is not None:
        return await plan.replayed(committed["state"])
    return await plan.outcome(committed["state"])
