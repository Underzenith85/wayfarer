"""Technology and travel facts for multi-world campaigns (Basic Set B511-B522)."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.simulation.resources import (
    Consume,
    Pool,
    Receipt,
    ResourceEvent,
    ResourceState,
)
from wayfarer.engine.world import EntityKind, World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

YEAR_SECONDS = 365 * 24 * 60 * 60
LOCAL_TECHNOLOGY_STEP_SECONDS = 2 * YEAR_SECONDS


class TechnologyPath(Record):
    """A setting-authored technological path, including divergent and superscience paths."""

    id: Id
    divergence_level: int | None = Field(default=None, ge=0, le=12)
    superscience: bool = False


class TechnologyField(Record):
    field: Id
    level: int = Field(ge=0, le=12)
    path_id: Id = "standard"


class Realm(Record):
    """A world or plane whose concrete destinations remain canonical world locations."""

    id: Id
    kind: Literal[
        "physical",
        "alternate",
        "mirror",
        "interpenetrating",
        "phase",
        "void",
        "virtual",
    ]
    baseline_technology_level: int = Field(ge=0, le=12)
    technology_fields: tuple[TechnologyField, ...] = ()
    location_ids: tuple[Id, ...] = Field(min_length=1)
    description: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def unique_fields_and_locations(self) -> Realm:
        if len({item.field for item in self.technology_fields}) != len(self.technology_fields):
            raise ValueError("Duplicate realm technology field")
        if len(set(self.location_ids)) != len(self.location_ids):
            raise ValueError("Duplicate realm location")
        return self


class TravelLink(Record):
    """An executable, authored connection between concrete locations in two realms."""

    id: Id
    origin_realm_id: Id
    destination_realm_id: Id
    origin_location_id: Id
    destination_location_id: Id
    method_id: Id
    capability_id: Id
    mode: Literal["physical", "projection"] = "physical"
    travel_seconds: int = Field(default=0, ge=0)
    fatigue_cost_per_traveler: int = Field(default=0, ge=0)
    required_fact_ids: tuple[Id, ...] = ()
    required_definition_ids: tuple[Id, ...] = ()
    required_item_definition_ids: tuple[Id, ...] = ()


class MaterialCost(Record):
    item_id: Id
    quantity: int = Field(ge=1)


class TechnologyQualification(Record):
    definition_id: Id
    technology_level: int = Field(ge=0, le=12)
    minimum_level: int = Field(default=12, ge=1)


class TechnologyProjectRule(Record):
    id: Id
    realm_id: Id
    field: Id
    from_level: int = Field(ge=0, le=11)
    to_level: int = Field(ge=1, le=12)
    duration_seconds: int = Field(gt=0)
    qualifications: tuple[TechnologyQualification, ...] = Field(min_length=1)
    material_costs: tuple[MaterialCost, ...] = Field(min_length=1)
    labor_available: bool = True
    host_cooperation: bool = True

    @model_validator(mode="after")
    def one_step_at_source_duration(self) -> TechnologyProjectRule:
        if self.to_level != self.from_level + 1:
            raise ValueError("Local technology projects advance exactly one TL step")
        if self.duration_seconds != LOCAL_TECHNOLOGY_STEP_SECONDS:
            raise ValueError("A local technology step requires two years")
        if len({item.item_id for item in self.material_costs}) != len(self.material_costs):
            raise ValueError("Duplicate local technology material item")
        return self


class WorldContextRules(Record):
    id: Id
    version: int = Field(ge=1)
    profile_id: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
    paths: tuple[TechnologyPath, ...] = (TechnologyPath(id="standard"),)
    realms: tuple[Realm, ...] = Field(min_length=1)
    links: tuple[TravelLink, ...] = ()
    technology_projects: tuple[TechnologyProjectRule, ...] = ()
    executable_capability_ids: tuple[Id, ...] = ()

    @model_validator(mode="after")
    def valid_graph(self) -> WorldContextRules:
        for values, label in (
            (self.paths, "technology path"),
            (self.realms, "realm"),
            (self.links, "travel link"),
            (self.technology_projects, "technology project"),
        ):
            if len({item.id for item in values}) != len(values):
                raise ValueError(f"Duplicate {label} ID")
        paths = {item.id for item in self.paths}
        realms = {item.id: item for item in self.realms}
        locations = {
            location_id: realm.id for realm in self.realms for location_id in realm.location_ids
        }
        if len(locations) != sum(len(realm.location_ids) for realm in self.realms):
            raise ValueError("A location may belong to only one realm")
        if any(
            field.path_id not in paths for realm in self.realms for field in realm.technology_fields
        ):
            raise ValueError("Realm technology field uses an unknown path")
        for link in self.links:
            if (
                link.origin_realm_id not in realms
                or link.destination_realm_id not in realms
                or locations.get(link.origin_location_id) != link.origin_realm_id
                or locations.get(link.destination_location_id) != link.destination_realm_id
            ):
                raise ValueError("Travel link does not connect its declared realms")
        for project in self.technology_projects:
            if project.realm_id not in realms:
                raise ValueError("Technology project uses an unknown realm")
        if len(set(self.executable_capability_ids)) != len(self.executable_capability_ids):
            raise ValueError("Duplicate executable world-travel capability")
        return self


class TechnologyProject(Record):
    id: Id
    rule_id: Id
    actor_id: Id
    contributor_ids: tuple[Id, ...]
    started_at: int = Field(ge=0)
    due_at: int = Field(gt=0)
    status: Literal["active", "completed"] = "active"
    completed_at: int | None = Field(default=None, ge=0)


class WorldTransfer(Record):
    id: Id
    link_id: Id
    actor_ids: tuple[Id, ...]
    origin_realm_id: Id
    destination_realm_id: Id
    departed_at: int = Field(ge=0)
    arrived_at: int = Field(ge=0)
    mode: Literal["physical", "projection"]


class Projection(Record):
    actor_id: Id
    realm_id: Id
    location_id: Id


class WorldContextState(Record):
    projects: tuple[TechnologyProject, ...] = ()
    transfers: tuple[WorldTransfer, ...] = ()
    projections: tuple[Projection, ...] = ()


class WorldContextCommand(Record):
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)


class StartTechnologyProject(WorldContextCommand):
    kind: Literal["technology-project-start"] = "technology-project-start"
    rule_id: Id
    contributor_ids: tuple[Id, ...] = Field(min_length=1)


class CompleteTechnologyProject(WorldContextCommand):
    kind: Literal["technology-project-complete"] = "technology-project-complete"
    project_id: Id


class TravelBetweenWorlds(WorldContextCommand):
    kind: Literal["world-transfer"] = "world-transfer"
    link_id: Id
    traveler_ids: tuple[Id, ...] = Field(min_length=1)


WorldCommand = Annotated[
    StartTechnologyProject | CompleteTechnologyProject | TravelBetweenWorlds,
    Field(discriminator="kind"),
]
WORLD_COMMAND_ADAPTER: TypeAdapter[WorldCommand] = TypeAdapter(WorldCommand)
WORLD_COMMAND_KINDS = frozenset(
    {"technology-project-start", "technology-project-complete", "world-transfer"}
)


class WorldContextOutcome(Record):
    kind: Literal["technology-project", "world-transfer"]
    status: Literal["started", "completed", "arrived", "projected"]
    subject_id: Id
    effective_technology_level: int | None = None
    destination_realm_id: Id | None = None


def effective_technology_level(
    rules: WorldContextRules, state: WorldContextState, realm_id: str, field: str | None = None
) -> int:
    realm = next((item for item in rules.realms if item.id == realm_id), None)
    if realm is None:
        raise ValidationError("Unknown campaign realm")
    base = realm.baseline_technology_level
    if field is not None:
        selected = next((item for item in realm.technology_fields if item.field == field), None)
        if selected is not None:
            base = selected.level
    completed = [
        project
        for project in state.projects
        if project.status == "completed"
        and (rule := next((r for r in rules.technology_projects if r.id == project.rule_id), None))
        is not None
        and rule.realm_id == realm_id
        and rule.field == (field or "general")
    ]
    return max(
        (
            next(r.to_level for r in rules.technology_projects if r.id == p.rule_id)
            for p in completed
        ),
        default=base,
    )


def technology_field(
    rules: WorldContextRules, state: WorldContextState, realm_id: str, field: str
) -> TechnologyField:
    realm = next((item for item in rules.realms if item.id == realm_id), None)
    if realm is None:
        raise ValidationError("Unknown campaign realm")
    authored = next((item for item in realm.technology_fields if item.field == field), None)
    return TechnologyField(
        field=field,
        level=effective_technology_level(rules, state, realm_id, field),
        path_id=authored.path_id if authored is not None else "standard",
    )


def _digest(command: WorldCommand) -> str:
    return hashlib.sha256(command.model_dump_json().encode()).hexdigest()


def _prior(resources: ResourceState, command: WorldCommand) -> WorldContextOutcome | None:
    receipt = next((item for item in resources.receipts if item.command_id == command.id), None)
    if receipt is None:
        return None
    if receipt.digest != _digest(command):
        raise ConflictError("World-context command ID reused")
    event = next(item for item in resources.events if item.id == "world-context:" + command.id)
    return WorldContextOutcome.model_validate_json(event.kind)


def _finish(
    resources: ResourceState, command: WorldCommand, outcome: WorldContextOutcome
) -> ResourceState:
    return resources.model_copy(
        update={
            "revision": resources.revision + 1,
            "receipts": resources.receipts
            + (Receipt(command_id=command.id, digest=_digest(command)),),
            "events": resources.events
            + (
                ResourceEvent(
                    id="world-context:" + command.id,
                    at=resources.game_time,
                    kind=outcome.model_dump_json(),
                    target_id=command.actor_id,
                ),
            ),
        }
    )


def _location_realm(rules: WorldContextRules, location_id: str) -> str | None:
    return next((realm.id for realm in rules.realms if location_id in realm.location_ids), None)


def validate_world_context(
    rules: WorldContextRules | None, state: WorldContextState, world: World
) -> None:
    if rules is None:
        if state != WorldContextState():
            raise ValidationError("World-context state exists without enabled rules")
        return
    locations = {item.id for item in world.entities if item.kind is EntityKind.LOCATION}
    if not {location for realm in rules.realms for location in realm.location_ids} <= locations:
        raise ValidationError("Realm references a non-location world entity")
    if len({item.id for item in state.projects}) != len(state.projects):
        raise ValidationError("Duplicate technology project ID")
    if len({item.id for item in state.transfers}) != len(state.transfers):
        raise ValidationError("Duplicate world transfer ID")
    if len({item.actor_id for item in state.projections}) != len(state.projections):
        raise ValidationError("An actor may have only one active projection")
    rule_ids = {item.id for item in rules.technology_projects}
    if any(item.rule_id not in rule_ids for item in state.projects):
        raise ValidationError("Technology project lost its authored rule")


def _skill_level(build: ValidatedBuild, skill_id: str) -> int | None:
    value = next((item.value for item in build.sheet.values if item.target == skill_id), None)
    if value is None or not value.is_finite() or value != value.to_integral_value():
        return None
    return int(value)


def _qualified(
    qualification: TechnologyQualification,
    contributor_ids: tuple[str, ...],
    builds: Mapping[str, ValidatedBuild],
) -> bool:
    for actor_id in contributor_ids:
        build = builds.get(actor_id)
        if build is None:
            continue
        purchase = next(
            (item for item in build.purchases if item.definition_id == qualification.definition_id),
            None,
        )
        if (
            purchase is not None
            and purchase.technology_level == qualification.technology_level
            and (_skill_level(build, qualification.definition_id) or 0)
            >= qualification.minimum_level
        ):
            return True
    return False


def _start_project(
    state: WorldContextState,
    resources: ResourceState,
    world: World,
    command: StartTechnologyProject,
    rules: WorldContextRules,
    builds: Mapping[str, ValidatedBuild],
    consume: Callable[[ResourceState, Consume], ResourceState],
) -> tuple[WorldContextState, ResourceState, WorldContextOutcome]:
    rule = next((item for item in rules.technology_projects if item.id == command.rule_id), None)
    if rule is None:
        raise ValidationError("Unknown authored technology project")
    if command.actor_id not in command.contributor_ids or len(set(command.contributor_ids)) != len(
        command.contributor_ids
    ):
        raise ValidationError("Technology project contributors must uniquely include its actor")
    entities = {item.id: item for item in world.entities}
    if any(
        actor_id not in builds
        or (entity := entities.get(actor_id)) is None
        or entity.kind is not EntityKind.ACTOR
        or entity.location_id is None
        or _location_realm(rules, entity.location_id) != rule.realm_id
        for actor_id in command.contributor_ids
    ):
        raise ValidationError("Technology contributors must be present in the project realm")
    if not rule.labor_available or not rule.host_cooperation:
        raise ValidationError("Local technology requires authored labor and host cooperation")
    if effective_technology_level(rules, state, rule.realm_id, rule.field) != rule.from_level:
        raise ConflictError("Local technology field no longer matches the project baseline")
    if not all(_qualified(item, command.contributor_ids, builds) for item in rule.qualifications):
        raise ValidationError("Local technology requires lower- and higher-TL skills at 12+")
    items = {item.id: item for item in resources.items}
    contributors = set(command.contributor_ids)
    if any(
        (item := items.get(cost.item_id)) is None
        or item.owner_id not in contributors
        or item.quantity < cost.quantity
        or item.equipped
        for cost in rule.material_costs
    ):
        raise ValidationError("Local technology requires its authored available materials")
    updated_resources = resources
    for index, cost in enumerate(rule.material_costs):
        item = items[cost.item_id]
        updated_resources = consume(
            updated_resources,
            Consume(
                id=f"{command.id}:material:{index}",
                actor_id=item.owner_id,
                expected_revision=updated_resources.revision,
                item_id=cost.item_id,
                quantity=cost.quantity,
            ),
        )
    project = TechnologyProject(
        id=command.id,
        rule_id=rule.id,
        actor_id=command.actor_id,
        contributor_ids=command.contributor_ids,
        started_at=resources.game_time,
        due_at=resources.game_time + rule.duration_seconds,
    )
    outcome = WorldContextOutcome(
        kind="technology-project", status="started", subject_id=project.id
    )
    return (
        state.model_copy(update={"projects": state.projects + (project,)}),
        _finish(updated_resources, command, outcome),
        outcome,
    )


def _complete_project(
    state: WorldContextState,
    resources: ResourceState,
    command: CompleteTechnologyProject,
    rules: WorldContextRules,
) -> tuple[WorldContextState, ResourceState, WorldContextOutcome]:
    project = next((item for item in state.projects if item.id == command.project_id), None)
    if project is None or project.actor_id != command.actor_id:
        raise ValidationError("Unknown actor-owned technology project")
    if project.status != "active":
        raise ConflictError("Technology project is already complete")
    if resources.game_time < project.due_at:
        raise ConflictError("Technology project is not yet due")
    rule = next(item for item in rules.technology_projects if item.id == project.rule_id)
    completed = project.model_copy(
        update={"status": "completed", "completed_at": resources.game_time}
    )
    updated = state.model_copy(
        update={
            "projects": tuple(
                completed if item.id == project.id else item for item in state.projects
            )
        }
    )
    outcome = WorldContextOutcome(
        kind="technology-project",
        status="completed",
        subject_id=project.id,
        effective_technology_level=rule.to_level,
    )
    return updated, _finish(resources, command, outcome), outcome


def _spend_fatigue(
    resources: ResourceState, traveler_ids: tuple[str, ...], amount: int
) -> tuple[Pool, ...]:
    if amount == 0:
        return resources.pools
    pools = {item.id: item for item in resources.pools}
    for actor_id in traveler_ids:
        pool = pools.get("fp:" + actor_id)
        if pool is None or pool.current < amount:
            raise ValidationError("World transfer requires available fatigue")
    travelers = set(traveler_ids)
    return tuple(
        pool.model_copy(update={"current": pool.current - amount})
        if pool.id.removeprefix("fp:") in travelers and pool.id.startswith("fp:")
        else pool
        for pool in resources.pools
    )


def _travel(
    state: WorldContextState,
    resources: ResourceState,
    world: World,
    command: TravelBetweenWorlds,
    rules: WorldContextRules,
    builds: Mapping[str, ValidatedBuild],
    advance: Callable[[ResourceState, int, str], ResourceState],
    *,
    system: bool,
) -> tuple[WorldContextState, ResourceState, World, WorldContextOutcome]:
    link = next((item for item in rules.links if item.id == command.link_id), None)
    if link is None:
        raise ValidationError("Unknown authored world-transfer link")
    if link.capability_id not in rules.executable_capability_ids:
        raise ValidationError("World-transfer method has no registered executable capability")
    if (
        command.actor_id not in command.traveler_ids
        or len(set(command.traveler_ids)) != len(command.traveler_ids)
        or (not system and command.traveler_ids != (command.actor_id,))
    ):
        raise ValidationError("World transfer requires authority for every traveler")
    entities = {item.id: item for item in world.entities}
    travelers = [entities.get(actor_id) for actor_id in command.traveler_ids]
    if any(
        item is None
        or item.kind is not EntityKind.ACTOR
        or item.location_id != link.origin_location_id
        for item in travelers
    ):
        raise ValidationError("World-transfer origin context changed")
    destination = entities.get(link.destination_location_id)
    if destination is None or destination.kind is not EntityKind.LOCATION:
        raise ValidationError("World-transfer destination is unavailable")
    required_facts = set(link.required_fact_ids)
    if any(
        not required_facts
        <= {fact_id for owner_id, fact_id in world.knowledge if owner_id == actor_id}
        for actor_id in command.traveler_ids
    ):
        raise ValidationError("World-transfer access knowledge is missing")
    required_definitions = set(link.required_definition_ids)
    if any(
        (build := builds.get(actor_id)) is None
        or not required_definitions <= {item.definition_id for item in build.purchases}
        for actor_id in command.traveler_ids
    ):
        raise ValidationError("World-transfer access capability is missing")
    required_items = set(link.required_item_definition_ids)
    if not required_items <= {
        item.definition_id
        for item in resources.items
        if item.owner_id in command.traveler_ids and item.ground is None
    }:
        raise ValidationError("World-transfer access equipment is missing")

    pools = _spend_fatigue(resources, command.traveler_ids, link.fatigue_cost_per_traveler)
    updated_resources = resources.model_copy(update={"pools": pools})
    if link.travel_seconds:
        updated_resources = advance(
            updated_resources,
            updated_resources.game_time + link.travel_seconds,
            command.id,
        )
    traveler_ids = set(command.traveler_ids)
    from dataclasses import replace

    moved_world = (
        world
        if link.mode == "projection"
        else replace(
            world,
            entities=tuple(
                replace(item, location_id=link.destination_location_id)
                if item.id in traveler_ids or item.owner_id in traveler_ids
                else item
                for item in world.entities
            ),
        )
    )
    moved_world.validate()
    transfer = WorldTransfer(
        id=command.id,
        link_id=link.id,
        actor_ids=command.traveler_ids,
        origin_realm_id=link.origin_realm_id,
        destination_realm_id=link.destination_realm_id,
        departed_at=resources.game_time,
        arrived_at=updated_resources.game_time,
        mode=link.mode,
    )
    outcome = WorldContextOutcome(
        kind="world-transfer",
        status="projected" if link.mode == "projection" else "arrived",
        subject_id=transfer.id,
        destination_realm_id=link.destination_realm_id,
    )
    update: dict[str, object] = {"transfers": state.transfers + (transfer,)}
    if link.mode == "projection":
        old = {item.actor_id: item for item in state.projections}
        old.update(
            {
                actor_id: Projection(
                    actor_id=actor_id,
                    realm_id=link.destination_realm_id,
                    location_id=link.destination_location_id,
                )
                for actor_id in command.traveler_ids
            }
        )
        update["projections"] = tuple(old[key] for key in sorted(old))
    else:
        update["projections"] = tuple(
            item for item in state.projections if item.actor_id not in traveler_ids
        )
    return (
        state.model_copy(update=update),
        _finish(updated_resources, command, outcome),
        moved_world,
        outcome,
    )


def apply_world_context(
    state: WorldContextState,
    resources: ResourceState,
    world: World,
    command: WorldCommand,
    rules: WorldContextRules,
    *,
    builds: Mapping[str, ValidatedBuild],
    consume: Callable[[ResourceState, Consume], ResourceState],
    advance: Callable[[ResourceState, int, str], ResourceState],
    system: bool = False,
) -> tuple[WorldContextState, ResourceState, World, WorldContextOutcome]:
    previous = _prior(resources, command)
    if previous is not None:
        return state, resources, world, previous
    if resources.revision != command.expected_revision:
        raise ConflictError("World-context revision changed")
    if command.kind == "technology-project-start":
        if not system:
            raise ValidationError("Starting local technology requires campaign authority")
        context, updated, outcome = _start_project(
            state, resources, world, command, rules, builds, consume
        )
        return context, updated, world, outcome
    if command.kind == "technology-project-complete":
        if not system:
            raise ValidationError("Completing local technology requires campaign authority")
        context, updated, outcome = _complete_project(state, resources, command, rules)
        return context, updated, world, outcome
    return _travel(state, resources, world, command, rules, builds, advance, system=system)
