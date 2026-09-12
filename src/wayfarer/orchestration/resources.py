"""Atomic resource commands using the existing SQLite/PostgreSQL event stores."""

import secrets

from pydantic import TypeAdapter

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.rules.catalog import reference
from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.simulation.equipment.objects import ObjectCommand, apply_object
from wayfarer.engine.simulation.hex_geometry import Hex, HexBattlefield
from wayfarer.engine.simulation.movement.transport import (
    TransportCommand,
    apply_transport,
    validate_transport,
)
from wayfarer.engine.simulation.resources import (
    COMMAND_ADAPTER,
    Advance,
    RechargePowerCell,
    ResourceEngine,
    ResourceState,
    Schedule,
)
from wayfarer.errors import ValidationError
from wayfarer.orchestration.entropy import CommandRandom, commit_command
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.postgres import AsyncPostgresStore


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
        for transport in resources.transports:
            validate_transport(self.engine, resources, transport)
        state = campaign.copy()
        state["resources_json"] = resources.model_dump_json()
        await self.store.insert(state)

    async def execute(
        self, cid: str, value: object, *, authenticated_actor_id: str, system: bool = False
    ) -> ResourceState:
        """Authentication/system authority comes from trusted transport, not JSON."""
        rng = CommandRandom()
        command = COMMAND_ADAPTER.validate_python(value)
        if command.actor_id != authenticated_actor_id or command.actor_id not in self.engine.actors:
            raise ValidationError("Command actor is not authorized")
        if isinstance(command, (Schedule, Advance, RechargePowerCell)) and not system:
            raise ValidationError("Clock and recharge commands require engine authority")
        payload = command.model_dump_json()

        def resolve(state: Campaign) -> CommandReceipt:
            if state.get("rules_ref") != reference(self.engine.rules):
                raise ValidationError("Campaign rules do not match the resource engine")
            raw = state.get("resources_json")
            if raw is None:
                raise ValidationError("Campaign has no resource state")
            resources = ResourceState.model_validate_json(raw)
            if resources.revision != state["revision"]:
                raise ValidationError("Resource and campaign revisions diverged")
            updated = self.engine.apply(resources, command, system=system, rng=rng)
            state["resources_json"] = updated.model_dump_json()
            state["revision"] = updated.revision
            return CommandReceipt(action="resource", outcome=command.kind)

        result = await commit_command(
            self.store,
            cid,
            command.id,
            command.expected_revision,
            payload,
            resolve,
            actor_id="system" if system else authenticated_actor_id,
            rng=rng,
        )
        return ResourceState.model_validate_json(result["state"]["resources_json"])

    async def execute_object(
        self,
        cid: str,
        value: object,
        *,
        authenticated_actor_id: str,
        system: bool = False,
        rng: RandomSource = secrets,
    ) -> ResourceState:
        """Internal resolved-damage transaction; no player-facing damage payload."""
        rng = CommandRandom(rng)
        command: ObjectCommand = TypeAdapter(ObjectCommand).validate_python(value)
        if not system or command.actor_id != authenticated_actor_id:
            raise ValidationError("Object commands require authenticated engine authority")
        if command.actor_id not in self.engine.actors:
            raise ValidationError("Object command actor is not authorized")
        payload = command.model_dump_json()

        def resolve(state: Campaign) -> CommandReceipt:
            if "play_json" in state:
                raise ValidationError("Live play object damage requires the combat transaction")
            if state.get("rules_ref") != reference(self.engine.rules):
                raise ValidationError("Campaign rules do not match the resource engine")
            raw = state.get("resources_json")
            if raw is None:
                raise ValidationError("Campaign has no resource state")
            resources = ResourceState.model_validate_json(raw)
            if resources.revision != state["revision"]:
                raise ValidationError("Resource and campaign revisions diverged")
            updated, _ = apply_object(self.engine, resources, command, system=True, rng=rng)
            state["resources_json"] = updated.model_dump_json()
            state["revision"] = updated.revision
            return CommandReceipt(action="resource", outcome=command.kind)

        result = await commit_command(
            self.store,
            cid,
            command.id,
            command.expected_revision,
            payload,
            resolve,
            actor_id="system",
            rng=rng,
        )
        return ResourceState.model_validate_json(result["state"]["resources_json"])

    async def execute_transport(
        self,
        cid: str,
        value: object,
        *,
        authenticated_actor_id: str,
        system: bool = False,
        rng: RandomSource = secrets,
        board: HexBattlefield | None = None,
        health: dict[str, int] | None = None,
        occupied: frozenset[Hex] = frozenset(),
    ) -> ResourceState:
        """Internal resolved-damage transaction; no player-facing damage payload."""
        rng = CommandRandom(rng)
        command: TransportCommand = TypeAdapter(TransportCommand).validate_python(value)
        if not system or command.actor_id != authenticated_actor_id:
            raise ValidationError("Transport commands require authenticated engine authority")
        if command.actor_id not in self.engine.actors:
            raise ValidationError("Transport command actor is not authorized")
        payload = command.model_dump_json()

        def resolve(state: Campaign) -> CommandReceipt:
            if "play_json" in state:
                raise ValidationError("Live play transport requires the combat transaction")
            if state.get("rules_ref") != reference(self.engine.rules):
                raise ValidationError("Campaign rules do not match the resource engine")
            raw = state.get("resources_json")
            if raw is None:
                raise ValidationError("Campaign has no resource state")
            resources = ResourceState.model_validate_json(raw)
            if resources.revision != state["revision"]:
                raise ValidationError("Resource and campaign revisions diverged")
            updated = apply_transport(
                self.engine,
                resources,
                command,
                system=True,
                rng=rng,
                board=board,
                health=health,
                occupied=occupied,
            )
            state["resources_json"] = updated.model_dump_json()
            state["revision"] = updated.revision
            return CommandReceipt(action="resource", outcome=command.kind)

        result = await commit_command(
            self.store,
            cid,
            command.id,
            command.expected_revision,
            payload,
            resolve,
            actor_id="system",
            rng=rng,
        )
        return ResourceState.model_validate_json(result["state"]["resources_json"])
