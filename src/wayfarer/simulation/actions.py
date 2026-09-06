"""Typed proposals, feasibility, deterministic resolution and canonical play state."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import replace
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer.character.compiler import ValidatedBuild
from wayfarer.character.power import Approval, CharacterProposal, PowerReviewer
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.catalog import SKILLS, DefinitionKind, ImplementationStatus
from wayfarer.rules.checks import CheckTrace, Modifier, Outcome, RandomSource, success_check
from wayfarer.rules.effects import DerivedValue, EffectEvaluator, MechanicalTarget
from wayfarer.simulation.resources import Advance, Consume, Record, ResourceEngine, ResourceState
from wayfarer.world import Entity, EntityKind, World

Id = Annotated[str, Field(min_length=1, max_length=100)]


class ActionCommand(Record):
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)
    hypothetical: bool = False


class Move(ActionCommand):
    kind: Literal["move"] = "move"
    destination_id: Id | None = None


class Inspect(ActionCommand):
    kind: Literal["inspect"] = "inspect"
    target_id: Id | None = None


class Attack(ActionCommand):
    kind: Literal["attack"] = "attack"
    target_id: Id | None = None
    weapon_id: Id | None = None


class UseItem(ActionCommand):
    kind: Literal["use_item"] = "use_item"
    item_id: Id | None = None
    quantity: int = Field(default=1, ge=1, le=1000000)


class Social(ActionCommand):
    kind: Literal["social"] = "social"
    target_id: Id | None = None
    approach: Literal["diplomacy", "intimidation", "deception"] = "diplomacy"


class Wait(ActionCommand):
    kind: Literal["wait"] = "wait"
    ticks: int = Field(ge=1, le=10000)


class Question(ActionCommand):
    kind: Literal["question"] = "question"
    text: str = Field(min_length=1, max_length=2000)


TypedAction = Annotated[
    Move | Inspect | Attack | UseItem | Social | Wait | Question, Field(discriminator="kind")
]
ACTION_ADAPTER: TypeAdapter[TypedAction] = TypeAdapter(TypedAction)


class ActorSetup(Record):
    actor_id: Id
    proposal: CharacterProposal
    aware_of: tuple[str, ...] = ()
    conditions: tuple[Literal["unconscious", "stunned", "restrained"], ...] = ()
    available_at: int = Field(default=0, ge=0)


class PlayActor(ActorSetup):
    approval: Approval | None = None


class CheckRule(Record):
    """Trusted scenario check with catalog skill and modifier provenance."""

    id: Id
    action: Literal["inspect", "social"]
    target_id: Id
    definition_id: Id
    package_id: str
    package_version: str
    duration: int = Field(default=1, ge=1, le=10000)
    modifier: int = Field(default=0, ge=-20, le=20)
    reveal_fact_ids: tuple[str, ...] = ()
    required_equipment: str | None = None
    not_before: int = Field(default=0, ge=0)


class ActionRules(Record):
    id: Id
    version: int = Field(ge=1)
    movement_ticks: int = Field(default=1, ge=1, le=10000)
    item_ticks: int = Field(default=1, ge=1, le=10000)
    maximum_wait: int = Field(default=100, ge=1, le=10000)
    fatigue_cost: int = Field(default=0, ge=0, le=100)
    checks: tuple[CheckRule, ...] = ()
    consumables: tuple[str, ...] = ()


class ActionResult(Record):
    status: Literal[
        "feasible",
        "clarification",
        "question",
        "rejected",
        "unsupported",
        "adjudication_required",
        "committed",
    ]
    revision: int
    code: str
    command_id: str
    check: CheckTrace | None = None
    derived: DerivedValue | None = None
    dependencies: tuple[DerivedValue, ...] = ()
    revealed_fact_ids: tuple[str, ...] = ()
    rules_digest: str = ""


class PlayState(Record):
    campaign_id: str
    revision: int = Field(default=0, ge=0)
    configuration_digest: str
    world: World
    resources: ResourceState
    actors: tuple[PlayActor, ...]
    approvals: tuple[Approval, ...] = ()
    last_result: ActionResult | None = None


class ActionEngine:
    def __init__(
        self, reviewer: PowerReviewer, resources: ResourceEngine, rules: ActionRules
    ) -> None:
        if reviewer.compiler.rules != resources.rules:
            raise ValidationError("Character and resource rules differ")
        self.reviewer, self.resources, self.rules = reviewer, resources, rules
        self.checks = {(r.action, r.target_id): r for r in rules.checks}
        if len(self.checks) != len(rules.checks) or len({r.id for r in rules.checks}) != len(
            rules.checks
        ):
            raise ValidationError("Ambiguous action check rules")
        definitions = reviewer.compiler.definitions
        for rule in rules.checks:
            definition = definitions.get(rule.definition_id)
            if (
                definition is None
                or definition.kind is not DefinitionKind.SKILL
                or definition.status is not ImplementationStatus.IMPLEMENTED
            ):
                raise ValidationError("Action check requires an implemented catalog skill")
            if (rule.package_id, rule.package_version) != reviewer.compiler.definition_packages[
                rule.definition_id
            ]:
                raise ValidationError("Check provenance is not pinned")
            if rule.action == "social" and rule.definition_id != "skill:diplomacy":
                raise ValidationError("Only diplomacy social checks are implemented")
            if (
                rule.required_equipment is not None
                and rule.required_equipment not in resources.specs
            ):
                raise ValidationError("Unknown required equipment")
        if any(key not in resources.specs for key in rules.consumables):
            raise ValidationError("Unknown consumable definition")
        payload = (
            rules.model_dump_json()
            + reviewer.policy.digest
            + repr(resources.rules)
            + repr(reviewer.compiler.effects)
            + "".join(spec.model_dump_json() for _, spec in sorted(resources.specs.items()))
        )
        self.digest = hashlib.sha256(payload.encode()).hexdigest()

    def validate(self, state: PlayState) -> None:
        if state.configuration_digest != self.digest:
            raise ValidationError("Play configuration changed; explicit migration required")
        if state.revision != state.resources.revision:
            raise ValidationError("Play and resource revisions diverged")
        state.world.validate()
        self.resources.validate(state.resources)
        entities = {e.id: e for e in state.world.entities}
        actors = {a.actor_id: a for a in state.actors}
        if len(actors) != len(state.actors) or not set(actors) <= self.resources.actors:
            raise ValidationError("Invalid play actor IDs")
        owners = {o.actor_id: o for o in state.resources.owners}
        pools = {p.id: p for p in state.resources.pools}
        for actor in state.actors:
            build = self.reviewer.compiler.compile(actor.proposal.draft).build
            if build is None:
                raise ValidationError("Play actor has an illegal build")
            owner = owners.get(actor.actor_id)
            if owner is None or set(owner.definitions) != {
                p.definition_id for p in build.purchases
            }:
                raise ValidationError("Resource prerequisites do not match the compiled build")
            values = {v.target: v.value for v in build.sheet.values}
            for kind, attribute in (("hp", "attribute:st"), ("fp", "attribute:ht")):
                pool = pools.get(f"{kind}:{actor.actor_id}")
                if pool is None or pool.maximum != values.get(attribute):
                    raise ValidationError("Runtime pool limit does not match the compiled build")
            entity = entities.get(actor.actor_id)
            if entity is None or entity.kind is not EntityKind.ACTOR:
                raise ValidationError("Play actor is not a world actor")
            if any(key not in entities for key in actor.aware_of):
                raise ValidationError("Unknown awareness reference")
            if actor.approval is not None:
                if (
                    actor.approval not in state.approvals
                    or actor.approval.recorded_revision > state.revision
                ):
                    raise ValidationError("Approval is not in the canonical ledger")
                self.reviewer.activate(
                    actor.proposal,
                    actor.approval,
                    campaign_id=state.campaign_id,
                    actor_id=actor.actor_id,
                )
        for entity in state.world.entities:
            if (
                entity.location_id is not None
                and entities[entity.location_id].kind is not EntityKind.LOCATION
            ):
                raise ValidationError("Entity location must reference a location")
        facts = {f.id for f in state.world.facts}
        for rule in self.rules.checks:
            if rule.target_id not in entities or not set(rule.reveal_fact_ids) <= facts:
                raise ValidationError("Invalid scenario check references")
            if rule.action == "social" and entities[rule.target_id].kind is not EntityKind.ACTOR:
                raise ValidationError("Social target must be an actor")

    @staticmethod
    def _location(entity: Entity) -> str | None:
        return entity.id if entity.kind is EntityKind.LOCATION else entity.location_id

    def assess(self, state: PlayState, command: TypedAction) -> ActionResult:
        self.validate(state)

        def result(
            status: Literal[
                "feasible",
                "clarification",
                "question",
                "rejected",
                "unsupported",
                "adjudication_required",
            ],
            code: str,
        ) -> ActionResult:
            return ActionResult(
                status=status,
                revision=state.revision,
                code=code,
                command_id=command.id,
                rules_digest=self.digest,
            )

        actor = next((a for a in state.actors if a.actor_id == command.actor_id), None)
        if actor is None:
            return result("rejected", "actor.unavailable")
        if command.expected_revision != state.revision:
            raise ConflictError("Play revision changed")
        if isinstance(command, Question) or command.hypothetical:
            return result("question", "action.no_effect")
        if actor.approval is None:
            return result("rejected", "character.approval_required")
        pools = {p.id: p for p in state.resources.pools}
        if not isinstance(command, Wait) and pools[f"hp:{actor.actor_id}"].current == 0:
            return result("rejected", "actor.incapacitated")
        if (
            not isinstance(command, Wait)
            and pools[f"fp:{actor.actor_id}"].current < self.rules.fatigue_cost
        ):
            return result("rejected", "resource.fatigue")
        if not isinstance(command, Wait) and actor.conditions:
            return result("rejected", "actor.condition")
        if actor.available_at > state.resources.game_time and not isinstance(command, Wait):
            return result("rejected", "actor.not_ready")
        entities = {e.id: e for e in state.world.entities}
        entity = entities[actor.actor_id]
        known = set(actor.aware_of) | {
            f.subject_id for f in state.world.perspective(actor.actor_id).facts
        }
        if entity.location_id is not None:
            known.add(entity.location_id)
        if isinstance(command, Wait):
            return (
                result("feasible", "wait.allowed")
                if command.ticks <= self.rules.maximum_wait
                else result("rejected", "wait.limit")
            )
        if isinstance(command, Move):
            if command.destination_id is None:
                return result("clarification", "move.destination_required")
            destination = entities.get(command.destination_id)
            if command.destination_id not in known or destination is None:
                return result("rejected", "target.unavailable")
            if destination.kind is not EntityKind.LOCATION or not any(
                c.source_id == entity.location_id and c.destination_id == destination.id
                for c in state.world.connections
            ):
                return result("rejected", "move.no_connection")
            return result("feasible", "move.allowed")
        if isinstance(command, UseItem):
            if command.item_id is None:
                return result("clarification", "item.required")
            item = next(
                (
                    i
                    for i in state.resources.items
                    if i.id == command.item_id and i.owner_id == actor.actor_id
                ),
                None,
            )
            if item is None:
                return result("rejected", "item.unavailable")
            if item.container_id is not None or item.equipped:
                return result("rejected", "item.inaccessible")
            if item.quantity < command.quantity:
                return result("rejected", "item.insufficient")
            if any(i.container_id == item.id for i in state.resources.items):
                return result("rejected", "item.container_not_empty")
            if item.definition_id not in self.rules.consumables:
                return result("unsupported", "item.use_unsupported")
            return result("feasible", "item.allowed")
        if command.target_id is None:
            return result("clarification", "target.required")
        target = entities.get(command.target_id)
        if command.target_id not in known or target is None:
            return result("rejected", "target.unavailable")
        if self._location(entity) is None or self._location(entity) != self._location(target):
            return result("rejected", "target.out_of_range")
        if isinstance(command, Attack):
            if command.weapon_id is None:
                return result("clarification", "attack.weapon_required")
            weapon = next(
                (
                    i
                    for i in state.resources.items
                    if i.id == command.weapon_id
                    and i.owner_id == actor.actor_id
                    and i.equipped
                    and i.ready
                ),
                None,
            )
            if weapon is None:
                return result("rejected", "attack.weapon_not_ready")
            return result("unsupported", "combat.not_implemented")
        if isinstance(command, Social) and command.approach != "diplomacy":
            return result("adjudication_required", "social.approach_requires_ruling")
        rule = self.checks.get((command.kind, command.target_id))
        if rule is None:
            return result("unsupported", "check.not_implemented")
        if state.resources.game_time < rule.not_before:
            return result("rejected", "check.not_ready")
        if rule.required_equipment is not None and not any(
            i.owner_id == actor.actor_id and i.definition_id == rule.required_equipment and i.ready
            for i in state.resources.items
        ):
            return result("rejected", "check.equipment_required")
        build, _ = self.reviewer.activate(
            actor.proposal, actor.approval, campaign_id=state.campaign_id, actor_id=actor.actor_id
        )
        if rule.definition_id not in {p.definition_id for p in build.purchases}:
            return result("rejected", "check.skill_required")
        return result("feasible", "check.allowed")

    def _target(
        self, state: PlayState, actor_id: str, build: ValidatedBuild, rule: CheckRule
    ) -> tuple[DerivedValue, tuple[DerivedValue, ...]]:
        values = {v.target: v.value for v in build.sheet.values}
        definition = self.reviewer.compiler.definitions[rule.definition_id]
        attribute, _ = SKILLS[definition.name]
        attribute_id = f"attribute:{attribute.lower()}"
        effects = self.resources.equipment_effects(state.resources, actor_id)
        evaluator = EffectEvaluator(
            (MechanicalTarget(attribute_id), MechanicalTarget(rule.definition_id, (attribute_id,)))
        )
        attribute_value = evaluator.evaluate(
            attribute_id,
            values[attribute_id],
            effects,
            context={"actor_id": actor_id},
            at=state.resources.game_time,
        )
        # Purchased bonuses are in the compiled sheet; apply only the equipment delta.
        base = values[rule.definition_id] + attribute_value.value - values[attribute_id]
        skill = evaluator.evaluate(
            rule.definition_id,
            base,
            effects,
            context={"actor_id": actor_id},
            at=state.resources.game_time,
        )
        return skill, (attribute_value,)

    def resolve(
        self, state: PlayState, command: TypedAction, *, rng: RandomSource = secrets
    ) -> tuple[PlayState, ActionResult]:
        feasible = self.assess(state, command)
        if feasible.status != "feasible":
            return state, feasible
        world, resources = state.world, state.resources
        if not isinstance(command, Wait) and self.rules.fatigue_cost:
            resources = resources.model_copy(
                update={
                    "pools": tuple(
                        p.model_copy(update={"current": p.current - self.rules.fatigue_cost})
                        if p.id == f"fp:{command.actor_id}"
                        else p
                        for p in resources.pools
                    )
                }
            )
        duration = 0
        trace: CheckTrace | None = None
        derived: DerivedValue | None = None
        dependencies: tuple[DerivedValue, ...] = ()
        revealed: tuple[str, ...] = ()
        if isinstance(command, Move):
            world = replace(
                world,
                entities=tuple(
                    replace(e, location_id=command.destination_id)
                    if e.id == command.actor_id
                    else e
                    for e in world.entities
                ),
            )
            duration = self.rules.movement_ticks
        elif isinstance(command, UseItem):
            assert command.item_id is not None
            resources = self.resources.apply(
                resources,
                Consume(
                    id=f"{command.id}:consume",
                    actor_id=command.actor_id,
                    expected_revision=resources.revision,
                    item_id=command.item_id,
                    quantity=command.quantity,
                ),
            )
            duration = self.rules.item_ticks
        elif isinstance(command, Wait):
            duration = command.ticks
        elif isinstance(command, (Inspect, Social)):
            assert command.target_id is not None
            rule = self.checks[(command.kind, command.target_id)]
            actor = next(a for a in state.actors if a.actor_id == command.actor_id)
            build, _ = self.reviewer.activate(
                actor.proposal,
                actor.approval,
                campaign_id=state.campaign_id,
                actor_id=actor.actor_id,
            )
            derived, dependencies = self._target(state, actor.actor_id, build, rule)
            if not derived.value.is_finite() or derived.value != derived.value.to_integral_value():
                raise ValidationError("Check target must be a finite integer")
            trace = success_check(
                int(derived.value),
                (Modifier(rule.modifier, rule.id, rule.definition_id, rule.package_version),),
                rng=rng,
                rules_package=rule.package_id,
                rules_version=rule.package_version,
            )
            if trace.outcome in (Outcome.SUCCESS, Outcome.CRITICAL_SUCCESS):
                revealed = rule.reveal_fact_ids
                for fact_id in revealed:
                    world = world.learn(actor.actor_id, fact_id)
            duration = rule.duration
        else:
            raise ValidationError("No implemented resolver")
        resources = self.resources.apply(
            resources,
            Advance(
                id=f"{command.id}:time",
                actor_id=command.actor_id,
                expected_revision=resources.revision,
                to=resources.game_time + duration,
            ),
            system=True,
        )
        # Subcommands execute atomically within one campaign command/revision.
        resources = resources.model_copy(update={"revision": state.revision + 1})
        result = ActionResult(
            status="committed",
            revision=state.revision + 1,
            code=f"{command.kind}.resolved",
            command_id=command.id,
            check=trace,
            derived=derived,
            dependencies=dependencies,
            revealed_fact_ids=revealed,
            rules_digest=self.digest,
        )
        updated = state.model_copy(
            update={
                "world": world,
                "resources": resources,
                "revision": state.revision + 1,
                "last_result": result,
            }
        )
        self.validate(updated)
        return updated, result
