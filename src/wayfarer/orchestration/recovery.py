"""Transactional authored setbacks, custody and resumable shared-time recovery."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from typing import Literal

from wayfarer.engine.character.compiler import pool_limits
from wayfarer.engine.rules.checks import Modifier, Outcome, success_check
from wayfarer.engine.simulation.actions import ActionCommand, PlayState
from wayfarer.engine.simulation.campaign.advancement import AdvancementEntry
from wayfarer.engine.simulation.campaign.party import QueuedActivity, Subgroup, group_for
from wayfarer.engine.simulation.health.condition_checks import definition_modifiers
from wayfarer.engine.simulation.health.recovery import (
    Captivity,
    RecoveryDecision,
    RecoveryOption,
    ReplacementRecord,
    SetbackRecord,
)
from wayfarer.engine.simulation.health.recovery_guard import captive as captive
from wayfarer.engine.simulation.health.recovery_guard import guard as guard
from wayfarer.engine.simulation.resources import Consume, Transfer, Unequip
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Campaign, CommandReceipt
from wayfarer.orchestration.advancement import (
    AdvanceCharacter,
    AdvancementService,
    _balance,
    _build,
)
from wayfarer.orchestration.entropy import commit_command
from wayfarer.orchestration.party import PartyService
from wayfarer.orchestration.play import PlayService


class RecoveryCommand(ActionCommand):
    kind: Literal["apply_setback", "choose_recovery"]
    rule_id: str
    target_actor_id: str


class RecoveryService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def _option(self, rule_id: str) -> RecoveryOption:
        rules = self.play.engine.rules.recovery
        option = next((o for o in rules.options if o.id == rule_id), None) if rules else None
        if option is None:
            raise ValidationError("Unknown recovery option")
        return option

    def setback(self, state: PlayState, rule_id: str, actor_id: str, command_id: str) -> PlayState:
        rules = self.play.engine.rules.recovery
        rule = next((r for r in rules.setbacks if r.id == rule_id), None) if rules else None
        if rule is None or actor_id not in rule.actor_ids:
            raise ValidationError("Unknown authored setback or target")
        known = {fact for _, fact in state.world.knowledge}
        if not set(rule.required_fact_ids) <= known:
            raise ValidationError("Setback lacks required evidence")
        if actor_id in state.recovery.dead_actor_ids:
            raise ConflictError("Character is already dead")
        # A pending defense cannot be erased by a setback. Resolved encounters may continue.
        if any(
            e.status == "active"
            and (
                e.pending_defense is not None
                or e.pending_unarmed is not None
                or e.wait_interrupt is not None
                or e.blocked_reason is not None
            )
            and actor_id in e.turn_order
            for e in state.encounters
        ):
            raise ConflictError("Resolve pending combat decision before setback")
        group = group_for(state, actor_id)
        if group.ready_through != state.resources.game_time or any(
            q.group_id == group.id for q in state.party.queue
        ):
            raise ConflictError("Setback target has unresolved shared-time activity")
        world, resources = state.world, state.resources
        for fact in (
            *rule.consequence_fact_ids,
            *((rule.failure_fact_id,) if rule.failure_fact_id else ()),
        ):
            world = world.learn(actor_id, fact)
        destination = rule.destination_scene_id or group.scene_id
        scenes = self.play.engine.rules.scenes
        scene = next((s for s in scenes.scenes if s.id == destination), None) if scenes else None
        if scene is None:
            raise ValidationError("Setback destination is unsupported")
        previous = captive(state, actor_id)
        captures = state.recovery.captivity
        if previous is not None and rule.kind != "incapacitation":
            captures = tuple(
                c.model_copy(update={"released_at": resources.game_time}) if c == previous else c
                for c in captures
            )
        if rule.kind in ("capture", "surrender"):
            if rule.captor_id is None or rule.custody_owner_id is None:
                raise ValidationError("Capture requires captor and inventory custody")
            # Unpack leaves first; authoritative inventory commands validate each transfer.
            confiscated = tuple(i.id for i in resources.items if i.owner_id == actor_id)
            while any(i.owner_id == actor_id for i in resources.items):
                item = next(
                    i
                    for i in resources.items
                    if i.owner_id == actor_id
                    and not any(child.container_id == i.id for child in resources.items)
                )
                if item.equipped:
                    resources = self.play.engine.resources.apply(
                        resources,
                        Unequip(
                            id=f"{command_id}:unequip:{item.id}",
                            actor_id=actor_id,
                            expected_revision=resources.revision,
                            item_id=item.id,
                        ),
                    )
                resources = self.play.engine.resources.apply(
                    resources,
                    Transfer(
                        id=f"{command_id}:custody:{item.id}",
                        actor_id=actor_id,
                        expected_revision=resources.revision,
                        item_id=item.id,
                        quantity=item.quantity,
                        owner_id=rule.custody_owner_id,
                    ),
                )
            captures += (
                Captivity(
                    actor_id=actor_id,
                    captor_id=rule.captor_id,
                    scene_id=destination,
                    custody_owner_id=rule.custody_owner_id,
                    restraints=rule.restraints,
                    permitted=rule.permitted,
                    confiscated_item_ids=tuple(
                        dict.fromkeys(
                            (*confiscated, *(previous.confiscated_item_ids if previous else ()))
                        )
                    ),
                    captured_at=resources.game_time,
                ),
            )
        world = replace(
            world,
            entities=tuple(
                replace(e, location_id=scene.location_id) if e.id == actor_id else e
                for e in world.entities
            ),
        )
        groups = tuple(g for g in state.party.groups if g.id != group.id)
        others = tuple(a for a in group.actor_ids if a != actor_id)
        if others:
            groups += (
                group.model_copy(update={"actor_ids": others, "generation": group.generation + 1}),
            )
        groups += (
            Subgroup(
                id=f"recovery:{actor_id}",
                scene_id=destination,
                actor_ids=(actor_id,),
                ready_through=resources.game_time,
                generation=group.generation + 1,
            ),
        )
        # A setback separates the target into a recovery subgroup. An encounter
        # cannot keep charging one clock while its participants occupy two groups.
        # Retain the historical fight; individual withdrawal is a separate lifecycle.
        encounters = tuple(
            encounter.model_copy(
                update={"status": "completed", "completion_reason": f"setback:{rule.kind}"}
            )
            if encounter.status == "active"
            and actor_id in encounter.turn_order
            and (others or destination != group.scene_id or rule.kind in ("retreat", "death"))
            else encounter.model_copy(
                update={
                    "participants": tuple(
                        participant.model_copy(
                            update={
                                "ready_item_ids": tuple(
                                    sorted(
                                        item.id
                                        for item in resources.items
                                        if item.owner_id == participant.actor_id
                                        and item.equipped
                                        and item.ready
                                    )
                                )
                            }
                        )
                        for participant in encounter.participants
                    )
                }
            )
            if encounter.status == "active"
            else encounter
            for encounter in state.encounters
        )
        conditions = ("unconscious",) if rule.kind in ("incapacitation", "death") else ()
        actors = tuple(
            a.model_copy(update={"conditions": tuple(dict.fromkeys((*a.conditions, *conditions)))})
            if a.actor_id == actor_id
            else a
            for a in state.actors
        )
        dead = state.recovery.dead_actor_ids + ((actor_id,) if rule.kind == "death" else ())
        state = state.model_copy(
            update={
                "encounters": encounters,
                "world": world,
                "resources": resources.model_copy(update={"revision": state.revision}),
                "actors": actors,
                "actor_scenes": tuple(
                    c.model_copy(update={"scene_id": destination}) if c.actor_id == actor_id else c
                    for c in state.actor_scenes
                ),
                "party": state.party.model_copy(update={"groups": groups}),
                "recovery": state.recovery.model_copy(
                    update={
                        "captivity": captures,
                        "dead_actor_ids": dead,
                        "impossible_objective_ids": tuple(
                            dict.fromkeys(
                                (
                                    *state.recovery.impossible_objective_ids,
                                    *rule.impossible_objective_ids,
                                )
                            )
                        ),
                        "setbacks": state.recovery.setbacks
                        + (
                            SetbackRecord(
                                id=command_id,
                                actor_id=actor_id,
                                rule_id=rule.id,
                                kind=rule.kind,
                                at=resources.game_time,
                            ),
                        ),
                    }
                ),
            }
        )
        return state

    def assess(self, state: PlayState, command: RecoveryCommand) -> RecoveryOption:
        option = self._option(command.rule_id)
        if (
            command.actor_id not in option.actor_ids
            or command.target_actor_id not in option.target_actor_ids
        ):
            raise ValidationError("Recovery choice is not authorized for these actors")
        group = group_for(state, command.actor_id)
        target_group = group_for(state, command.target_actor_id)
        if group.scene_id != option.scene_id or target_group.scene_id != option.scene_id:
            raise ValidationError("Recovery requires physical arrival at the authored scene")
        known = {f.id for f in state.world.perspective(command.actor_id).facts}
        if not set(option.required_fact_ids) <= known:
            raise ValidationError("Recovery requires evidence known to the acting character")
        actor = next(a for a in state.actors if a.actor_id == command.actor_id)
        if option.kind not in ("rest", "replace") and (
            "unconscious" in actor.conditions
            or "stunned" in actor.conditions
            or actor.available_at > state.resources.game_time
            or next(p.current for p in state.resources.pools if p.id == f"hp:{command.actor_id}")
            == 0
        ):
            raise ValidationError("Incapacitated characters cannot perform this recovery action")
        held = captive(state, command.actor_id)
        if held and option.kind not in held.permitted:
            raise ValidationError("This choice is infeasible under the current restraints")
        if command.actor_id in state.recovery.dead_actor_ids and option.kind != "replace":
            raise ValidationError("Dead characters cannot act")
        if option.kind in ("escape", "rescue") and captive(state, command.target_actor_id) is None:
            raise ValidationError("Target is already free")
        if option.kind == "escape" and command.actor_id != command.target_actor_id:
            raise ValidationError("Escape acts on the captive's own state")
        if option.kind == "rescue" and held:
            raise ValidationError("Outside rescue requires a free actor")
        if (
            option.kind in ("rest", "replace", "advance")
            and command.actor_id != command.target_actor_id
        ):
            raise ValidationError("Personal downtime requires the actor's own choice")
        if (
            option.kind == "replace"
            and command.target_actor_id not in state.recovery.dead_actor_ids
        ):
            raise ValidationError("Replacement requires a dead character")
        if option.kind not in ("rescue", "assist", "communicate", "negotiate") and any(
            e.status == "active" and command.actor_id in e.turn_order for e in state.encounters
        ):
            raise ConflictError("This downtime action requires leaving combat")
        if any(
            e.status == "active"
            and (e.pending_defense is not None or e.pending_unarmed is not None)
            and {command.actor_id, command.target_actor_id} & set(e.turn_order)
            for e in state.encounters
        ):
            raise ConflictError("Resolve pending combat choice before recovery")
        return option

    def choose(self, state: PlayState, command: RecoveryCommand) -> PlayState:
        option = self.assess(state, command)
        group = group_for(state, command.actor_id)
        if group.paused or any(q.group_id == group.id for q in state.party.queue):
            raise ConflictError("Subgroup already has pending work or is paused")
        if group.ready_through != state.resources.game_time:
            raise ConflictError("Recovery starts at committed shared time")
        if not option.supported:
            decision = RecoveryDecision(
                id=command.id,
                actor_id=command.actor_id,
                target_actor_id=command.target_actor_id,
                option_id=option.id,
                due=state.resources.game_time,
                status="adjudication_required",
            )
            return state.model_copy(
                update={
                    "recovery": state.recovery.model_copy(
                        update={"decisions": state.recovery.decisions + (decision,)}
                    )
                }
            )
        due = group.ready_through + option.ticks
        decision = RecoveryDecision(
            id=command.id,
            actor_id=command.actor_id,
            target_actor_id=command.target_actor_id,
            option_id=option.id,
            due=due,
            status="pending",
        )
        queued = QueuedActivity(
            id=command.id,
            actor_id=command.actor_id,
            group_id=group.id,
            generation=group.generation,
            start=group.ready_through,
            due=due,
            command_json=command.model_dump_json(),
            family="recovery",
        )
        return state.model_copy(
            update={
                "recovery": state.recovery.model_copy(
                    update={"decisions": state.recovery.decisions + (decision,)}
                ),
                "party": state.party.model_copy(
                    update={
                        "queue": state.party.queue + (queued,),
                        "groups": tuple(
                            g.model_copy(update={"ready_through": due}) if g.id == group.id else g
                            for g in state.party.groups
                        ),
                    }
                ),
            }
        )

    def finish(self, state: PlayState, command: RecoveryCommand) -> PlayState:
        decision = next(d for d in state.recovery.decisions if d.id == command.id)
        if decision.status != "pending":
            raise ConflictError("Recovery decision already resolved")
        before = state
        check_json = None
        status: Literal["committed", "failed", "rejected"] = "committed"
        try:
            option = self.assess(state, command)
            resources = state.resources
            if option.cost:
                item = next(
                    (
                        i
                        for i in resources.items
                        if i.owner_id == command.actor_id
                        and i.definition_id == option.cost_definition_id
                        and i.quantity >= option.cost
                    ),
                    None,
                )
                if item is None:
                    raise ValidationError("Recovery cannot afford its explicit cost")
                resources = self.play.engine.resources.apply(
                    resources,
                    Consume(
                        id=f"{command.id}:cost",
                        actor_id=command.actor_id,
                        expected_revision=resources.revision,
                        item_id=item.id,
                        quantity=option.cost,
                    ),
                )
            state = state.model_copy(
                update={"resources": resources.model_copy(update={"revision": state.revision})}
            )
            if option.check_rule_id:
                rule = next(
                    r for r in self.play.engine.rules.checks if r.id == option.check_rule_id
                )
                build = _build(self.play, state, command.actor_id)
                value = next(
                    (v for v in build.sheet.values if v.target == rule.definition_id), None
                )
                if value is None or (
                    rule.required_equipment
                    and not any(
                        i.owner_id == command.actor_id
                        and i.definition_id == rule.required_equipment
                        and i.ready
                        for i in state.resources.items
                    )
                ):
                    raise ValidationError("Recovery check lacks skill or equipment")
                trace = success_check(
                    int(value.value),
                    (Modifier(rule.modifier, "recovery", rule.definition_id, rule.package_version),)
                    + definition_modifiers(
                        state.resources,
                        command.actor_id,
                        rule.definition_id,
                        self.play.engine.reviewer.compiler.definitions,
                    ),
                    rng=self.play.rng,
                    rules_package=rule.package_id,
                    rules_version=rule.package_version,
                )
                check_json = json.dumps(asdict(trace))
                if trace.outcome not in (Outcome.SUCCESS, Outcome.CRITICAL_SUCCESS):
                    status = "failed"
            facts = option.success_fact_ids if status == "committed" else option.failure_fact_ids
            world = state.world
            for fact in facts:
                world = world.learn(command.actor_id, fact)
            state = state.model_copy(update={"world": world})
            if status == "committed":
                state = self._effect(state, command, option)
        except ValidationError, ConflictError:
            state, status = before, "rejected"
        return state.model_copy(
            update={
                "recovery": state.recovery.model_copy(
                    update={
                        "decisions": tuple(
                            d.model_copy(update={"status": status, "check_json": check_json})
                            if d.id == command.id
                            else d
                            for d in state.recovery.decisions
                        )
                    }
                )
            }
        )

    def _effect(
        self, state: PlayState, command: RecoveryCommand, option: RecoveryOption
    ) -> PlayState:
        actor_id = command.target_actor_id
        resources = state.resources
        if option.kind == "advance":
            if option.advancement is None:
                raise ValidationError("No authored advancement draft")
            return AdvancementService(self.play).reduce_purchase(
                state,
                AdvanceCharacter(
                    id=f"{command.id}:advance",
                    actor_id=actor_id,
                    expected_revision=state.revision,
                    expected_build_revision=_build(self.play, state, actor_id).revision,
                    draft=option.advancement,
                    reason=f"Downtime: {option.id}",
                ),
                revision=state.revision,
            )
        if option.communication_fact_ids:
            known = {f.id for f in state.world.perspective(command.actor_id).facts}
            if not set(option.communication_fact_ids) <= known:
                raise ValidationError("Cannot communicate unknown facts")
            world = state.world
            for recipient in option.recipient_actor_ids:
                for fact in option.communication_fact_ids:
                    world = world.learn(recipient, fact)
            state = state.model_copy(update={"world": world})
        if option.kind in ("escape", "rescue"):
            held = captive(state, actor_id)
            assert held is not None
            state = state.model_copy(
                update={
                    "recovery": state.recovery.model_copy(
                        update={
                            "captivity": tuple(
                                c.model_copy(update={"released_at": resources.game_time})
                                if c == held
                                else c
                                for c in state.recovery.captivity
                            )
                        }
                    )
                }
            )
        elif option.kind in ("recover_items", "resupply"):
            if option.kind == "resupply":
                ids: tuple[str | None, ...] = (option.stock_item_id,)
            else:
                records = tuple(c for c in state.recovery.captivity if c.actor_id == actor_id)
                if not records or captive(state, actor_id):
                    raise ValidationError("Recover gear after release")
                ids = tuple(dict.fromkeys(i for c in records for i in c.confiscated_item_ids))
            for item_id in ids:
                item = next((i for i in resources.items if i.id == item_id), None)
                if item is None:
                    if option.kind == "resupply":
                        raise ValidationError("Resupply stock is exhausted")
                    continue
                if item.owner_id == actor_id or item.container_id is not None:
                    if option.kind == "resupply":
                        raise ValidationError("Resupply stock is unavailable")
                    continue
                if option.kind == "recover_items" and not any(
                    item.id in c.confiscated_item_ids and item.owner_id == c.custody_owner_id
                    for c in state.recovery.captivity
                    if c.actor_id == actor_id
                ):
                    raise ValidationError("Equipment has left recorded custody")
                owner = next(e for e in state.world.entities if e.id == item.owner_id)
                target = next(e for e in state.world.entities if e.id == actor_id)
                if owner.location_id != target.location_id:
                    raise ValidationError("Cannot recover remote inventory")
                resources = self.play.engine.resources.apply(
                    resources,
                    Transfer(
                        id=f"{command.id}:item:{item.id}",
                        actor_id=item.owner_id,
                        expected_revision=resources.revision,
                        item_id=item.id,
                        quantity=option.stock_quantity
                        if option.kind == "resupply"
                        else item.quantity,
                        owner_id=actor_id,
                        new_item_id=f"{command.id}:stock" if option.kind == "resupply" else None,
                    ),
                )
        elif option.kind == "rest":
            if any(
                p.id in (f"hp:{actor_id}", f"fp:{actor_id}")
                and (p.injury is not None or p.fatigue is not None)
                for p in resources.pools
            ):
                raise ValidationError("Profile recovery requires a timed GURPS recovery task")
            resources = resources.model_copy(
                update={
                    "pools": tuple(
                        p.model_copy(
                            update={
                                "current": min(
                                    p.maximum,
                                    p.current
                                    + (
                                        option.recovery_hp
                                        if p.id == f"hp:{actor_id}"
                                        else option.recovery_fp
                                    ),
                                )
                            }
                        )
                        if p.id in (f"hp:{actor_id}", f"fp:{actor_id}")
                        else p
                        for p in resources.pools
                    )
                }
            )
            hp = next(p for p in resources.pools if p.id == f"hp:{actor_id}")
            state = state.model_copy(
                update={
                    "actors": tuple(
                        a.model_copy(
                            update={
                                "conditions": tuple(c for c in a.conditions if c != "unconscious")
                            }
                        )
                        if a.actor_id == actor_id and hp.current > 0
                        else a
                        for a in state.actors
                    )
                }
            )
        elif option.kind == "replace":
            if option.replacement is None:
                raise ValidationError("No authored replacement")
            reviewer = self.play.engine.reviewer
            review = reviewer.review(option.replacement)
            if review.status != "automatic" or review.compilation.build is None:
                raise ValidationError("Replacement requires legal, automatically approved build")
            actor = next(a for a in state.actors if a.actor_id == actor_id)
            approval = reviewer.approve(
                option.replacement,
                campaign_id=state.campaign_id,
                actor_id=actor_id,
                revision=state.revision,
            )
            build = review.compilation.build
            limits = pool_limits(build)
            resources = resources.model_copy(
                update={
                    "owners": tuple(
                        o.model_copy(
                            update={"definitions": tuple(p.definition_id for p in build.purchases)}
                        )
                        if o.actor_id == actor_id
                        else o
                        for o in resources.owners
                    ),
                    "pools": tuple(
                        p.model_copy(
                            update={
                                "maximum": limits[p.id.split(":", 1)[0]],
                                "current": limits[p.id.split(":", 1)[0]],
                            }
                        )
                        if p.id in (f"hp:{actor_id}", f"fp:{actor_id}")
                        else p
                        for p in resources.pools
                    ),
                }
            )
            # Retire earned points so replacement cannot reuse the predecessor's advancement bank.
            balance = _balance(state, actor_id)
            ledger = state.advancement
            if balance:
                ledger += (
                    AdvancementEntry(
                        id=f"{command.id}:retire",
                        actor_id=actor_id,
                        kind="purchase",
                        points=-balance,
                        revision=state.revision,
                        build_before=_build(self.play, state, actor_id).revision,
                        build_after=build.revision,
                        reason="Retire predecessor advancement balance",
                    ),
                )
            state = state.model_copy(
                update={
                    "advancement": ledger,
                    "actors": tuple(
                        a.model_copy(
                            update={
                                "proposal": option.replacement,
                                "approval": approval,
                                "conditions": (),
                                "available_at": resources.game_time,
                            }
                        )
                        if a.actor_id == actor_id
                        else a
                        for a in state.actors
                    ),
                    "approvals": state.approvals + (approval,),
                    "recovery": state.recovery.model_copy(
                        update={
                            "dead_actor_ids": tuple(
                                a for a in state.recovery.dead_actor_ids if a != actor_id
                            ),
                            "replacements": state.recovery.replacements
                            + (
                                ReplacementRecord(
                                    actor_id=actor_id,
                                    previous_proposal=actor.proposal,
                                    at=resources.game_time,
                                ),
                            ),
                        }
                    ),
                }
            )
        return state.model_copy(
            update={"resources": resources.model_copy(update={"revision": state.revision})}
        )

    async def execute(self, cid: str, value: object, *, authenticated_actor_id: str) -> PlayState:
        command = RecoveryCommand.model_validate(value)
        if command.actor_id != authenticated_actor_id or command.hypothetical:
            raise ValidationError("Recovery command is unauthorized")
        if (
            command.kind == "apply_setback"
            and command.actor_id not in self.play.engine.reviewer.gm_ids
        ):
            raise ValidationError("Setbacks require trusted GM authority and authored evidence")
        payload = command.model_dump_json()

        def resolve(campaign: Campaign) -> CommandReceipt:
            state = self.play._load(campaign)
            revision = state.revision + 1
            state = state.model_copy(
                update={
                    "revision": revision,
                    "resources": state.resources.model_copy(update={"revision": revision}),
                }
            )
            state = (
                self.setback(state, command.rule_id, command.target_actor_id, command.id)
                if command.kind == "apply_setback"
                else self.choose(state, command)
            )
            state = PartyService(self.play).flush(state)
            state = self.play.checkpoint(state)
            self.play.commit(campaign, state)
            return CommandReceipt(action="recovery", outcome=command.kind)

        result = await commit_command(
            self.play.store,
            cid,
            command.id,
            command.expected_revision,
            payload,
            resolve,
            actor_id=command.actor_id,
            rng=self.play.rng,
        )
        return self.play._load(result["state"])
