"""Transactional power approvals and typed actions. No model calls in transactions."""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from typing import TYPE_CHECKING

from pydantic import Field
from pydantic import ValidationError as SchemaError

from wayfarer.character.compiler import pool_limits
from wayfarer.character.physical_traits import physical_traits
from wayfarer.character.power import Approval
from wayfarer.errors import ValidationError
from wayfarer.models import Campaign, Event, Record, Roll
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.postgres import AsyncPostgresStore
from wayfarer.rules.catalog import reference
from wayfarer.rules.checks import Outcome, RandomSource
from wayfarer.rules.injury_types import InjuryStatus
from wayfarer.rules.recovery_types import FatigueStatus
from wayfarer.simulation.access import CampaignMember
from wayfarer.simulation.actions import (
    ACTION_ADAPTER,
    ActionEngine,
    ActionResult,
    ActorSetup,
    PlayActor,
    PlayState,
    TypedAction,
)
from wayfarer.simulation.adjudication import expire_rulings
from wayfarer.simulation.resources import Pool, ResourceState
from wayfarer.simulation.scenes import ActorScene, JournalEntry, SceneEvent
from wayfarer.world import World

if TYPE_CHECKING:
    from wayfarer.orchestration.profiles import ProfileRuntime


class ApproveCharacter(Record):
    id: str = Field(min_length=1, max_length=100)
    actor_id: str = Field(min_length=1, max_length=100)
    target_actor_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=2000)


class PlayService:
    def __init__(
        self,
        store: AsyncSQLiteStore | AsyncPostgresStore,
        engine: ActionEngine,
        *,
        rng: RandomSource = secrets,
        profiles: ProfileRuntime | None = None,
    ) -> None:
        self.store, self.engine, self.rng = store, engine, rng
        # When present, campaigns pinned to another registered profile dispatch to
        # that profile's service. Without a runtime, mismatched pins fail closed.
        self.profiles = profiles

    def for_campaign(self, campaign: Campaign) -> PlayService:
        """Dispatch to the campaign's exact registered profile, then bind its scenario."""
        if self.profiles is not None and campaign.get("rules_ref") != reference(
            self.engine.resources.rules
        ):
            return self.profiles.for_campaign(campaign)
        return self.bind(campaign)

    def bind(self, campaign: Campaign) -> PlayService:
        """Bind a saved scenario without sharing mutable per-campaign runtime state."""
        from wayfarer.simulation.social_policy import parse_graph

        encoded = campaign.get("scenario_graph_json")
        if encoded is None:
            return self
        graph = parse_graph(encoded)
        engine = ActionEngine(
            self.engine.reviewer,
            self.engine.resources.for_world(graph.world),
            graph.runtime_rules(),
        )
        if (
            engine.digest == self.engine.digest
            and engine.resources.actors == self.engine.resources.actors
        ):
            return self
        return PlayService(self.store, engine, rng=self.rng, profiles=self.profiles)

    def initial_state(
        self,
        campaign: Campaign,
        world: World,
        resources: ResourceState,
        actors: tuple[ActorSetup, ...],
        members: tuple[CampaignMember, ...] | None = None,
    ) -> PlayState:
        """Trusted scenario input; player drafts never supply approval records."""
        if campaign["revision"] != 0 or resources.revision != 0:
            raise ValidationError("Initial revisions must be zero")
        if any(
            event.id.startswith(("ability:", "spell:", "spell-backfire:", "mana-refund:"))
            for event in resources.events
        ):
            raise ValidationError("Initial resources cannot seed supernatural execution receipts")
        if campaign.get("rules_ref") != reference(self.engine.resources.rules):
            raise ValidationError("Campaign rules do not match the play engine")
        if "resources_json" in campaign or "play_json" in campaign:
            raise ValidationError("Campaign already has an engine checkpoint")
        from wayfarer.rules.physical_traits import NO_PHYSICAL_TRAITS

        if any(
            p.injury is not None
            and (p.injury.physical_traits != NO_PHYSICAL_TRAITS or p.injury.surprise is not None)
            for p in resources.pools
        ):
            raise ValidationError(
                "Initial resources cannot seed physical trait projections or surprise"
            )
        approvals: list[Approval] = []
        play_actors: list[PlayActor] = []
        owners = {o.actor_id: o for o in resources.owners}
        pools = {p.id: p for p in resources.pools}
        for actor in actors:
            if actor.actor_id not in owners:
                raise ValidationError("Play actor needs a resource owner")
            review = self.engine.reviewer.review(actor.proposal)
            build = review.compilation.build
            if build is None:
                raise ValidationError("Scenario character is point-illegal")
            values = {v.target: int(v.value) for v in build.sheet.values}
            if not {"attribute:st", "attribute:ht"} <= values.keys():
                raise ValidationError("Character has no supported runtime resources")
            owners[actor.actor_id] = owners[actor.actor_id].model_copy(
                update={"definitions": tuple(p.definition_id for p in build.purchases)}
            )
            for name, maximum in pool_limits(build).items():
                key = f"{name}:{actor.actor_id}"
                injury = None
                fatigue = None
                profile = self.engine.reviewer.compiler.statistics_profile
                if name == "hp" and profile in ("gurps-lite-4e-2004", "gurps-basic-set-4e-2004"):
                    injury = InjuryStatus.model_validate(
                        {
                            "profile_id": profile,
                            "physical_traits": physical_traits(
                                build, self.engine.reviewer.compiler.definitions
                            ),
                            "anatomy": actor.body.anatomy if actor.body else None,
                            "male_groin": actor.body.male_groin if actor.body else False,
                            "tolerance": actor.body.tolerance if actor.body else None,
                        }
                    )
                if name == "fp" and profile in ("gurps-lite-4e-2004", "gurps-basic-set-4e-2004"):
                    fatigue = FatigueStatus.model_validate({"profile_id": profile})
                pools[key] = Pool(
                    id=key, current=maximum, maximum=maximum, injury=injury, fatigue=fatigue
                )
            approval = None
            if review.status == "automatic":
                approval = self.engine.reviewer.approve(
                    actor.proposal, campaign_id=campaign["id"], actor_id=actor.actor_id, revision=0
                )
                approvals.append(approval)
            play_actors.append(
                PlayActor(
                    actor_id=actor.actor_id,
                    proposal=actor.proposal,
                    aware_of=actor.aware_of,
                    conditions=actor.conditions,
                    available_at=actor.available_at,
                    body=actor.body,
                    held_item_hands=actor.held_item_hands,
                    approval=approval,
                )
            )
        resources = resources.model_copy(
            update={"owners": tuple(owners.values()), "pools": tuple(pools.values())}
        )
        if members is None:
            members = tuple(
                CampaignMember(
                    principal_id=actor.actor_id, role="player", actor_ids=(actor.actor_id,)
                )
                for actor in actors
            ) + tuple(
                CampaignMember(principal_id=gm_id, role="gm")
                for gm_id in sorted(self.engine.reviewer.gm_ids)
                if gm_id not in {actor.actor_id for actor in actors}
            )
        actor_scenes: tuple[ActorScene, ...] = ()
        scene_events: tuple[SceneEvent, ...] = ()
        journal: tuple[JournalEntry, ...] = ()
        fired_scene_triggers: tuple[str, ...] = ()
        if self.engine.rules.scenes is not None:
            rules = self.engine.rules.scenes
            rules.validate_world(world)
            by_location = {scene.location_id: scene for scene in rules.scenes}
            cursors: list[ActorScene] = []
            events: list[SceneEvent] = []
            entries: list[JournalEntry] = []
            fired: set[str] = set()
            for actor in actors:
                entity = next(entity for entity in world.entities if entity.id == actor.actor_id)
                scene = by_location.get(entity.location_id or "")
                if scene is None:
                    raise ValidationError("Actor location has no configured scene")
                cursors.append(ActorScene(actor_id=actor.actor_id, scene_id=scene.id))
                events.append(
                    SceneEvent(
                        id=f"initial:{actor.actor_id}",
                        actor_id=actor.actor_id,
                        scene_id=scene.id,
                        kind="entered",
                        at=0,
                    )
                )
                for discovery in rules.discoveries:
                    if discovery.scene_id == scene.id and discovery.mode == "automatic":
                        world = world.learn(actor.actor_id, discovery.fact_id)
                        entries.append(
                            JournalEntry(
                                id=f"initial:{actor.actor_id}:{discovery.id}",
                                actor_id=actor.actor_id,
                                scene_id=scene.id,
                                fact_id=discovery.fact_id,
                                at=0,
                            )
                        )
                for trigger in rules.triggers:
                    if (
                        trigger.scene_id == scene.id
                        and trigger.phase == "entry"
                        and trigger.id not in fired
                    ):
                        world = world.learn(actor.actor_id, trigger.fact_id)
                        fired.add(trigger.id)
            actor_scenes, scene_events, journal = tuple(cursors), tuple(events), tuple(entries)
            fired_scene_triggers = tuple(sorted(fired))
        state = PlayState(
            campaign_id=campaign["id"],
            configuration_digest=self.engine.digest,
            world=world,
            resources=resources,
            actors=tuple(play_actors),
            approvals=tuple(approvals),
            members=members,
            actor_scenes=actor_scenes,
            scene_events=scene_events,
            journal=journal,
            fired_scene_triggers=fired_scene_triggers,
        )
        if self.engine.rules.party is not None:
            from wayfarer.simulation.party import migrate

            state = migrate(state)
        from wayfarer.orchestration.npcs import initialize

        state = initialize(self, state)
        self.engine.validate(state)
        return state

    async def create(
        self,
        campaign: Campaign,
        world: World,
        resources: ResourceState,
        actors: tuple[ActorSetup, ...],
        members: tuple[CampaignMember, ...] | None = None,
    ) -> PlayState:
        state = self.initial_state(campaign, world, resources, actors, members)
        stored = campaign.copy()
        stored["play_json"] = state.model_dump_json()
        await self.store.insert(stored)
        return state

    def _load(self, campaign: Campaign) -> PlayState:
        if campaign.get("rules_ref") != reference(self.engine.resources.rules):
            raise ValidationError("Campaign rules do not match the play engine")
        raw = campaign.get("play_json")
        if raw is None:
            raise ValidationError("Campaign has no typed play state")
        state = PlayState.model_validate_json(raw)
        if state.campaign_id != campaign["id"] or state.revision != campaign["revision"]:
            raise ValidationError("Campaign and play checkpoint disagree")
        if self.engine.rules.party is not None:
            from wayfarer.simulation.party import migrate

            state = migrate(state)
        from wayfarer.simulation.encounter_context import migrate_unique

        state = migrate_unique(state, self.engine.rules.scenes, self.engine.rules.combat)
        self.engine.validate(state)
        return state

    def checkpoint(
        self, state: PlayState, *, before: PlayState | None = None, run_npcs: bool = True
    ) -> PlayState:
        from wayfarer.orchestration.npcs import checkpoint as npc_checkpoint
        from wayfarer.orchestration.objectives import checkpoint
        from wayfarer.orchestration.spell_backfires import perceive, recover_stuns
        from wayfarer.orchestration.spell_effects import checkpoint as spell_checkpoint
        from wayfarer.simulation.spell_backfires import refund_due

        resources = state.resources
        for actor in state.actors:
            resources = refund_due(resources, actor.actor_id)
        state = state.model_copy(update={"resources": resources})
        from wayfarer.orchestration.held_missiles import checkpoint as held_checkpoint
        from wayfarer.orchestration.held_missiles import concentration_checkpoint

        if before is not None:
            state = concentration_checkpoint(self, state, before)
            state = held_checkpoint(self, state, before)
        before_fire = state
        state = spell_checkpoint(self, state)
        state = perceive(state)
        state = recover_stuns(self, state)
        state = concentration_checkpoint(self, state, before_fire)
        state = held_checkpoint(self, state, before_fire)
        if run_npcs:
            state = npc_checkpoint(self, state)
        return checkpoint(self, state, before=before)

    @staticmethod
    def propose(value: object) -> TypedAction:
        try:
            return ACTION_ADAPTER.validate_python(value)
        except SchemaError as exc:
            raise ValidationError("Invalid typed action proposal") from exc

    @staticmethod
    def _authorize(command: TypedAction, authenticated_actor_id: str) -> None:
        if command.actor_id != authenticated_actor_id:
            raise ValidationError("Command actor is not authorized")

    async def preview(
        self, cid: str, value: object, *, authenticated_actor_id: str
    ) -> ActionResult:
        command = self.propose(value)
        self._authorize(command, authenticated_actor_id)
        return self.engine.assess(self._load(await self.store.read(cid)), command)

    async def execute(
        self,
        cid: str,
        value: object,
        *,
        authenticated_actor_id: str,
        authorize: Callable[[Campaign], None] | None = None,
    ) -> ActionResult:
        command = self.propose(value)
        self._authorize(command, authenticated_actor_id)
        payload = json.dumps(
            {"operation": "typed-action", "command": command.model_dump(mode="json")},
            sort_keys=True,
            separators=(",", ":"),
        )
        # Read the checkpoint before checking the receipt. If an identical command
        # commits during either read, duplicate() or commit_turn() returns its result;
        # assessment must not observe the newer revision after a receipt miss.
        checkpoint = await self.store.read(cid)
        duplicate = await self.store.duplicate(cid, command.id, payload)
        if duplicate is not None:
            result = self._load(duplicate).last_result
            if result is None:
                raise ValidationError("Missing committed action result")
            return result
        feasible = self.engine.assess(self._load(checkpoint), command)
        if feasible.status != "feasible":
            return feasible

        def resolve(campaign: Campaign) -> Event:
            from wayfarer.simulation.party import synchronous

            if authorize is not None:
                authorize(campaign)
            current = self._load(campaign)
            synchronous(current, command.actor_id)
            state, result = self.engine.resolve(current, command, rng=self.rng)
            if result.status != "committed":
                raise ValidationError("Action is no longer feasible")
            state = self.checkpoint(state, before=current)
            self.engine.validate(state)
            campaign["play_json"] = state.model_dump_json()
            campaign["revision"] = state.revision
            roll: Roll | None = None
            if result.check is not None:
                check = result.check
                roll = Roll(
                    dice=list(check.dice),
                    total=check.total,
                    target=check.effective_target,
                    success=check.outcome in (Outcome.SUCCESS, Outcome.CRITICAL_SUCCESS),
                    critical="success"
                    if check.outcome is Outcome.CRITICAL_SUCCESS
                    else "failure"
                    if check.outcome is Outcome.CRITICAL_FAILURE
                    else None,
                )
            return Event(
                input=payload, action="typed-action", outcome=result.model_dump_json(), roll=roll
            )

        committed = await self.store.commit_turn(
            cid, command.id, command.expected_revision, payload, resolve, actor_id=command.actor_id
        )
        result = self._load(committed["state"]).last_result
        if result is None:
            raise ValidationError("Missing committed action result")
        return result

    async def approve(self, cid: str, value: object, *, authenticated_gm_id: str) -> Approval:
        try:
            command = ApproveCharacter.model_validate(value)
        except SchemaError as exc:
            raise ValidationError("Invalid approval command") from exc
        if (
            command.actor_id != authenticated_gm_id
            or authenticated_gm_id not in self.engine.reviewer.gm_ids
        ):
            raise ValidationError("Approval requires GM authority")
        payload = json.dumps(
            {"operation": "power-approval", "command": command.model_dump(mode="json")},
            sort_keys=True,
            separators=(",", ":"),
        )

        def resolve(campaign: Campaign) -> Event:
            state = self._load(campaign)
            actor = next((a for a in state.actors if a.actor_id == command.target_actor_id), None)
            if actor is None:
                raise ValidationError("Unknown approval target")
            approval = self.engine.reviewer.approve(
                actor.proposal,
                campaign_id=cid,
                actor_id=actor.actor_id,
                revision=state.revision + 1,
                approver_id=authenticated_gm_id,
                reason=command.reason,
            )
            updated = state.model_copy(
                update={
                    "revision": state.revision + 1,
                    "resources": state.resources.model_copy(
                        update={"revision": state.revision + 1}
                    ),
                    "actors": tuple(
                        a.model_copy(update={"approval": approval})
                        if a.actor_id == actor.actor_id
                        else a
                        for a in state.actors
                    ),
                    "approvals": state.approvals + (approval,),
                    "rulings": expire_rulings(
                        state.rulings, state.revision + 1, state.resources.game_time
                    ),
                }
            )
            self.engine.validate(updated)
            campaign["revision"], campaign["play_json"] = (
                updated.revision,
                updated.model_dump_json(),
            )
            return Event(
                input=payload,
                action="power-approval",
                outcome=approval.model_dump_json(),
                roll=None,
            )

        committed = await self.store.commit_turn(
            cid,
            command.id,
            command.expected_revision,
            payload,
            resolve,
            actor_id=authenticated_gm_id,
        )
        state = self._load(committed["state"])
        approval = next(a.approval for a in state.actors if a.actor_id == command.target_actor_id)
        if approval is None:
            raise ValidationError("Missing committed approval")
        return approval
