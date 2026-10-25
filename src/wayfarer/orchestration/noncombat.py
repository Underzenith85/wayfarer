"""Resolve authored approaches with existing catalog/effects checks under a lock."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Literal

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.rules.checks import CheckTrace, Modifier, Outcome
from wayfarer.engine.simulation.action_engine.engine import TaskCheckPreparation
from wayfarer.engine.simulation.actions import ActionCommand, CheckRule, PlayState
from wayfarer.engine.simulation.campaign.party import synchronous
from wayfarer.engine.simulation.health.condition_checks import definition_modifiers
from wayfarer.engine.simulation.health.recovery_guard import guard
from wayfarer.engine.simulation.magic.concentration import require_idle_concentration
from wayfarer.engine.simulation.magic.enchanting import busy_actor_ids as enchanting_actor_ids
from wayfarer.engine.simulation.magic.enchanting_calendar import require_enchanting_free
from wayfarer.engine.simulation.resources import Advance
from wayfarer.engine.simulation.social.noncombat import NoncombatEncounter
from wayfarer.engine.simulation.traits.innate_criticals import require_innate_actor_action
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id
from wayfarer.orchestration.noncombat_generations import enchanting_rest as current_enchanting_rest
from wayfarer.orchestration.noncombat_generations import (
    minimum_effective_skill as current_minimum_effective_skill,
)
from wayfarer.orchestration.pipeline import ActsAs, CommandPlan, submit

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService


class NoncombatCommand(ActionCommand):
    kind: Literal["start_noncombat", "approach_noncombat", "withdraw_noncombat"]
    encounter_id: Id
    selection_id: Id | None = None


_REST_ADMISSION = {"approach_noncombat": current_enchanting_rest}
_MINIMUM_ADMISSION = {"approach_noncombat": current_minimum_effective_skill}


class NoncombatService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def _approach_check(
        self,
        prepared: TaskCheckPreparation,
        *,
        minimum_effective_skill: bool,
    ) -> CheckTrace:
        # B345 forbids nondefense attempts below 3 after all task modifiers.
        if (
            minimum_effective_skill
            and prepared.target + sum(m.value for m in prepared.modifiers) < 3
        ):
            raise ValidationError("Noncombat effective skill must be at least 3")
        return prepared.check(rng=self.play.rng)

    def _prepare_check_inputs(
        self, state: PlayState, actor_id: str, rule: CheckRule, *, correct_skill_context: bool
    ) -> TaskCheckPreparation:
        actor = next((a for a in state.actors if a.actor_id == actor_id), None)
        if actor is None:
            raise ValidationError("Noncombat actor unavailable")
        build, _ = self.play.engine.reviewer.activate(
            actor.proposal, actor.approval, campaign_id=state.campaign_id, actor_id=actor_id
        )
        if rule.definition_id not in {p.definition_id for p in build.purchases}:
            raise ValidationError("Approach requires a purchased skill")
        value, dependencies = self.play.engine._target(
            state,
            actor_id,
            build,
            rule,
            default_context=self.play.engine.reviewer.compiler.default_skill_context(
                build.purchases
            )
            if correct_skill_context
            else None,
        )
        modifiers = (
            Modifier(rule.modifier, rule.id, rule.definition_id, rule.package_version),
        ) + definition_modifiers(
            state.resources,
            actor_id,
            rule.definition_id,
            self.play.engine.reviewer.compiler.definitions,
        )
        return TaskCheckPreparation(
            campaign_id=state.campaign_id,
            actor_id=actor_id,
            action=rule.action,
            target_id=rule.target_id,
            configuration_digest=self.play.engine.digest,
            target=int(value.value),
            modifiers=modifiers,
            rules_package=rule.package_id,
            rules_version=rule.package_version,
            derived=value,
            dependencies=dependencies,
        )

    def prepare_approach_check(
        self, state: PlayState, command: NoncombatCommand, *, correct_skill_context: bool
    ) -> TaskCheckPreparation:
        """Pure exact inputs shared by queue admission and actual approach scoring."""
        rules = self.play.engine.rules.noncombat
        encounter = next(
            (
                e
                for e in state.noncombat
                if e.id == command.encounter_id and e.actor_id == command.actor_id
            ),
            None,
        )
        if rules is None or encounter is None or encounter.status != "choice":
            raise ValidationError("No pending noncombat choice")
        rule = next((r for r in rules.encounters if r.id == encounter.rule_id), None)
        approach = (
            next((a for a in rule.approaches if a.id == command.selection_id), None)
            if rule
            else None
        )
        if approach is None:
            raise ValidationError("Unsupported approach")
        check = next(r for r in self.play.engine.rules.checks if r.id == approach.check_rule_id)
        return self._prepare_check_inputs(
            state, command.actor_id, check, correct_skill_context=correct_skill_context
        )

    @staticmethod
    def _require_approach_interval(
        state: PlayState, actor_id: str, duration: int, *, enchanting_rest: bool
    ) -> None:
        if enchanting_rest:
            require_enchanting_free(
                state.resources, actor_id, through=state.resources.game_time + duration
            )
            if actor_id in enchanting_actor_ids(state.resources.enchantment_projects):
                require_idle_concentration(state.resources, actor_id)

    def reduce(
        self,
        state: PlayState,
        command: NoncombatCommand,
        *,
        advance_time: bool = True,
        enchanting_rest: bool = False,
        minimum_effective_skill: bool = False,
        correct_skill_context: bool = False,
    ) -> PlayState:

        require_innate_actor_action(state, command.actor_id)
        guard(state, command.actor_id, command.kind)
        rules = self.play.engine.rules.noncombat
        if rules is None:
            raise ValidationError("Noncombat rules are not configured")
        actor = next((a for a in state.actors if a.actor_id == command.actor_id), None)
        cursor = next((c for c in state.actor_scenes if c.actor_id == command.actor_id), None)
        if actor is None or cursor is None or actor.approval is None:
            raise ValidationError("Noncombat actor unavailable")
        if any(e.status == "active" and actor.actor_id in e.turn_order for e in state.encounters):
            raise ConflictError("Actor is in combat")
        if (
            actor.conditions
            or next(p.current for p in state.resources.pools if p.id == f"hp:{actor.actor_id}") == 0
        ):
            raise ValidationError("Actor is incapacitated")
        old = next((e for e in state.noncombat if e.id == command.encounter_id), None)
        if command.kind == "start_noncombat":
            if old is not None or any(
                e.actor_id == actor.actor_id and e.status == "choice" for e in state.noncombat
            ):
                raise ConflictError(
                    "Noncombat encounter already exists or actor has a pending choice"
                )
            rule = next((r for r in rules.encounters if r.id == command.selection_id), None)
        else:
            if old is None or old.actor_id != actor.actor_id or old.status != "choice":
                raise ConflictError("No pending encounter for this actor")
            rule = next((r for r in rules.encounters if r.id == old.rule_id), None)
        if rule is None or rule.scene_id != cursor.scene_id:
            raise ValidationError("Encounter rule unavailable in this scene")
        revision = state.revision + 1
        encounter = old or NoncombatEncounter(
            id=command.encounter_id,
            rule_id=rule.id,
            actor_id=actor.actor_id,
            pending_choices=tuple(a.id for a in rule.approaches),
            revision=revision,
        )
        resources, world = state.resources, state.world
        revealed: tuple[str, ...] = ()
        if command.kind == "withdraw_noncombat":
            if command.selection_id is not None:
                raise ValidationError("Withdrawal has no selection")
            encounter = encounter.model_copy(update={"status": "withdrawn", "pending_choices": ()})
            revealed = rule.withdrawal_fact_ids
        elif command.kind == "approach_noncombat":
            approach = next((a for a in rule.approaches if a.id == command.selection_id), None)
            if approach is None:
                raise ValidationError("Unsupported approach")
            check_rule = next(
                r for r in self.play.engine.rules.checks if r.id == approach.check_rule_id
            )
            self._require_approach_interval(
                state, actor.actor_id, check_rule.duration, enchanting_rest=enchanting_rest
            )
            pools = {p.id: p for p in resources.pools}
            fp = pools[f"fp:{actor.actor_id}"]
            if fp.current < approach.fatigue_cost:
                raise ValidationError("Insufficient fatigue")
            target = next(e for e in world.entities if e.id == check_rule.target_id)
            entity = next(e for e in world.entities if e.id == actor.actor_id)
            if (target.location_id or target.id) != entity.location_id:
                raise ValidationError("Noncombat check target is remote")
            if (
                actor.available_at > resources.game_time
                or check_rule.not_before > resources.game_time
            ):
                raise ConflictError("Noncombat actor or check is not ready")
            if check_rule.required_equipment is not None and not any(
                i.owner_id == actor.actor_id
                and i.definition_id == check_rule.required_equipment
                and i.ready
                for i in resources.items
            ):
                raise ValidationError("Check equipment is required")
            prepared = self._prepare_check_inputs(
                state,
                actor.actor_id,
                check_rule,
                correct_skill_context=enchanting_rest or correct_skill_context,
            )
            trace = self._approach_check(
                prepared,
                minimum_effective_skill=minimum_effective_skill,
            )
            passed = trace.outcome in (Outcome.SUCCESS, Outcome.CRITICAL_SUCCESS)
            progress = encounter.progress + (
                approach.success_progress if passed else approach.failure_progress
            )
            failures = encounter.failures + (0 if passed else 1)
            status: Literal["choice", "success", "failure", "withdrawn"] = "choice"
            # Exhausted stakes take precedence over progress on the same failed check.
            if failures >= rule.maximum_failures:
                status = "failure"
            elif progress >= rule.required_progress:
                status = "success"
            revealed = approach.success_fact_ids if passed else approach.failure_fact_ids
            if status == "success":
                revealed += rule.completion_fact_ids
            elif status == "failure":
                revealed += rule.defeat_fact_ids
            pools[fp.id] = fp.model_copy(update={"current": fp.current - approach.fatigue_cost})
            hp = pools[f"hp:{actor.actor_id}"]
            pools[hp.id] = hp.model_copy(
                update={"current": max(0, hp.current - (0 if passed else approach.failure_hp_cost))}
            )
            resources = resources.model_copy(update={"pools": tuple(pools.values())})
            if advance_time:
                state = self.play.advance_clock(
                    state.model_copy(update={"world": world, "resources": resources}),
                    Advance(
                        id=f"{command.id}:time",
                        actor_id=actor.actor_id,
                        expected_revision=resources.revision,
                        to=resources.game_time + check_rule.duration,
                    ),
                )
                resources, world = state.resources, state.world
            encounter = encounter.model_copy(
                update={
                    "status": status,
                    "progress": progress,
                    "failures": failures,
                    "checks": encounter.checks + (trace,),
                    "pending_choices": encounter.pending_choices if status == "choice" else (),
                }
            )
        for fact_id in revealed:
            world = world.learn(actor.actor_id, fact_id)
        encounter = encounter.model_copy(
            update={
                "revision": revision,
                "revealed_fact_ids": tuple(
                    dict.fromkeys((*encounter.revealed_fact_ids, *revealed))
                ),
            }
        )
        return state.model_copy(
            update={
                "revision": revision,
                "world": world,
                "resources": resources.model_copy(update={"revision": revision}),
                "noncombat": tuple(e for e in state.noncombat if e.id != encounter.id)
                + (encounter,),
            }
        )

    def plan(
        self,
        command: NoncombatCommand,
        *,
        enchanting_rest: bool = False,
        minimum_effective_skill: bool = False,
    ) -> CommandPlan[NoncombatEncounter]:
        """What a noncombat command writes; the pipeline decides whether it runs."""
        payload = json.dumps(
            {
                "operation": "noncombat",
                "command": command.model_dump(mode="json"),
                **({"enchanting_rest_generation": 1} if enchanting_rest else {}),
                **({"minimum_effective_skill_generation": 1} if minimum_effective_skill else {}),
            },
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            current = self.play._load(campaign)
            if command.kind == "approach_noncombat":
                synchronous(current, command.actor_id, enchanting_rest=enchanting_rest)
            state = self.reduce(
                current,
                command,
                enchanting_rest=enchanting_rest,
                minimum_effective_skill=minimum_effective_skill,
            )
            if state.party.groups:
                state = state.model_copy(
                    update={
                        "party": state.party.model_copy(
                            update={
                                "groups": tuple(
                                    g.model_copy(
                                        update={
                                            "ready_through": max(
                                                g.ready_through, state.resources.game_time
                                            )
                                        }
                                    )
                                    for g in state.party.groups
                                )
                            }
                        )
                    }
                )
            state = self.play.checkpoint(state, before=current)
            self.play.commit(campaign, state)
            result = next(e for e in state.noncombat if e.id == command.encounter_id)
            return CommandReceipt(action="noncombat", outcome=result.model_dump_json())

        async def outcome(campaign: Campaign) -> NoncombatEncounter:
            return next(
                e for e in self.play._load(campaign).noncombat if e.id == command.encounter_id
            )

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=command.actor_id,
            outcome=outcome,
            control=(ActsAs(command.actor_id),),
            hypothetical=command.hypothetical,
            rng=self.play.rng,
        )

    async def execute(self, cid: str, value: object, *, principal_id: str) -> NoncombatEncounter:
        try:
            command = NoncombatCommand.model_validate(value)
        except ValueError as exc:
            raise ValidationError("Invalid noncombat command") from exc
        capture = _REST_ADMISSION.get(command.kind)
        current = False if capture is None else await capture(self.play, cid, command.id)
        minimum_capture = _MINIMUM_ADMISSION.get(command.kind)
        minimum = (
            False if minimum_capture is None else await minimum_capture(self.play, cid, command.id)
        )
        return await submit(
            self.play,
            cid,
            self.plan(command, enchanting_rest=current, minimum_effective_skill=minimum),
            principal_id=principal_id,
        )
