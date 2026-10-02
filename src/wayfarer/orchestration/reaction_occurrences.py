"""The bounded NPC occurrence's paid prerequisite and terminal decision joins."""

import hashlib
import json

from wayfarer.engine.rules.social.gurps_social import ReactionModifier
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.npcs import (
    NPCDecision,
    NPCProgress,
    NPCSocialAction,
    NPCSocialPlan,
)
from wayfarer.engine.simulation.health.fright_state import blocked, requires_adjudication
from wayfarer.engine.simulation.resources import Advance, Consume
from wayfarer.engine.simulation.social.social import SocialDisclosure
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record
from wayfarer.orchestration.npcs import initialize
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.reaction_context import social_context
from wayfarer.orchestration.reaction_records import AuthoredSocialReaction, NPCReactionSource


class PreparedOccurrence(Record):
    plan: NPCSocialPlan
    progress: NPCProgress
    action: NPCSocialAction
    known_fact_ids: tuple[str, ...]
    occurrence_id: str


def occurrence_source(
    play: PlayService,
    state: PlayState,
    actor_id: str,
    source: NPCReactionSource,
    *,
    require_due: bool = True,
    validate_selection: bool = True,
) -> tuple[AuthoredSocialReaction, PreparedOccurrence]:
    rules = play.engine.rules.npcs
    plan = (
        next((plan for plan in rules.plans if plan.id == source.plan_id), None) if rules else None
    )
    progress = next(
        (progress for progress in state.npcs.progress if progress.plan_id == source.plan_id), None
    )
    if not isinstance(plan, NPCSocialPlan) or progress is None or plan.actor_id != actor_id:
        raise ValidationError("Reaction requires the current authored NPC social plan and actor")
    if (
        (require_due and progress.next_due > state.resources.game_time)
        or progress.spent_actions >= plan.action_budget
        or progress.clock >= plan.clock_limit
    ):
        raise ConflictError("NPC reaction occurrence is not currently due")
    known = tuple(sorted(fact.id for fact in state.world.perspective(actor_id).facts))
    options = tuple(
        action for action in plan.actions if set(action.required_fact_ids) <= set(known)
    )
    proposal = next(
        (
            decision
            for decision in state.npcs.decisions
            if decision.plan_id == plan.id
            and decision.due == progress.next_due
            and decision.status == "proposed"
        ),
        None,
    )
    action = next(
        (action for action in options if proposal and action.id == proposal.action_id),
        options[0] if options else None,
    )
    if not validate_selection:
        action = next(
            (candidate for candidate in plan.actions if candidate.id == source.action_id), None
        )
    if not isinstance(action, NPCSocialAction) or action.id != source.action_id:
        raise ValidationError("Reaction does not match the selected authored NPC occurrence")
    if action.reveal_fact_ids or action.recipient_ids:
        raise ValidationError("Social disclosure must use its bounded outcome policy")
    if blocked(state.resources, actor_id) or requires_adjudication(state.resources, actor_id):
        raise ValidationError("NPC cannot act through a fright consequence")
    trigger = action.social
    if trigger.kind not in ("reaction", "influence", "skill") or (
        trigger.kind != "reaction" and trigger.skill_id != "skill:diplomacy"
    ):
        raise ValidationError("Occurrence does not contain a supported reaction target")
    occurrence_id = (
        "npc-social:"
        + hashlib.sha256(
            json.dumps([plan.id, progress.spent_actions, action.id], separators=(",", ":")).encode()
        ).hexdigest()
    )
    return AuthoredSocialReaction(
        trigger_id=occurrence_id,
        subject_id=trigger.subject_id,
        active_interaction=source.active_interaction,
        sapient=source.sapient,
        mode=trigger.kind,
        modifiers=()
        if trigger.kind == "skill"
        else (ReactionModifier("situation", trigger.modifier, occurrence_id, True),),
        standing=trigger.standing,
        required_fact_ids=trigger.required_fact_ids,
        disclosure=SocialDisclosure(trigger.disclosure_fact_ids),
        npc_will=trigger.npc_will,
        conditions=trigger.conditions,
    ), PreparedOccurrence(
        plan=plan,
        progress=progress,
        action=action,
        known_fact_ids=known,
        occurrence_id=occurrence_id,
    )


def prepare_occurrence(
    play: PlayService,
    state: PlayState,
    actor_id: str,
    source: NPCReactionSource,
) -> tuple[PlayState, AuthoredSocialReaction, PreparedOccurrence]:
    state = initialize(play, state)
    rules = play.engine.rules.npcs
    assert rules is not None
    plans = {plan.id: plan for plan in rules.plans}
    eligible = sorted(
        (
            progress
            for progress in state.npcs.progress
            if progress.spent_actions < plans[progress.plan_id].action_budget
            and progress.clock < plans[progress.plan_id].clock_limit
        ),
        key=lambda progress: (progress.next_due, progress.plan_id),
    )
    target = next((progress for progress in eligible if progress.plan_id == source.plan_id), None)
    if not eligible or target is None or eligible[0].next_due != target.next_due:
        raise ConflictError("Prepare the next authored NPC occurrence in chronological order")
    due = target.next_due
    authored, planned = occurrence_source(
        play, state, actor_id, source, require_due=False, validate_selection=False
    )
    # Validate voluntary action and approved levels before prior or target dice.
    # The actual scheduler below decides which option is legal after earlier work.
    social_context(play, state, actor_id, "npc-prepare:" + planned.occurrence_id, authored)
    if due > state.resources.game_time:
        state = play.advance_clock(
            state,
            Advance(
                id=f"npc-reaction-time:{source.plan_id}:{target.spent_actions}",
                actor_id=actor_id,
                expected_revision=state.resources.revision,
                to=due,
            ),
            run_npcs=False,
        )
        state = play.checkpoint(state, run_npcs=False)
        state = state.model_copy(
            update={
                "party": state.party.model_copy(
                    update={
                        "groups": tuple(
                            group.model_copy(update={"ready_through": state.resources.game_time})
                            for group in state.party.groups
                        ),
                    }
                )
            }
        )
        state = state.model_copy(
            update={"resources": state.resources.model_copy(update={"revision": state.revision})}
        )
    state = play.checkpoint(state, stop_before_npc=(source.plan_id, target.spent_actions))
    authored, prepared = occurrence_source(play, state, actor_id, source)
    action = prepared.action
    if action.cost:
        item = next(
            (
                item
                for item in state.resources.items
                if item.owner_id == actor_id
                and item.definition_id == action.cost_definition_id
                and item.quantity >= action.cost
            ),
            None,
        )
        if item is None:
            raise ValidationError("NPC cannot afford action")
        resources = play.engine.resources.apply(
            state.resources,
            Consume(
                id=f"npc:{prepared.plan.id}:{prepared.progress.spent_actions}:cost",
                actor_id=actor_id,
                expected_revision=state.resources.revision,
                item_id=item.id,
                quantity=action.cost,
            ),
        )
        state = state.model_copy(
            update={"resources": resources.model_copy(update={"revision": state.revision})}
        )
    return state, authored, prepared


def finish_occurrence(
    state: PlayState, prepared: PreparedOccurrence, *, cancelled: bool
) -> PlayState:
    plan, progress, action = prepared.plan, prepared.progress, prepared.action
    decision = NPCDecision(
        id=f"npc:{plan.id}:{progress.spent_actions}",
        plan_id=plan.id,
        action_id=action.id,
        due=progress.next_due,
        status="rejected" if cancelled else "committed",
        known_fact_ids=prepared.known_fact_ids,
    )
    updated = progress.model_copy(
        update={
            "next_due": progress.next_due + plan.interval,
            "spent_actions": progress.spent_actions + 1,
            "clock": progress.clock + (not cancelled),
        }
    )
    decisions = tuple(
        decision if previous.id == decision.id else previous for previous in state.npcs.decisions
    )
    if updated.spent_actions < plan.action_budget and updated.clock < plan.clock_limit:
        decisions += (
            NPCDecision(
                id=f"npc:{plan.id}:{updated.spent_actions}",
                plan_id=plan.id,
                due=updated.next_due,
                status="pending",
            ),
        )
    return state.model_copy(
        update={
            "npcs": state.npcs.model_copy(
                update={
                    "progress": tuple(
                        updated if previous.plan_id == plan.id else previous
                        for previous in state.npcs.progress
                    ),
                    "decisions": decisions,
                }
            )
        }
    )
