"""Transactional power approvals and typed actions. No model calls in transactions."""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from typing import TYPE_CHECKING

from pydantic import Field
from pydantic import ValidationError as SchemaError

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.character.compiler import pool_limits
from wayfarer.engine.character.power import Approval
from wayfarer.engine.character.traits.physical import physical_traits
from wayfarer.engine.rules.catalog import reference
from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.rules.traits.physical import NO_PHYSICAL_TRAITS
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.recovery import FatigueStatus
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import (
    ACTION_ADAPTER,
    ActionResult,
    ActorSetup,
    PlayActor,
    PlayState,
    TypedAction,
)
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.campaign.adjudication import expire_rulings
from wayfarer.engine.simulation.campaign.encounter_context import migrate_unique
from wayfarer.engine.simulation.campaign.party import migrate, synchronous
from wayfarer.engine.simulation.campaign.scenario_references import verify
from wayfarer.engine.simulation.campaign.scenes import ActorScene, JournalEntry, SceneEvent
from wayfarer.engine.simulation.campaign.social_policy import parse_graph
from wayfarer.engine.simulation.combat.profiles import CombatRules
from wayfarer.engine.simulation.combat.sensory_state import checkpoint as sensory_checkpoint
from wayfarer.engine.simulation.events import action_result
from wayfarer.engine.simulation.magic.analyze_magic_state import pending_actor_ids
from wayfarer.engine.simulation.magic.analyze_magic_work import checkpoint as analysis_checkpoint
from wayfarer.engine.simulation.magic.area_fire import checkpoint as spell_checkpoint
from wayfarer.engine.simulation.magic.backfire_transitions import perceive, recover_stuns
from wayfarer.engine.simulation.magic.backfires import refund_due
from wayfarer.engine.simulation.magic.detect_magic_state import pending_actor_ids as detect_pending
from wayfarer.engine.simulation.magic.detect_magic_work import checkpoint as detection_checkpoint
from wayfarer.engine.simulation.magic.enchanting_lifecycle import (
    checkpoint as enchanting_checkpoint,
)
from wayfarer.engine.simulation.magic.great_haste_effects import (
    checkpoint as great_haste_checkpoint,
)
from wayfarer.engine.simulation.magic.haste_effects import checkpoint as haste_checkpoint
from wayfarer.engine.simulation.magic.held_missiles import checkpoint as held_checkpoint
from wayfarer.engine.simulation.magic.held_missiles import concentration_checkpoint
from wayfarer.engine.simulation.magic.item_state import checkpoint as item_magic_checkpoint
from wayfarer.engine.simulation.magic.power_lifecycle import checkpoint as power_checkpoint
from wayfarer.engine.simulation.magic.power_wearer import checkpoint as wearer_checkpoint
from wayfarer.engine.simulation.magic.staff_casting_state import checkpoint as staff_checkpoint
from wayfarer.engine.simulation.resources import Advance, Pool, ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.traits.size_geometry import checkpoint as size_geometry_checkpoint
from wayfarer.engine.world import World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record
from wayfarer.orchestration.clock import CommandInstant, capture_instant
from wayfarer.orchestration.cyclic_clock import advance as advance_cyclic_clock
from wayfarer.orchestration.entropy import CommandRandom, SeedSource, token_seed
from wayfarer.orchestration.great_haste_budget import checkpoint as settle_haste_budget
from wayfarer.orchestration.npcs import checkpoint as npc_checkpoint
from wayfarer.orchestration.npcs import initialize
from wayfarer.orchestration.objectives import checkpoint as objective_checkpoint
from wayfarer.orchestration.pipeline import ActsAs, CommandPlan, Seats, Trusted, submit
from wayfarer.orchestration.sessions import SessionRegistry
from wayfarer.orchestration.symptom_generations import correct_symptom_attributes
from wayfarer.orchestration.task_records import PRIVATE_PREFIXES as TASK_PRIVATE_PREFIXES
from wayfarer.orchestration.transformations import shapeshifting_checkpoint
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.postgres import AsyncPostgresStore

if TYPE_CHECKING:
    from wayfarer.orchestration.profiles import ProfileRuntime


def _require_analysis_free(state: PlayState, command: TypedAction) -> None:
    if command.kind not in ("question", "wait") and command.actor_id in (
        pending_actor_ids(state.resources) | detect_pending(state.resources)
    ):
        raise ConflictError("Finish or cancel Analyze Magic before acting")


class ApproveCharacter(Record):
    id: str = Field(min_length=1, max_length=100)
    actor_id: str = Field(min_length=1, max_length=100)
    target_actor_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=2000)


class PlayService:
    @property
    def rng(self) -> RandomSource:
        return self._rng.injected or self._rng

    @rng.setter
    def rng(self, value: RandomSource) -> None:
        self._rng = CommandRandom(value)

    @property
    def rules_context(self) -> RulesContext:
        return RulesContext(
            rng=self.rng,
            resources=self.engine.resources,
            reviewer=self.engine.reviewer,
            rules=self.engine.rules,
            combat=self.engine.combat,
            correct_symptom_attributes=correct_symptom_attributes(),
            clock=self.advance_clock,
        )

    def advance_clock(
        self,
        state: PlayState,
        command: Advance,
        rng: RandomSource | None = None,
        *,
        run_npcs: bool = True,
    ) -> PlayState:
        return advance_cyclic_clock(self, state, command, rng or self.rng, run_npcs=run_npcs)

    def __init__(
        self,
        store: AsyncSQLiteStore | AsyncPostgresStore,
        engine: ActionEngine,
        *,
        rng: RandomSource = secrets,
        profiles: ProfileRuntime | None = None,
        sessions: SessionRegistry | None = None,
        instants: Callable[[], CommandInstant] = capture_instant,
        seeds: SeedSource = token_seed,
    ) -> None:
        self.store, self.engine, self.rng = store, engine, CommandRandom(rng)
        # When present, campaigns pinned to another registered profile dispatch to
        # that profile's service. Without a runtime, mismatched pins fail closed.
        self.profiles = profiles
        # Every service derived from this one shares these, so two handles onto the
        # same campaign take the same lock and read the same scripted sources.
        self.sessions = SessionRegistry() if sessions is None else sessions
        self.instants, self.seeds = instants, seeds

    def derived(
        self,
        engine: ActionEngine,
        *,
        rng: RandomSource | None = None,
        profiles: ProfileRuntime | None = None,
    ) -> PlayService:
        """A sibling over the same store, session registry and injected sources."""
        return PlayService(
            self.store,
            engine,
            rng=secrets if rng is None else rng,
            profiles=profiles,
            sessions=self.sessions,
            instants=self.instants,
            seeds=self.seeds,
        )

    def for_campaign(self, campaign: Campaign) -> PlayService:
        """Dispatch to the campaign's exact registered profile, then bind its scenario."""
        if self.profiles is not None and campaign.get("rules_ref") != reference(
            self.engine.resources.rules
        ):
            return self.profiles.for_campaign(campaign)
        return self.bind(campaign)

    def bind(self, campaign: Campaign, *, migration_target: bool = False) -> PlayService:
        """Bind a saved scenario without sharing mutable per-campaign runtime state."""

        encoded = campaign.get("scenario_graph_json")
        if encoded is None:
            # No pinned scenario: only a migrated map configuration can rebind.
            override = campaign.get("combat_rules_json")
            if override is None:
                return self
            original = self.engine.rules.combat
            if original is None:
                raise ValidationError("Map migration requires configured combat rules")
            saved = CombatRules.model_validate_json(override)
            engine = self.sessions.bind(
                campaign["id"],
                self.engine.reviewer,
                self.engine.resources,
                self.engine.rules.model_copy(
                    update={
                        "combat": original.model_copy(update={"battlefields": saved.battlefields})
                    }
                ),
            )
            if engine.digest == self.engine.digest:
                return self
            return self.derived(engine, rng=self.rng, profiles=self.profiles)
        graph = parse_graph(encoded)
        engine = self.sessions.bind(
            campaign["id"],
            self.engine.reviewer,
            self.engine.resources.for_world(graph.world),
            graph.runtime_rules(),
        )

        if not migration_target and campaign.get("rules_ref") != reference(
            self.engine.resources.rules
        ):
            raise ValidationError("Campaign rules do not match the play engine")
        verify(campaign, runtime_digest=None if migration_target else engine.digest)
        if (
            engine.digest == self.engine.digest
            and engine.resources.actors == self.engine.resources.actors
        ):
            return self
        return self.derived(engine, rng=self.rng, profiles=self.profiles)

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
            event.id.startswith(
                (
                    *TASK_PRIVATE_PREFIXES,
                    "combat-abandoned-attack:",
                    "unarmed-random:",
                    "combat-sense:",
                    "combat-sense-invalidated:",
                    "ability:",
                    "spell:",
                    "runtime-spell:",
                    "spell-ritual:",
                    "staff-construction:",
                    "haste-manufacture:",
                    "haste-wearable-construction:",
                    "staff-casting-intent:",
                    "staff-casting-touch:",
                    "staff-casting-invalid:",
                    "casting-targeting:",
                    "item-magic-loss:",
                    "enchantment:",
                    "enchantment-rest:",
                    "enchantment-lifecycle:",
                    "enchantment-loss:",
                    "enchantment-power:",
                    "power-cast-origin:",
                    "armoury-familiarity:",
                    "armoury-parts:",
                    "armoury-time:",
                    "armoury-training:",
                    "armoury-default:",
                    "armoury-tools:",
                    "armoury-tool-selection:",
                    "water-parcels:",
                    "water-collection:",
                    "water-inventory:",
                    "water-state:",
                    "water-channel:",
                    "water-host:",
                    "water-discovery:",
                    "water-cast-plan:",
                    "water-scene:",
                    "water-mist:",
                    "apportation-channel:",
                    "apportation-route:",
                    "apportation-host:",
                    "great-haste-channel:",
                    "great-haste-active:",
                    "great-haste-ended:",
                    "great-haste-host:",
                    "great-haste-casting-origin:",
                    "great-haste-step-lease:",
                    "great-haste-named-origin:",
                    "great-haste-step-resolved:",
                    "analyze-magic:",
                    "detect-magic:",
                    "identify-spell:",
                    "haste-channel:",
                    "haste-item:",
                    "haste-mana:",
                    "haste-switch:",
                    "haste-host:",
                    "power-wearer:",
                    "lock-state:",
                    "lock-channel:",
                    "lock-host:",
                    "lock-backfire-choice:",
                    "spell-backfire:",
                    "mana-refund:",
                    "harmful-physiology:",
                    "physiology:",
                    "composed-source:",
                    "composed-pending:",
                    "composed-finished:",
                    "composed-result:",
                    "composed-resolution:",
                    "cyclic-host-binding:",
                    "cyclic-host:",
                    "innate-critical:",
                    "innate-critical-outcome:",
                    "fatigue-critical-knockdown:",
                )
            )
            for event in resources.events
        ):
            raise ValidationError("Initial resources cannot seed supernatural execution receipts")
        if campaign.get("rules_ref") != reference(self.engine.resources.rules):
            raise ValidationError("Campaign rules do not match the play engine")
        if "resources_json" in campaign or "play_json" in campaign:
            raise ValidationError("Campaign already has an engine checkpoint")

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
            journal=journal,
            fired_scene_triggers=fired_scene_triggers,
        ).model_copy(update={"scene_events": scene_events})
        if self.engine.rules.party is not None:
            state = migrate(state)

        state = initialize(self, state)
        self.engine.validate(state)
        return state

    def _load(self, campaign: Campaign) -> PlayState:

        if campaign.get("rules_ref") != reference(self.engine.resources.rules):
            raise ValidationError("Campaign rules do not match the play engine")
        verify(campaign, runtime_digest=self.engine.digest)
        raw = campaign.get("play_json")
        if raw is None:
            raise ValidationError("Campaign has no typed play state")
        state = PlayState.model_validate_json(raw)
        if state.campaign_id != campaign["id"] or state.revision != campaign["revision"]:
            raise ValidationError("Campaign and play checkpoint disagree")
        if self.engine.rules.party is not None:
            state = migrate(state)

        state = migrate_unique(state, self.engine.rules.scenes, self.engine.rules.combat)
        self.engine.validate(state)
        return state

    def commit(self, campaign: Campaign, state: PlayState) -> None:
        """Validate a resolved play state and record it as the campaign's checkpoint.

        This is the only verb that writes play state onto a campaign row; every
        transaction ends here so the persisted revision and checkpoint agree.
        """
        self.engine.validate(state)
        record_play_state(campaign, state)

    def checkpoint(
        self,
        state: PlayState,
        *,
        before: PlayState | None = None,
        run_npcs: bool = True,
        stop_before_npc: tuple[str, int] | None = None,
    ) -> PlayState:
        configured_magic = self.engine.rules.spells.magic_items if self.engine.rules.spells else ()
        resources = item_magic_checkpoint(
            state.resources,
            before=before.resources if before is not None else None,
            configured_bindings=configured_magic,
        )
        for actor in state.actors:
            resources = refund_due(resources, actor.actor_id)
        state = state.model_copy(update={"resources": resources})

        if before is not None:
            state = analysis_checkpoint(self.rules_context, state, before)
            state = detection_checkpoint(self.rules_context, state, before)
            state = concentration_checkpoint(self.rules_context, state, before)
            state = held_checkpoint(self.rules_context, state, before)
        state = wearer_checkpoint(self.rules_context, state)
        state = power_checkpoint(self.rules_context, state, before=before)
        before_fire = state
        state = spell_checkpoint(self.rules_context, state)
        state = great_haste_checkpoint(self.rules_context, state)
        state = settle_haste_budget(self, state)
        state = perceive(state)
        state = recover_stuns(self.rules_context, state)
        state = concentration_checkpoint(self.rules_context, state, before_fire)
        state = held_checkpoint(self.rules_context, state, before_fire)
        state = analysis_checkpoint(self.rules_context, state, before_fire)
        state = detection_checkpoint(self.rules_context, state, before_fire)
        state = shapeshifting_checkpoint(self, state, before=before)
        state = size_geometry_checkpoint(self.rules_context, state)
        before_late_magic = state
        if run_npcs:
            before_npcs = state
            state = npc_checkpoint(self, state, stop_before=stop_before_npc)
            state = shapeshifting_checkpoint(self, state, before=before_npcs)
            state = size_geometry_checkpoint(self.rules_context, state)
        state = state.model_copy(
            update={
                "resources": item_magic_checkpoint(
                    state.resources, configured_bindings=configured_magic
                )
            }
        )
        state = power_checkpoint(self.rules_context, state, before=before_late_magic)
        state = wearer_checkpoint(self.rules_context, state)
        state = haste_checkpoint(self.rules_context, state)
        state = objective_checkpoint(self, state, before=before)
        if before is not None:
            state = staff_checkpoint(state, before=before)
            state = sensory_checkpoint(state, before=before)
        state = analysis_checkpoint(self.rules_context, state, before_late_magic)
        state = detection_checkpoint(self.rules_context, state, before_late_magic)
        return enchanting_checkpoint(state, before=before)

    @staticmethod
    def propose(value: object) -> TypedAction:
        try:
            return ACTION_ADAPTER.validate_python(value)
        except SchemaError as exc:
            raise ValidationError("Invalid typed action proposal") from exc

    async def preview(self, cid: str, value: object, *, principal_id: str) -> ActionResult:
        command = self.propose(value)
        ActsAs(command.actor_id)(principal_id)
        state = self._load(await self.store.read(cid))
        _require_analysis_free(state, command)
        return self.engine.assess(state, command)

    def plan(self, command: TypedAction, checkpoint: Campaign) -> CommandPlan[ActionResult]:
        """What a typed action writes; the pipeline decides whether it runs.

        The checkpoint is read before the pipeline looks for a receipt. If an
        identical command commits during either read, the retry lookup or the
        transaction returns its result; assessment must not observe the newer
        revision after a receipt miss.
        """
        payload = json.dumps(
            {"operation": "typed-action", "command": command.model_dump(mode="json")},
            sort_keys=True,
            separators=(",", ":"),
        )

        def assess() -> ActionResult | None:
            state = self._load(checkpoint)
            _require_analysis_free(state, command)
            feasible = self.engine.assess(state, command)
            return None if feasible.status == "feasible" else feasible

        def resolve(campaign: Campaign) -> CommandReceipt:
            current = self._load(campaign)
            _require_analysis_free(current, command)
            synchronous(current, command.actor_id, enchanting_rest=True)
            state, resolved_events = self.engine.resolve(
                current,
                command,
                rng=self.rng,
                correct_symptom_attributes=correct_symptom_attributes(),
                clock=self.advance_clock,
            )
            result = action_result(resolved_events)
            if result.status != "committed":
                raise ValidationError("Action is no longer feasible")
            state = self.checkpoint(state, before=current)
            self.commit(campaign, state)
            return CommandReceipt(action="typed-action", outcome=result.model_dump_json())

        async def outcome(campaign: Campaign) -> ActionResult:
            result = self._load(campaign).last_result
            if result is None:
                raise ValidationError("Missing committed action result")
            return result

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=command.actor_id,
            outcome=outcome,
            control=(ActsAs(command.actor_id),),
            assess=assess,
            rng=self.rng,
        )

    async def execute(
        self,
        cid: str,
        value: object,
        *,
        principal_id: str,
        authorize: Callable[[Campaign], None] | None = None,
    ) -> ActionResult:
        command = self.propose(value)
        return await submit(
            self,
            cid,
            self.plan(command, await self.store.read(cid)),
            principal_id=principal_id,
            authorize=authorize,
        )

    def approval_plan(
        self, cid: str, command: ApproveCharacter, state: PlayState
    ) -> CommandPlan[Approval]:
        """What a power approval writes; the pipeline decides whether it runs."""
        payload = json.dumps(
            {"operation": "power-approval", "command": command.model_dump(mode="json")},
            sort_keys=True,
            separators=(",", ":"),
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            state = self._load(campaign)
            actor = next((a for a in state.actors if a.actor_id == command.target_actor_id), None)
            if actor is None:
                raise ValidationError("Unknown approval target")
            approval = self.engine.reviewer.approve(
                actor.proposal,
                campaign_id=cid,
                actor_id=actor.actor_id,
                revision=state.revision + 1,
                approver_id=command.actor_id,
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
            self.commit(campaign, updated)
            return CommandReceipt(action="power-approval", outcome=approval.model_dump_json())

        async def outcome(campaign: Campaign) -> Approval:
            state = self._load(campaign)
            approval = next(
                a.approval for a in state.actors if a.actor_id == command.target_actor_id
            )
            if approval is None:
                raise ValidationError("Missing committed approval")
            return approval

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=command.actor_id,
            outcome=outcome,
            control=(
                Trusted(self.engine.reviewer.gm_ids),
                ActsAs(command.actor_id),
                Seats(state, refusal="Command requires current GM authority"),
            ),
            rng=self.rng,
        )

    async def approve(self, cid: str, value: object, *, principal_id: str) -> Approval:
        try:
            command = ApproveCharacter.model_validate(value)
        except SchemaError as exc:
            raise ValidationError("Invalid approval command") from exc
        state = self._load(await self.store.read(cid))
        return await submit(
            self, cid, self.approval_plan(cid, command, state), principal_id=principal_id
        )


def record_play_state(campaign: Campaign, state: PlayState) -> None:
    """Write an already validated play checkpoint and its revision onto the campaign row."""
    campaign["revision"] = state.revision
    campaign["play_json"] = state.model_dump_json()
