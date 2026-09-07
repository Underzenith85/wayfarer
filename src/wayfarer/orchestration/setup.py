"""Revision-checked lobby edits and atomic opening-scene activation."""

import json
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid5

from wayfarer.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    StorageError,
    ValidationError,
)
from wayfarer.models import Campaign, Event
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.studio import ScenarioStudio
from wayfarer.rules.catalog import reference
from wayfarer.simulation.access import CampaignMember
from wayfarer.simulation.setup import CreateSetup, Seat, Setup, SetupCommand

if TYPE_CHECKING:
    from wayfarer.orchestration.providers import Orchestrator


class SetupService:
    def __init__(self, access: CampaignAccess, *, engine_controls: bool = False) -> None:
        self.engine_controls = engine_controls
        self.access = access
        self.play = access.play

    @staticmethod
    def load(campaign: Campaign) -> Setup:
        if "setup_json" not in campaign:
            raise NotFoundError("Setup not found")
        return Setup.model_validate_json(campaign["setup_json"])

    @staticmethod
    def seat(setup: Setup, principal: str) -> Seat:
        seat = next((s for s in setup.seats if s.principal_id == principal), None)
        if seat is None:
            raise NotFoundError("Setup not found")
        return seat

    async def create(self, command: CreateSetup, *, principal_id: str) -> dict[str, object]:
        cid = str(uuid5(NAMESPACE_URL, json.dumps([principal_id, command.id])))
        payload = command.model_dump_json()
        setup = Setup(
            host_id=principal_id,
            creation_json=payload,
            brief=command.brief,
            graph=command.graph,
            seats=(Seat(principal_id=principal_id, joined=True),),
        )
        campaign = Campaign(
            id=cid,
            revision=0,
            rules="pinned",
            rules_ref=reference(self.play.engine.resources.rules),
            setup_json=setup.model_dump_json(),
            character={
                "name": "Party",
                "concept": "",
                "attributes": {"ST": 10, "DX": 10, "IQ": 10, "HT": 10},
                "skills": {},
                "traits": [],
            },
            scenario={
                "title": command.brief.premise[:200],
                "premise": command.brief.premise[:2000],
                "location": "Draft",
                "contact": "Draft",
                "clue": "Draft",
                "secret": "Draft",
                "objective": "Draft",
            },
            hp=0,
            fp=0,
            minutes=0,
            location="",
            inventory=[],
            discoveries=[],
            flags=[],
            complete=False,
            messages=[],
        )
        from wayfarer import validation

        validation.campaign(campaign)
        try:
            existing = await self.play.store.read(cid)
        except NotFoundError:
            try:
                await self.play.store.insert(campaign)
            except StorageError:
                # A simultaneous retry may have inserted the same identity.
                existing = await self.play.store.read(cid)
            else:
                existing = campaign
        if self.load(existing).creation_json != payload:
            raise ConflictError("Creation identity already used for different input")
        return await self.read(cid, principal_id=principal_id)

    async def listing(self, *, principal_id: str) -> list[dict[str, object]]:
        result = []
        for item in await self.play.store.listing():
            campaign = await self.play.store.read(item["id"])
            if "setup_json" in campaign and any(
                s.principal_id == principal_id for s in self.load(campaign).seats
            ):
                result.append(await self.read(item["id"], principal_id=principal_id))
        return result

    async def read(self, cid: str, *, principal_id: str) -> dict[str, object]:
        campaign = await self.play.store.read(cid)
        setup = self.load(campaign)
        seat = self.seat(setup, principal_id)
        # The graph includes secrets. Only its author sees the editable graph.
        return {
            "id": cid,
            "engine_controls": self.engine_controls,
            "revision": campaign["revision"],
            "host_id": setup.host_id,
            "phase": setup.phase,
            "brief": setup.brief.model_dump(mode="json"),
            "seats": [s.model_dump(mode="json") for s in setup.seats],
            "graph": setup.graph.model_dump(mode="json")
            if setup.host_id == principal_id and setup.graph
            else None,
            "title": setup.graph.title if setup.graph else campaign["scenario"]["title"],
            "opening_action": setup.graph.opening_action
            if setup.graph and setup.phase in ("active", "paused", "completed")
            else None,
            "rules": campaign.get("rules_ref"),
            "adventures": [a.project(seat.actor_ids) for a in setup.adventures],
            "next_adventure": {
                "id": setup.next_graph.id,
                "title": setup.next_graph.title,
                "opening_action": setup.next_graph.opening_action,
            }
            if setup.next_graph
            else None,
        }

    async def execute(
        self, cid: str, command: SetupCommand, *, principal_id: str
    ) -> dict[str, object]:
        current = await self.play.store.read(cid)
        self.seat(self.load(current), principal_id)
        payload = json.dumps(
            {"principal": principal_id, "command": command.model_dump(mode="json")}, sort_keys=True
        )
        key = "setup:" + command.id

        def resolve(campaign: Campaign) -> Event:
            setup = self.load(campaign)
            me = self.seat(setup, principal_id)
            op = command.operation
            if op not in ("join", "ready") and setup.host_id != principal_id:
                raise AuthorizationError("Only the host can change setup or lifecycle")
            if op in ("edit", "invite", "join", "assign", "ready") and setup.phase not in (
                "draft",
                "ready",
            ):
                raise ConflictError("Party and controller assignments are locked after activation")
            seats = list(setup.seats)
            if op == "edit":
                setup = setup.model_copy(
                    update={"brief": command.brief or setup.brief, "graph": command.graph}
                )
                seats = [s.model_copy(update={"ready": False, "actor_ids": ()}) for s in seats]
            elif op == "invite":
                if not command.principal_id or any(
                    s.principal_id == command.principal_id for s in seats
                ):
                    raise ValidationError("Invite a distinct player identity")
                if len(seats) >= 30:
                    raise ValidationError("Party is full")
                seats.append(Seat(principal_id=command.principal_id))
                seats = [s.model_copy(update={"ready": False}) for s in seats]
            elif op == "join":
                seats = [s.model_copy(update={"joined": True}) if s == me else s for s in seats]
            elif op == "assign":
                target = next(
                    (s for s in seats if s.principal_id == command.principal_id and s.joined), None
                )
                available = (
                    {
                        a.actor_id
                        for a in setup.graph.actors
                        if a.actor_id not in setup.graph.npc_actor_ids
                    }
                    if setup.graph
                    else set()
                )
                if (
                    target is None
                    or len(set(command.actor_ids)) != len(command.actor_ids)
                    or not set(command.actor_ids) <= available
                ):
                    raise ValidationError("Assign known player characters to a joined player")
                if any(set(command.actor_ids) & set(s.actor_ids) for s in seats if s != target):
                    raise ConflictError("Character already has a controller")
                seats = [
                    s.model_copy(update={"actor_ids": command.actor_ids, "ready": False})
                    if s == target
                    else s.model_copy(update={"ready": False})
                    for s in seats
                ]
            elif op == "ready":
                if not me.joined or not me.actor_ids:
                    raise ValidationError("Join and select a legal character first")
                self.validate(setup)
                seats = [
                    s.model_copy(update={"ready": command.ready}) if s == me else s for s in seats
                ]
            elif op == "preview":
                if setup.phase != "completed" or command.graph is None:
                    raise ConflictError("Complete the adventure before previewing its successor")
                from wayfarer.orchestration.continuation import prepare

                graph, _ = prepare(self.play, campaign, setup, command.graph)
                setup = setup.model_copy(update={"next_graph": graph})
            elif op == "continue":
                if setup.phase != "completed" or setup.next_graph is None:
                    raise ConflictError("Save and review a next-adventure preview first")
                from wayfarer.orchestration.continuation import prepare

                graph, state = prepare(self.play, campaign, setup, setup.next_graph)
                campaign["play_json"] = state.model_dump_json()
                campaign["scenario_graph_json"] = graph.model_dump_json()
                campaign["scenario"]["title"] = graph.title
                setup = setup.model_copy(
                    update={
                        "graph": graph,
                        "brief": graph.brief,
                        "next_graph": None,
                        "phase": "active",
                    }
                )
            elif op == "activate":
                if setup.phase != "ready" or not all(
                    s.joined and s.ready and s.actor_ids for s in seats
                ):
                    raise ConflictError("Every invited player must join and confirm readiness")
                self.validate(setup)
                assert setup.graph is not None
                graph = setup.graph
                assigned = [a for s in seats for a in s.actor_ids]
                if len(set(assigned)) != len(assigned) or set(assigned) != {
                    a.actor_id for a in graph.actors
                } - set(graph.npc_actor_ids):
                    raise ValidationError("Every player character needs exactly one controller")
                studio = ScenarioStudio(self.play, npc_reviewer=self.play.engine.reviewer)
                activated = PlayService(self.play.store, studio.engine(graph), rng=self.play.rng)
                seed = campaign.copy()
                seed["revision"] = 0
                members = tuple(
                    CampaignMember(
                        principal_id=s.principal_id, role="player", actor_ids=s.actor_ids
                    )
                    for s in seats
                ) + tuple(
                    CampaignMember(principal_id=gm, role="gm")
                    for gm in sorted(self.play.engine.reviewer.gm_ids)
                    if gm not in {s.principal_id for s in seats}
                )
                state = activated.initial_state(
                    seed, graph.world, graph.resources, graph.actors, members
                )
                campaign["play_json"] = state.model_dump_json()
                campaign["scenario_graph_json"] = graph.model_dump_json()
                campaign["scenario"]["title"] = graph.title
                setup = setup.model_copy(update={"phase": "active"})
            else:
                transitions = {
                    "pause": ("active", "paused"),
                    "resume": ("paused", "active"),
                    "complete": ("active", "completed"),
                    "archive": ("completed", "archived"),
                    "unarchive": ("archived", "completed"),
                }
                source, destination = transitions[op]
                if setup.phase != source:
                    raise ConflictError("Invalid lifecycle transition")
                from wayfarer.simulation.actions import PlayState

                state = PlayState.model_validate_json(campaign["play_json"])
                if op == "complete" and state.objectives.outcome == "ongoing":
                    raise ValidationError("The engine must determine the adventure outcome first")
                if op in ("pause", "complete", "archive") and any(
                    t.phase not in ("complete", "clarification") for t in state.director
                ):
                    raise ConflictError("Finish the in-flight turn before changing lifecycle")
                if op == "complete":
                    if state.party.queue or any(e.status == "active" for e in state.encounters):
                        raise ConflictError(
                            "Resolve queued actions and active combat before completion"
                        )
                    from wayfarer.simulation.continuation import AdventureSnapshot

                    if setup.graph is None:
                        raise ValidationError("Missing completed scenario")
                    setup = setup.model_copy(
                        update={
                            "adventures": setup.adventures
                            + (AdventureSnapshot(graph=setup.graph, state=state),)
                        }
                    )
                setup = setup.model_copy(update={"phase": destination})
            if op in ("edit", "invite", "join", "assign", "ready"):
                setup = setup.model_copy(
                    update={
                        "phase": "ready"
                        if all(s.ready and s.joined and s.actor_ids for s in seats)
                        else "draft"
                    }
                )
            setup = setup.model_copy(update={"seats": tuple(seats)})
            revision = campaign["revision"] + 1
            if "play_json" in campaign:
                from wayfarer.simulation.actions import PlayState

                state = PlayState.model_validate_json(campaign["play_json"])
                state = state.model_copy(
                    update={
                        "revision": revision,
                        "resources": state.resources.model_copy(update={"revision": revision}),
                        "lifecycle": setup.phase,
                        "rulings": tuple(
                            r.model_copy(update={"valid_revision": revision})
                            if r.current_status(state.revision, state.resources.game_time)
                            in ("pending", "approved")
                            else r
                            for r in state.rulings
                        ),
                    }
                )
                campaign["play_json"] = state.model_dump_json()
            campaign["revision"] = revision
            campaign["setup_json"] = setup.model_dump_json()
            campaign["complete"] = setup.phase in ("completed", "archived")
            return Event(input=payload, action="setup", outcome=setup.phase, roll=None)

        await self.play.store.commit_turn(
            cid, key, command.expected_revision, payload, resolve, actor_id=principal_id
        )
        return await self.read(cid, principal_id=principal_id)

    def validate(self, setup: Setup) -> None:
        if setup.graph is None or setup.graph.brief != setup.brief:
            raise ValidationError("Select a scenario matching the saved setup brief")
        report = ScenarioStudio(self.play, npc_reviewer=self.play.engine.reviewer).validate(
            setup.graph
        )
        if not report.valid:
            raise ValidationError(
                "; ".join(f.message for f in report.findings if f.severity == "error")
            )

    async def generate(
        self, cid: str, command: SetupCommand, llm: Orchestrator, *, principal_id: str
    ) -> dict[str, object]:
        from wayfarer.orchestration.providers import ProviderRequest
        from wayfarer.simulation.studio import ScenarioGraph

        campaign = await self.play.store.read(cid)
        setup = self.load(campaign)
        self.seat(setup, principal_id)
        if (
            command.operation not in ("edit", "preview")
            or command.brief
            or command.graph
            or command.actor_ids
            or command.principal_id
        ):
            raise ValidationError("Generation uses the saved brief and party")
        generation_id = "generation:" + command.id
        for event in await self.play.store.history(cid):
            if event.command_id == "setup:" + generation_id:
                prior = json.loads(event.event["input"])
                if (
                    prior["principal"] != principal_id
                    or prior["command"]["expected_revision"] != command.expected_revision
                    or prior["command"]["operation"] != command.operation
                ):
                    raise ConflictError("Generation identity already used")
                return await self.read(cid, principal_id=principal_id)
        if setup.host_id != principal_id:
            raise AuthorizationError("Only the host can generate an adventure")
        if (
            setup.phase
            not in (("completed",) if command.operation == "preview" else ("draft", "ready"))
            or campaign["revision"] != command.expected_revision
        ):
            raise ConflictError("Setup changed; reload before generating")
        party = (
            tuple(a for a in setup.graph.actors if a.actor_id not in setup.graph.npc_actor_ids)
            if setup.graph
            else ()
        )
        if command.operation == "preview":
            from wayfarer.simulation.actions import ActorSetup, PlayState

            current_state = PlayState.model_validate_json(campaign["play_json"])
            player_ids = {a for seat in setup.seats for a in seat.actor_ids}
            party = tuple(
                ActorSetup(
                    actor_id=a.actor_id,
                    proposal=a.proposal,
                    aware_of=a.aware_of,
                    conditions=a.conditions,
                    available_at=a.available_at,
                )
                for a in current_state.actors
                if a.actor_id in player_ids
            )
        raw = await llm._call(
            ProviderRequest(
                operation="scenario_draft",
                session_id=f"setup:{cid}:{principal_id}",
                context_json=json.dumps(
                    {
                        "brief": setup.brief.model_dump(mode="json"),
                        "party": [a.model_dump(mode="json") for a in party],
                        "catalog_ids": sorted(self.play.engine.reviewer.compiler.definitions),
                        "continuation": json.loads(campaign["play_json"])
                        if command.operation == "preview"
                        else None,
                        "used_adventure_ids": [a.graph.id for a in setup.adventures],
                        "used_objective_ids": [a.graph.objectives.id for a in setup.adventures],
                    }
                ),
                prompt="Create a playable runtime scenario using only supported catalog mechanics. Preserve the supplied party exactly.",
                output_schema=ScenarioGraph.model_json_schema(),
            )
        )
        graph = ScenarioGraph.model_validate_json(raw)
        if party and {a.actor_id: a.proposal for a in party} != {
            a.actor_id: a.proposal for a in graph.actors if a.actor_id not in graph.npc_actor_ids
        }:
            raise ValidationError("Generated adventure changed the selected party")
        if command.operation == "edit":
            self.validate(setup.model_copy(update={"graph": graph}))
        # Provider failures and late replies leave the saved draft untouched.
        return await self.execute(
            cid,
            command.model_copy(update={"id": generation_id, "graph": graph}),
            principal_id=principal_id,
        )
