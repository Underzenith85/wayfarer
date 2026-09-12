"""Transactional adapter for the explicit combat encounter state machine."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace

from pydantic import ValidationError as SchemaError

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.adjudication import expire_rulings
from wayfarer.engine.simulation.combat.combat import (
    BasicSpatialContext,
    BasicSpatialFact,
    Combatant,
    CombatEngine,
    CombatResult,
    CombatWithdrawal,
    CoverSpatialFact,
    Encounter,
    HexSpatialContext,
    ReachSpatialFact,
    RetreatSpatialFact,
    VisibilitySpatialFact,
    basic_visible,
)
from wayfarer.engine.simulation.combat.commands import (
    COMBAT_ADAPTER as COMBAT_ADAPTER,
)
from wayfarer.engine.simulation.combat.commands import BasicJoinPlacement as BasicJoinPlacement
from wayfarer.engine.simulation.combat.commands import (
    BasicMove as BasicMove,
)
from wayfarer.engine.simulation.combat.commands import (
    ChooseDefense as ChooseDefense,
)
from wayfarer.engine.simulation.combat.commands import (
    CombatCommand as CombatCommand,
)
from wayfarer.engine.simulation.combat.commands import (
    ContinueCriticalMiss as ContinueCriticalMiss,
)
from wayfarer.engine.simulation.combat.commands import (
    DeclareBasicSpatialFacts as DeclareBasicSpatialFacts,
)
from wayfarer.engine.simulation.combat.commands import (
    DeclareThrownLanding as DeclareThrownLanding,
)
from wayfarer.engine.simulation.combat.commands import (
    EndEncounter as EndEncounter,
)
from wayfarer.engine.simulation.combat.commands import HexJoinPlacement as HexJoinPlacement
from wayfarer.engine.simulation.combat.commands import (
    HexPlacement as HexPlacement,
)
from wayfarer.engine.simulation.combat.commands import (
    JoinEncounter as JoinEncounter,
)
from wayfarer.engine.simulation.combat.commands import (
    MigrateEncounterBasic as MigrateEncounterBasic,
)
from wayfarer.engine.simulation.combat.commands import (
    MigrateEncounterHex as MigrateEncounterHex,
)
from wayfarer.engine.simulation.combat.commands import (
    RepairEquipment as RepairEquipment,
)
from wayfarer.engine.simulation.combat.commands import (
    ResolveChokeEffects as ResolveChokeEffects,
)
from wayfarer.engine.simulation.combat.commands import (
    ResolveWeaponExplosion as ResolveWeaponExplosion,
)
from wayfarer.engine.simulation.combat.commands import (
    ResumeInterruptedTurn as ResumeInterruptedTurn,
)
from wayfarer.engine.simulation.combat.commands import (
    RetrieveEquipment as RetrieveEquipment,
)
from wayfarer.engine.simulation.combat.commands import SquareJoinPlacement as SquareJoinPlacement
from wayfarer.engine.simulation.combat.commands import (
    StartBasicEncounter as StartBasicEncounter,
)
from wayfarer.engine.simulation.combat.commands import (
    StartEncounter as StartEncounter,
)
from wayfarer.engine.simulation.combat.commands import (
    TakeCombatTurn as TakeCombatTurn,
)
from wayfarer.engine.simulation.combat.commands import (
    TakeUnarmedTurn as TakeUnarmedTurn,
)
from wayfarer.engine.simulation.combat.commands import (
    TypedCombatCommand as TypedCombatCommand,
)
from wayfarer.engine.simulation.combat.commands import (
    WithdrawEncounter as WithdrawEncounter,
)
from wayfarer.engine.simulation.combat.lite_resolution import resolve_injury
from wayfarer.engine.simulation.combat.maneuvers import ATTACK_MANEUVERS
from wayfarer.engine.simulation.resources import Advance, Pool, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Campaign, CommandReceipt
from wayfarer.orchestration.entropy import commit_command
from wayfarer.orchestration.play import PlayService


class CombatService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    async def _recorded_result(self, cid: str, command_id: str) -> CombatResult:
        for entry in await self.play.store.history(cid):
            if entry.command_id == command_id:
                return CombatResult.model_validate_json(entry.event["outcome"])
        raise ValidationError("Missing committed combat receipt")

    @staticmethod
    def propose(value: object) -> TypedCombatCommand:
        try:
            return COMBAT_ADAPTER.validate_python(value)
        except SchemaError as exc:
            raise ValidationError("Invalid combat command") from exc

    @staticmethod
    def _encounter(state: PlayState, encounter_id: str) -> Encounter:
        encounter = next((e for e in state.encounters if e.id == encounter_id), None)
        if encounter is None:
            raise ValidationError("Unknown encounter")
        return encounter

    async def execute(
        self, cid: str, value: object, *, authenticated_actor_id: str
    ) -> CombatResult:
        command = self.propose(value)
        if command.actor_id != authenticated_actor_id:
            raise ValidationError("Combat command actor is not authorized")
        bound = self.play.for_campaign(await self.play.store.read(cid))
        if bound is not self.play:
            return await CombatService(bound).execute(
                cid, value, authenticated_actor_id=authenticated_actor_id
            )
        engine = self.play.engine.combat
        if engine is None:
            raise ValidationError("Campaign combat is not configured")
        if isinstance(
            command,
            (
                StartEncounter,
                StartBasicEncounter,
                DeclareBasicSpatialFacts,
                EndEncounter,
                MigrateEncounterBasic,
                MigrateEncounterHex,
                ContinueCriticalMiss,
                DeclareThrownLanding,
                ResolveWeaponExplosion,
            ),
        ) and (command.actor_id not in self.play.engine.reviewer.gm_ids):
            raise ValidationError("Encounter lifecycle requires GM authority")
        if (
            isinstance(command, JoinEncounter)
            and command.joining_actor_id is not None
            and command.actor_id not in self.play.engine.reviewer.gm_ids
        ):
            raise ValidationError("GM admission requires GM authority")
        payload = json.dumps(
            {"operation": "combat", "command": command.model_dump(mode="json")},
            sort_keys=True,
            separators=(",", ":"),
        )
        duplicate = await self.play.store.duplicate(cid, command.id, payload)
        if duplicate is not None:
            return await self._recorded_result(cid, command.id)

        def resolve(campaign: Campaign) -> CommandReceipt:
            play, before, effective_command = _bind_combat_command(campaign, command, self.play)
            updated, result = reduce_combat(before, effective_command, CombatContext(play, before))
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(action="combat", outcome=result.model_dump_json())

        committed = await commit_command(
            self.play.store,
            cid,
            command.id,
            command.expected_revision,
            payload,
            resolve,
            actor_id=command.actor_id,
            rng=self.play.rng,
        )
        if committed["kind"] == "replayed":
            return await self._recorded_result(cid, command.id)
        result = (
            self.play.for_campaign(committed["state"])._load(committed["state"]).last_combat_result
        )
        if result is None:
            raise ValidationError("Missing committed combat result")
        return result


def _bind_combat_command(
    campaign: Campaign,
    command: TypedCombatCommand,
    play: PlayService,
) -> tuple[PlayService, PlayState, TypedCombatCommand]:
    before = play._load(campaign)
    if isinstance(command, MigrateEncounterHex):
        from wayfarer.orchestration.battlefield_templates import prepare

        return prepare(campaign, play, before, command)
    return play, before, command


@dataclass(frozen=True)
class CombatContext:
    """Command-scoped inputs; never carries a mutable transaction callback frame."""

    play: PlayService
    initial_state: PlayState
    resuming: bool = False
    reaction: bool = False

    @property
    def engine(self) -> CombatEngine:
        engine = self.play.engine.combat
        assert engine is not None
        return engine


@dataclass(frozen=True)
class CombatStep:
    state: PlayState
    encounter: Encounter
    resources: ResourceState
    result: CombatResult
    blast_deferred_ticks: int = 0
    defense_before: Encounter | None = None


def _elapsed_combat_ticks(prior: Encounter | None, encounter: Encounter) -> int:
    """Return newly settled shared seconds without flattening actor-relative turns.

    A completed initiative cycle settles one shared second.  If combat ends because
    a turn completed partway through a cycle, that final one-second turn must also
    settle; otherwise a decisive first action would consume no world time.  A GM
    ending combat without another turn remains a zero-time lifecycle operation.
    """
    if prior is None:
        return 0
    ticks = max(0, encounter.round - prior.round)
    completed_during_partial_cycle = (
        prior.status == "active"
        and encounter.status == "completed"
        and encounter.round == prior.round
        and encounter.turn_index != prior.turn_index
    )
    return ticks + int(completed_during_partial_cycle)


def _prepare_command(
    state: PlayState, command: TypedCombatCommand, context: CombatContext
) -> tuple[PlayState, TypedCombatCommand, CombatContext]:
    engine = context.engine
    resuming = context.resuming
    reaction = context.reaction
    if isinstance(command, EndEncounter):
        from wayfarer.engine.simulation.combat.explosions import blasts

        if any(
            not b.resolved and b.encounter_id == command.encounter_id
            for b in blasts(state.resources)
        ):
            raise ConflictError("Resolve armed explosives before ending the encounter")
    resuming = False
    reaction = False
    if isinstance(command, ResumeInterruptedTurn):
        paused = CombatService._encounter(state, command.encounter_id)
        interrupt = paused.wait_interrupt
        if interrupt is None or not interrupt.ready or interrupt.actor_id != command.actor_id:
            raise ConflictError("No interrupted turn is ready for this actor")
        # An interrupted unarmed turn had not begun when it paused, so it resumes
        # whole instead of replaying turn bookkeeping the armed path already spent.
        unarmed_turn = json.loads(interrupt.command_json)["kind"] == "take_unarmed_turn"
        saved: TakeCombatTurn | TakeUnarmedTurn
        if unarmed_turn:
            saved = (
                TakeCombatTurn(
                    id=command.id,
                    actor_id=command.actor_id,
                    expected_revision=command.expected_revision,
                    encounter_id=command.encounter_id,
                    maneuver="do_nothing",
                )
                if command.cancel
                else TakeUnarmedTurn.model_validate_json(interrupt.command_json)
            )
        else:
            saved = TakeCombatTurn.model_validate_json(interrupt.command_json)
        if command.cancel and not unarmed_turn:
            assert isinstance(saved, TakeCombatTurn)
            saved = saved.model_copy(
                update={
                    "maneuver": "do_nothing",
                    "shots": 1,
                    "reload_ammunition_id": None,
                    "unload_ammunition": False,
                    "fast_draw": False,
                    "cocking_aid_id": None,
                    "let_down_bow": False,
                    "recover_thrown_item": False,
                    "firearm_service": None,
                    "firearm_service_skill": "weapon",
                    "destination": None,
                    "hex_path": (),
                    "hex_facing": None,
                    "facing": None,
                    "posture": None,
                    "item_id": None,
                    "target_id": None,
                    "mode_id": None,
                    "attack_option": None,
                    "defense_option": None,
                    "wait_trigger": None,
                    "step_timing": "before",
                    "second_item_id": None,
                    "second_target_id": None,
                    "second_mode_id": None,
                    "braced": False,
                }
            )
        command = saved.model_copy(
            update={"id": command.id, "expected_revision": command.expected_revision}
        )
        state = state.model_copy(
            update={
                "encounters": tuple(
                    e.model_copy(update={"wait_interrupt": None}) if e.id == paused.id else e
                    for e in state.encounters
                )
            }
        )
        resuming = not unarmed_turn
    elif isinstance(command, TakeCombatTurn):
        paused = CombatService._encounter(state, command.encounter_id)
        reaction = (
            paused.wait_interrupt is not None
            and paused.wait_interrupt.waiter_id == command.actor_id
        )
        if (
            reaction
            and paused.wait_interrupt is not None
            and command.maneuver != "do_nothing"
            and command.mode_id != paused.wait_interrupt.declaration.mode_id
        ):
            raise ValidationError("Wait reaction must use the declared weapon mode")
    from wayfarer.engine.simulation.health.fright import can_defend, maneuver_allowed
    from wayfarer.orchestration.recovery import guard

    if isinstance(command, (TakeCombatTurn, TakeUnarmedTurn)) and not maneuver_allowed(
        state.resources, command.actor_id, command.maneuver
    ):
        raise ValidationError("This fright condition does not permit that maneuver")
    guard(
        state,
        command.actor_id,
        command.kind,
        allow_fright=(
            isinstance(command, TakeCombatTurn)
            and maneuver_allowed(state.resources, command.actor_id, command.maneuver)
            or isinstance(command, ChooseDefense)
            and (command.defense == "none" or can_defend(state.resources, command.actor_id))
        ),
    )
    if engine.rules.gurps_equipment is not None:
        from wayfarer.engine.rules.types.hazard import require_hazards_settled
        from wayfarer.engine.rules.types.recovery import require_settled

        affected = {command.actor_id}
        if isinstance(command, StartEncounter):
            affected.update(p.actor_id for p in command.placements)
        elif isinstance(command, StartBasicEncounter):
            affected.update(command.participant_ids)
        elif isinstance(command, JoinEncounter) and command.joining_actor_id is not None:
            affected.add(command.joining_actor_id)
        elif (
            isinstance(command, (TakeCombatTurn, TakeUnarmedTurn)) and command.target_id is not None
        ):
            affected.add(command.target_id)
        elif isinstance(command, ChooseDefense):
            selected_encounter = CombatService._encounter(state, command.encounter_id)
            if selected_encounter.pending_unarmed is not None:
                affected.update(
                    (
                        selected_encounter.pending_unarmed.actor_id,
                        selected_encounter.pending_unarmed.target_id,
                    )
                )
            pending = selected_encounter.pending_defense
            if pending is not None:
                affected.update((pending.attacker_id, pending.defender_id))
        require_settled(
            state.resources.recovery_tasks, frozenset(affected), state.resources.game_time
        )
        if not isinstance(command, ResolveChokeEffects):
            for active_encounter in state.encounters:
                if command.actor_id in active_encounter.turn_order:
                    affected.update(g.target_id for g in active_encounter.grips)
            require_hazards_settled(
                state.resources.hazards, frozenset(affected), state.resources.game_time
            )
    if command.expected_revision != state.revision:
        raise ConflictError("Play revision changed")
    return state, command, replace(context, resuming=resuming, reaction=reaction)


def _start_encounter(
    state: PlayState, command: StartEncounter, context: CombatContext
) -> CombatStep:
    play = context.play
    engine = context.engine
    resources = state.resources
    if any(e.id == command.encounter_id for e in state.encounters):
        raise ConflictError("Encounter ID already exists")
    actor_map = {actor.actor_id: actor for actor in state.actors}
    if len({p.actor_id for p in command.placements}) != len(command.placements) or any(
        p.actor_id not in actor_map for p in command.placements
    ):
        raise ValidationError("Encounter placements require unique play actors")
    initiatives: dict[str, int] = {}
    pools = {pool.id: pool for pool in state.resources.pools}
    for placement in command.placements:
        actor = actor_map[placement.actor_id]
        if engine.rules.gurps_equipment is not None:
            from wayfarer.engine.simulation.combat.melee import fatigue_ready

            if not fatigue_ready(state, actor.actor_id):
                raise ValidationError("Exhausted actor cannot start combat")
        hp = pools.get(f"hp:{actor.actor_id}")
        if (
            actor.conditions
            or hp is None
            or (hp.injury.incapacitated if hp.injury else hp.current == 0)
        ):
            raise ValidationError("Incapacitated actor cannot start combat")
        build, _ = play.engine.reviewer.activate(
            actor.proposal,
            actor.approval,
            campaign_id=state.campaign_id,
            actor_id=actor.actor_id,
        )
        values = {value.target: int(value.value) for value in build.sheet.values}
        initiatives[actor.actor_id] = values["attribute:dx"]
    encounter = engine.start(
        command.encounter_id,
        command.battlefield_id,
        command.placements,
        initiatives,
        state.world,
        resources,
        frozenset(actor_map),
    )
    from wayfarer.engine.simulation.campaign.encounter_context import bind_scene

    if play.engine.rules.scenes is not None or command.scene_id is not None:
        encounter = bind_scene(encounter, play.engine.rules.scenes, engine.rules, command.scene_id)
    if engine.rules.gurps_equipment is not None:
        from wayfarer.engine.simulation.combat.objects.locations import bind_initial_hands

        encounter = bind_initial_hands(play.rules_context, state, encounter)
    from wayfarer.engine.simulation.combat.ranged import declare

    encounter = declare(play.rules_context, encounter, command.ranged_situations)
    encounters = state.encounters + (encounter,)
    from wayfarer.engine.simulation.campaign.encounter_context import validate_contexts

    validate_contexts(
        state.model_copy(update={"encounters": encounters}),
        play.engine.rules.scenes,
        engine.rules,
    )
    result = CombatResult(
        encounter_id=encounter.id,
        code="combat.started",
        round=encounter.round,
        current_actor_id=encounter.current_actor_id,
        available=engine.available(encounter, encounter.current_actor_id),
    )
    return CombatStep(state, encounter, resources, result)


def _start_basic_encounter(
    state: PlayState, command: StartBasicEncounter, context: CombatContext
) -> CombatStep:
    play = context.play
    engine = context.engine
    resources = state.resources
    if any(e.id == command.encounter_id for e in state.encounters):
        raise ConflictError("Encounter ID already exists")
    actor_map = {actor.actor_id: actor for actor in state.actors}
    if len(set(command.participant_ids)) != len(command.participant_ids) or any(
        actor_id not in actor_map for actor_id in command.participant_ids
    ):
        raise ValidationError("Basic encounter requires unique play actors")
    if any(
        fact.provenance.source != "scenario"
        or fact.provenance.source_id != command.scene_id
        or fact.provenance.declared_by != command.actor_id
        or fact.provenance.declared_revision != command.expected_revision
        or fact.provenance.invalidated_revision is not None
        for fact in command.facts
    ):
        raise ValidationError("Initial basic facts require scoped scenario provenance")
    initiatives: dict[str, int] = {}
    pools = {pool.id: pool for pool in state.resources.pools}
    for actor_id in command.participant_ids:
        actor = actor_map[actor_id]
        if engine.rules.gurps_equipment is not None:
            from wayfarer.engine.simulation.combat.melee import fatigue_ready

            if not fatigue_ready(state, actor.actor_id):
                raise ValidationError("Exhausted actor cannot start combat")
        hp = pools.get(f"hp:{actor.actor_id}")
        if (
            actor.conditions
            or hp is None
            or (hp.injury.incapacitated if hp.injury else hp.current == 0)
        ):
            raise ValidationError("Incapacitated actor cannot start combat")
        build, _ = play.engine.reviewer.activate(
            actor.proposal,
            actor.approval,
            campaign_id=state.campaign_id,
            actor_id=actor.actor_id,
        )
        values = {value.target: int(value.value) for value in build.sheet.values}
        initiatives[actor.actor_id] = values["attribute:dx"]
    encounter = engine.start_basic(
        command.encounter_id,
        command.participant_ids,
        command.facts,
        initiatives,
        state.world,
        resources,
        frozenset(actor_map),
    )
    from wayfarer.engine.simulation.campaign.encounter_context import bind_scene

    encounter = bind_scene(encounter, play.engine.rules.scenes, engine.rules, command.scene_id)
    if engine.rules.gurps_equipment is not None:
        from wayfarer.engine.simulation.combat.objects.locations import bind_initial_hands

        encounter = bind_initial_hands(play.rules_context, state, encounter)
    from wayfarer.engine.simulation.combat.ranged import declare

    encounter = declare(play.rules_context, encounter, command.ranged_situations)
    encounters = state.encounters + (encounter,)
    from wayfarer.engine.simulation.campaign.encounter_context import validate_contexts

    validate_contexts(
        state.model_copy(update={"encounters": encounters}),
        play.engine.rules.scenes,
        engine.rules,
    )
    return CombatStep(
        state,
        encounter,
        resources,
        CombatResult(
            encounter_id=encounter.id,
            code="combat.started",
            round=encounter.round,
            current_actor_id=encounter.current_actor_id,
            available=engine.available(encounter, encounter.current_actor_id),
        ),
    )


def _prepare_encounter(
    state: PlayState, command: TypedCombatCommand, context: CombatContext
) -> Encounter:
    play = context.play
    engine = context.engine
    encounter = CombatService._encounter(state, command.encounter_id)
    if play.engine.rules.scenes is not None:
        from wayfarer.engine.simulation.campaign.encounter_context import bind_scene

        encounter = bind_scene(encounter, play.engine.rules.scenes, engine.rules)
    if encounter.spatial_kind == "hex" and isinstance(command, (TakeCombatTurn, TakeUnarmedTurn)):
        from wayfarer.orchestration.tactical_view import visible_actors

        if command.target_id is not None and command.target_id not in visible_actors(
            state, encounter, command.actor_id, board=play.rules_context.hex_map(encounter)
        ):
            raise ValidationError("Target is unavailable")
        if isinstance(command, TakeCombatTurn) and command.wait_trigger is not None:
            trigger = command.wait_trigger
            visible = visible_actors(
                state, encounter, command.actor_id, board=play.rules_context.hex_map(encounter)
            )
            if any(
                a is not None and a not in visible
                for a in (
                    trigger.actor_id,
                    trigger.target_id,
                    trigger.reaction_target_id,
                )
            ):
                raise ValidationError("Target is unavailable")
    elif encounter.spatial_kind == "basic" and isinstance(
        command, (TakeCombatTurn, TakeUnarmedTurn)
    ):
        spatial = encounter.spatial
        assert isinstance(spatial, BasicSpatialContext)
        if command.target_id is not None:
            if not basic_visible(encounter, command.actor_id, command.target_id):
                raise ValidationError("Target is unavailable")
            cover = spatial.active("cover", command.actor_id, command.target_id)
            if not isinstance(cover, CoverSpatialFact):
                raise ValidationError("Basic combat requires an authoritative cover fact")
            if cover.cover == "full":
                raise ValidationError("Full cover blocks the target")
        if isinstance(command, TakeCombatTurn) and command.wait_trigger is not None:
            for actor_id in (
                command.wait_trigger.actor_id,
                command.wait_trigger.target_id,
                command.wait_trigger.reaction_target_id,
            ):
                if actor_id is not None and not basic_visible(
                    encounter, command.actor_id, actor_id
                ):
                    raise ValidationError("Target is unavailable")
    from wayfarer.engine.simulation.combat.unarmed import guard_control

    guard_control(encounter, command, state)
    from wayfarer.engine.simulation.combat.tactical_transitions import prepare_defense

    if isinstance(command, ChooseDefense):
        if encounter.pending_unarmed is None and (
            command.parry_mode_id is not None or command.second_parry_mode_id is not None
        ):
            from wayfarer.engine.simulation.combat.melee import mode
            from wayfarer.engine.simulation.equipment.catalog import MeleeMode, RangedMode

            pending = encounter.pending_defense
            if (
                engine.rules.gurps_equipment is None
                or pending is None
                or pending.spell_cast_id is not None
            ):
                raise ValidationError(
                    "Explicit parry damage modes require a weapon or unarmed attack"
                )
            incoming = mode(
                play.rules_context,
                state,
                pending.attacker_id,
                pending.weapon_id,
                pending.mode_id,
            )
            if not isinstance(incoming, MeleeMode) and not (
                isinstance(incoming, RangedMode) and incoming.thrown
            ):
                raise ValidationError("Explicit parry damage modes require a parryable attack")
        encounter = prepare_defense(play.rules_context, state, encounter, command)
    return encounter


def _migrate(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    play = context.play
    resources = state.resources
    assert isinstance(command, MigrateEncounterHex)
    from wayfarer.engine.simulation.combat.tactical_transitions import migrate

    encounter = migrate(play.rules_context, state, encounter, command)
    result = CombatResult(
        encounter_id=encounter.id,
        code="combat.hex_migrated",
        round=encounter.round,
        current_actor_id=encounter.current_actor_id,
    )
    return CombatStep(state, encounter, resources, result)


def _migrate_basic(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    assert isinstance(command, MigrateEncounterBasic)
    from wayfarer.engine.simulation.combat.tactical_transitions import migrate_basic

    encounter = migrate_basic(context.play.rules_context, state, encounter, command)
    return CombatStep(
        state,
        encounter,
        state.resources,
        CombatResult(
            encounter_id=encounter.id,
            code="combat.basic_migrated",
            round=encounter.round,
            current_actor_id=encounter.current_actor_id,
        ),
    )


def _declare_basic_facts(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    assert isinstance(command, DeclareBasicSpatialFacts)
    spatial = encounter.spatial
    if not isinstance(spatial, BasicSpatialContext) or encounter.status != "active":
        raise ValidationError("Basic spatial facts require an active basic encounter")
    if any(
        fact.provenance.source != "gm-adjudication"
        or fact.provenance.source_id != command.id
        or fact.provenance.declared_by != command.actor_id
        or fact.provenance.declared_revision != command.expected_revision
        or fact.provenance.invalidated_revision is not None
        or fact.subject_id not in encounter.turn_order
        or fact.object_id not in encounter.turn_order
        for fact in command.facts
    ):
        raise ValidationError("Basic spatial adjudication requires scoped GM provenance")

    def key(fact: BasicSpatialFact) -> tuple[str, str, str]:
        return (
            fact.kind,
            min(fact.subject_id, fact.object_id) if fact.kind == "distance" else fact.subject_id,
            max(fact.subject_id, fact.object_id) if fact.kind == "distance" else fact.object_id,
        )

    keys = {key(fact) for fact in command.facts}
    if len(keys) != len(command.facts):
        raise ValidationError("Basic spatial adjudication contains duplicate facts")
    retained = tuple(
        fact.model_copy(
            update={
                "provenance": fact.provenance.model_copy(
                    update={"invalidated_revision": command.expected_revision}
                )
            }
        )
        if fact.provenance.invalidated_revision is None and key(fact) in keys
        else fact
        for fact in spatial.facts
    )
    encounter = encounter.model_copy(
        update={"spatial_context": spatial.model_copy(update={"facts": retained + command.facts})}
    )
    context.engine.validate(
        encounter,
        state.world,
        state.resources,
        frozenset(actor.actor_id for actor in state.actors),
    )
    return CombatStep(
        state,
        encounter,
        state.resources,
        CombatResult(
            encounter_id=encounter.id,
            code="combat.basic_spatial_facts_declared",
            round=encounter.round,
            current_actor_id=encounter.current_actor_id,
            available=context.engine.available(encounter, encounter.current_actor_id),
        ),
    )


def _explosion(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    play = context.play
    resources = state.resources
    assert isinstance(command, ResolveWeaponExplosion)
    from wayfarer.engine.simulation.combat.thrown.explosions import resolve_blast

    state, encounter, blast_deferred_ticks = resolve_blast(
        play.rules_context,
        state,
        encounter,
        blast_id=command.blast_id,
        command_id=command.id,
        responses=command.responses,
        object_cover=command.object_cover,
        object_sizes=command.object_sizes,
        center=command.center,
        environment=command.environment,
    )
    resources = state.resources
    result = CombatResult(
        encounter_id=encounter.id,
        code="combat.weapon_explosion_resolved",
        round=encounter.round,
        current_actor_id=encounter.current_actor_id,
    )
    return CombatStep(state, encounter, resources, result, blast_deferred_ticks)


def _landing(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    play = context.play
    resources = state.resources
    assert isinstance(command, DeclareThrownLanding)
    from wayfarer.engine.simulation.combat.thrown.items import declare_landing

    resources = declare_landing(
        play.rules_context, state, encounter, command.item_id, command.landing, command.id
    )
    state = state.model_copy(update={"resources": resources})
    result = CombatResult(
        encounter_id=encounter.id,
        code="combat.thrown_landing_declared",
        round=encounter.round,
        current_actor_id=encounter.current_actor_id,
    )
    return CombatStep(state, encounter, resources, result)


def _critical(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    play = context.play
    resources = state.resources
    assert isinstance(command, ContinueCriticalMiss)
    from wayfarer.engine.simulation.combat.criticals.continuation import continue_critical

    state, encounter, continuation = continue_critical(
        play.rules_context,
        state,
        encounter,
        critical_id=command.critical_id,
        command_id=command.id,
        stage=command.stage,
    )
    resources = state.resources
    result = CombatResult(
        encounter_id=encounter.id,
        code="critical." + continuation.status,
        round=encounter.round,
        current_actor_id=encounter.current_actor_id,
    )
    return CombatStep(state, encounter, resources, result)


def _retrieve(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    play = context.play
    resources = state.resources
    assert isinstance(command, RetrieveEquipment)
    from wayfarer.engine.simulation.equipment.retrieval import (
        retrieve as retrieve_field,
    )

    state, retrieval_task = retrieve_field(
        play.rules_context,
        state,
        encounter,
        actor_id=command.actor_id,
        item_id=command.item_id,
        command_id=command.id,
        stage=command.stage,
        task_id=command.task_id,
    )
    resources = state.resources
    result = CombatResult(
        encounter_id=encounter.id,
        code="equipment.retrieval_" + retrieval_task.status,
        round=encounter.round,
        current_actor_id=encounter.current_actor_id,
    )
    return CombatStep(state, encounter, resources, result)


def _repair(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    play = context.play
    resources = state.resources
    assert isinstance(command, RepairEquipment)
    from wayfarer.engine.simulation.equipment.repair_transitions import repair

    state, task = repair(
        play.rules_context,
        state,
        actor_id=command.actor_id,
        item_id=command.item_id,
        command_id=command.id,
        stage=command.stage,
        task_id=command.task_id,
    )
    resources = state.resources
    result = CombatResult(
        encounter_id=encounter.id,
        code="equipment.repair_" + task.status,
        round=encounter.round,
        current_actor_id=encounter.current_actor_id,
    )
    return CombatStep(state, encounter, resources, result)


def _choke(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    play = context.play
    resources = state.resources
    assert isinstance(command, ResolveChokeEffects)
    from wayfarer.engine.simulation.combat.unarmed import resolve_choke

    state, result = resolve_choke(play.rules_context, state, encounter, command)
    resources = state.resources
    return CombatStep(state, encounter, resources, result)


def _unarmed(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    play = context.play
    resources = state.resources
    assert isinstance(command, (TakeUnarmedTurn, ChooseDefense))
    from wayfarer.engine.simulation.combat.unarmed import execute_unarmed

    state, encounter, result = execute_unarmed(play.rules_context, state, encounter, command)
    resources = state.resources
    return CombatStep(state, encounter, resources, result)


def _join(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    play = context.play
    engine = context.engine
    resources = state.resources
    assert isinstance(command, JoinEncounter)
    from wayfarer.engine.simulation.campaign.party import group_for

    if (
        encounter.status != "active"
        or encounter.pending_defense is not None
        or encounter.pending_unarmed is not None
        or encounter.wait_interrupt is not None
        or encounter.blocked_reason
    ):
        raise ConflictError("Reinforcements join between resolved combat stages")
    joining_actor_id = command.joining_actor_id or command.actor_id
    if joining_actor_id in encounter.turn_order:
        raise ConflictError("Actor already participates")
    prior_withdrawal = next(
        (
            entry
            for entry in reversed(encounter.withdrawals)
            if entry.actor.actor_id == joining_actor_id
        ),
        None,
    )
    if prior_withdrawal is not None and encounter.turn_index != 0:
        raise ConflictError("Returning combatants rejoin only at a round boundary")
    joining_actor = next((a for a in state.actors if a.actor_id == joining_actor_id), None)
    if (
        joining_actor is None
        or joining_actor.conditions
        or joining_actor.available_at > resources.game_time
    ):
        raise ValidationError("Reinforcement joining_actor is unavailable")
    if next(p.current for p in resources.pools if p.id == f"hp:{joining_actor.actor_id}") == 0:
        raise ValidationError("Reinforcement joining_actor is incapacitated")
    source = group_for(state, joining_actor_id)
    target_group = group_for(state, encounter.current_actor_id)
    if source.scene_id != target_group.scene_id or source.paused or target_group.paused:
        raise ValidationError("Reinforcements must be in the encounter scene")
    if (
        source.ready_through != resources.game_time
        or target_group.ready_through != resources.game_time
        or any(q.group_id in (source.id, target_group.id) for q in state.party.queue)
    ):
        raise ConflictError("Reinforcement arrival requires synchronized time")
    placement = command.placement
    if placement is None and command.position is not None:
        placement = SquareJoinPlacement(position=command.position, facing=command.facing)
    if placement is None or placement.kind != encounter.spatial_kind:
        raise ValidationError("Reinforcement placement must match the encounter representation")
    basic_facts: tuple[BasicSpatialFact, ...] = ()
    if isinstance(placement, BasicJoinPlacement):
        if command.joining_actor_id is None:
            raise ValidationError("Basic reinforcement placement requires GM admission")
        basic_facts = placement.facts
        if any(
            fact.provenance.source != "gm-adjudication"
            or fact.provenance.source_id != command.id
            or fact.provenance.declared_by != command.actor_id
            or fact.provenance.declared_revision != command.expected_revision
            or fact.provenance.invalidated_revision is not None
            or joining_actor_id not in (fact.subject_id, fact.object_id)
            or fact.subject_id == fact.object_id
            or not {fact.subject_id, fact.object_id}
            <= set(encounter.turn_order + (joining_actor_id,))
            for fact in basic_facts
        ):
            raise ValidationError("Basic reinforcement facts require scoped GM provenance")
        keys = {
            (
                fact.kind,
                min(fact.subject_id, fact.object_id)
                if fact.kind == "distance"
                else fact.subject_id,
                max(fact.subject_id, fact.object_id) if fact.kind == "distance" else fact.object_id,
            )
            for fact in basic_facts
        }
        required = {
            (kind, subject, object_id)
            for other in encounter.turn_order
            for kind, subject, object_id in (
                ("distance", min(joining_actor_id, other), max(joining_actor_id, other)),
                *(
                    (kind, left, right)
                    for kind in ("reach", "visibility", "cover", "obstacle", "retreat")
                    for left, right in ((joining_actor_id, other), (other, joining_actor_id))
                ),
            )
        }
        if keys != required or len(keys) != len(basic_facts):
            raise ValidationError(
                "Basic reinforcement placement requires one complete fact set per combatant"
            )
    if prior_withdrawal is None:
        build, _ = play.engine.reviewer.activate(
            joining_actor.proposal,
            joining_actor.approval,
            campaign_id=state.campaign_id,
            actor_id=joining_actor.actor_id,
        )
        initiative = int(next(v.value for v in build.sheet.values if v.target == "attribute:dx"))
        participant = Combatant(
            actor_id=joining_actor.actor_id,
            initiative=initiative,
            position=placement.position
            if isinstance(placement, (SquareJoinPlacement, HexJoinPlacement))
            else None,
            facing=placement.facing if isinstance(placement, SquareJoinPlacement) else "north",
            hex_facing=placement.facing if isinstance(placement, HexJoinPlacement) else None,
            reach=engine.rules.default_reach,
            movement_allowance=engine.rules.movement_allowance,
            ready_item_ids=tuple(
                sorted(
                    i.id
                    for i in resources.items
                    if i.owner_id == joining_actor.actor_id and i.equipped and i.ready
                )
            ),
        )
    else:
        participant = prior_withdrawal.actor.model_copy(
            update={
                "position": placement.position
                if isinstance(placement, (SquareJoinPlacement, HexJoinPlacement))
                else None,
                "facing": placement.facing
                if isinstance(placement, SquareJoinPlacement)
                else prior_withdrawal.actor.facing,
                "hex_facing": placement.facing if isinstance(placement, HexJoinPlacement) else None,
            }
        )
    if isinstance(encounter.spatial, BasicSpatialContext):
        encounter = encounter.model_copy(
            update={
                "spatial_context": encounter.spatial.model_copy(
                    update={"facts": encounter.spatial.facts + basic_facts}
                )
            }
        )
    encounter = encounter.add_participant(participant)
    joined_participants = encounter.participants
    order = tuple(
        p.actor_id for p in sorted(joined_participants, key=lambda p: (-p.initiative, p.actor_id))
    )
    current_actor = encounter.current_actor_id
    encounter = encounter.model_copy(
        update={
            "turn_order": order,
            "turn_index": order.index(current_actor),
        }
    )
    if source.id != target_group.id:
        remaining = tuple(a for a in source.actor_ids if a != joining_actor.actor_id)
        groups = tuple(
            g.model_copy(
                update={
                    "actor_ids": g.actor_ids + (joining_actor.actor_id,),
                    "generation": g.generation + 1,
                }
            )
            if g.id == target_group.id
            else g.model_copy(update={"actor_ids": remaining, "generation": g.generation + 1})
            if g.id == source.id
            else g
            for g in state.party.groups
            if g.id != source.id or remaining
        )
        state = state.model_copy(
            update={"party": state.party.model_copy(update={"groups": groups})}
        )
    if engine.rules.gurps_equipment is not None:
        from wayfarer.engine.simulation.combat.objects.locations import bind_initial_hands

        encounter = bind_initial_hands(play.rules_context, state, encounter)
    if isinstance(placement, HexJoinPlacement):
        from wayfarer.engine.simulation.combat.tactical import sight

        joined = next(p for p in encounter.participants if p.actor_id == joining_actor_id)
        board = play.rules_context.require_hex(encounter)
        if not any(
            sight(encounter, observer, joined, board=board)
            for observer in encounter.participants
            if observer.actor_id != joining_actor_id
        ):
            raise ValidationError("Hex reinforcement must arrive in visible placement")
    result = CombatResult(
        encounter_id=encounter.id,
        code="combat.reinforcement_arrived",
        round=encounter.round,
        current_actor_id=current_actor,
    )
    return CombatStep(state, encounter, resources, result)


def _end(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    resources = state.resources
    assert isinstance(command, EndEncounter)
    if (
        encounter.status != "active"
        or encounter.pending_defense is not None
        or encounter.blocked_reason
    ):
        raise ConflictError("Encounter cannot end during a pending defense")
    encounter = encounter.model_copy(
        update={"status": "completed", "completion_reason": command.reason}
    )
    result = CombatResult(
        encounter_id=encounter.id,
        code="combat.completed",
        round=encounter.round,
        current_actor_id=encounter.current_actor_id,
    )
    return CombatStep(state, encounter, resources, result)


def _withdraw(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    """Finalize a resolved flight without granting another movement or action."""
    assert isinstance(command, WithdrawEncounter)
    if (
        encounter.status != "active"
        or encounter.pending_defense is not None
        or encounter.pending_unarmed is not None
        or encounter.wait_interrupt is not None
        or encounter.blocked_reason is not None
    ):
        raise ConflictError("Withdrawal requires a resolved combat boundary")
    actor = next((p for p in encounter.participants if p.actor_id == command.actor_id), None)
    if actor is None:
        raise ValidationError("Actor is not a combat participant")
    if actor.last_maneuver != "move":
        raise ValidationError("Withdraw after resolving a Move maneuver")
    if (
        actor.grappled
        or actor.pinned
        or actor.entangled is not None
        or any(command.actor_id in (grip.holder_id, grip.target_id) for grip in encounter.grips)
    ):
        raise ValidationError("Escape restraint before withdrawing")

    others = tuple(p for p in encounter.participants if p.actor_id != command.actor_id)
    spatial = encounter.spatial
    if isinstance(spatial, BasicSpatialContext):
        for other in others:
            reach = spatial.active("reach", other.actor_id, command.actor_id)
            visible = spatial.active("visibility", other.actor_id, command.actor_id)
            retreat = spatial.active("retreat", command.actor_id, other.actor_id)
            if (
                not isinstance(reach, ReachSpatialFact)
                or reach.relation != "separated"
                or not isinstance(visible, VisibilitySpatialFact)
                or visible.visible
                or not isinstance(retreat, RetreatSpatialFact)
                or not retreat.feasible
            ):
                raise ValidationError("Basic withdrawal requires safe authoritative facts")
        spatial = spatial.model_copy(
            update={
                "facts": tuple(
                    fact
                    for fact in spatial.facts
                    if command.actor_id not in (fact.subject_id, fact.object_id)
                )
            }
        )
    elif isinstance(spatial, HexSpatialContext):
        from wayfarer.engine.simulation.combat.tactical import sight
        from wayfarer.engine.simulation.hex_geometry import Hex, neighbor

        board = context.play.rules_context.require_hex(encounter)
        cells = {cell.position for cell in board.cells}
        position = encounter.placement(command.actor_id).position
        assert isinstance(position, Hex)
        if not any(neighbor(position, direction) not in cells for direction in range(6)):
            raise ValidationError("Hex withdrawal requires a battlefield boundary")
        if any(
            CombatEngine.distance(encounter.placement(other.actor_id).position, position)
            <= other.reach
            or sight(encounter, other, actor, board=board)
            for other in others
        ):
            raise ValidationError("A visible or reachable combatant can pursue")
        spatial = spatial.model_copy(
            update={
                "placements": tuple(
                    placement
                    for placement in spatial.placements
                    if placement.actor_id != command.actor_id
                )
            }
        )
    else:
        raise ValidationError("Square withdrawal needs explicit adjudication")

    from wayfarer.engine.simulation.campaign.party import Subgroup, group_for

    if not state.party.groups:
        raise ValidationError("Withdrawal requires shared-time party state")
    group = group_for(state, command.actor_id)
    if len(group.actor_ids) < 2 or any(g.id == command.new_group_id for g in state.party.groups):
        raise ValidationError("Withdrawal requires a fresh independent subgroup")
    new_group = Subgroup(
        id=command.new_group_id,
        scene_id=group.scene_id,
        actor_ids=(command.actor_id,),
        ready_through=group.ready_through,
    )
    groups = tuple(
        g.model_copy(
            update={
                "actor_ids": tuple(a for a in g.actor_ids if a != command.actor_id),
                "generation": g.generation + 1,
            }
        )
        if g.id == group.id
        else g
        for g in state.party.groups
    ) + (new_group,)
    state = state.model_copy(update={"party": state.party.model_copy(update={"groups": groups})})

    removed_index = encounter.turn_order.index(command.actor_id)
    order = tuple(a for a in encounter.turn_order if a != command.actor_id)
    round_number = encounter.round
    if removed_index == encounter.turn_index:
        if removed_index == len(order):
            turn_index = 0
            round_number += 1
        else:
            turn_index = removed_index
    else:
        turn_index = encounter.turn_index - int(removed_index < encounter.turn_index)
    completed = len(order) == 1
    encounter = encounter.model_copy(
        update={
            "participants": others,
            "turn_order": order,
            "turn_index": turn_index,
            "round": round_number,
            "spatial_context": spatial,
            "status": "completed" if completed else "active",
            "completion_reason": "withdrawal" if completed else None,
            "close_pairs": tuple(
                pair for pair in encounter.close_pairs if command.actor_id not in pair
            ),
            "ranged_situations": tuple(
                situation
                for situation in encounter.ranged_situations
                if command.actor_id not in (situation.attacker_id, situation.defender_id)
            ),
            "withdrawals": encounter.withdrawals
            + (
                CombatWithdrawal(
                    actor=actor,
                    round=encounter.round,
                    turn_index=removed_index,
                    group_id=command.new_group_id,
                ),
            ),
        }
    )
    return CombatStep(
        state,
        encounter,
        state.resources,
        CombatResult(
            encounter_id=encounter.id,
            code="combat.withdrawn",
            round=encounter.round,
            current_actor_id=encounter.current_actor_id,
        ),
    )


def preview_withdrawal(
    play: PlayService, state: PlayState, encounter: Encounter, command: WithdrawEncounter
) -> None:
    """Validate a withdrawal choice without committing state or advancing clocks."""
    _withdraw(state, command, encounter, CombatContext(play, state))


def _validate_turn(
    state: PlayState, command: TakeCombatTurn, encounter: Encounter, context: CombatContext
) -> tuple[PlayState, Encounter]:
    play = context.play
    engine = context.engine
    resources = state.resources
    from wayfarer.engine.simulation.magic.effects import require_not_dazed

    if command.maneuver != "do_nothing":
        require_not_dazed(resources, command.actor_id)
    from wayfarer.engine.simulation.abilities import interrupt_concentration
    from wayfarer.engine.simulation.combat.ranged import validate_command

    validate_command(play.rules_context, state, encounter, command)
    resources = interrupt_concentration(resources, command.actor_id, command.id)
    state = state.model_copy(update={"resources": resources})
    if command.maneuver == "ready" and command.item_id:
        from wayfarer.engine.simulation.combat.thrown.flight import retrieve

        if command.recover_thrown_item:
            from wayfarer.engine.simulation.combat.thrown.items import recover

            state = recover(play.rules_context, state, encounter, command)
        else:
            state = retrieve(state, encounter, command.actor_id, command.item_id)
        resources = state.resources
    if command.hit_location is not None and (
        command.maneuver not in ATTACK_MANEUVERS or engine.rules.gurps_equipment is None
    ):
        raise ValidationError("Hit location requires GURPS attack dispatch")
    if command.target_item_id and (
        command.maneuver not in ATTACK_MANEUVERS
        or command.hit_location
        or engine.rules.gurps_equipment is None
        or command.attack_option == "double"
    ):
        raise ValidationError("Object targeting requires a single GURPS attack")
    if command.ready_hand is not None and (
        command.maneuver != "ready" or engine.rules.gurps_equipment is None
    ):
        raise ValidationError("Hand selection requires GURPS Ready")
    if engine.rules.gurps_equipment is not None:
        from wayfarer.engine.simulation.combat.objects.locations import validate_posture

        validate_posture(state, command.actor_id, command.posture)
    actor = next(a for a in state.actors if a.actor_id == command.actor_id)
    hp = next(p for p in resources.pools if p.id == f"hp:{actor.actor_id}")
    if (hp.injury.incapacitated if hp.injury else hp.current == 0) or actor.conditions:
        raise ValidationError("Incapacitated actor cannot act")
    if actor.available_at > resources.game_time and command.maneuver not in (
        "wait",
        "do_nothing",
    ):
        raise ValidationError("Actor is recovering from injury")
    if command.maneuver == "attack" and engine.rules.attacks:
        item = next((i for i in resources.items if i.id == command.item_id), None)
        if item is None or not any(
            p.definition_id == item.definition_id for p in engine.rules.attacks
        ):
            raise ValidationError("Unsupported combat weapon or attack mode")
    if command.mode_id is not None and (
        command.maneuver not in ATTACK_MANEUVERS | {"feint", "aim", "ready"}
        or engine.rules.gurps_equipment is None
    ):
        raise ValidationError("Weapon mode requires GURPS attack dispatch")
    if (
        command.maneuver in ATTACK_MANEUVERS | {"feint"}
        or command.maneuver == "aim"
        and command.transport_id is not None
    ) and engine.rules.gurps_equipment is not None:
        from wayfarer.engine.simulation.combat.melee import mode

        selected_mode = mode(
            play.rules_context,
            state,
            command.actor_id,
            command.item_id or "",
            command.mode_id,
        )
        if not command.suppression_zones:
            from wayfarer.engine.simulation.combat.objects.locations import validate_target

            validate_target(
                play.rules_context,
                state,
                encounter,
                command.actor_id,
                command.target_id or "",
                selected_mode,
                command.hit_location,
            )
        from wayfarer.engine.simulation.equipment.catalog import MeleeMode

        if command.second_item_id is not None:
            second_mode = mode(
                play.rules_context,
                state,
                command.actor_id,
                command.second_item_id,
                command.second_mode_id,
            )
            if (
                not isinstance(selected_mode, MeleeMode)
                or not isinstance(second_mode, MeleeMode)
                or selected_mode.hands != 1
                or second_mode.hands != 1
            ):
                raise ValidationError("Two-weapon Double requires one-handed melee modes")

        encounter = engine._replace(
            encounter,
            next(p for p in encounter.participants if p.actor_id == command.actor_id).model_copy(
                update={
                    "reach": max(selected_mode.reach) if isinstance(selected_mode, MeleeMode) else 1
                }
            ),
        )
        if command.transport_id is not None:
            from wayfarer.engine.simulation.equipment.catalog import RangedMode
            from wayfarer.engine.simulation.hex_geometry import Hex

            transport = next(
                (t for t in resources.transports if t.id == command.transport_id), None
            )
            participant = next(p for p in encounter.participants if p.actor_id == command.actor_id)
            mount = selected_mode.mount if isinstance(selected_mode, RangedMode) else None
            if (
                transport is None
                or transport.mechanics_version != 2
                or command.actor_id not in transport.occupants
                or mount is None
                or not mount.vehicle_mounted
            ):
                raise ValidationError("Vehicle fire requires its occupant and vehicle-mounted mode")
            if transport.last_turn == resources.game_time:
                raise ConflictError("Vehicle already acted this second")
            if (
                encounter.spatial_kind != "hex"
                or participant.position != Hex(q=transport.q, r=transport.r)
                or participant.hex_facing != transport.facing
            ):
                raise ValidationError("Vehicle and gunner require one synchronized encounter pose")
            crew = next(i for i in resources.items if i.id == command.item_id).mount_crew
            if not set(crew) <= set(transport.occupants):
                raise ValidationError("Vehicle-mounted weapon crew must occupy its vehicle")
    if (
        command.maneuver == "wait"
        and command.wait_trigger is not None
        and command.wait_trigger.stop_thrust
    ):
        if encounter.spatial_kind == "basic":
            raise ValidationError("Basic stop thrust requires explicit GM adjudication")
        from wayfarer.engine.simulation.combat.melee import mode
        from wayfarer.engine.simulation.equipment.catalog import MeleeMode

        assert command.wait_trigger.item_id is not None
        trigger_mode = mode(
            play.rules_context,
            state,
            command.actor_id,
            command.wait_trigger.item_id,
            command.wait_trigger.mode_id,
        )
        assert isinstance(trigger_mode, MeleeMode)
        waiter = next(p for p in encounter.participants if p.actor_id == command.actor_id)
        encounter = engine._replace(
            encounter, waiter.model_copy(update={"reach": max(trigger_mode.reach)})
        )
    return state, encounter


def _preview_turn(
    state: PlayState,
    command: TakeCombatTurn,
    encounter: Encounter,
    context: CombatContext,
    hp: Pool,
    forced: bool,
) -> None:
    play = context.play
    engine = context.engine
    resources = state.resources
    if not forced and not (hp.injury and hp.injury.stunned):
        preview, _, preview_result = engine.take_turn(
            encounter,
            actor_id=command.actor_id,
            maneuver=command.maneuver,
            resources=resources,
            destination=command.destination,
            facing=command.facing,
            posture=command.posture,
            item_id=command.item_id,
            target_id=command.target_id,
            command_id=command.id,
            attack_option=command.attack_option,
            defense_option=command.defense_option,
            wait_trigger=command.wait_trigger,
            step_timing=command.step_timing,
            second_item_id=command.second_item_id,
            second_target_id=command.second_target_id,
            second_mode_id=command.second_mode_id,
            command_json=command.model_dump_json(),
            hex_path=command.hex_path,
            hex_facing=command.hex_facing,
            basic_move=command.basic_move,
            spatial_revision=(
                command.expected_revision + 1 if command.basic_move is not None else None
            ),
            suppression_fire=bool(command.suppression_zones),
        )
        if command.suppression_zones:
            from wayfarer.engine.simulation.combat.ranged import prepare_suppression_fire

            prepare_suppression_fire(
                play.rules_context, state, preview, command, engine.hex_map(preview)
            )
        if preview.pending_defense is not None:
            from wayfarer.engine.simulation.combat.melee import prepare_attack
            from wayfarer.engine.simulation.combat.ranged import prepare_spraying_fire

            preview = prepare_attack(
                play.rules_context,
                state,
                preview,
                preview.pending_defense.mode_id
                if preview.pending_defense.suppression_zone_id is not None
                else command.mode_id,
                hit_location=(
                    "random"
                    if preview.pending_defense.suppression_zone_id is not None
                    else command.hit_location
                ),
                target_item_id=command.target_item_id,
                shots=(
                    preview.pending_defense.shots
                    if preview.pending_defense.suppression_zone_id is not None
                    else command.shots
                ),
            )
            prepare_spraying_fire(play.rules_context, state, preview, command)
        if command.maneuver == "aim" and preview_result.code != "combat.wait_triggered":
            from wayfarer.engine.simulation.combat.maneuver_transitions import observe

            observe(play.rules_context, state, preview, command)
    return None


def _begin_turn(
    state: PlayState, command: TakeCombatTurn, encounter: Encounter, context: CombatContext
) -> tuple[PlayState, Encounter, TakeCombatTurn]:
    play = context.play
    engine = context.engine
    initial_state = context.initial_state
    resuming = context.resuming
    reaction = context.reaction
    resources = state.resources
    hp = next(p for p in state.resources.pools if p.id == "hp:" + command.actor_id)
    from wayfarer.engine.simulation.combat.melee import (
        exertion,
        injury_turn,
        movement,
    )

    participant = next(p for p in encounter.participants if p.actor_id == command.actor_id)
    encounter = engine._replace(
        encounter,
        participant.model_copy(
            update={"movement_allowance": movement(play.rules_context, state, command.actor_id)}
        ),
    )
    forced = participant.forced_do_nothing
    _preview_turn(state, command, encounter, context, hp, forced)
    if not resuming and not reaction:
        state = injury_turn(
            play.rules_context,
            state,
            command.actor_id,
            command.id,
            start=True,
            do_nothing=command.maneuver == "do_nothing"
            or forced
            or bool(hp.injury and hp.injury.stunned),
        )
    resources = state.resources
    started_hp = next(p for p in resources.pools if p.id == hp.id)
    assert started_hp.injury is not None
    allowed = not (started_hp.injury.incapacitated or started_hp.injury.stunned or forced)
    from wayfarer.engine.simulation.combat.objects.combat import worn_stress

    state, encounter = worn_stress(
        play.rules_context,
        state,
        encounter,
        command.actor_id,
        command.id,
    )
    resources = state.resources
    if allowed and command.maneuver != "do_nothing" and not resuming:
        state, allowed = exertion(play.rules_context, state, command.actor_id, command.id)
        resources = state.resources
    if (
        allowed
        and command.item_id
        and command.maneuver in ("attack", "all_out_attack", "move_and_attack", "aim", "feint")
    ):
        from wayfarer.engine.simulation.combat.objects.combat import stress

        state, encounter = stress(
            play.rules_context,
            state,
            encounter,
            command.actor_id,
            command.id,
            (command.item_id,),
        )
        resources = state.resources
        allowed = any(
            i.id == command.item_id
            and i.ready
            and (
                i.condition is None
                or not i.condition.disabled
                or any(
                    old.id == i.id and old.condition and old.condition.disabled
                    for old in initial_state.resources.items
                )
            )
            for i in resources.items
        )
    encounter = engine._replace(
        encounter,
        next(p for p in encounter.participants if p.actor_id == command.actor_id).model_copy(
            update={
                "movement_allowance": movement(play.rules_context, state, command.actor_id)
                if allowed
                else 0
            }
        ),
    )
    if not allowed:
        if command.recover_thrown_item:
            from wayfarer.engine.simulation.combat.thrown.items import undo_recovery

            resources = undo_recovery(initial_state.resources, resources, command.item_id)
            state = state.model_copy(update={"resources": resources})
        elif command.maneuver == "ready" and command.item_id:
            original = next(i for i in initial_state.resources.items if i.id == command.item_id)
            if original.ground is not None:
                resources = resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(update={"ground": original.ground})
                            if i.id == original.id
                            else i
                            for i in resources.items
                        )
                    }
                )
                state = state.model_copy(update={"resources": resources})
        command_for_turn = command.model_copy(
            update={
                "maneuver": "do_nothing",
                "shots": 1,
                "spray_targets": (),
                "suppression_zones": (),
                "reload_ammunition_id": None,
                "unload_ammunition": False,
                "fast_draw": False,
                "cocking_aid_id": None,
                "let_down_bow": False,
                "recover_thrown_item": False,
                "firearm_service": None,
                "firearm_service_skill": "weapon",
                "item_id": None,
                "mode_id": None,
                "target_id": None,
                "destination": None,
                "hex_path": (),
                "hex_facing": None,
                "facing": None,
                "posture": None,
                "attack_option": None,
                "defense_option": None,
                "wait_trigger": None,
                "step_timing": "before",
                "second_item_id": None,
                "second_target_id": None,
                "second_mode_id": None,
                "braced": False,
                "hit_location": None,
                "ready_hand": None,
            }
        )
    else:
        command_for_turn = command
    return state, encounter, command_for_turn


def _prepare_attack_turn(
    state: PlayState,
    command: TakeCombatTurn,
    encounter: Encounter,
    context: CombatContext,
    command_for_turn: TakeCombatTurn,
    resources: ResourceState,
    result: CombatResult,
) -> CombatStep | None:
    if context.engine.rules.gurps_equipment is None or result.code == "combat.wait_triggered":
        return None
    pending = encounter.pending_defense
    if pending is not None and pending.suppression_zone_id is not None:
        from wayfarer.engine.simulation.combat.melee import prepare_attack

        encounter = prepare_attack(
            context.play.rules_context,
            state.model_copy(update={"resources": resources}),
            encounter,
            pending.mode_id,
            hit_location="random",
            shots=pending.shots,
        )
        assert encounter.pending_defense is not None
        result = result.model_copy(update={"available": encounter.pending_defense.allowed})
        return CombatStep(state, encounter, resources, result)
    if command_for_turn.maneuver not in ATTACK_MANEUVERS:
        return None
    if command_for_turn.suppression_zones:
        from wayfarer.engine.simulation.combat.melee import injury_turn
        from wayfarer.engine.simulation.combat.ranged import prepare_suppression_fire

        state, encounter = prepare_suppression_fire(
            context.play.rules_context,
            state.model_copy(update={"resources": resources}),
            encounter,
            command_for_turn,
            context.engine.hex_map(encounter),
        )
        state = injury_turn(
            context.play.rules_context,
            state,
            command.actor_id,
            command.id,
            start=False,
            do_nothing=False,
        )
        return CombatStep(state, encounter, state.resources, result)
    from wayfarer.engine.simulation.combat.melee import prepare_attack
    from wayfarer.engine.simulation.combat.ranged import prepare_spraying_fire

    if command.laser_sight:
        assert encounter.pending_defense is not None
        encounter = encounter.model_copy(
            update={
                "pending_defense": encounter.pending_defense.model_copy(
                    update={"laser_sight": True}
                )
            }
        )
    encounter = prepare_attack(
        context.play.rules_context,
        state,
        encounter,
        command.mode_id,
        hit_location=command.hit_location,
        target_item_id=command.target_item_id,
        shots=command.shots,
    )
    encounter = prepare_spraying_fire(context.play.rules_context, state, encounter, command)
    assert encounter.pending_defense is not None
    if command.transport_id is not None:
        transport = next(t for t in resources.transports if t.id == command.transport_id)
        encounter = encounter.model_copy(
            update={
                "pending_defense": encounter.pending_defense.model_copy(
                    update={
                        "transport_id": transport.id,
                        "vehicle_attack_penalty": transport.attack_penalty,
                        "vehicle_aim_lost": transport.aim_lost,
                    }
                )
            }
        )
        resources = resources.model_copy(
            update={
                "transports": tuple(
                    t.model_copy(
                        update={
                            "attack_penalty": 0,
                            "aim_lost": False,
                            "last_turn": resources.game_time,
                        }
                    )
                    if t.id == transport.id
                    else t
                    for t in resources.transports
                )
            }
        )
        state = state.model_copy(update={"resources": resources})
    assert encounter.pending_defense is not None
    result = result.model_copy(update={"available": encounter.pending_defense.allowed})
    return CombatStep(state, encounter, resources, result)


def _after_turn(
    state: PlayState,
    command: TakeCombatTurn,
    encounter: Encounter,
    context: CombatContext,
    command_for_turn: TakeCombatTurn,
    resources: ResourceState,
    result: CombatResult,
) -> CombatStep:
    from wayfarer.engine.simulation.combat.melee import injury_turn

    play = context.play
    engine = context.engine
    initial_state = context.initial_state
    reaction = context.reaction
    if command_for_turn.second_item_id is not None and result.code != "combat.wait_triggered":
        from wayfarer.engine.simulation.combat.melee import build as build_character

        compiled = build_character(play.rules_context, state, command.actor_id)
        if any(purchase.definition_id == "trait:ambidexterity" for purchase in compiled.purchases):
            attacker = next(p for p in encounter.participants if p.actor_id == command.actor_id)
            encounter = engine._replace(
                encounter,
                attacker.model_copy(
                    update={
                        "maneuver_state": attacker.maneuver_state.model_copy(
                            update={
                                "attack_bonus": 0,
                                "second_attack_penalty": 0,
                            }
                        )
                    }
                ),
            )
    if (
        command_for_turn.maneuver == "ready"
        and engine.rules.gurps_equipment is not None
        and result.code != "combat.wait_triggered"
    ):
        if command_for_turn.reload_ammunition_id is not None:
            from wayfarer.engine.simulation.combat.ranged import reload_weapon

            resources = reload_weapon(
                play.rules_context,
                state.model_copy(update={"resources": resources}),
                command_for_turn,
            )
        if command_for_turn.unload_ammunition:
            from wayfarer.engine.simulation.combat.ranged import unload_weapon

            resources = unload_weapon(
                play.rules_context,
                state.model_copy(update={"resources": resources}),
                command_for_turn,
            )
        if command_for_turn.let_down_bow:
            from wayfarer.engine.simulation.combat.melee import mode
            from wayfarer.engine.simulation.combat.ranged_readiness import let_down
            from wayfarer.engine.simulation.equipment.catalog import RangedMode

            selected = mode(
                play.rules_context,
                state,
                command.actor_id,
                command.item_id or "",
                command.mode_id,
            )
            assert isinstance(selected, RangedMode)
            resources = let_down(
                state.model_copy(update={"resources": resources}),
                command_for_turn,
                selected,
            )
        if command_for_turn.mount_crew:
            from wayfarer.engine.simulation.combat.mounts import assign_crew

            resources = assign_crew(
                play.rules_context,
                state.model_copy(update={"resources": resources}),
                encounter,
                command_for_turn,
            )
        if command_for_turn.escape_entanglement:
            from wayfarer.engine.simulation.combat.entangle_transitions import escape_binding

            encounter = escape_binding(
                play.rules_context,
                state.model_copy(update={"resources": resources}),
                encounter,
                command_for_turn.actor_id,
            )
        if command_for_turn.firearm_service is not None:
            from wayfarer.engine.simulation.combat.firearm_transitions import service

            resources = service(
                play.rules_context,
                state.model_copy(update={"resources": resources}),
                encounter,
                command_for_turn,
            )
        from wayfarer.engine.simulation.combat.objects.locations import bind_ready_hand

        encounter = bind_ready_hand(
            play.rules_context,
            state.model_copy(update={"resources": resources}),
            encounter,
            command.actor_id,
            command.item_id or "",
            command.ready_hand,
        )
        from wayfarer.engine.simulation.combat.unarmed import grapple_ready

        state, encounter = grapple_ready(
            play.rules_context,
            state.model_copy(update={"resources": resources}),
            encounter,
            command_for_turn,
        )
        resources = state.resources
    if engine.rules.gurps_equipment is not None and result.code != "combat.wait_triggered":
        acted = next(p for p in encounter.participants if p.actor_id == command.actor_id)
        encounter = engine._replace(
            encounter, acted.model_copy(update={"forced_do_nothing": False})
        )
    if (
        command_for_turn.maneuver in ("aim", "feint") or command_for_turn.attack_option == "feint"
    ) and result.code != "combat.wait_triggered":
        from wayfarer.engine.simulation.combat.maneuver_transitions import observe

        encounter = observe(play.rules_context, state, encounter, command_for_turn)
    prepared = _prepare_attack_turn(
        state, command, encounter, context, command_for_turn, resources, result
    )
    if prepared is not None:
        state, encounter, resources, result = (
            prepared.state,
            prepared.encounter,
            prepared.resources,
            prepared.result,
        )
    elif (
        engine.rules.gurps_equipment is not None
        and not reaction
        and result.code != "combat.wait_triggered"
    ):
        state = injury_turn(
            play.rules_context,
            state.model_copy(update={"resources": resources}),
            command.actor_id,
            command.id,
            start=False,
            do_nothing=command_for_turn.maneuver == "do_nothing",
        )
        resources = state.resources
    if (
        command_for_turn.maneuver == "aim"
        and command.transport_id is not None
        and result.code != "combat.wait_triggered"
    ):
        resources = resources.model_copy(
            update={
                "transports": tuple(
                    t.model_copy(update={"aim_lost": False, "last_turn": resources.game_time})
                    if t.id == command.transport_id
                    else t
                    for t in resources.transports
                )
            }
        )
        state = state.model_copy(update={"resources": resources})
    if command.recover_thrown_item and result.code == "combat.wait_triggered":
        from wayfarer.engine.simulation.combat.thrown.items import undo_recovery

        resources = undo_recovery(initial_state.resources, resources, command.item_id)
        state = state.model_copy(update={"resources": resources})
    return CombatStep(state, encounter, resources, result)


def _take_turn(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    engine = context.engine
    assert isinstance(command, TakeCombatTurn)
    state, encounter = _validate_turn(state, command, encounter, context)
    if context.engine.rules.gurps_equipment is not None:
        state, encounter, command_for_turn = _begin_turn(state, command, encounter, context)
    else:
        command_for_turn = command
    resources = state.resources
    encounter, resources, result = engine.take_turn(
        encounter,
        actor_id=command.actor_id,
        maneuver=command_for_turn.maneuver,
        resources=resources,
        destination=command_for_turn.destination,
        facing=command_for_turn.facing,
        posture=command_for_turn.posture,
        item_id=command_for_turn.item_id,
        target_id=command_for_turn.target_id,
        command_id=command.id,
        attack_option=command_for_turn.attack_option,
        defense_option=command_for_turn.defense_option,
        wait_trigger=command_for_turn.wait_trigger,
        step_timing=command_for_turn.step_timing,
        second_item_id=command_for_turn.second_item_id,
        second_target_id=command_for_turn.second_target_id,
        second_mode_id=command_for_turn.second_mode_id,
        command_json=command_for_turn.model_dump_json(),
        hex_path=command_for_turn.hex_path,
        hex_facing=command_for_turn.hex_facing,
        basic_move=command_for_turn.basic_move,
        spatial_revision=(
            command.expected_revision + 1 if command_for_turn.basic_move is not None else None
        ),
        suppression_fire=bool(command_for_turn.suppression_zones),
    )
    return _after_turn(state, command, encounter, context, command_for_turn, resources, result)


def _defend(
    state: PlayState, command: TypedCombatCommand, encounter: Encounter, context: CombatContext
) -> CombatStep:
    play = context.play
    engine = context.engine
    resources = state.resources
    assert isinstance(command, ChooseDefense)
    if encounter.pending_unarmed is not None:
        return _unarmed(state, command, encounter, context)
    if command.catch_thrown:
        from wayfarer.engine.simulation.combat.thrown.items import validate_catch

        validate_catch(play.rules_context, state, encounter, command)
    from wayfarer.engine.simulation.abilities import interrupt_concentration
    from wayfarer.engine.simulation.magic.effects import require_not_dazed

    if command.defense != "none":
        require_not_dazed(resources, command.actor_id)
        resources = interrupt_concentration(
            resources, command.actor_id, command.id, distraction=True
        )
        state = state.model_copy(update={"resources": resources})
    previous = encounter
    selected_defense = command.defense
    if engine.rules.gurps_equipment is not None:
        from wayfarer.engine.simulation.combat.melee import exertion, resolve_melee

        pending = encounter.pending_defense
        if (
            pending is None
            or pending.defender_id != command.actor_id
            or command.defense not in pending.allowed
        ):
            raise ValidationError("Defense is not available to this actor")
        from wayfarer.engine.simulation.combat.melee import validate_defense_choices

        validate_defense_choices(
            play.rules_context,
            state,
            encounter,
            selected_defense,
            command.item_id,
            command.second_defense,
            command.second_item_id,
            parry_mode_id=command.parry_mode_id,
            second_parry_mode_id=command.second_parry_mode_id,
        )
        if selected_defense != "none":
            state, allowed = exertion(play.rules_context, state, command.actor_id, command.id)
            if not allowed:
                selected_defense = "none"
        from wayfarer.engine.simulation.combat.objects.combat import worn_stress

        state, encounter = worn_stress(
            play.rules_context,
            state,
            encounter,
            command.actor_id,
            command.id,
        )
        if selected_defense != "none":
            from wayfarer.engine.simulation.combat.melee import defense_value
            from wayfarer.engine.simulation.combat.objects.combat import defense_stress

            participant = next(p for p in encounter.participants if p.actor_id == command.actor_id)
            _, used = defense_value(
                play.rules_context,
                state,
                participant,
                selected_defense,
                command.item_id,
                parry_mode_id=command.parry_mode_id,
            )
            state, encounter = defense_stress(
                play.rules_context,
                state,
                encounter,
                command.actor_id,
                command.id,
                None if used in ("left-hand", "right-hand") else used,
            )
            if (
                used
                and used not in ("left-hand", "right-hand")
                and not any(i.id == used and i.ready for i in state.resources.items)
            ):
                selected_defense = "none"
        state, encounter, injury = resolve_melee(
            play.rules_context,
            state,
            encounter,
            selected_defense,
            command.item_id,
            second_defense=command.second_defense if selected_defense != "none" else None,
            second_item_id=command.second_item_id if selected_defense != "none" else None,
            parry_mode_id=command.parry_mode_id if selected_defense != "none" else None,
            second_parry_mode_id=command.second_parry_mode_id
            if selected_defense != "none"
            else None,
            catch_thrown=command.catch_thrown,
        )
        from wayfarer.engine.simulation.combat.melee import injury_turn

        attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
        if (
            not attacker.maneuver_state.attacks_remaining
            and (encounter.wait_interrupt is None or not encounter.wait_interrupt.reacting)
            and not (encounter.pending_defense and encounter.pending_defense.spray_targets)
            and pending.suppression_zone_id is None
        ):
            state = injury_turn(
                play.rules_context,
                state,
                pending.attacker_id,
                pending.id,
                start=False,
                do_nothing=False,
            )
        if pending.suppression_zone_id is not None and not any(
            any(
                zone.id == queued.zone_id and zone.remaining_hits > 0
                for zone in encounter.suppression_zones
            )
            for queued in pending.suppression_attacks
        ):
            assert pending.interrupted_actor_id is not None
            state = injury_turn(
                play.rules_context,
                state,
                pending.interrupted_actor_id,
                pending.id,
                start=False,
                do_nothing=False,
            )
        resources = state.resources
    elif (
        command.item_id is not None
        or command.second_defense is not None
        or command.second_item_id is not None
    ):
        raise ValidationError("Defense equipment selection requires GURPS dispatch")
    encounter, result = engine.choose_defense(
        encounter, actor_id=command.actor_id, selected=selected_defense
    )
    if encounter.pending_defense is not None and engine.rules.gurps_equipment is not None:
        from wayfarer.engine.simulation.combat.melee import prepare_attack

        queued = encounter.pending_defense
        encounter = prepare_attack(
            play.rules_context,
            state,
            encounter,
            queued.mode_id,
            hit_location=queued.hit_location,
            target_item_id=queued.target_item_id,
            shots=queued.shots,
        )
    if engine.rules.attacks or engine.rules.gurps_equipment is not None:
        if engine.rules.gurps_equipment is None:
            state, injury = resolve_injury(play.rules_context, state, previous, command.defense)
        resources = state.resources
        encounter = encounter.model_copy(update={"wounds": encounter.wounds + (injury,)})
        alive = {
            p.id.removeprefix("hp:")
            for p in resources.pools
            if p.id.startswith("hp:")
            and (not p.injury.incapacitated if p.injury else p.current > 0)
        }
        if len(alive.intersection(encounter.turn_order)) < 2:
            encounter = encounter.model_copy(
                update={
                    "status": "completed",
                    "completion_reason": "incapacitation",
                    "pending_defense": None,
                    "wait_interrupt": None,
                }
            )
        else:
            while encounter.current_actor_id not in alive:
                encounter = engine._advance(encounter)
        result = result.model_copy(
            update={
                "code": "combat.resolved",
                "injury": injury,
                "round": encounter.round,
                "current_actor_id": encounter.current_actor_id,
                "available": engine.available(encounter, encounter.current_actor_id),
            }
        )
    return CombatStep(state, encounter, resources, result, defense_before=previous)


def _settle_combat(
    step: CombatStep,
    command: TypedCombatCommand,
    encounters: tuple[Encounter, ...],
    context: CombatContext,
) -> tuple[CombatStep, tuple[Encounter, ...]]:
    play = context.play
    engine = context.engine
    initial_state = context.initial_state
    state = step.state
    encounter = step.encounter
    resources = step.resources
    result = step.result
    if (
        engine.rules.gurps_equipment is not None
        and encounter.pending_defense is None
        and encounter.pending_unarmed is None
        and encounter.status == "active"
    ):
        from wayfarer.engine.simulation.combat.melee import fatigue_ready

        conscious = {
            p.id.removeprefix("hp:")
            for p in resources.pools
            if p.id.startswith("hp:")
            and p.injury is not None
            and not p.injury.incapacitated
            and fatigue_ready(
                state.model_copy(update={"resources": resources}), p.id.removeprefix("hp:")
            )
        }
        if len(conscious.intersection(encounter.turn_order)) < 2:
            encounter = encounter.model_copy(
                update={"status": "completed", "completion_reason": "incapacitation"}
            )
        else:
            while encounter.current_actor_id not in conscious:
                encounter = engine._advance(encounter)
        encounters = tuple(encounter if e.id == encounter.id else e for e in encounters)
        result = result.model_copy(
            update={
                "round": encounter.round,
                "current_actor_id": encounter.current_actor_id,
                "available": engine.available(encounter, encounter.current_actor_id),
            }
        )
    if engine.rules.gurps_equipment is not None:
        from wayfarer.engine.simulation.combat.unarmed import retire_chokes, settle_control

        prior_grips = next((e.grips for e in initial_state.encounters if e.id == encounter.id), ())
        encounter = settle_control(state.model_copy(update={"resources": resources}), encounter)
        state = retire_chokes(
            play.rules_context,
            state.model_copy(update={"resources": resources}),
            prior_grips,
            encounter.grips,
            command.id,
        )
        resources = state.resources
        encounters = tuple(encounter if e.id == encounter.id else e for e in encounters)
    if encounter.status == "completed" and engine.rules.gurps_equipment is not None:
        from wayfarer.engine.simulation.combat.objects.locations import settle_crippling

        state = settle_crippling(
            play.rules_context,
            state.model_copy(update={"resources": resources}),
            encounter,
            command.id,
        )
        resources = state.resources
    if engine.rules.gurps_equipment is not None:
        held = {i.id for i in resources.items if i.ready and i.equipped}
        hands = {
            p.actor_id: tuple((i, h) for i, h in p.hand_bindings if i in held)
            for p in encounter.participants
        }
        encounter = encounter.model_copy(
            update={
                "participants": tuple(
                    p.model_copy(
                        update={
                            "hand_bindings": hands[p.actor_id],
                            "ready_item_ids": tuple(
                                sorted(
                                    i.id
                                    for i in resources.items
                                    if i.owner_id == p.actor_id and i.ready and i.equipped
                                )
                            ),
                        }
                    )
                    for p in encounter.participants
                )
            }
        )
        encounters = tuple(encounter if e.id == encounter.id else e for e in encounters)
        state = state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(update={"held_item_hands": hands[a.actor_id]})
                    if a.actor_id in hands
                    else a
                    for a in state.actors
                )
            }
        )
    return replace(
        step, state=state, encounter=encounter, resources=resources, result=result
    ), encounters


def _finish_combat(
    step: CombatStep,
    command: TypedCombatCommand,
    encounters: tuple[Encounter, ...],
    context: CombatContext,
) -> tuple[PlayState, CombatResult]:
    play = context.play
    engine = context.engine
    initial_state = context.initial_state
    state = step.state
    encounter = step.encounter
    resources = step.resources
    result = step.result
    blast_deferred_ticks = step.blast_deferred_ticks
    party = state.party
    if party.groups:
        participants = set(encounter.turn_order)
        involved = tuple(g for g in party.groups if set(g.actor_ids) & participants)
        if len(involved) != 1 or not participants <= set(involved[0].actor_ids):
            raise ValidationError("Combat participants must share one subgroup")
        group = involved[0]
        if group.paused or any(q.group_id == group.id for q in party.queue):
            raise ConflictError("Combat subgroup is paused or has pending activity")
        if group.ready_through > resources.game_time:
            raise ConflictError("Combat waits at the shared-time barrier")
        prior = next((e for e in state.encounters if e.id == encounter.id), None)
        ticks = _elapsed_combat_ticks(prior, encounter) + blast_deferred_ticks
        from wayfarer.engine.simulation.combat.explosions import defer_round

        resources, ticks = defer_round(resources, encounter.id, ticks, command.id)
        party = party.model_copy(
            update={
                "groups": tuple(
                    g.model_copy(update={"ready_through": g.ready_through + ticks})
                    if g.id == group.id
                    else g
                    for g in party.groups
                )
            }
        )
    elif (
        engine.rules.attacks
        or engine.rules.gurps_equipment is not None
        or play.engine.rules.abilities
    ):
        prior = next((e for e in state.encounters if e.id == encounter.id), None)
        ticks = _elapsed_combat_ticks(prior, encounter) + blast_deferred_ticks
        from wayfarer.engine.simulation.combat.explosions import defer_round

        resources, ticks = defer_round(resources, encounter.id, ticks, command.id)
        if ticks:
            resources = play.engine.resources.apply(
                resources,
                Advance(
                    id="combat-time:" + hashlib.sha256(command.id.encode()).hexdigest()
                    if engine.rules.gurps_equipment
                    else f"{command.id}:round-time",
                    actor_id=encounter.current_actor_id,
                    expected_revision=resources.revision,
                    to=resources.game_time + ticks,
                ),
                system=True,
                rng=play.rng,
            )
    from wayfarer.engine.simulation.combat.ranged_readiness import interrupted_draws

    resources = interrupted_draws(
        play.rules_context, initial_state, resources, encounter.id, encounter
    )
    revision = state.revision + 1
    if encounter.spatial_kind == "hex":
        from wayfarer.engine.simulation.combat.tactical import TacticalTrace

        checks: tuple[CheckTrace, ...] = (result.injury.attack,) if result.injury else ()
        if result.injury and result.injury.defense:
            checks += (result.injury.defense,)
        if result.unarmed:
            checks = result.unarmed.checks
        trace = TacticalTrace(
            command_id=command.id,
            actor_id=command.actor_id,
            code=result.code,
            totals=tuple(c.total for c in checks),
            targets=tuple(c.effective_target for c in checks),
            injury=result.injury.injury
            if result.injury
            else result.unarmed.injury
            if result.unarmed
            else 0,
        )
        encounter = encounter.model_copy(
            update={"tactical_traces": (encounter.tactical_traces + (trace,))[-50:]}
        )
        encounters = tuple(encounter if e.id == encounter.id else e for e in encounters)
    resources = resources.model_copy(update={"revision": revision})
    updated = state.model_copy(
        update={
            "revision": revision,
            "party": party,
            "resources": resources,
            "encounters": encounters,
            "last_combat_result": result,
            "rulings": expire_rulings(state.rulings, revision, resources.game_time),
        }
    )
    if updated.party.groups:
        from wayfarer.orchestration.party import PartyService

        updated = PartyService(play).flush(updated)
    if (
        isinstance(command, ChooseDefense)
        and engine.rules.attacks
        and encounter.status == "completed"
        and encounter.spatial_kind != "basic"
    ):
        previous = step.defense_before
        injury = result.injury
        assert previous is not None and injury is not None
        world = updated.world
        for consequence in engine.rules.consequences:
            if (
                consequence.battlefield_id
                in (
                    encounter.battlefield_id,
                    play.rules_context.require_hex(encounter).source_template_id
                    if encounter.spatial_kind == "hex"
                    else None,
                )
                and previous.pending_defense is not None
                and consequence.defeated_actor_id == previous.pending_defense.defender_id
                and injury.incapacitated
            ):
                for recipient in consequence.recipient_actor_ids:
                    for fact in consequence.fact_ids:
                        world = world.learn(recipient, fact)
        updated = updated.model_copy(update={"world": world})
    if isinstance(command, TakeCombatTurn):
        from wayfarer.engine.simulation.magic.area_fire import crossings

        updated = crossings(
            play.rules_context,
            updated,
            initial_state,
            command.actor_id,
            command.encounter_id,
            command.hex_path,
            command.id,
        )
    return updated, result


_COMBAT_STEPS: dict[
    str, Callable[[PlayState, TypedCombatCommand, Encounter, CombatContext], CombatStep]
] = {
    "declare_basic_spatial_facts": _declare_basic_facts,
    "migrate_encounter_hex": _migrate,
    "migrate_encounter_basic": _migrate_basic,
    "resolve_weapon_explosion": _explosion,
    "declare_thrown_landing": _landing,
    "continue_critical_miss": _critical,
    "retrieve_equipment": _retrieve,
    "repair_equipment": _repair,
    "resolve_choke_effects": _choke,
    "take_unarmed_turn": _unarmed,
    "join_encounter": _join,
    "withdraw_encounter": _withdraw,
    "end_encounter": _end,
    "take_combat_turn": _take_turn,
    "choose_defense": _defend,
}


def reduce_combat(
    state: PlayState, command: TypedCombatCommand, context: CombatContext
) -> tuple[PlayState, CombatResult]:
    state, command, context = _prepare_command(state, command, context)
    if isinstance(command, (StartEncounter, StartBasicEncounter)):
        step = (
            _start_encounter(state, command, context)
            if isinstance(command, StartEncounter)
            else _start_basic_encounter(state, command, context)
        )
        encounters = step.state.encounters + (step.encounter,)
    else:
        encounter = _prepare_encounter(state, command, context)
        step = _COMBAT_STEPS[command.kind](state, command, encounter, context)
        if isinstance(command, ChooseDefense):
            from wayfarer.engine.simulation.combat.tactical_transitions import finish_defense

            step = replace(
                step,
                encounter=finish_defense(
                    context.play.rules_context, step.state, step.encounter, command
                ),
            )
        encounters = tuple(
            step.encounter if e.id == step.encounter.id else e for e in step.state.encounters
        )
    step, encounters = _settle_combat(step, command, encounters, context)
    return _finish_combat(step, command, encounters, context)
