"""Authored futuristic and anomalous artifacts (Campaigns B478-B480).

Artifacts are inventory objects, but their meaning is campaign state.  This
module keeps discovery scoped, derives TL context from the world model, and
dispatches only explicitly registered effect families.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.checks import (
    CheckTrace,
    Modifier,
    ModifierKind,
    RandomSource,
    draw_dice,
    draw_index,
)
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.campaign.world_context import (
    WorldContextRules,
    WorldContextState,
    technology_field,
)
from wayfarer.engine.simulation.equipment.objects import DamageObject, apply_object
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.resources import (
    Item,
    Receipt,
    ResourceEvent,
    ResourceState,
    Scheduled,
)
from wayfarer.engine.world import EntityKind, World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PROFILE: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"

if TYPE_CHECKING:
    from wayfarer.engine.simulation.resource_engine import ResourceEngine


class ArtifactProcedure(Record):
    skill_id: Id
    technology_field: Id
    native_technology_level: int = Field(ge=0, le=12)
    modifier: int = Field(default=0, ge=-20, le=20)


class ArtifactProperty(Record):
    id: Id
    label: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    capability_ids: tuple[Id, ...] = ()


class ArtifactStateEffect(Record):
    """A persisted condition, malfunction, or transformation effect."""

    kind: Literal["artifact-state"] = "artifact-state"
    id: Id
    state: Literal["condition", "malfunction", "transformation"]
    target: Literal["operator", "target-actor", "artifact", "target-item"]
    duration_seconds: int | None = Field(default=None, gt=0)


class ArtifactInjuryEffect(Record):
    kind: Literal["injury"] = "injury"
    id: Id
    target: Literal["operator", "target-actor"]
    dice: int = Field(default=0, ge=0, le=20)
    add: int = Field(default=0, ge=-20, le=20)
    damage_type: Literal["cr", "cut", "imp", "pi-", "pi", "pi+", "pi++", "burn"] = "cr"
    ignore_dr: bool = False

    @model_validator(mode="after")
    def nonzero_damage(self) -> ArtifactInjuryEffect:
        if self.dice == 0 and self.add <= 0:
            raise ValueError("Artifact injury must be capable of positive damage")
        return self


class ArtifactObjectEffect(Record):
    kind: Literal["object-damage"] = "object-damage"
    id: Id
    target: Literal["artifact", "target-item"]
    basic_damage: int = Field(ge=0)
    damage_type: Literal["cr", "cut", "imp", "pi-", "pi", "pi+", "pi++", "burn"] = "cr"
    armor_divisor: Decimal = Field(default=Decimal(1), gt=0, allow_inf_nan=False)


ArtifactEffect = Annotated[
    ArtifactStateEffect | ArtifactInjuryEffect | ArtifactObjectEffect,
    Field(discriminator="kind"),
]
ARTIFACT_EFFECT_ADAPTER: TypeAdapter[ArtifactEffect] = TypeAdapter(ArtifactEffect)


class ArtifactCapability(Record):
    id: Id
    property_id: Id
    operation: ArtifactProcedure
    effect_family: Id
    effect: ArtifactEffect
    side_effect_ids: tuple[Id, ...] = ()
    charge_cost: int = Field(default=0, ge=0)
    required_fact_ids: tuple[Id, ...] = ()
    required_definition_ids: tuple[Id, ...] = ()
    required_item_definition_ids: tuple[Id, ...] = ()


class ArtifactAnalysis(Record):
    id: Id
    artifact_id: Id
    property_id: Id
    procedure: ArtifactProcedure
    duration_seconds: int = Field(default=60, ge=60)


class ArtifactDefinition(Record):
    id: Id
    item_definition_id: Id
    apparent_function: str = Field(min_length=1, max_length=1000)
    actual_capability: str = Field(min_length=1, max_length=2000)
    origin: str = Field(min_length=1, max_length=1000)
    property_definitions: tuple[ArtifactProperty, ...] = Field(min_length=1)
    public_property_ids: tuple[Id, ...] = ()
    capabilities: tuple[ArtifactCapability, ...] = Field(min_length=1)
    repair: ArtifactProcedure | None = None
    campaign_permitted: bool = True

    @model_validator(mode="after")
    def valid_definition(self) -> ArtifactDefinition:
        properties = {item.id for item in self.property_definitions}
        capabilities = {item.id for item in self.capabilities}
        if len(properties) != len(self.property_definitions):
            raise ValueError("Duplicate artifact property ID")
        if len(capabilities) != len(self.capabilities):
            raise ValueError("Duplicate artifact capability ID")
        if not set(self.public_property_ids) <= properties:
            raise ValueError("Unknown public artifact property")
        for capability in self.capabilities:
            if capability.property_id not in properties:
                raise ValueError("Artifact capability requires a known property definition")
            if not set(capability.side_effect_ids) <= capabilities:
                raise ValueError("Unknown artifact side-effect capability")
        for property_definition in self.property_definitions:
            linked = {
                capability.id
                for capability in self.capabilities
                if capability.property_id == property_definition.id
            }
            if set(property_definition.capability_ids) != linked:
                raise ValueError("Artifact property capability binding is inconsistent")
        side_effects = {
            side_effect_id
            for capability in self.capabilities
            for side_effect_id in capability.side_effect_ids
        }
        if any(
            capability.side_effect_ids
            for capability in self.capabilities
            if capability.id in side_effects
        ):
            raise ValueError("Artifact side effects cannot recursively select side effects")
        return self


class ArtifactRules(Record):
    id: Id
    version: int = Field(ge=1)
    profile_id: Literal["gurps-basic-set-4e-2004"] = PROFILE
    artifacts: tuple[ArtifactDefinition, ...] = Field(min_length=1)
    analyses: tuple[ArtifactAnalysis, ...] = ()
    executable_capability_ids: tuple[Id, ...] = ()
    executable_effect_families: tuple[Literal["artifact-state", "injury", "object"], ...] = ()

    @model_validator(mode="after")
    def valid_rules(self) -> ArtifactRules:
        if len({item.id for item in self.artifacts}) != len(self.artifacts):
            raise ValueError("Duplicate artifact definition ID")
        if len({item.item_definition_id for item in self.artifacts}) != len(self.artifacts):
            raise ValueError("One inventory definition may bind only one artifact")
        if len({item.id for item in self.analyses}) != len(self.analyses):
            raise ValueError("Duplicate artifact analysis ID")
        if len(set(self.executable_capability_ids)) != len(self.executable_capability_ids):
            raise ValueError("Duplicate executable artifact capability")
        if len(set(self.executable_effect_families)) != len(self.executable_effect_families):
            raise ValueError("Duplicate executable artifact effect family")
        artifacts = {item.id: item for item in self.artifacts}
        for analysis in self.analyses:
            artifact = artifacts.get(analysis.artifact_id)
            if artifact is None or analysis.property_id not in {
                item.id for item in artifact.property_definitions
            }:
                raise ValueError("Artifact analysis references an unknown property")
        return self


class ArtifactKnowledge(Record):
    actor_id: Id
    artifact_id: Id
    property_ids: tuple[Id, ...]


class ArtifactAttempt(Record):
    id: Id
    actor_id: Id
    artifact_id: Id
    purpose: Literal["analysis", "operation", "repair"]
    procedure_id: Id
    local_technology_level: int = Field(ge=0, le=12)
    native_technology_level: int = Field(ge=0, le=12)
    check: CheckTrace


class ArtifactOccurrence(Record):
    id: Id
    artifact_id: Id
    capability_id: Id
    effect_id: Id | None = None
    side_effect_id: Id | None = None
    target_id: Id | None = None
    damage_rolls: tuple[int, ...] = ()


class ArtifactState(Record):
    knowledge: tuple[ArtifactKnowledge, ...] = ()
    attempts: tuple[ArtifactAttempt, ...] = ()
    occurrences: tuple[ArtifactOccurrence, ...] = ()


class ArtifactCommand(Record):
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)


class AnalyzeArtifact(ArtifactCommand):
    kind: Literal["artifact-analyze"] = "artifact-analyze"
    item_id: Id
    analysis_id: Id


class OperateArtifact(ArtifactCommand):
    kind: Literal["artifact-operate"] = "artifact-operate"
    item_id: Id
    capability_id: Id
    target_actor_id: Id | None = None
    target_item_id: Id | None = None


ArtifactCommandUnion = Annotated[AnalyzeArtifact | OperateArtifact, Field(discriminator="kind")]
ARTIFACT_COMMAND_ADAPTER: TypeAdapter[ArtifactCommandUnion] = TypeAdapter(ArtifactCommandUnion)
ARTIFACT_COMMAND_KINDS = frozenset({"artifact-analyze", "artifact-operate"})


class ArtifactOutcome(Record):
    kind: Literal["artifact-analysis", "artifact-operation"]
    status: Literal["identified", "unknown", "activated", "failed"]
    artifact_id: Id
    property_ids: tuple[Id, ...] = ()
    capability_id: Id | None = None
    effect_id: Id | None = None
    side_effect_id: Id | None = None
    check: CheckTrace
    private: str = ""
    consequence: str | None = None


class ArtifactView(Record):
    artifact_id: Id
    apparent_function: str
    known_properties: tuple[ArtifactProperty, ...]
    capability_ids: tuple[Id, ...]


class ArtifactProcedureContext(Record):
    skill_id: Id
    native_technology_level: int = Field(ge=0, le=12)
    local_technology_level: int = Field(ge=0, le=12)
    modifier: int


def visible_artifact(
    rules: ArtifactRules, state: ArtifactState, artifact_id: str, actor_id: str
) -> ArtifactView:
    artifact = _artifact(rules, artifact_id)
    learned = next(
        (
            set(item.property_ids)
            for item in state.knowledge
            if item.actor_id == actor_id and item.artifact_id == artifact_id
        ),
        set(),
    )
    visible = set(artifact.public_property_ids) | learned
    properties = tuple(item for item in artifact.property_definitions if item.id in visible)
    capability_ids = tuple(
        capability.id for capability in artifact.capabilities if capability.property_id in visible
    )
    return ArtifactView(
        artifact_id=artifact.id,
        apparent_function=artifact.apparent_function,
        known_properties=properties,
        capability_ids=capability_ids,
    )


def artifact_procedure_context(
    procedure: ArtifactProcedure,
    world_rules: WorldContextRules,
    world_state: WorldContextState,
    world: World,
    actor_id: str,
) -> ArtifactProcedureContext:
    entity = next(
        (item for item in world.entities if item.id == actor_id and item.kind is EntityKind.ACTOR),
        None,
    )
    if entity is None or entity.location_id is None:
        raise ValidationError("Artifact procedure requires an actor at a world location")
    realm = next(
        (item for item in world_rules.realms if entity.location_id in item.location_ids), None
    )
    if realm is None:
        raise ValidationError("Artifact procedure location has no technology realm")
    local = technology_field(world_rules, world_state, realm.id, procedure.technology_field).level
    return ArtifactProcedureContext(
        skill_id=procedure.skill_id,
        native_technology_level=procedure.native_technology_level,
        local_technology_level=local,
        modifier=procedure.modifier - abs(procedure.native_technology_level - local),
    )


def validate_artifacts(
    rules: ArtifactRules | None,
    state: ArtifactState,
    resources: ResourceState,
) -> None:
    if rules is None:
        if state != ArtifactState():
            raise ValidationError("Artifact state exists without enabled rules")
        return
    artifacts = {item.id: item for item in rules.artifacts}
    if len({(item.actor_id, item.artifact_id) for item in state.knowledge}) != len(state.knowledge):
        raise ValidationError("Duplicate artifact knowledge record")
    if len({item.id for item in state.attempts}) != len(state.attempts):
        raise ValidationError("Duplicate artifact attempt ID")
    if len({item.id for item in state.occurrences}) != len(state.occurrences):
        raise ValidationError("Duplicate artifact occurrence ID")
    for knowledge in state.knowledge:
        artifact = artifacts.get(knowledge.artifact_id)
        if artifact is None or not set(knowledge.property_ids) <= {
            item.id for item in artifact.property_definitions
        }:
            raise ValidationError("Artifact knowledge references an unknown property")


def _artifact(rules: ArtifactRules, artifact_id: str) -> ArtifactDefinition:
    result = next((item for item in rules.artifacts if item.id == artifact_id), None)
    if result is None:
        raise ValidationError("Unknown authored artifact")
    return result


def _owned_artifact(
    rules: ArtifactRules, resources: ResourceState, actor_id: str, item_id: str
) -> tuple[ArtifactDefinition, Item]:
    item = next(
        (item for item in resources.items if item.id == item_id and item.owner_id == actor_id),
        None,
    )
    if item is None or item.ground is not None or item.container_id is not None:
        raise ValidationError("Artifact must be owned, retrieved, and accessible")
    artifact = next(
        (entry for entry in rules.artifacts if entry.item_definition_id == item.definition_id),
        None,
    )
    if artifact is None:
        raise ValidationError("Inventory item is not an authored artifact")
    if not artifact.campaign_permitted:
        raise ValidationError("Artifact is not permitted by this campaign")
    return artifact, item


def _skill_level(build: ValidatedBuild, skill_id: str) -> int:
    value = next((item.value for item in build.sheet.values if item.target == skill_id), None)
    if value is None or not value.is_finite() or value != value.to_integral_value():
        raise ValidationError("Artifact procedure requires its authored skill")
    return int(value)


def _digest(command: ArtifactCommandUnion) -> str:
    return hashlib.sha256(command.model_dump_json().encode()).hexdigest()


def _prior(resources: ResourceState, command: ArtifactCommandUnion) -> ArtifactOutcome | None:
    receipt = next((item for item in resources.receipts if item.command_id == command.id), None)
    if receipt is None:
        return None
    if receipt.digest != _digest(command):
        raise ConflictError("Artifact command ID reused with a different payload")
    event = next(item for item in resources.events if item.id == "artifact:" + command.id)
    return ArtifactOutcome.model_validate_json(event.kind)


def _check(
    build: ValidatedBuild,
    context: ArtifactProcedureContext,
    extra: tuple[Modifier, ...],
    rng: RandomSource,
) -> CheckTrace:
    modifiers = (
        Modifier(
            context.modifier,
            "artifact-technology-context",
            "campaigns:b478:artifacts",
            PROFILE,
            ModifierKind.SITUATIONAL,
        ),
        *extra,
    )
    return success_roll(PROFILE, _skill_level(build, context.skill_id), modifiers, rng=rng)


def _learn(
    state: ArtifactState, actor_id: str, artifact_id: str, property_id: str
) -> ArtifactState:
    current = next(
        (
            item
            for item in state.knowledge
            if item.actor_id == actor_id and item.artifact_id == artifact_id
        ),
        None,
    )
    record = ArtifactKnowledge(
        actor_id=actor_id,
        artifact_id=artifact_id,
        property_ids=tuple(sorted(set(current.property_ids if current else ()) | {property_id})),
    )
    return state.model_copy(
        update={
            "knowledge": tuple(
                item
                for item in state.knowledge
                if (item.actor_id, item.artifact_id) != (actor_id, artifact_id)
            )
            + (record,)
        }
    )


def _target(effect: ArtifactEffect, command: OperateArtifact) -> str:
    if effect.target == "operator":
        return command.actor_id
    if effect.target == "artifact":
        return command.item_id
    if effect.target == "target-actor" and command.target_actor_id is not None:
        return command.target_actor_id
    if effect.target == "target-item" and command.target_item_id is not None:
        return command.target_item_id
    raise ValidationError("Artifact effect requires its typed target")


def _family(effect: ArtifactEffect) -> Literal["artifact-state", "injury", "object"]:
    if isinstance(effect, ArtifactStateEffect):
        return "artifact-state"
    if isinstance(effect, ArtifactInjuryEffect):
        return "injury"
    return "object"


def _preflight_effect(
    effect: ArtifactEffect,
    command: OperateArtifact,
    rules: ArtifactRules,
    resources: ResourceState,
    world: World,
) -> None:
    if _family(effect) not in rules.executable_effect_families:
        raise ValidationError("Artifact effect family has no registered adapter")
    target = _target(effect, command)
    if isinstance(effect, ArtifactInjuryEffect):
        if target not in {item.id for item in world.entities if item.kind is EntityKind.ACTOR}:
            raise ValidationError("Artifact injury target is not a world actor")
        if not any(
            item.id == "hp:" + target and item.injury is not None for item in resources.pools
        ):
            raise ValidationError("Artifact injury target lacks authoritative injury state")
    elif isinstance(effect, ArtifactObjectEffect):
        if not any(item.id == target and item.condition is not None for item in resources.items):
            raise ValidationError("Artifact object target lacks authoritative object state")
    elif effect.target in ("operator", "target-actor"):
        if target not in {item.id for item in world.entities if item.kind is EntityKind.ACTOR}:
            raise ValidationError("Artifact state target is not a world actor")
    elif not any(item.id == target for item in resources.items):
        raise ValidationError("Artifact state target is not an inventory item")


def _apply_effect(
    effect: ArtifactEffect,
    command: OperateArtifact,
    resources: ResourceState,
    *,
    resource_engine: ResourceEngine,
    builds: Mapping[str, ValidatedBuild],
    rng: RandomSource,
    suffix: str,
) -> tuple[ResourceState, str, tuple[int, ...]]:
    target = _target(effect, command)
    if isinstance(effect, ArtifactStateEffect):
        effect_id = f"artifact:{command.id}:{suffix}:{effect.state}:{effect.id}:{target}"
        scheduled = resources.scheduled
        if effect.duration_seconds is not None:
            scheduled += (
                Scheduled(
                    id=effect_id + ":expire",
                    due=resources.game_time + effect.duration_seconds,
                    kind="expire",
                    target_id=effect_id,
                ),
            )
        return (
            resources.model_copy(
                update={
                    "active_effect_ids": tuple(
                        sorted(set(resources.active_effect_ids) | {effect_id})
                    ),
                    "scheduled": scheduled,
                }
            ),
            target,
            (),
        )
    if isinstance(effect, ArtifactInjuryEffect):
        rolls = draw_dice(rng, effect.dice)
        damage = max(0, sum(rolls) + effect.add)
        build = builds.get(target)
        if build is None or build.statistics is None:
            raise ValidationError("Artifact injury requires compiled target statistics")
        updated, _ = apply_injury(
            resources,
            Wound(
                id=f"{command.id}:{suffix}:injury",
                actor_id=target,
                expected_revision=resources.revision,
                basic_damage=damage,
                resistance=0 if effect.ignore_dr else 0,
                damage_type=effect.damage_type,
                injury_source="internal" if effect.ignore_dr else "attack",
            ),
            ht=build.statistics.ht,
            rng=rng,
            system=True,
        )
        return updated, target, rolls
    assert isinstance(effect, ArtifactObjectEffect)
    updated, _ = apply_object(
        resource_engine,
        resources,
        DamageObject(
            id=f"{command.id}:{suffix}:object",
            actor_id=command.actor_id,
            expected_revision=resources.revision,
            item_id=target,
            basic_damage=effect.basic_damage,
            damage_type=effect.damage_type,
            armor_divisor=effect.armor_divisor,
        ),
        system=True,
        rng=rng,
    )
    return updated, target, ()


def _finish(
    state: ArtifactState,
    resources: ResourceState,
    command: ArtifactCommandUnion,
    outcome: ArtifactOutcome,
) -> tuple[ArtifactState, ResourceState, ArtifactOutcome]:
    resources = resources.model_copy(
        update={
            "revision": resources.revision + 1,
            "receipts": resources.receipts
            + (Receipt(command_id=command.id, digest=_digest(command)),),
            "events": resources.events
            + (
                ResourceEvent(
                    id="artifact:" + command.id,
                    at=resources.game_time,
                    target_id=outcome.artifact_id,
                    kind=outcome.model_dump_json(),
                ),
            ),
        }
    )
    return state, resources, outcome


def _analyze_artifact(
    state: ArtifactState,
    resources: ResourceState,
    world: World,
    command: AnalyzeArtifact,
    rules: ArtifactRules,
    world_rules: WorldContextRules,
    world_state: WorldContextState,
    *,
    build: ValidatedBuild,
    artifact: ArtifactDefinition,
    advance: Callable[[ResourceState, int, str], ResourceState],
    rng: RandomSource,
) -> tuple[ArtifactState, ResourceState, ArtifactOutcome]:
    analysis = next((item for item in rules.analyses if item.id == command.analysis_id), None)
    if analysis is None or analysis.artifact_id != artifact.id:
        raise ValidationError("Unknown authored artifact analysis")
    context = artifact_procedure_context(
        analysis.procedure, world_rules, world_state, world, command.actor_id
    )
    previous = sum(
        attempt.actor_id == command.actor_id
        and attempt.artifact_id == artifact.id
        and attempt.purpose == "analysis"
        for attempt in state.attempts
    )
    repeated = (
        (
            Modifier(
                -previous,
                "repeated-artifact-analysis",
                "campaigns:b478:enigmatic-device-table",
                PROFILE,
                ModifierKind.REPEATED_ATTEMPT,
            ),
        )
        if previous
        else ()
    )
    check = _check(build, context, repeated, rng)
    resources = advance(resources, resources.game_time + analysis.duration_seconds, command.id)
    attempt = ArtifactAttempt(
        id=command.id,
        actor_id=command.actor_id,
        artifact_id=artifact.id,
        purpose="analysis",
        procedure_id=analysis.id,
        local_technology_level=context.local_technology_level,
        native_technology_level=context.native_technology_level,
        check=check,
    )
    state = state.model_copy(update={"attempts": state.attempts + (attempt,)})
    if check.outcome.succeeded:
        state = _learn(state, command.actor_id, artifact.id, analysis.property_id)
    view = visible_artifact(rules, state, artifact.id, command.actor_id)
    outcome = ArtifactOutcome(
        kind="artifact-analysis",
        status="identified" if check.outcome.succeeded else "unknown",
        artifact_id=artifact.id,
        property_ids=tuple(item.id for item in view.known_properties),
        check=check,
    )
    return _finish(state, resources, command, outcome)


def _require_operation(
    state: ArtifactState,
    resources: ResourceState,
    world: World,
    command: OperateArtifact,
    rules: ArtifactRules,
    artifact: ArtifactDefinition,
    item: Item,
    build: ValidatedBuild,
) -> tuple[ArtifactCapability, list[ArtifactCapability]]:
    capability = next(
        (item for item in artifact.capabilities if item.id == command.capability_id), None
    )
    view = visible_artifact(rules, state, artifact.id, command.actor_id)
    if (
        capability is None
        or capability.id not in rules.executable_capability_ids
        or capability.property_id not in {item.id for item in view.known_properties}
    ):
        raise ValidationError("Artifact capability is unavailable")
    if capability.effect_family != _family(capability.effect):
        raise ValidationError("Artifact capability effect-family binding is inconsistent")
    side_effects = [
        next(item for item in artifact.capabilities if item.id == side_id)
        for side_id in capability.side_effect_ids
    ]
    for candidate in (capability, *side_effects):
        if candidate.id not in rules.executable_capability_ids:
            raise ValidationError("Artifact side effect is not registered for execution")
        if candidate.effect_family != _family(candidate.effect):
            raise ValidationError("Artifact effect-family binding is inconsistent")
        _preflight_effect(candidate.effect, command, rules, resources, world)
    known_facts = {fact_id for owner_id, fact_id in world.knowledge if owner_id == command.actor_id}
    purchased = {entry.definition_id for entry in build.purchases}
    carried = {
        entry.definition_id
        for entry in resources.items
        if entry.owner_id == command.actor_id and entry.ground is None
    }
    if not set(capability.required_fact_ids) <= known_facts:
        raise ValidationError("Artifact operating knowledge is missing")
    if not set(capability.required_definition_ids) <= purchased:
        raise ValidationError("Artifact operating qualification is missing")
    if not set(capability.required_item_definition_ids) <= carried:
        raise ValidationError("Artifact operating equipment is missing")
    if capability.charge_cost:
        if item.charges is None or item.charges < capability.charge_cost:
            raise ValidationError("Artifact power is depleted")
    return capability, side_effects


def _operate_artifact(
    state: ArtifactState,
    resources: ResourceState,
    world: World,
    command: OperateArtifact,
    rules: ArtifactRules,
    world_rules: WorldContextRules,
    world_state: WorldContextState,
    *,
    build: ValidatedBuild,
    builds: Mapping[str, ValidatedBuild],
    artifact: ArtifactDefinition,
    item: Item,
    resource_engine: ResourceEngine,
    rng: RandomSource,
) -> tuple[ArtifactState, ResourceState, ArtifactOutcome]:
    capability, side_effects = _require_operation(
        state, resources, world, command, rules, artifact, item, build
    )
    context = artifact_procedure_context(
        capability.operation, world_rules, world_state, world, command.actor_id
    )
    check = _check(build, context, (), rng)
    attempt = ArtifactAttempt(
        id=command.id,
        actor_id=command.actor_id,
        artifact_id=artifact.id,
        purpose="operation",
        procedure_id=capability.id,
        local_technology_level=context.local_technology_level,
        native_technology_level=context.native_technology_level,
        check=check,
    )
    state = state.model_copy(update={"attempts": state.attempts + (attempt,)})
    if not check.outcome.succeeded:
        outcome = ArtifactOutcome(
            kind="artifact-operation",
            status="failed",
            artifact_id=artifact.id,
            capability_id=capability.id,
            check=check,
        )
        return _finish(state, resources, command, outcome)
    if capability.charge_cost:
        resources = resources.model_copy(
            update={
                "items": tuple(
                    entry.model_copy(update={"charges": entry.charges - capability.charge_cost})
                    if entry.id == command.item_id and entry.charges is not None
                    else entry
                    for entry in resources.items
                )
            }
        )
    resources, target_id, rolls = _apply_effect(
        capability.effect,
        command,
        resources,
        resource_engine=resource_engine,
        builds=builds,
        rng=rng,
        suffix="primary",
    )
    selected = None
    if side_effects:
        selected = side_effects[draw_index(rng, len(side_effects))]
        resources, side_target, side_rolls = _apply_effect(
            selected.effect,
            command,
            resources,
            resource_engine=resource_engine,
            builds=builds,
            rng=rng,
            suffix="side",
        )
        target_id = side_target
        rolls += side_rolls
    occurrence = ArtifactOccurrence(
        id=command.id,
        artifact_id=artifact.id,
        capability_id=capability.id,
        effect_id=capability.effect.id,
        side_effect_id=selected.id if selected else None,
        target_id=target_id,
        damage_rolls=rolls,
    )
    state = state.model_copy(update={"occurrences": state.occurrences + (occurrence,)})
    outcome = ArtifactOutcome(
        kind="artifact-operation",
        status="activated",
        artifact_id=artifact.id,
        capability_id=capability.id,
        effect_id=capability.effect.id,
        side_effect_id=selected.id if selected else None,
        check=check,
    )
    return _finish(state, resources, command, outcome)


def apply_artifact(
    state: ArtifactState,
    resources: ResourceState,
    world: World,
    command: ArtifactCommandUnion,
    rules: ArtifactRules,
    world_rules: WorldContextRules,
    world_state: WorldContextState,
    *,
    builds: Mapping[str, ValidatedBuild],
    resource_engine: ResourceEngine,
    advance: Callable[[ResourceState, int, str], ResourceState],
    rng: RandomSource,
) -> tuple[ArtifactState, ResourceState, ArtifactOutcome]:
    """Apply one atomic, receipt-idempotent analysis or operation."""

    prior = _prior(resources, command)
    if prior is not None:
        return state, resources, prior
    if command.expected_revision != resources.revision:
        raise ConflictError("Artifact resource revision changed")
    build = builds.get(command.actor_id)
    if build is None:
        raise ValidationError("Artifact command actor is not playable")
    artifact, item = _owned_artifact(rules, resources, command.actor_id, command.item_id)
    if isinstance(command, AnalyzeArtifact):
        return _analyze_artifact(
            state,
            resources,
            world,
            command,
            rules,
            world_rules,
            world_state,
            build=build,
            artifact=artifact,
            advance=advance,
            rng=rng,
        )
    return _operate_artifact(
        state,
        resources,
        world,
        command,
        rules,
        world_rules,
        world_state,
        build=build,
        builds=builds,
        artifact=artifact,
        item=item,
        resource_engine=resource_engine,
        rng=rng,
    )
