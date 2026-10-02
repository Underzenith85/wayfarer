"""Private campaign continuations for named, still-unrolled reaction roles.

B66 declaration, authority, cooldown, persistence and replay belong to the
registered task host. These serializable domain seams freeze prerequisites and
continue existing campaign consequences without replaying any earlier dice.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field, model_validator

from wayfarer.engine.rules.checks import NO_RANDOM, CheckTrace, RandomSource
from wayfarer.engine.rules.gurps_checks import (
    Contestant,
    QuickContestTrace,
    quick_contest,
    replay_success,
    resolve_quick_contest,
)
from wayfarer.engine.rules.social.gurps_social import (
    Reaction,
    ReactionModifier,
    ReactionTrace,
    evaluate_reaction,
)
from wayfarer.engine.simulation.campaign.adjudication import expire_rulings
from wayfarer.engine.simulation.campaign.administration import (
    AdministrationOutcome,
    KnowledgeSource,
    ResolveReaction,
    cancel_administration_reaction,
    resolve_administration_reaction,
)
from wayfarer.engine.simulation.campaign.economics import (
    CheckLoyalty,
    EconomicsOutcome,
    FindHireling,
    cancel_economics_reaction,
    hireling_reaction_rule,
    rescue_reaction_modifiers,
    resolve_economics_reaction,
    search_for_hireling,
)
from wayfarer.engine.simulation.campaign.law import (
    LawOutcome,
    ResolveLawCase,
    cancel_law_reaction,
    law_reaction_context,
    resolve_law_reaction,
)
from wayfarer.engine.simulation.campaign.reaction_context import (
    CampaignReactionInteraction as CampaignReactionInteraction,
)
from wayfarer.engine.simulation.campaign.reaction_context import (
    campaign_social_command,
    campaign_social_context,
)
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.social.reactions import (
    PreparedReaction,
    ResolvedReactionContext,
    cancel_prepared_reaction,
    prepare_reaction,
    recognize_reaction,
    resolve_prepared_reaction,
    validate_prepared_reaction,
)
from wayfarer.engine.simulation.social.social import SocialCommand, SocialDisclosure
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

if TYPE_CHECKING:
    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.engine.simulation.campaign.procedures import CampaignProcedureEngine


class ReactionKnowledgeBinding(Record):
    """GM-bound existing knowledge source and explicit accepted reaction bands."""

    source_id: Id
    outcomes: tuple[Reaction, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_outcomes(self) -> ReactionKnowledgeBinding:
        if len(set(self.outcomes)) != len(self.outcomes):
            raise ValueError("Duplicate administration reaction band")
        return self


class JudgeReactionPolicy(Record):
    """B508 leaves a unilateral judge's decision to the GM; make it explicit."""

    kind: Literal["judge"] = "judge"
    outcomes: tuple[Reaction, ...] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_outcomes(self) -> JudgeReactionPolicy:
        if len(set(self.outcomes)) != len(self.outcomes) or not self.reason.strip():
            raise ValueError("Judge policy requires unique bands and an authored reason")
        return self


class AdversarialReactionPolicy(Record):
    """B508: contest margin modifies the verdict; Neutral follows that contest."""

    kind: Literal["adversarial"] = "adversarial"


LawReactionPolicy = Annotated[
    JudgeReactionPolicy | AdversarialReactionPolicy, Field(discriminator="kind")
]
CampaignReactionCommand = Annotated[
    FindHireling | CheckLoyalty | ResolveLawCase | ResolveReaction, Field(discriminator="kind")
]
CampaignReactionOutcome = EconomicsOutcome | LawOutcome | AdministrationOutcome


class RescuePermanentBonus(Record):
    """An explicit GM decision for one named PC's loss in this qualifying rescue.

    B519 supplies neither the numeric amount nor a mandatory application band.
    Keep the GM's declared condition separate from the reaction's modifier.
    """

    amount: int = Field(gt=0)
    rescuer_actor_id: Id
    loss: Literal["serious-injury", "death"]
    applies_on: Literal["grateful", "any-reaction"]
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def justified(self) -> RescuePermanentBonus:
        if not self.reason.strip():
            raise ValueError("Permanent rescue bonus requires the GM's reason")
        return self


class CampaignReactionSource(Record):
    kind: Literal["campaign"] = "campaign"
    role: Literal["initial-loyalty", "rescue-loyalty", "law", "administration"]
    command: CampaignReactionCommand
    interaction: CampaignReactionInteraction
    npc_id: Id | None = None
    knowledge: ReactionKnowledgeBinding | None = None
    law_policy: LawReactionPolicy | None = None
    rescue_bonus: RescuePermanentBonus | None = None

    @model_validator(mode="after")
    def exact_role(self) -> CampaignReactionSource:
        kinds = {
            "initial-loyalty": "hire",
            "rescue-loyalty": "loyalty",
            "law": "procedure",
            "administration": "reaction",
        }
        if self.interaction.actor_id != self.command.actor_id:
            raise ValueError("Interaction actor must be the actual campaign reaction recipient")
        if self.command.kind != kinds[self.role]:
            raise ValueError("Campaign reaction role does not match its source command")
        if (self.knowledge is not None) != (self.role == "administration"):
            raise ValueError("Administration requires exactly one bound knowledge consequence")
        if (self.law_policy is not None) != (self.role == "law"):
            raise ValueError("Law requires an explicit authored verdict policy")
        if self.rescue_bonus is not None and self.role != "rescue-loyalty":
            raise ValueError("Permanent rescue bonus requires the rescue-loyalty role")
        if self.role == "law" and self.npc_id is None:
            raise ValueError("Law requires its authored reacting judge or jury representative")
        return self


class PreparedCampaignReaction(Record):
    source: CampaignReactionSource
    preparation_id: Id
    actor_id: Id
    npc_id: Id
    profile_id: Literal["gurps-basic-set-4e-2004"]
    modifiers: tuple[ReactionModifier, ...]
    reaction: PreparedReaction
    context_digest: str = Field(min_length=64, max_length=64)
    frozen_search: CheckTrace | None = None
    frozen_contest: QuickContestTrace | None = None


@dataclass(frozen=True)
class _Context:
    npc_id: str
    profile_id: Literal["gurps-basic-set-4e-2004"]
    modifiers: tuple[ReactionModifier, ...]
    binding: str


def _source_json(source: CampaignReactionSource) -> str:
    # Bind the original source command exactly. Current resource revisions may
    # drift with the task clock, but the recorded source itself never changes.
    return json.dumps(source.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def _actor(engine: CampaignProcedureEngine, state: PlayState, actor_id: str) -> str:
    actor = next((item for item in state.actors if item.actor_id == actor_id), None)
    if actor is None:
        raise ValidationError("Campaign reaction requires a playable receiving actor")
    build = engine.reviewer.compiler.compile(actor.proposal.draft).build
    if build is None:
        raise ValidationError("Campaign reaction requires a valid current actor build")
    if "unconscious" in actor.conditions:
        raise ValidationError("Unconscious actor cannot undertake this campaign reaction")
    return actor.model_dump_json()


def _npc(state: PlayState, actor_id: str, npc_id: str, player_actor_ids: tuple[str, ...]) -> None:
    entities = {item.id: item for item in state.world.entities}
    if (
        actor_id == npc_id
        or actor_id not in entities
        or npc_id not in entities
        or entities[actor_id].kind.value != "actor"
        or entities[npc_id].kind.value != "actor"
        or npc_id in player_actor_ids
    ):
        raise ValidationError("Campaign reaction requires distinct receiving actor and NPC")


def _economic_context(
    engine: CampaignProcedureEngine, state: PlayState, source: CampaignReactionSource
) -> _Context:
    rules = engine.economics
    if rules is None:
        raise ValidationError("Economics command is not enabled")
    command = source.command
    if isinstance(command, FindHireling):
        rule = hireling_reaction_rule(state.economics, command, rules)
        actor = next(item for item in state.actors if item.actor_id == command.actor_id)
        build = engine.reviewer.compiler.compile(actor.proposal.draft).build
        assert build is not None
        values = {value.target: value.value for value in build.sheet.values}
        target = values.get(rule.search_target_id)
        if target is None or not target.is_finite() or target != target.to_integral_value():
            raise ValidationError("Hireling search requires its approved integer target")
        return _Context(
            rule.hireling_id,
            rules.profile_id,
            rule.loyalty_modifiers,
            rules.model_dump_json() + state.economics.model_dump_json(),
        )
    if not isinstance(command, CheckLoyalty):
        raise ValidationError("Invalid economic reaction role")
    contract = next(
        (item for item in state.economics.hirelings if item.id == command.contract_id), None
    )
    circumstance = next(
        (item for item in rules.loyalty if item.id == command.circumstance_id), None
    )
    if contract is None or circumstance is None:
        raise ValidationError("Unknown authored rescue contract or circumstance")
    rescue_rule = next((item for item in rules.hirelings if item.id == contract.rule_id), None)
    if (
        rescue_rule is None
        or command.actor_id != contract.employer_id
        or not contract.active
        or circumstance.hireling_rule_id != rescue_rule.id
        or circumstance.kind != "rescue"
    ):
        raise ValidationError("Rescue role does not match the current authored contract")
    modifiers = rescue_reaction_modifiers(contract, circumstance, rescue_rule, state.world)
    return _Context(
        rescue_rule.hireling_id,
        rules.profile_id,
        modifiers,
        rules.model_dump_json() + state.economics.model_dump_json(),
    )


def _law_context(
    engine: CampaignProcedureEngine, state: PlayState, source: CampaignReactionSource
) -> _Context:
    rules = engine.law
    command = source.command
    if rules is None or not isinstance(command, ResolveLawCase):
        raise ValidationError("Law reaction procedure is not enabled")
    case, procedure = law_reaction_context(state.law, command, rules)
    if procedure.resolution != "reaction" or case.subject_id != command.actor_id:
        raise ValidationError("Law reaction must concern its receiving actor's case")
    if isinstance(source.law_policy, AdversarialReactionPolicy):
        if (
            procedure.kind != "trial"
            or procedure.opposing_target is None
            or procedure.success_status != "acquitted"
            or procedure.failure_status != "convicted"
        ):
            raise ValidationError(
                "Adversarial verdict requires defense/prosecution targets and acquittal/conviction"
            )
    if isinstance(source.law_policy, AdversarialReactionPolicy) and any(
        value.kind == "situation" and value.source_id == "law:adversarial-contest"
        for value in procedure.reaction_modifiers
    ):
        raise ValidationError("Authored modifiers cannot replace the frozen contest adjustment")
    assert source.npc_id is not None
    return _Context(
        source.npc_id,
        rules.profile_id,
        procedure.reaction_modifiers,
        rules.model_dump_json() + case.model_dump_json(),
    )


def _knowledge_source(
    engine: CampaignProcedureEngine, source: CampaignReactionSource
) -> KnowledgeSource:
    if engine.administration is None or source.knowledge is None:
        raise ValidationError("Administration reaction requires its bound knowledge source")
    knowledge = next(
        (item for item in engine.administration.knowledge if item.id == source.knowledge.source_id),
        None,
    )
    if knowledge is None or knowledge.audience == "gm":
        raise ValidationError(
            "Administration reaction requires an actor or campaign knowledge source"
        )
    return knowledge


def _administration_context(
    engine: CampaignProcedureEngine, state: PlayState, source: CampaignReactionSource
) -> _Context:
    rules = engine.administration
    command = source.command
    if rules is None or not isinstance(command, ResolveReaction):
        raise ValidationError("Administration reaction is not enabled")
    context = next((item for item in rules.reactions if item.id == command.context_id), None)
    if context is None:
        raise ValidationError("Unknown authored reaction context")
    knowledge = _knowledge_source(engine, source)
    facts = set(knowledge.fact_ids)
    if not facts <= {item.id for item in state.world.facts}:
        raise ValidationError("Reaction knowledge source references unknown facts")
    if not facts <= {fact for actor, fact in state.world.knowledge if actor == context.npc_id}:
        raise ValidationError("Reacting NPC does not know the bound information")
    if any(item.id == command.id for item in state.administration.knowledge):
        raise ConflictError("Administration consequence already exists")
    return _Context(
        context.npc_id,
        rules.profile_id,
        context.modifiers,
        rules.model_dump_json() + state.administration.model_dump_json(),
    )


def _context(
    engine: CampaignProcedureEngine,
    state: PlayState,
    source: CampaignReactionSource,
    player_actor_ids: tuple[str, ...],
) -> _Context:
    actor = _actor(engine, state, source.command.actor_id)
    handlers = {
        "initial-loyalty": _economic_context,
        "rescue-loyalty": _economic_context,
        "law": _law_context,
        "administration": _administration_context,
    }
    context = handlers[source.role](engine, state, source)
    _npc(state, source.command.actor_id, context.npc_id, player_actor_ids)
    if source.npc_id is not None and source.npc_id != context.npc_id:
        raise ValidationError("Reaction source names a different NPC")
    if source.rescue_bonus is not None:
        rescuer = next(
            (
                entity
                for entity in state.world.entities
                if entity.id == source.rescue_bonus.rescuer_actor_id
            ),
            None,
        )
        if rescuer is None or rescuer.kind.value != "actor" or rescuer.id == context.npc_id:
            raise ValidationError("Permanent rescue bonus requires its named rescuer actor")
        # The GM declares the PC's injury/death in this rescue. Do not infer it
        # from present HP, or require a dead/former PC to retain a playable build.
    # Canonical validation before prerequisite or target dice, without RNG.
    evaluate_reaction(context.profile_id, context.modifiers, (1, 1, 1))
    binding = json.dumps(
        (
            state.campaign_id,
            state.configuration_digest,
            actor,
            tuple(
                item.model_dump_json() for item in state.actors if item.actor_id == context.npc_id
            ),
            asdict(state.world),
            state.resources.model_dump(
                mode="json", exclude={"revision", "game_time", "receipts", "events"}
            ),
            context.binding,
        ),
        sort_keys=True,
        default=str,
    )
    return _Context(context.npc_id, context.profile_id, context.modifiers, binding)


def _digest(
    context: _Context,
    source: CampaignReactionSource,
    preparation_id: str,
    search: CheckTrace | None,
    contest: QuickContestTrace | None,
) -> str:
    payload = (
        _source_json(source),
        context.binding,
        preparation_id,
        asdict(search) if search is not None else None,
        asdict(contest) if contest is not None else None,
    )
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _prior(state: PlayState, source: CampaignReactionSource) -> CampaignReactionOutcome | None:
    command = source.command
    receipt = next(
        (item for item in state.resources.receipts if item.command_id == command.id), None
    )
    if receipt is None:
        return None
    if receipt.digest != hashlib.sha256(command.model_dump_json().encode()).hexdigest():
        raise ConflictError("Campaign reaction source command ID reused")
    if isinstance(command, (FindHireling, CheckLoyalty)):
        event = next(
            item for item in state.resources.events if item.id == "economics:" + command.id
        )
        return EconomicsOutcome.model_validate_json(event.kind)
    if isinstance(command, ResolveLawCase):
        event = next(item for item in state.resources.events if item.id == "law:" + command.id)
        return LawOutcome.model_validate_json(event.kind)
    event = next(
        item for item in state.resources.events if item.id == "campaign-admin:" + command.id
    )
    return AdministrationOutcome.model_validate_json(event.kind)


def _law_contest(
    engine: CampaignProcedureEngine,
    state: PlayState,
    source: CampaignReactionSource,
    rng: RandomSource,
) -> QuickContestTrace | None:
    if not isinstance(source.law_policy, AdversarialReactionPolicy):
        return None
    assert engine.law is not None and isinstance(source.command, ResolveLawCase)
    case, procedure = law_reaction_context(state.law, source.command, engine.law)
    assert procedure.opposing_target is not None
    return quick_contest(
        engine.law.profile_id,
        Contestant(source.command.actor_id, procedure.primary_target),
        Contestant("authority:" + case.jurisdiction_id, procedure.opposing_target),
        rng=rng,
    )


def _modifiers(
    context: _Context, contest: QuickContestTrace | None
) -> tuple[ReactionModifier, ...]:
    if contest is None:
        return context.modifiers
    margin = (
        contest.victory_margin if contest.winner == contest.first_id else -contest.victory_margin
    )
    return context.modifiers + (ReactionModifier("situation", margin, "law:adversarial-contest"),)


def _updated(state: PlayState, resources: ResourceState, **updates: object) -> PlayState:
    return state.model_copy(
        update={
            **updates,
            "resources": resources,
            "revision": resources.revision,
            "rulings": expire_rulings(state.rulings, resources.revision, resources.game_time),
        }
    )


def _prepare_social(
    engine: CampaignProcedureEngine,
    state: PlayState,
    source: CampaignReactionSource,
    context: _Context,
    contest: QuickContestTrace | None,
    player_actor_ids: tuple[str, ...],
    recognition_sources: tuple[SocialCommand, ...],
) -> PreparedReaction:
    return prepare_reaction(
        state.resources,
        state.world,
        campaign_social_command(state, source, context.npc_id),
        campaign_social_context(
            engine, state, source, context.profile_id, _modifiers(context, contest)
        ),
        SocialDisclosure(),
        profile_id=context.profile_id,
        player_actor_ids=player_actor_ids,
        system=True,
        recognition_sources=recognition_sources,
    )


def recognize_campaign_reaction(
    prepared: PreparedCampaignReaction, *, rng: RandomSource
) -> ResolvedReactionContext:
    """Host calls once only after all authority/current-source/Luck admission checks."""
    return recognize_reaction(prepared.reaction, rng=rng)


def _reaction_updated(state: PlayState, resources: ResourceState, **updates: object) -> PlayState:
    # Recognition and source consequences are one command, not two public turns.
    return _updated(
        state, resources.model_copy(update={"revision": state.resources.revision + 1}), **updates
    )


def prepare_campaign_reaction(
    engine: CampaignProcedureEngine,
    state: PlayState,
    source: CampaignReactionSource,
    actor_id: str | None = None,
    command_id: str | None = None,
    *,
    rng: RandomSource,
    player_actor_ids: tuple[str, ...],
    recognition_sources: tuple[SocialCommand, ...] = (),
) -> tuple[PlayState, PreparedCampaignReaction | None, CampaignReactionOutcome | None]:
    """Return prerequisite-only state, an unrolled continuation, or a terminal receipt.

    Successful search/contest traces have no existing independent world effects;
    the host must retain these frozen traces and source identity on cancellation.
    Failed search commits the existing terminal not-found receipt, without Luck.
    """
    source = CampaignReactionSource.model_validate(source)
    if actor_id is not None and actor_id != source.command.actor_id:
        raise ValidationError("Campaign reaction owner differs from its source actor")
    prior = _prior(state, source)
    if prior is not None:
        return state, None, prior
    if source.command.expected_revision != state.resources.revision:
        raise ConflictError("Campaign reaction preparation revision changed")
    context = _context(engine, state, source, player_actor_ids)
    # Validate approved standing/legacy recognition provenance before even a
    # separate search or contest. This preparation consumes no recognition dice.
    _prepare_social(engine, state, source, context, None, player_actor_ids, recognition_sources)
    search = None
    if isinstance(source.command, FindHireling):
        assert engine.economics is not None
        rule = hireling_reaction_rule(state.economics, source.command, engine.economics)
        actor = next(item for item in state.actors if item.actor_id == source.command.actor_id)
        build = engine.reviewer.compiler.compile(actor.proposal.draft).build
        assert build is not None
        search = search_for_hireling(
            rule, engine.economics, {value.target: value.value for value in build.sheet.values}, rng
        )
        if not search.outcome.succeeded:
            economics, resources, outcome = resolve_economics_reaction(
                state.economics, state.resources, source.command, engine.economics, None
            )
            return _updated(state, economics=economics, resources=resources), None, outcome
    contest = _law_contest(engine, state, source, rng)
    reaction = _prepare_social(
        engine, state, source, context, contest, player_actor_ids, recognition_sources
    )
    preparation_id = command_id or source.command.id
    prepared = PreparedCampaignReaction(
        source=source,
        preparation_id=preparation_id,
        actor_id=source.command.actor_id,
        npc_id=context.npc_id,
        profile_id=context.profile_id,
        modifiers=reaction.modifiers,
        reaction=reaction,
        context_digest=_digest(context, source, preparation_id, search, contest),
        frozen_search=search,
        frozen_contest=contest,
    )
    return state, prepared, None


def _validate_search(
    engine: CampaignProcedureEngine, state: PlayState, prepared: PreparedCampaignReaction
) -> None:
    command = prepared.source.command
    search = prepared.frozen_search
    if not isinstance(command, FindHireling):
        if search is not None:
            raise ValidationError("Foreign hireling search prerequisite")
        return
    assert engine.economics is not None
    rule = hireling_reaction_rule(state.economics, command, engine.economics)
    actor = next(item for item in state.actors if item.actor_id == command.actor_id)
    build = engine.reviewer.compiler.compile(actor.proposal.draft).build
    assert build is not None
    target = next(
        value.value for value in build.sheet.values if value.target == rule.search_target_id
    )
    if (
        search is None
        or not search.outcome.succeeded
        or replay_success(search) != search
        or search.base_target != int(target)
        or search.modifiers != rule.search_modifiers
        or search.rules_package != prepared.profile_id
    ):
        raise ValidationError("Invalid frozen hireling search")


def _validate_contest(
    engine: CampaignProcedureEngine, state: PlayState, prepared: PreparedCampaignReaction
) -> None:
    contest = prepared.frozen_contest
    source = prepared.source
    if not isinstance(source.law_policy, AdversarialReactionPolicy):
        if contest is not None:
            raise ValidationError("Foreign adversarial trial prerequisite")
        return
    assert engine.law is not None and isinstance(source.command, ResolveLawCase)
    case, procedure = law_reaction_context(state.law, source.command, engine.law)
    assert procedure.opposing_target is not None
    if contest is None or contest != resolve_quick_contest(
        prepared.profile_id,
        Contestant(prepared.actor_id, procedure.primary_target),
        Contestant("authority:" + case.jurisdiction_id, procedure.opposing_target),
        first_dice=contest.first.dice,
        second_dice=contest.second.dice,
    ):
        raise ValidationError("Invalid frozen adversarial trial contest")


def validate_prepared_campaign_reaction(
    engine: CampaignProcedureEngine,
    state: PlayState,
    prepared: PreparedCampaignReaction,
    *,
    player_actor_ids: tuple[str, ...],
) -> None:
    prepared = PreparedCampaignReaction.model_validate(prepared)
    if _prior(state, prepared.source) is not None:
        raise ConflictError("Campaign reaction source has already committed")
    context = _context(engine, state, prepared.source, player_actor_ids)
    if (
        prepared.actor_id != prepared.source.command.actor_id
        or prepared.npc_id != context.npc_id
        or prepared.profile_id != context.profile_id
        or prepared.modifiers != prepared.reaction.modifiers
        or prepared.context_digest
        != _digest(
            context,
            prepared.source,
            prepared.preparation_id,
            prepared.frozen_search,
            prepared.frozen_contest,
        )
    ):
        raise ConflictError("Campaign reaction preparation is foreign or its context changed")
    _validate_search(engine, state, prepared)
    _validate_contest(engine, state, prepared)
    validate_prepared_reaction(
        state.resources,
        state.world,
        campaign_social_command(state, prepared.source, prepared.npc_id),
        campaign_social_context(
            engine,
            state,
            prepared.source,
            prepared.profile_id,
            _modifiers(context, prepared.frozen_contest),
        ),
        SocialDisclosure(),
        prepared.reaction,
        profile_id=prepared.profile_id,
        player_actor_ids=player_actor_ids,
        system=True,
    )
    evaluate_reaction(prepared.profile_id, prepared.modifiers, (1, 1, 1))


def _law_succeeded(prepared: PreparedCampaignReaction, selected: ReactionTrace) -> bool:
    policy = prepared.source.law_policy
    if isinstance(policy, JudgeReactionPolicy):
        return selected.outcome in policy.outcomes
    assert isinstance(policy, AdversarialReactionPolicy) and prepared.frozen_contest is not None
    if selected.outcome == "neutral":
        return prepared.frozen_contest.winner in (None, prepared.actor_id)
    return selected.outcome in ("good", "very-good", "excellent")


def _permanent_bonus(prepared: PreparedCampaignReaction, selected: ReactionTrace) -> int:
    bonus = prepared.source.rescue_bonus
    if bonus is None:
        return 0
    if bonus.applies_on == "any-reaction" or selected.outcome in ("good", "very-good", "excellent"):
        return bonus.amount
    return 0


def resolve_prepared_campaign_reaction(
    engine: CampaignProcedureEngine,
    state: PlayState,
    prepared: PreparedCampaignReaction,
    selected: ReactionTrace,
    *,
    rng: RandomSource,
    player_actor_ids: tuple[str, ...],
    recognized: ResolvedReactionContext | None = None,
) -> tuple[PlayState, CampaignReactionOutcome]:
    """Commit selected actual effects; only later distinct time procedures use RNG."""
    prior = _prior(state, prepared.source)
    if prior is not None:
        return state, prior
    validate_prepared_campaign_reaction(engine, state, prepared, player_actor_ids=player_actor_ids)
    recognized = recognized or recognize_campaign_reaction(prepared, rng=NO_RANDOM)
    context = _context(engine, state, prepared.source, player_actor_ids)
    reaction_resources, _, _ = resolve_prepared_reaction(
        state.resources,
        state.world,
        campaign_social_command(state, prepared.source, prepared.npc_id),
        campaign_social_context(
            engine,
            state,
            prepared.source,
            prepared.profile_id,
            _modifiers(context, prepared.frozen_contest),
        ),
        SocialDisclosure(),
        prepared.reaction,
        recognized,
        selected,
        profile_id=prepared.profile_id,
        player_actor_ids=player_actor_ids,
        system=True,
    )
    command = prepared.source.command
    if isinstance(command, (FindHireling, CheckLoyalty)):
        assert engine.economics is not None
        economics, resources, economic_outcome = resolve_economics_reaction(
            state.economics,
            reaction_resources,
            command,
            engine.economics,
            selected,
            permanent_bonus=_permanent_bonus(prepared, selected),
        )
        return _reaction_updated(state, economics=economics, resources=resources), economic_outcome
    if isinstance(command, ResolveLawCase):
        assert engine.law is not None
        private = json.dumps(
            {
                "reaction": asdict(selected),
                "contest": asdict(prepared.frozen_contest) if prepared.frozen_contest else None,
            },
            default=str,
        )
        law, resources, law_outcome = resolve_law_reaction(
            state.law,
            reaction_resources,
            command,
            engine.law,
            succeeded=_law_succeeded(prepared, selected),
            private=private,
            advance=engine._advance(command.actor_id, rng),
        )
        return _reaction_updated(state, law=law, resources=resources), law_outcome
    assert prepared.source.knowledge is not None
    admin, resources, world, admin_outcome = resolve_administration_reaction(
        state.administration,
        reaction_resources,
        state.world,
        command,
        selected,
        _knowledge_source(engine, prepared.source),
        prepared.source.knowledge.outcomes,
    )
    return _reaction_updated(
        state, administration=admin, resources=resources, world=world
    ), admin_outcome


def cancel_prepared_campaign_reaction(
    engine: CampaignProcedureEngine,
    state: PlayState,
    prepared: PreparedCampaignReaction,
) -> tuple[PlayState, CampaignReactionOutcome]:
    """Close the original source, retaining prerequisite results in host history.

    The host checks GM authority and exact pending identity. Current source rules,
    build or context may be invalid; cancellation must remain possible then. Its
    normal source receipt prevents bypassing cancellation through legacy dispatch.
    """
    del engine  # Deliberately no current-source or build validation on cancellation.
    prepared = PreparedCampaignReaction.model_validate(prepared)
    prior = _prior(state, prepared.source)
    if prior is not None:
        return state, prior
    reaction_resources = cancel_prepared_reaction(state.resources, prepared.reaction, system=True)
    command = prepared.source.command
    if isinstance(command, (FindHireling, CheckLoyalty)):
        resources, economic_outcome = cancel_economics_reaction(reaction_resources, command)
        return _reaction_updated(state, resources), economic_outcome
    if isinstance(command, ResolveLawCase):
        resources, law_outcome = cancel_law_reaction(reaction_resources, command)
        return _reaction_updated(state, resources), law_outcome
    resources, admin_outcome = cancel_administration_reaction(reaction_resources, command)
    return _reaction_updated(state, resources), admin_outcome
