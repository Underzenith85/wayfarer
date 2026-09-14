"""The campaign runtime: the container every adapter receives.

It owns the dependencies a campaign write needs — the play services, the session
registry behind them, the provider job worker and the store handles those are
built from — and exposes the authorized queries, commands and perspective-safe
resumable streams on top of them. Nothing here is a module global, so two
runtimes in one process share no lock, no engine cache and no job partition.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from wayfarer import contracts
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import CharacterCompiler
from wayfarer.engine.character.power import CharacterProposal, PowerReview
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup, PlayState
from wayfarer.engine.simulation.campaign.access import CampaignMember, StreamEvent
from wayfarer.engine.simulation.combat.profiles import CombatRules
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.world import World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.commands import Submission, family_for
from wayfarer.orchestration.medical import EnvironmentResolver
from wayfarer.orchestration.membership import member_for, require_control
from wayfarer.orchestration.origins import origin_scope
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.process_kinds import registered
from wayfarer.orchestration.processes import ProcessRegistry
from wayfarer.orchestration.projections import ViewRequest, project
from wayfarer.orchestration.sessions import Store
from wayfarer.orchestration.views import campaign_view
from wayfarer.orchestration.workshop_options import (
    CharacterPreviewResult,
    preview_character,
)
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.catalog import CatalogStore
from wayfarer.persistence.events import CommandOrigin, CommandRecord
from wayfarer.persistence.processes import ProcessStore


@dataclass(frozen=True)
class CampaignStores:
    """The store handles one runtime owns. Adapters receive the runtime, not these."""

    campaigns: Store
    catalog: CatalogStore
    processes: ProcessStore

    @classmethod
    def on(cls, store: Store) -> CampaignStores:
        catalog = CatalogStore(store)
        return cls(campaigns=store, catalog=catalog, processes=ProcessStore(catalog))


class CampaignRuntime:
    def __init__(
        self,
        play: PlayService,
        medical_environment: EnvironmentResolver | None = None,
        *,
        stores: CampaignStores | None = None,
        processes: ProcessRegistry | None = None,
        partition: str = "default",
    ) -> None:
        self.play = play
        self.medical_environment = medical_environment
        self.stores = CampaignStores.on(play.store) if stores is None else stores
        # One worker per runtime, so two runtimes never contend for a partition.
        self.processes = (
            registered(ProcessRegistry(self.stores.processes, partition=partition))
            if processes is None
            else processes
        )
        self.partition = self.processes.partition

    def for_service(self, play: PlayService) -> CampaignRuntime:
        """The same runtime around another service over these stores.

        A composition that binds a second engine to one store — a setup service on
        its own scenario, a rebound campaign — must reuse this runtime's stores and
        worker, and the service must come from ``PlayService.derived`` so it shares
        the session registry. Two runtimes over one store would mean two locks on a
        campaign and two workers on a partition.
        """
        return CampaignRuntime(
            play,
            self.medical_environment,
            stores=self.stores,
            processes=self.processes,
        )

    async def for_campaign(self, cid: str) -> CampaignRuntime:
        """Reconstruct an activated scenario's pinned runtime after restart."""
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        return self if play is self.play else self.for_service(play)

    @staticmethod
    def member(state: PlayState, principal_id: str) -> CampaignMember:
        return member_for(state, principal_id)

    @property
    def rules(self) -> ActionRules:
        return self.play.engine.rules

    async def checkpoint(self, cid: str) -> PlayState:
        """The campaign's current play state, loaded through its own pinned engine."""
        runtime = await self.for_campaign(cid)
        return runtime.play._load(await runtime.play.store.read(cid))

    async def history(self, cid: str) -> list[CommandRecord]:
        return await self.play.store.history(cid)

    @property
    def compiler(self) -> CharacterCompiler:
        """The character compiler this campaign's engine is pinned to."""
        return self.play.engine.reviewer.compiler

    def directs(self, principal_id: str) -> bool:
        """Whether this principal holds the server's configured director authority.

        Campaign membership says a principal plays the director's part; this says
        the deployment trusts them with it. Both are needed to approve.
        """
        return principal_id in self.play.engine.reviewer.gm_ids

    def preview(self, proposal: CharacterProposal) -> CharacterPreviewResult:
        """Compile a draft without committing anything, for an editor's feedback."""
        return preview_character(self.play.engine.reviewer, proposal)

    def bound(self, campaign: Campaign) -> CampaignRuntime:
        """This runtime on the engine the campaign is pinned to."""
        play = self.play.for_campaign(campaign)
        return self if play is self.play else self.for_service(play)

    def load(self, campaign: Campaign) -> PlayState:
        """The checkpoint this campaign carries, validated against its pinned engine."""
        return self.play.for_campaign(campaign)._load(campaign)

    def review(self, proposal: CharacterProposal) -> PowerReview:
        """Compile and review a character against this campaign's power policy."""
        return self.play.engine.reviewer.review(proposal)

    def ledger_path(self) -> Path:
        """Where the v1 receipt database sits beside a file-backed campaign store."""
        if not isinstance(self.play.store, AsyncSQLiteStore):
            raise ValueError("Configure v1_ledger_path for the durable API receipt database")
        return self.play.store.path.with_suffix(".v1.sqlite3")

    async def duplicate(self, cid: str, command_id: str, payload: str) -> Campaign | None:
        """The receipt of an identical earlier command, if this one is a retry."""
        return await self.play.store.duplicate(cid, command_id, payload)

    async def create(
        self, campaign: Campaign, *, command_id: str, text: str, principal_id: str = "system"
    ) -> Campaign:
        """Commit a campaign envelope as its stream's first command, once per identity.

        The campaign lock makes a simultaneous retry wait rather than race the
        insert, and the genesis receipt makes the retry answerable from the log.
        """
        cid = campaign["id"]
        contracts.campaign(campaign)
        async with self.play.sessions.serialized(cid):
            result = await self.play.store.commit_genesis(
                campaign,
                command_id=command_id,
                actor_id=principal_id,
                text=text,
                recorded_at_us=self.play.instants().unix_microseconds,
            )
            if result["kind"] == "replayed":
                return await self.play.store.read(cid)
            return result["state"]

    async def seed(
        self,
        campaign: Campaign,
        world: World,
        resources: ResourceState,
        actors: tuple[ActorSetup, ...],
        members: tuple[CampaignMember, ...] | None = None,
        *,
        command_id: str = "setup:seed",
    ) -> PlayState:
        """Trusted activation: build the starting checkpoint and commit it as genesis."""
        state = self.play.initial_state(campaign, world, resources, actors, members)
        stored = campaign.copy()
        self.play.commit(stored, state)
        await self.create(
            stored,
            command_id=command_id,
            text=json.dumps({"operation": "seed", "campaign": campaign["id"]}, sort_keys=True),
        )
        return state

    def view(
        self, state: PlayState, member: CampaignMember, rules: CombatRules | None = None
    ) -> dict[str, object]:
        """The audience-filtered campaign view, built where every reader's is."""
        return campaign_view(state, member, self.rules.combat if rules is None else rules)

    async def read(self, cid: str, *, principal_id: str) -> dict[str, object]:
        return await self.project("campaign", cid, principal_id=principal_id)

    async def project(self, name: str, cid: str, *, principal_id: str) -> dict[str, object]:
        """Resolve one registered projection for this reader, on its pinned engine."""
        runtime = await self.for_campaign(cid)
        if runtime is not self:
            return await runtime.project(name, cid, principal_id=principal_id)
        state = self.play._load(await self.play.store.read(cid))
        member = self.member(state, principal_id)
        return project(name, ViewRequest(runtime=self, state=state, member=member))

    async def submit_json(
        self, cid: str, value: object, *, principal_id: str, origin: CommandOrigin | None = None
    ) -> dict[str, object]:
        with origin_scope(origin):
            return await self._execute(cid, value, principal_id=principal_id)

    async def _execute(self, cid: str, value: object, *, principal_id: str) -> dict[str, object]:
        runtime = await self.for_campaign(cid)
        if runtime is not self:
            return await runtime._execute(cid, value, principal_id=principal_id)
        if not isinstance(value, dict):
            raise ValidationError("Invalid typed campaign command")
        campaign = await self.play.store.read(cid)
        state = self.play._load(campaign)
        member = self.member(state, principal_id)
        if state.lifecycle != "active":
            raise ConflictError("Resume an active campaign before acting")
        family = family_for(value.get("kind"))
        try:
            submission = Submission(
                play=self.play,
                cid=cid,
                campaign=campaign,
                command=family.parse(json.dumps(value)),
                state=state,
                member=member,
                principal_id=principal_id,
                medical_environment=self.medical_environment,
            )
            for precondition in family.preconditions:
                precondition(submission)
            family.authorize(submission)
            await family.service(submission)
        except ValueError as exc:
            raise ValidationError("Invalid typed campaign command") from exc
        return await self.read(cid, principal_id=principal_id)

    @staticmethod
    def control(member: CampaignMember, actor_id: str) -> None:
        require_control(member, actor_id)

    async def events(
        self, cid: str, *, principal_id: str, after: int = 0, limit: int = 100
    ) -> tuple[StreamEvent, ...]:
        runtime = await self.for_campaign(cid)
        if runtime is not self:
            return await runtime.events(cid, principal_id=principal_id, after=after, limit=limit)
        if after < 0 or not 1 <= limit <= 100:
            raise ValidationError("Invalid stream cursor or limit")
        current = self.play._load(await self.play.store.read(cid))
        member = self.member(current, principal_id)
        history = await self.play.store.history(cid)
        if after > current.revision:
            raise ConflictError("Stream cursor is ahead of campaign")
        result: list[StreamEvent] = []
        for event in history:
            if event.resulting_revision <= after:
                continue
            state = PlayState.model_validate_json(event.state_after["play_json"])
            result.append(
                StreamEvent(
                    cursor=event.resulting_revision,
                    command_id=event.command_id
                    if member.role == "gm" or event.actor_id in member.actor_ids
                    else "redacted",
                    actor_id=event.actor_id
                    if member.role == "gm" or event.actor_id in member.actor_ids
                    else "redacted",
                    action=event.event["action"]
                    if member.role == "gm" or event.actor_id in member.actor_ids
                    else "private",
                    outcome=(
                        event.event["outcome"]
                        if member.role == "gm"
                        or (
                            event.actor_id in member.actor_ids
                            and event.event["action"] != "objectives"
                        )
                        else ""
                    ),
                    projection=project(
                        "stream", ViewRequest(runtime=self, state=state, member=member)
                    ),
                )
            )
            if len(result) == limit:
                break
        return tuple(result)
