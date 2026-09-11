"""Stable-order offscreen decisions, committed with the shared-time checkpoint."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Literal

from wayfarer import validation
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Campaign, CommandReceipt
from wayfarer.orchestration.entropy import commit_command
from wayfarer.persistence.events import CommandOrigin
from wayfarer.rules.mundane_traits.runtime import Audience
from wayfarer.rules.social_hooks import Reputation, Standing
from wayfarer.simulation.actions import ActionCommand, PlayState
from wayfarer.simulation.npcs import (
    NPCDecision,
    NPCProgress,
    NPCSocialAction,
    NPCSocialStanding,
    NPCSocialTrigger,
)
from wayfarer.simulation.resources import Consume

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService
    from wayfarer.orchestration.providers import Orchestrator


class NPCProposal(ActionCommand):
    kind: Literal["propose_npc"] = "propose_npc"
    plan_id: str
    action_id: str


def initialize(play: PlayService, state: PlayState) -> PlayState:
    rules = play.engine.rules.npcs
    if rules is None or state.npcs.progress:
        return state
    progress = tuple(NPCProgress(plan_id=p.id, next_due=p.first_due) for p in rules.plans)
    return state.model_copy(
        update={
            "npcs": state.npcs.model_copy(
                update={
                    "progress": progress,
                    "decisions": tuple(
                        NPCDecision(
                            id=f"npc:{p.id}:0", plan_id=p.id, due=p.first_due, status="pending"
                        )
                        for p in rules.plans
                    ),
                }
            )
        }
    )


def due_times(play: PlayService, state: PlayState, frontier: int) -> set[int]:
    rules = play.engine.rules.npcs
    if rules is None:
        return set()
    plans = {p.id: p for p in rules.plans}
    times: set[int] = set()
    for progress in state.npcs.progress:
        plan = plans[progress.plan_id]
        remaining = min(
            plan.action_budget - progress.spent_actions, plan.clock_limit - progress.clock
        )
        times.update(
            progress.next_due + i * plan.interval
            for i in range(remaining)
            if progress.next_due + i * plan.interval <= frontier
        )
    return times


def checkpoint(play: PlayService, state: PlayState) -> PlayState:
    rules = play.engine.rules.npcs
    if rules is None:
        return state
    state = initialize(play, state)
    plans = {p.id: p for p in rules.plans}
    for _ in range(rules.checkpoint_budget):
        eligible = sorted(
            (
                p
                for p in state.npcs.progress
                if p.next_due <= state.resources.game_time
                and p.spent_actions < plans[p.plan_id].action_budget
                and p.clock < plans[p.plan_id].clock_limit
            ),
            key=lambda p: (p.next_due, p.plan_id),
        )
        if not eligible:
            break
        progress = eligible[0]
        plan = plans[progress.plan_id]
        known = frozenset(f.id for f in state.world.perspective(plan.actor_id).facts)
        options = tuple(a for a in plan.actions if set(a.required_fact_ids) <= known)
        proposal = next(
            (
                d
                for d in state.npcs.decisions
                if d.plan_id == plan.id and d.due == progress.next_due and d.status == "proposed"
            ),
            None,
        )
        choice = next(
            (a for a in options if proposal and a.id == proposal.action_id),
            options[0] if options else None,
        )
        outcome: Literal["committed", "fallback", "rejected"] = "committed"
        if choice is None:
            outcome = "fallback"
        before = state
        if choice is not None:
            try:
                resources = state.resources
                from wayfarer.simulation.fright import blocked, requires_adjudication

                if blocked(resources, plan.actor_id) or requires_adjudication(
                    resources, plan.actor_id
                ):
                    raise ValidationError("NPC cannot act through a fright consequence")
                if isinstance(choice, NPCSocialAction) and (
                    choice.reveal_fact_ids or choice.recipient_ids
                ):
                    raise ValidationError("Social disclosure must use its bounded outcome policy")
                if choice.cost:
                    item = next(
                        (
                            i
                            for i in resources.items
                            if i.owner_id == plan.actor_id
                            and i.definition_id == choice.cost_definition_id
                            and i.quantity >= choice.cost
                        ),
                        None,
                    )
                    if item is None:
                        raise ValidationError("NPC cannot afford action")
                    resources = play.engine.resources.apply(
                        resources,
                        Consume(
                            id=f"npc:{plan.id}:{progress.spent_actions}:cost",
                            actor_id=plan.actor_id,
                            expected_revision=resources.revision,
                            item_id=item.id,
                            quantity=choice.cost,
                        ),
                    )
                world = state.world
                # All communication content must already be known to the sender.
                if not set(choice.reveal_fact_ids) <= known:
                    raise ValidationError("NPC cannot communicate unknown facts")
                for recipient in choice.recipient_ids:
                    for fact in choice.reveal_fact_ids:
                        world = world.learn(recipient, fact)
                state = state.model_copy(
                    update={
                        "world": world,
                        "resources": resources.model_copy(update={"revision": state.revision}),
                    }
                )
                if isinstance(choice, NPCSocialAction):
                    state = social_occurrence(
                        play,
                        state,
                        plan.actor_id,
                        choice.social,
                        "npc-social:"
                        + hashlib.sha256(
                            json.dumps(
                                [plan.id, progress.spent_actions, choice.id],
                                separators=(",", ":"),
                            ).encode()
                        ).hexdigest(),
                    )
                if choice.kind == "transfer_prisoner":
                    from wayfarer.orchestration.recovery import RecoveryService

                    if choice.setback_rule_id is None or choice.target_actor_id is None:
                        raise ValidationError("Prisoner transfer requires an authored destination")
                    captive = next(
                        (
                            c
                            for c in state.recovery.captivity
                            if c.actor_id == choice.target_actor_id and c.released_at is None
                        ),
                        None,
                    )
                    if captive is None or captive.captor_id != plan.actor_id:
                        raise ValidationError("NPC does not hold this prisoner")
                    state = RecoveryService(play).setback(
                        state,
                        choice.setback_rule_id,
                        choice.target_actor_id,
                        f"npc:{plan.id}:{progress.spent_actions}",
                    )
            except ValidationError, ConflictError:
                state, outcome = before, "rejected"
        decision = NPCDecision(
            id=f"npc:{plan.id}:{progress.spent_actions}",
            plan_id=plan.id,
            action_id=choice.id if choice else None,
            due=progress.next_due,
            status=outcome,
            known_fact_ids=tuple(sorted(known)),
        )
        updated = progress.model_copy(
            update={
                "next_due": progress.next_due + plan.interval,
                "spent_actions": progress.spent_actions + 1,
                "clock": progress.clock + (outcome == "committed"),
            }
        )
        decisions = tuple(decision if d.id == decision.id else d for d in state.npcs.decisions)
        if updated.spent_actions < plan.action_budget and updated.clock < plan.clock_limit:
            decisions += (
                NPCDecision(
                    id=f"npc:{plan.id}:{updated.spent_actions}",
                    plan_id=plan.id,
                    due=updated.next_due,
                    status="pending",
                ),
            )
        state = state.model_copy(
            update={
                "npcs": state.npcs.model_copy(
                    update={
                        "progress": tuple(
                            updated if p.plan_id == plan.id else p for p in state.npcs.progress
                        ),
                        "decisions": decisions,
                    }
                )
            }
        )
    # Bounded by the finite plan budgets; no recursive reaction/model calls.
    return state


def _standing(authored: NPCSocialStanding) -> tuple[Standing, Audience]:
    """Translate authored standing into the engine-owned reaction hooks."""
    return (
        Standing(
            appearance=authored.appearance,
            reputations=tuple(
                Reputation(
                    reputation.id,
                    reputation.level,
                    reputation.scope,
                    reputation.recognition,
                    reputation.classes,
                    reputation.hidden,
                )
                for reputation in authored.reputations
            ),
        ),
        Audience(
            perceptible=authored.audience_perceptible,
            audible=authored.audience_audible,
            recognizes_status=authored.audience_recognizes_status,
            attracted=authored.audience_attracted,
            classes=authored.audience_classes,
        ),
    )


def social_occurrence(
    play: PlayService,
    state: PlayState,
    actor_id: str,
    trigger: NPCSocialTrigger,
    occurrence_id: str,
) -> PlayState:
    from wayfarer.orchestration.social import ResolvedInteraction, dispatch
    from wayfarer.rules.gurps_social import (
        InfluenceConditions,
        ReactionModifier,
        influence_procedure,
    )
    from wayfarer.simulation.mechanics.gurps_melee import build
    from wayfarer.simulation.social import SocialCommand, SocialContext, SocialDisclosure

    profile_id = play.engine.reviewer.compiler.statistics_profile
    if profile_id is None:
        raise ValidationError("Authored social triggers require an exact GURPS profile")
    target, will, ht = 10, trigger.npc_will, 10
    context = SocialContext(profile_id, target)
    if trigger.kind in ("fright", "self-control"):
        if not any(a.actor_id == trigger.subject_id for a in state.actors):
            raise ValidationError("Social trigger subject requires an approved build")
        compiled = build(play.rules_context, state, trigger.subject_id)
        assert compiled.statistics is not None
        will, ht = compiled.statistics.will, compiled.statistics.ht
        target = will + trigger.modifier
        if trigger.kind == "self-control":
            actor = next(a for a in state.actors if a.actor_id == trigger.subject_id)
            purchase = next(
                (p for p in actor.proposal.draft.purchases if p.definition_id == trigger.trait_id),
                None,
            )
            definition = play.engine.reviewer.compiler.definitions.get(trigger.trait_id or "")
            if (
                purchase is None
                or purchase.trait is None
                or definition is None
                or definition.point_cost is None
            ):
                raise ValidationError("Self-control trigger requires an approved disadvantage")
            context.trait_options = purchase.trait
            context.trait_rules = definition.trait_rules
            context.trait_base = definition.point_cost
            context.trait_levels = purchase.amount
            context.self_control_modifier = trigger.modifier
    elif trigger.kind == "influence":
        if not any(a.actor_id == actor_id for a in state.actors):
            raise ValidationError("Influence initiator requires an approved build")
        compiled = build(play.rules_context, state, actor_id)
        value = next((v for v in compiled.sheet.values if v.target == trigger.skill_id), None)
        if value is None:
            raise ValidationError("Influence skill has no approved level")
        target = int(value.value)
        context.skill = influence_procedure(trigger.skill_id)
        context.influence_conditions = InfluenceConditions(
            specious_intimidation=trigger.specious_intimidation
        )
        # A built subject's Will is authoritative; npc_will is only for unbuilt NPCs.
        if any(a.actor_id == trigger.subject_id for a in state.actors):
            subject = build(play.rules_context, state, trigger.subject_id)
            assert subject.statistics is not None
            will = subject.statistics.will
    elif trigger.kind == "skill":
        # #345: the authored trigger names the procedure and the circumstances;
        # the initiator's approved level and the subject's Will come from builds.
        from wayfarer.rules.mundane_skills.social import require_procedure

        procedure = require_procedure(profile_id, trigger.skill_id)
        if not any(a.actor_id == actor_id for a in state.actors):
            raise ValidationError("Social skill initiator requires an approved build")
        compiled = build(play.rules_context, state, actor_id)
        value = next((v for v in compiled.sheet.values if v.target == procedure.id), None)
        if value is None:
            raise ValidationError("Social skill has no approved level")
        context.procedure_id = procedure.id
        context.skill_level = int(value.value)
        context.conditions = frozenset(trigger.conditions)
        if procedure.influence:
            context.skill = influence_procedure(procedure.id)
            context.influence_conditions = InfluenceConditions(
                specious_intimidation=trigger.specious_intimidation
            )
        if procedure.paired:
            # B198: the less fluent party decides, so the subject needs the skill too.
            if not any(a.actor_id == trigger.subject_id for a in state.actors):
                raise ValidationError("A paired social skill needs both approved builds")
            other = build(play.rules_context, state, trigger.subject_id)
            partner = next((v for v in other.sheet.values if v.target == procedure.id), None)
            if partner is None:
                raise ValidationError("The other party has no approved level for this skill")
            context.partner_skill = int(partner.value)
        # A built subject's Will is authoritative; npc_will is only for unbuilt
        # NPCs. The trigger carries no modifier: the procedure derives its own.
        will = trigger.npc_will
        if any(a.actor_id == trigger.subject_id for a in state.actors):
            resisting = build(play.rules_context, state, trigger.subject_id)
            assert resisting.statistics is not None
            will = resisting.statistics.will
    context.target, context.will, context.ht = target, will, ht
    context.required_fact_ids = trigger.required_fact_ids
    if trigger.kind in ("reaction", "influence", "skill"):
        if trigger.kind != "skill":
            context.modifiers = (
                ReactionModifier("situation", trigger.modifier, occurrence_id, True),
            )
        if trigger.standing is not None:
            context.standing, context.audience = _standing(trigger.standing)
    command = SocialCommand(
        id="social-occurrence:" + hashlib.sha256(occurrence_id.encode()).hexdigest(),
        actor_id=actor_id,
        subject_id=trigger.subject_id,
        kind=trigger.kind,
        trigger_id=occurrence_id,
        expected_revision=state.resources.revision,
    )
    updated, _ = dispatch(
        play,
        state,
        command,
        ResolvedInteraction(
            context,
            SocialDisclosure(trigger.disclosure_fact_ids),
        ),
    )
    return updated.model_copy(
        update={
            "revision": state.revision,
            "resources": updated.resources.model_copy(update={"revision": state.revision}),
        }
    )


class NPCService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    async def propose_generated(
        self,
        cid: str,
        *,
        llm: Orchestrator,
        command_id: str,
        authenticated_gm_id: str,
        plan_id: str,
    ) -> PlayState:
        """Generate a bounded advisory choice before submitting a typed NPC proposal."""
        from wayfarer.orchestration.providers import ProviderRequest

        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        if authenticated_gm_id not in play.engine.reviewer.gm_ids:
            raise ValidationError("NPC proposals require trusted director authority")
        state = play._load(campaign)
        rules = play.engine.rules.npcs
        plan = next((p for p in rules.plans if p.id == plan_id), None) if rules else None
        if plan is None:
            raise ValidationError("Unknown bounded NPC plan")
        reply = await llm.job_reply(
            ProviderRequest(
                operation="intent",
                session_id=f"npc:{cid}:{plan_id}",
                context_json=plan.model_dump_json(),
                prompt="Choose one listed action_id for this NPC plan.",
                output_schema={
                    "type": "object",
                    "properties": {
                        "action_id": {"type": "string", "enum": [a.id for a in plan.actions]}
                    },
                    "required": ["action_id"],
                    "additionalProperties": False,
                },
            ),
            cid=cid,
            revision=state.revision,
            principal=authenticated_gm_id,
            actor=authenticated_gm_id,
            key=command_id,
        )
        choice = validation.mapping(validation.decode(reply.payload_json))
        action_id = validation.string(choice["action_id"])
        command = NPCProposal(
            id=command_id,
            actor_id=authenticated_gm_id,
            expected_revision=state.revision,
            plan_id=plan_id,
            action_id=action_id,
        )
        return await NPCService(play).propose(
            cid,
            command,
            authenticated_gm_id=authenticated_gm_id,
            origin=CommandOrigin.proposal(
                "npc", choice, provider=reply.provider, model=reply.model
            ),
        )

    async def propose(
        self,
        cid: str,
        value: object,
        *,
        authenticated_gm_id: str,
        origin: CommandOrigin | None = None,
    ) -> PlayState:
        command = NPCProposal.model_validate(value)
        if (
            command.actor_id != authenticated_gm_id
            or authenticated_gm_id not in self.play.engine.reviewer.gm_ids
        ):
            raise ValidationError("NPC proposals require trusted director authority")
        if command.hypothetical:
            raise ValidationError("Hypothetical proposal cannot be persisted")
        payload = command.model_dump_json()

        def resolve(campaign: Campaign) -> CommandReceipt:
            state = self.play._load(campaign)
            rules = self.play.engine.rules.npcs
            plan = (
                next((p for p in rules.plans if p.id == command.plan_id), None) if rules else None
            )
            progress = next((p for p in state.npcs.progress if p.plan_id == command.plan_id), None)
            if (
                plan is None
                or progress is None
                or command.action_id not in {a.id for a in plan.actions}
            ):
                raise ValidationError("Unknown bounded NPC option")
            if any(
                d.plan_id == plan.id and d.due == progress.next_due and d.status == "proposed"
                for d in state.npcs.decisions
            ):
                raise ValidationError("NPC already has a pending proposal")
            revision = state.revision + 1
            decision = NPCDecision(
                id=command.id,
                plan_id=plan.id,
                action_id=command.action_id,
                due=progress.next_due,
                status="proposed",
            )
            state = state.model_copy(
                update={
                    "revision": revision,
                    "resources": state.resources.model_copy(update={"revision": revision}),
                    "npcs": state.npcs.model_copy(
                        update={"decisions": state.npcs.decisions + (decision,)}
                    ),
                }
            )
            self.play.commit(campaign, state)
            return CommandReceipt(action="npc", outcome="proposed")

        result = await commit_command(
            self.play.store,
            cid,
            command.id,
            command.expected_revision,
            payload,
            resolve,
            actor_id=command.actor_id,
            rng=self.play.rng,
            origin=origin,
        )
        return self.play._load(result["state"])
