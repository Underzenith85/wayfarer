"""Atomic resource commands using the existing SQLite/PostgreSQL event stores."""

from wayfarer.errors import ValidationError
from wayfarer.models import Campaign, Event
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.postgres import AsyncPostgresStore
from wayfarer.rules.catalog import reference
from wayfarer.simulation.resources import (
    COMMAND_ADAPTER,
    Advance,
    ResourceEngine,
    ResourceState,
    Schedule,
)


class ResourceService:
    def __init__(
        self, store: AsyncSQLiteStore | AsyncPostgresStore, engine: ResourceEngine
    ) -> None:
        self.store, self.engine = store, engine

    async def create(self, campaign: Campaign, resources: ResourceState) -> None:
        """Trusted scenario activation; cannot overwrite an existing campaign."""
        if campaign["revision"] != 0 or resources.revision != 0:
            raise ValidationError("Initial revisions must be zero")
        if campaign.get("rules_ref") != reference(self.engine.rules):
            raise ValidationError("Campaign rules do not match the resource engine")
        self.engine.validate(resources)
        state = campaign.copy()
        state["resources_json"] = resources.model_dump_json()
        await self.store.insert(state)

    async def execute(
        self, cid: str, value: object, *, authenticated_actor_id: str, system: bool = False
    ) -> ResourceState:
        """Authentication/system authority comes from trusted transport, not JSON."""
        command = COMMAND_ADAPTER.validate_python(value)
        if command.actor_id != authenticated_actor_id or command.actor_id not in self.engine.actors:
            raise ValidationError("Command actor is not authorized")
        if isinstance(command, (Schedule, Advance)) and not system:
            raise ValidationError("Clock commands require engine authority")
        payload = command.model_dump_json()

        def resolve(state: Campaign) -> Event:
            if state.get("rules_ref") != reference(self.engine.rules):
                raise ValidationError("Campaign rules do not match the resource engine")
            raw = state.get("resources_json")
            if raw is None:
                raise ValidationError("Campaign has no resource state")
            resources = ResourceState.model_validate_json(raw)
            if resources.revision != state["revision"]:
                raise ValidationError("Resource and campaign revisions diverged")
            updated = self.engine.apply(resources, command, system=system)
            state["resources_json"] = updated.model_dump_json()
            state["revision"] = updated.revision
            return Event(input=payload, action="resource", outcome=command.kind, roll=None)

        result = await self.store.commit_turn(
            cid, command.id, command.expected_revision, payload, resolve, actor_id=command.actor_id
        )
        return ResourceState.model_validate_json(result["state"]["resources_json"])
