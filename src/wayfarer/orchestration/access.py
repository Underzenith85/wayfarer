"""Authorized campaign queries, commands, and perspective-safe resumable streams."""

from __future__ import annotations

import json
from dataclasses import asdict, replace

from wayfarer.engine.simulation.actions import ACTION_ADAPTER, PlayState
from wayfarer.engine.simulation.campaign.access import CampaignMember, StreamEvent
from wayfarer.engine.simulation.campaign.studio import ScenarioGraph
from wayfarer.engine.simulation.combat.engine import hex_template
from wayfarer.engine.simulation.combat.profiles import CombatRules
from wayfarer.engine.simulation.health.fright import projection as fright_projection
from wayfarer.engine.simulation.resources import wire_weight
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.combat import COMBAT_ADAPTER, CombatService
from wayfarer.orchestration.encounter_scenes import EncounterSceneService, MigrateEncounterScenes
from wayfarer.orchestration.fright import FrightDecision, FrightService
from wayfarer.orchestration.fright_builds import FrightBuildService
from wayfarer.orchestration.medical import EnvironmentResolver
from wayfarer.orchestration.membership import member_for, require_control
from wayfarer.orchestration.noncombat import NoncombatCommand, NoncombatService
from wayfarer.orchestration.npcs import NPCProposal, NPCService
from wayfarer.orchestration.objectives import ObjectiveCommand, ObjectiveService
from wayfarer.orchestration.origins import origin_scope
from wayfarer.orchestration.party import PartyCommand, PartyService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.player_medical import PlayerRecoveryCommand
from wayfarer.orchestration.player_medical import choices as medical_choices
from wayfarer.orchestration.player_medical import execute as execute_medical
from wayfarer.orchestration.recovery import RecoveryCommand, RecoveryService, guard
from wayfarer.orchestration.scenes import SCENE_ADAPTER, SceneService
from wayfarer.orchestration.tactical_view import legacy_encounter
from wayfarer.persistence.events import CommandOrigin


class CampaignAccess:
    def __init__(
        self, play: PlayService, medical_environment: EnvironmentResolver | None = None
    ) -> None:
        self.play = play
        self.medical_environment = medical_environment

    async def runtime(self, cid: str) -> CampaignAccess:
        """Reconstruct an activated scenario's pinned runtime after restart."""
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        return self if play is self.play else CampaignAccess(play, self.medical_environment)

    @staticmethod
    def _member(state: PlayState, principal_id: str) -> CampaignMember:
        return member_for(state, principal_id)

    @staticmethod
    def _projection(
        state: PlayState, member: CampaignMember, rules: CombatRules | None = None
    ) -> dict[str, object]:

        if member.role == "gm":
            return {
                "campaign_id": state.campaign_id,
                "lifecycle": state.lifecycle,
                "revision": state.revision,
                "game_time": state.resources.game_time,
                "role": member.role,
                "actors": tuple(actor.actor_id for actor in state.actors),
                "world": asdict(state.world),
                "fright": fright_projection(state.resources, (), director=True),
            }
        perspectives: dict[str, object] = {}
        for actor_id in member.actor_ids:
            own = next(e for e in state.world.entities if e.id == actor_id)
            perspective = state.world.perspective(actor_id)
            known = {f.id for f in perspective.facts}
            perspective = replace(
                perspective,
                commitments=tuple(
                    c
                    for c in perspective.commitments
                    if c.reveal_fact_id in known
                    or (c.reveal_fact_id is None and actor_id in (c.debtor_id, c.creditor_id))
                ),
            )
            perspective = replace(
                perspective,
                entities=tuple(
                    e
                    if e.id == actor_id or e.location_id == own.location_id
                    else replace(e, location_id=None, owner_id=None)
                    for e in perspective.entities
                ),
            )
            perspectives[actor_id] = asdict(perspective)
        groups = tuple(g for g in state.party.groups if set(g.actor_ids) & set(member.actor_ids))
        visible_objectives = state.objectives.model_dump(
            mode="json", exclude={"evidence", "settled_reward_ids"}
        )
        visible_objectives["progress"] = tuple(
            {"objective_id": e.objective_id, "satisfied": e.satisfied}
            for e in state.objectives.evidence
            if not e.visible_to or set(e.visible_to) & set(member.actor_ids)
        )
        return {
            "campaign_id": state.campaign_id,
            "lifecycle": state.lifecycle,
            "principal_id": member.principal_id,
            "shared_time": len(state.party.groups) > 1,
            "rulings": tuple(
                r.model_dump(
                    mode="json",
                    include={"id", "actor_id", "status", "alternatives", "selected_id", "reason"},
                )
                for r in state.rulings
                if r.actor_id in member.actor_ids
            ),
            "director": tuple(
                t.model_dump(
                    mode="json",
                    exclude={
                        "command_json",
                        "request_json",
                        "session_id",
                        "principal_id",
                        "outcome_json",
                    },
                )
                for t in state.director
                if t.actor_id in member.actor_ids
            ),
            "journal": tuple(
                e.model_dump(mode="json") for e in state.journal if e.actor_id in member.actor_ids
            ),
            "scene_cursors": tuple(
                e.model_dump(mode="json")
                for e in state.actor_scenes
                if e.actor_id in member.actor_ids
            ),
            "encounters": tuple(
                legacy_encounter(
                    state,
                    e,
                    member,
                    board=hex_template(e, rules) if e.spatial_kind == "hex" else None,
                )
                for e in state.encounters
                if set(e.turn_order) & set(member.actor_ids)
            ),
            "resolution": state.last_result.model_dump(mode="json")
            if state.last_result
            and any(
                t.actor_id in member.actor_ids
                and t.command_json
                and json.loads(t.command_json).get("id") == state.last_result.command_id
                for t in state.director
            )
            else None,
            "revision": state.revision,
            "game_time": state.resources.game_time,
            "role": member.role,
            "actors": member.actor_ids,
            "fright": fright_projection(state.resources, member.actor_ids),
            "inventory": tuple(
                i.model_dump(mode="json")
                for i in state.resources.items
                if i.owner_id in member.actor_ids
            ),
            "status": tuple(
                a.model_dump(mode="json", include={"actor_id", "conditions", "available_at"})
                for a in state.actors
                if a.actor_id in member.actor_ids
            ),
            "pools": tuple(
                p.model_dump(mode="json")
                for p in state.resources.pools
                if any(p.id == f"{kind}:{a}" for a in member.actor_ids for kind in ("hp", "fp"))
            ),
            "perspectives": perspectives,
            "subgroups": tuple(g.model_dump(mode="json") for g in groups),
            "pending_activities": tuple(
                q.model_dump(mode="json", exclude={"command_json"})
                for q in state.party.queue
                if q.actor_id in member.actor_ids
            ),
            "activity_receipts": tuple(
                r.model_dump(mode="json")
                for r in state.party.receipts
                if r.actor_id in member.actor_ids
            ),
            "noncombat": tuple(
                e.model_dump(mode="json") for e in state.noncombat if e.actor_id in member.actor_ids
            ),
            "objectives": visible_objectives,
            "captivity": tuple(
                c.model_dump(mode="json")
                for c in state.recovery.captivity
                if c.actor_id in member.actor_ids
            ),
            "recovery_decisions": tuple(
                d.model_dump(mode="json")
                for d in state.recovery.decisions
                if d.actor_id in member.actor_ids
            ),
            "dead_actor_ids": tuple(
                a for a in state.recovery.dead_actor_ids if a in member.actor_ids
            ),
        }

    async def read(self, cid: str, *, principal_id: str) -> dict[str, object]:
        runtime = await self.runtime(cid)
        if runtime is not self:
            return await runtime.read(cid, principal_id=principal_id)
        state = self.play._load(await self.play.store.read(cid))
        member = self._member(state, principal_id)
        projection = self._projection(state, member, self.play.engine.rules.combat)
        if member.role != "player":
            return projection
        compiler = self.play.engine.reviewer.compiler
        projection["characters"] = tuple(
            {
                "actor_id": a.actor_id,
                "name": a.proposal.draft.name,
                "values": tuple(
                    {"target": v.target, "value": str(v.value)} for v in build.sheet.values
                ),
                "spent": build.spent,
            }
            for a in state.actors
            if a.actor_id in member.actor_ids
            if (build := compiler.compile(a.proposal.draft).build) is not None
        )
        projection["equipment"] = tuple(
            {
                "id": i.id,
                "name": compiler.definitions[i.definition_id].name,
                "unit_weight": wire_weight(
                    self.play.engine.resources.specs[i.definition_id].unit_weight
                ),
            }
            for i in state.resources.items
            if i.owner_id in member.actor_ids
        )
        projection["scenes"] = tuple(
            {
                "actor_id": cursor.actor_id,
                "id": scene.id,
                "title": scene.title,
                "exits": tuple(
                    {"id": e.id, "destination_id": e.destination_id}
                    for e in scene.exits
                    if set(e.required_fact_ids)
                    <= {f.id for f in state.world.perspective(cursor.actor_id).facts}
                ),
            }
            for cursor in state.actor_scenes
            if cursor.actor_id in member.actor_ids
            for scene in (
                self.play.engine.rules.scenes.scenes if self.play.engine.rules.scenes else ()
            )
            if scene.id == cursor.scene_id
        )

        choices: list[dict[str, object]] = []
        recovery = RecoveryService(self.play)
        for option in (
            self.play.engine.rules.recovery.options if self.play.engine.rules.recovery else ()
        ):
            for actor_id in member.actor_ids:
                visible = {e.id for e in state.world.perspective(actor_id).entities}
                for target_id in option.target_actor_ids:
                    if target_id not in visible or not option.supported:
                        continue
                    candidate = RecoveryCommand(
                        id="preview",
                        actor_id=actor_id,
                        expected_revision=state.revision,
                        kind="choose_recovery",
                        rule_id=option.id,
                        target_actor_id=target_id,
                    )
                    try:
                        recovery.assess(state, candidate)
                    except ValidationError, ConflictError:
                        continue
                    choices.append(
                        {
                            "id": option.id,
                            "kind": option.kind,
                            "actor_id": actor_id,
                            "target_actor_id": target_id,
                        }
                    )
        projection["recovery_choices"] = choices

        medical, medical_tasks, _private = medical_choices(
            self.play, state, member.actor_ids, self.medical_environment
        )
        projection["gurps_recovery_choices"] = medical
        projection["gurps_recovery_tasks"] = medical_tasks

        scene_choices: list[dict[str, object]] = []
        entities = {e.id: e for e in state.world.entities}
        for actor in state.actors:
            if actor.actor_id not in member.actor_ids:
                continue
            for check in self.play.engine.rules.checks:
                target = entities[check.target_id]
                if (
                    check.action == "inspect"
                    and check.target_id in actor.aware_of
                    and target.location_id == entities[actor.actor_id].location_id
                ):
                    scene_choices.append(
                        {
                            "id": actor.actor_id + ":" + check.id,
                            "actor_id": actor.actor_id,
                            "label": "Inspect: " + target.name,
                            "command": {"kind": "inspect", "target_id": target.id},
                        }
                    )
            if self.play.engine.rules.noncombat:
                cursor = next(c for c in state.actor_scenes if c.actor_id == actor.actor_id)
                for rule in self.play.engine.rules.noncombat.encounters:
                    encounter_id = actor.actor_id + ":" + rule.id
                    if rule.scene_id == cursor.scene_id and not any(
                        e.id == encounter_id for e in state.noncombat
                    ):
                        scene_choices.append(
                            {
                                "id": encounter_id,
                                "actor_id": actor.actor_id,
                                "label": "Begin: " + rule.id,
                                "command": {
                                    "kind": "start_noncombat",
                                    "selection_id": rule.id,
                                    "encounter_id": encounter_id,
                                },
                            }
                        )
        projection["scene_choices"] = scene_choices
        return projection

    async def execute(
        self, cid: str, value: object, *, principal_id: str, origin: CommandOrigin | None = None
    ) -> dict[str, object]:
        with origin_scope(origin):
            return await self._execute(cid, value, principal_id=principal_id)

    async def _execute(self, cid: str, value: object, *, principal_id: str) -> dict[str, object]:
        runtime = await self.runtime(cid)
        if runtime is not self:
            return await runtime._execute(cid, value, principal_id=principal_id)
        state = self.play._load(await self.play.store.read(cid))
        member = self._member(state, principal_id)
        if not isinstance(value, dict):
            raise ValidationError("Invalid typed campaign command")
        if state.lifecycle != "active":
            raise ConflictError("Resume an active campaign before acting")
        kind = value.get("kind")

        if (
            isinstance(value.get("actor_id"), str)
            and isinstance(kind, str)
            and kind
            not in (
                "gurps_recovery",
                "care",
                "panic-response",
                "propose_fright_build",
                "approve_fright_build",
                "take_combat_turn",
                "take_unarmed_turn",
                "choose_defense",
                "resume_interrupted_turn",
            )
        ):
            guard(state, str(value["actor_id"]), kind)
        raw = json.dumps(value)
        try:
            if kind in (
                "propose_fright_build",
                "approve_fright_build",
            ):
                await FrightBuildService(self.play).execute(cid, value, principal_id=principal_id)
            elif kind in ("care", "panic-response"):
                await FrightService(self.play).execute(
                    cid,
                    FrightDecision.model_validate_json(raw),
                    authenticated_gm_id=principal_id,
                )
            elif kind == "migrate_encounter_scenes":
                migration = MigrateEncounterScenes.model_validate_json(raw)
                if member.role != "gm":
                    raise AuthorizationError("Encounter scene migration requires GM authority")
                self._control(member, migration.actor_id)
                await EncounterSceneService(self.play).execute(
                    cid, migration, authenticated_gm_id=migration.actor_id
                )
            elif kind == "gurps_recovery":
                player_recovery = PlayerRecoveryCommand.model_validate_json(raw)
                self._control(member, player_recovery.actor_id)
                await execute_medical(
                    self.play,
                    state,
                    player_recovery,
                    controlled_actor_ids=member.actor_ids,
                    environment=self.medical_environment,
                )
            elif kind in ("request_ruling", "decide_ruling", "execute_ruling"):
                # deferred: access -> adjudication -> play -> npcs -> providers -> access.
                # The provider needs an access handle to answer a director's question.
                from wayfarer.orchestration.adjudication import RULING_ADAPTER, AdjudicationService

                ruling = RULING_ADAPTER.validate_json(raw)
                self._control(member, ruling.actor_id)
                await AdjudicationService(self.play).submit(
                    cid, ruling, authenticated_actor_id=ruling.actor_id
                )
            elif kind in ("apply_setback", "choose_recovery"):
                recovery_command = RecoveryCommand.model_validate_json(raw)
                self._control(member, recovery_command.actor_id)
                await RecoveryService(self.play).execute(
                    cid, recovery_command, authenticated_actor_id=recovery_command.actor_id
                )
            elif kind == "propose_npc":
                proposal = NPCProposal.model_validate_json(raw)
                self._control(member, proposal.actor_id)
                await NPCService(self.play).propose(
                    cid, proposal, authenticated_gm_id=proposal.actor_id
                )
            elif kind in (
                "start_encounter",
                "start_basic_encounter",
                "declare_basic_spatial_facts",
                "take_combat_turn",
                "resume_interrupted_turn",
                "choose_defense",
                "end_encounter",
                "join_encounter",
                "withdraw_encounter",
                "migrate_encounter_hex",
                "migrate_encounter_basic",
            ):
                combat = COMBAT_ADAPTER.validate_json(raw)

                campaign = await self.play.store.read(cid)
                graph = (
                    ScenarioGraph.model_validate_json(campaign["scenario_graph_json"])
                    if "scenario_graph_json" in campaign
                    else None
                )
                if not (member.role == "gm" and graph and combat.actor_id in graph.npc_actor_ids):
                    self._control(member, combat.actor_id)
                await CombatService(self.play).execute(
                    cid, combat, authenticated_actor_id=combat.actor_id
                )
            elif kind in ("observe_scene", "travel_scene"):
                scene = SCENE_ADAPTER.validate_json(raw)
                self._control(member, scene.actor_id)
                await SceneService(self.play).execute(
                    cid, scene, authenticated_actor_id=scene.actor_id
                )
            elif kind in ("start_noncombat", "approach_noncombat", "withdraw_noncombat"):
                noncombat = NoncombatCommand.model_validate_json(raw)
                self._control(member, noncombat.actor_id)
                await NoncombatService(self.play).execute(
                    cid, noncombat, authenticated_actor_id=noncombat.actor_id
                )
            elif kind in ("evaluate_objectives", "abandon_scenario"):
                objective = ObjectiveCommand.model_validate_json(raw)
                self._control(member, objective.actor_id)
                await ObjectiveService(self.play).execute(
                    cid, objective, authenticated_actor_id=objective.actor_id
                )
            elif kind in (
                "split_party",
                "rejoin_party",
                "queue_activity",
                "pause_group",
                "resume_group",
                "signal_scene",
                "transfer_item",
            ):
                party = PartyCommand.model_validate_json(raw)
                self._control(member, party.actor_id)
                await PartyService(self.play).execute(
                    cid, party, authenticated_actor_id=party.actor_id
                )
            else:
                command = ACTION_ADAPTER.validate_json(raw)
                self._control(member, command.actor_id)
                await self.play.execute(cid, command, authenticated_actor_id=command.actor_id)
        except ValueError as exc:
            raise ValidationError("Invalid typed campaign command") from exc
        return await self.read(cid, principal_id=principal_id)

    @staticmethod
    def _control(member: CampaignMember, actor_id: str) -> None:
        require_control(member, actor_id)

    async def events(
        self, cid: str, *, principal_id: str, after: int = 0, limit: int = 100
    ) -> tuple[StreamEvent, ...]:
        runtime = await self.runtime(cid)
        if runtime is not self:
            return await runtime.events(cid, principal_id=principal_id, after=after, limit=limit)
        if after < 0 or not 1 <= limit <= 100:
            raise ValidationError("Invalid stream cursor or limit")
        current = self.play._load(await self.play.store.read(cid))
        member = self._member(current, principal_id)
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
                    projection=self._projection(state, member, self.play.engine.rules.combat),
                )
            )
            if len(result) == limit:
                break
        return tuple(result)
