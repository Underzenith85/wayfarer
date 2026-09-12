"""Cross-record validation of pinned lifecycle policy and persisted state."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfarer.engine.simulation.npcs import NPCSocialAction
from wayfarer.engine.world import EntityKind
from wayfarer.errors import ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.actions import ActionRules, PlayState


def validate_lifecycle(state: PlayState, rules: ActionRules) -> None:
    actors = {a.actor_id for a in state.actors}
    entities = {e.id: e for e in state.world.entities}
    facts = {f.id for f in state.world.facts}
    scenes = {s.id for s in rules.scenes.scenes} if rules.scenes else set()
    owners = {o.actor_id for o in state.resources.owners}
    if (rules.npcs or rules.recovery) and (not rules.party or not rules.scenes):
        configured = "npcs" if rules.npcs else "recovery"
        missing = " and ".join(
            name for name, value in (("party", rules.party), ("scenes", rules.scenes)) if not value
        )
        raise ValidationError(
            f"Lifecycle rules require shared-time party and scene rules; {configured} rules are "
            f"configured but {missing} rules are missing",
            reference=configured,
        )
    if rules.npcs:
        plans = {p.id: p for p in rules.npcs.plans}
        if len(plans) != len(rules.npcs.plans):
            raise ValidationError("Duplicate NPC plan")
        if sum(p.action_budget for p in plans.values()) > rules.npcs.checkpoint_budget:
            raise ValidationError("NPC plan budgets exceed checkpoint loop limit")
        for p in plans.values():
            if p.actor_id not in entities or entities[p.actor_id].kind != EntityKind.ACTOR:
                raise ValidationError("NPC plan actor does not exist")
            if p.faction_id and (
                p.faction_id not in entities or entities[p.faction_id].kind != EntityKind.FACTION
            ):
                raise ValidationError("Unknown NPC faction")
            if len({a.id for a in p.actions}) != len(p.actions):
                raise ValidationError("Duplicate NPC action")
            for a in p.actions:
                if isinstance(a, NPCSocialAction):
                    trigger = a.social
                    if (
                        trigger.subject_id not in entities
                        or entities[trigger.subject_id].kind != EntityKind.ACTOR
                        or not set((*trigger.required_fact_ids, *trigger.disclosure_fact_ids))
                        <= facts
                        or a.reveal_fact_ids
                        or a.recipient_ids
                        or (
                            trigger.kind not in ("reaction", "influence")
                            and trigger.disclosure_fact_ids
                        )
                        or (trigger.kind == "self-control" and trigger.trait_id is None)
                    ):
                        raise ValidationError("Invalid authored social trigger")
                if (
                    not set((*a.required_fact_ids, *a.reveal_fact_ids)) <= facts
                    or not set(a.recipient_ids) <= entities.keys()
                ):
                    raise ValidationError("Invalid NPC knowledge references")
                if a.cost and (p.actor_id not in owners or a.cost_definition_id is None):
                    raise ValidationError("NPC cost requires an inventory owner and definition")
                if a.kind == "transfer_prisoner":
                    r = (
                        next(
                            (r for r in rules.recovery.setbacks if r.id == a.setback_rule_id), None
                        )
                        if rules.recovery
                        else None
                    )
                    if (
                        r is None
                        or r.kind != "capture"
                        or r.captor_id != p.actor_id
                        or a.target_actor_id not in r.actor_ids
                    ):
                        raise ValidationError("Invalid authored captor transfer")
        if {p.plan_id for p in state.npcs.progress} != plans.keys():
            raise ValidationError("Missing NPC progress")
        if len({p.plan_id for p in state.npcs.progress}) != len(state.npcs.progress):
            raise ValidationError("Duplicate NPC progress")
        for progress in state.npcs.progress:
            rule = plans[progress.plan_id]
            if (
                progress.spent_actions > rule.action_budget
                or progress.clock > rule.clock_limit
                or progress.next_due != rule.first_due + progress.spent_actions * rule.interval
            ):
                raise ValidationError("Invalid NPC clock or action budget")
        for decision in state.npcs.decisions:
            plan = plans.get(decision.plan_id)
            if (
                plan is None
                or (
                    decision.action_id is not None
                    and decision.action_id not in {a.id for a in plan.actions}
                )
                or not set(decision.known_fact_ids) <= facts
            ):
                raise ValidationError("Invalid persisted NPC decision")
        expected_pending = {
            (p.plan_id, p.next_due)
            for p in state.npcs.progress
            if p.spent_actions < plans[p.plan_id].action_budget
            and p.clock < plans[p.plan_id].clock_limit
        }
        if {
            (d.plan_id, d.due) for d in state.npcs.decisions if d.status == "pending"
        } != expected_pending:
            raise ValidationError("NPC pending schedule disagrees with clock")
        if len({d.id for d in state.npcs.decisions}) != len(state.npcs.decisions):
            raise ValidationError("Duplicate NPC decision")
    elif state.npcs.progress or state.npcs.decisions:
        raise ValidationError("NPC state requires policy")
    if rules.recovery:
        if len({r.id for r in rules.recovery.setbacks}) != len(rules.recovery.setbacks) or len(
            {o.id for o in rules.recovery.options}
        ) != len(rules.recovery.options):
            raise ValidationError("Duplicate recovery rule")
        for r in rules.recovery.setbacks:
            if (
                not set(r.actor_ids) <= actors
                or not set((*r.required_fact_ids, *r.consequence_fact_ids)) <= facts
            ):
                raise ValidationError("Invalid setback references")
            if r.destination_scene_id and r.destination_scene_id not in scenes:
                raise ValidationError("Unknown detention/retreat scene")
            if r.kind in ("capture", "surrender") and (
                r.captor_id not in entities or r.custody_owner_id not in owners
            ):
                raise ValidationError("Capture requires a known captor and custody owner")
            if r.impossible_objective_ids and (
                not rules.objectives
                or not set(r.impossible_objective_ids)
                <= {o.id for o in rules.objectives.objectives}
                or r.failure_fact_id not in facts
            ):
                raise ValidationError(
                    "Impossible objectives require explicit failure/fallback evidence"
                )
            if (
                r.impossible_objective_ids
                and rules.objectives
                and not all(
                    any(
                        p.kind == "known"
                        and p.subject_id == actor_id
                        and p.value == r.failure_fact_id
                        and not p.negate
                        for p in rules.objectives.failures
                    )
                    for actor_id in r.actor_ids
                )
            ):
                raise ValidationError(
                    "Impossible objectives need an authored terminal failure transition"
                )
            if r.failure_fact_id and r.failure_fact_id not in facts:
                raise ValidationError("Unknown setback failure evidence")
        for o in rules.recovery.options:
            if (
                not set((*o.actor_ids, *o.target_actor_ids)) <= actors
                or o.scene_id not in scenes
                or not set((*o.required_fact_ids, *o.success_fact_ids, *o.failure_fact_ids))
                <= facts
            ):
                raise ValidationError("Invalid recovery option references")
            if (
                not set(o.recipient_actor_ids) <= actors
                or not set(o.communication_fact_ids) <= facts
            ):
                raise ValidationError("Unknown authored communication route")
            if o.communication_fact_ids and (
                not o.recipient_actor_ids or o.kind not in ("communicate", "negotiate", "assist")
            ):
                raise ValidationError("Facts require an authored communication action")
            if o.check_rule_id and o.check_rule_id not in {r.id for r in rules.checks}:
                raise ValidationError("Unsupported recovery check")
            if o.cost and o.cost_definition_id is None:
                raise ValidationError("Recovery cost requires a resource definition")
        active = [c.actor_id for c in state.recovery.captivity if c.released_at is None]
        if len(set(active)) != len(active) or not set(active) <= actors:
            raise ValidationError("Duplicate or unknown captive")
        for c in state.recovery.captivity:
            if (
                c.captor_id not in entities
                or c.custody_owner_id not in owners
                or c.scene_id not in scenes
            ):
                raise ValidationError("Invalid persisted captivity")
            if (
                c.released_at is None
                and next(s.scene_id for s in state.actor_scenes if s.actor_id == c.actor_id)
                != c.scene_id
            ):
                raise ValidationError("Captive left detention without release")
        if not set(state.recovery.dead_actor_ids) <= actors:
            raise ValidationError("Unknown dead actor")
        if len({d.id for d in state.recovery.decisions}) != len(state.recovery.decisions):
            raise ValidationError("Duplicate recovery decision")
        if {d.id for d in state.recovery.decisions if d.status == "pending"} != {
            q.id for q in state.party.queue if q.family == "recovery"
        }:
            raise ValidationError("Recovery pending decisions and shared-time queue disagree")
    elif state.recovery != type(state.recovery)():
        raise ValidationError("Recovery state requires policy")
