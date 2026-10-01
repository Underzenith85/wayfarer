"""Approved purchase declaration and current scene authority for B61/B201/B366.

This family deliberately has no transport adapter. Its commitments use the same
turn, exertion, injury, encounter, defense and settlement records as combat.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.character.traits.attack_defense import attack_defense_traits
from wayfarer.engine.rules.tables.ranged import range_penalty
from wayfarer.engine.rules.types.hazard import require_hazards_settled
from wayfarer.engine.rules.types.recovery import require_settled
from wayfarer.engine.simulation.abilities import interrupt_concentration
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, exertion, injury_turn, movement
from wayfarer.engine.simulation.combat.encounter import (
    CombatResult,
    Encounter,
    PendingDefense,
    RangedSituation,
    basic_distance,
)
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.equipment_effects import worn_stress
from wayfarer.engine.simulation.combat.physical_defenses import physical_defenses
from wayfarer.engine.simulation.combat.ranged.situation import situation
from wayfarer.engine.simulation.combat.sensory_state import evidence
from wayfarer.engine.simulation.combat.spatial import BasicSpatialContext, CoverSpatialFact
from wayfarer.engine.simulation.combat.tactical import pose
from wayfarer.engine.simulation.combat.turn_commitment import prepare
from wayfarer.engine.simulation.combat.visibility import (
    combat_visibility,
    targetable,
    visible_actors,
)
from wayfarer.engine.simulation.combat.vocabulary import Maneuver
from wayfarer.engine.simulation.health.fright import maneuver_allowed
from wayfarer.engine.simulation.health.hit_locations import disabled
from wayfarer.engine.simulation.health.recovery_guard import guard
from wayfarer.engine.simulation.health.symptom_state import acute_blindness
from wayfarer.engine.simulation.hex_geometry import ranged_distance
from wayfarer.engine.simulation.magic.area_fire import armor
from wayfarer.engine.simulation.magic.concentration import require_idle_concentration
from wayfarer.engine.simulation.magic.effects import require_not_dazed
from wayfarer.engine.simulation.resources import Command, ResourceEvent
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.traits.composed_attacks import AttackCompositionContext
from wayfarer.engine.simulation.traits.composed_sources import (
    PENDING_PREFIX,
    BindComposedSource,
    ComposedPending,
    ComposedSource,
    current_source,
    identity,
    pending_binding,
    raw_build,
)
from wayfarer.engine.simulation.traits.innate_criticals import require_innate_action
from wayfarer.engine.simulation.traits.size_forms import size_delta
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id


class UseComposedAttack(Command):
    kind: Literal["declare", "aim"]
    encounter_id: Id
    source_id: Id
    target_id: Id


class ResistComposedAttack(Command):
    kind: Literal["resist"] = "resist"
    encounter_id: Id
    pending_id: Id
    resist: bool


class AbandonComposedAttack(Command):
    kind: Literal["abandon"] = "abandon"
    encounter_id: Id
    pending_id: Id


class ContinueComposedCritical(Command):
    kind: Literal["continue-critical"] = "continue-critical"
    encounter_id: Id
    pending_id: Id
    critical_id: Id
    context_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_id: Id
    reason: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    effect: Literal["lose-balance", "disable-source"]
    duration_seconds: int | None = Field(default=None, ge=1, le=31536000)


ComposedCommand = (
    BindComposedSource
    | UseComposedAttack
    | ResistComposedAttack
    | AbandonComposedAttack
    | ContinueComposedCritical
)
ADAPTER: TypeAdapter[ComposedCommand] = TypeAdapter(
    Annotated[ComposedCommand, Field(discriminator="kind")]
)


@dataclass(frozen=True)
class CurrentAttack:
    source: ComposedSource
    attacker: ValidatedBuild
    target: ValidatedBuild
    context: AttackCompositionContext
    resistance: int
    speed: float
    size: int
    visibility_penalty: int


def attack_target(
    runtime: RulesContext, state: PlayState, encounter: Encounter, current: CurrentAttack
) -> int:
    source = current.source
    attacker = build(runtime, state, source.actor_id)
    assert attacker.statistics is not None
    trained = {
        v.target: int(v.value)
        for v in attacker.sheet.values
        if v.target.startswith("skill:innate-attack-")
    }
    skill = trained.get(
        "skill:innate-attack-" + source.specialty,
        max([attacker.statistics.dx - 4] + [v - 2 for v in trained.values()]),
    )
    hp = next(p for p in state.resources.pools if p.id == "hp:" + source.actor_id)
    target = skill + range_penalty(current.context.distance_yards + current.speed)
    target += current.size + size_delta(state.resources, current.context.target_id)
    target += current.visibility_penalty
    if current.context.aim_seconds:
        target += source.profile.accuracy + max(0, current.context.aim_seconds - 1)
    if hp.injury is not None:
        target -= hp.injury.shock
        target += hp.injury.physical_traits.darkness(encounter.darkness_penalty)
    if target < 3:
        raise ValidationError("Composed attack effective skill is below three")
    return target


def _require_subjects(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    source: ComposedSource,
    target_id: str,
) -> ValidatedBuild:
    if encounter.status != "active" or {source.actor_id, target_id} - set(encounter.turn_order):
        raise ConflictError("Composed attack participants are no longer active")
    if source.actor_id == target_id:
        raise ValidationError("Composed attack requires another actor")
    for actor_id in (source.actor_id, target_id):
        hp = next((p for p in state.resources.pools if p.id == "hp:" + actor_id), None)
        actor = next(a for a in state.actors if a.actor_id == actor_id)
        if hp is None or hp.injury is None or hp.injury.incapacitated or actor.conditions:
            raise ConflictError(
                "Composed attack participant is incapacitated; retire the pending use"
            )
    return raw_build(runtime, state, target_id)


def _require_perception(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    source: ComposedSource,
    target_id: str,
) -> None:
    if not targetable(encounter, source.actor_id, target_id, state=state):
        raise ValidationError("Composed attack requires current target perception")
    if encounter.spatial_kind == "hex" and not acute_blindness(state.resources, source.actor_id):
        if target_id not in visible_actors(
            state, encounter, source.actor_id, board=runtime.hex_map(encounter)
        ):
            raise ValidationError("Composed attack target is not currently visible")
    if isinstance(encounter.spatial, BasicSpatialContext):
        cover = encounter.spatial.active("cover", source.actor_id, target_id)
        if not isinstance(cover, CoverSpatialFact) or cover.cover == "full":
            raise ValidationError("Composed attack requires current unblocked cover facts")


def _delivery_scene(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    source: ComposedSource,
    target_id: str,
) -> RangedSituation:
    if source.emitter_limb is not None and source.emitter_limb in disabled(
        state.resources, source.actor_id
    ):
        raise ValidationError("The bound emitting arm is disabled")
    if source.specialty in ("beam", "projectile"):
        participant = next(p for p in encounter.participants if p.actor_id == source.actor_id)
        if participant.pinned or participant.arm_locked:
            raise ValidationError("Innate Attack specialty requires an unrestrained aiming hand")
    profile = source.profile
    if profile.malediction_range == "none":
        scene = situation(runtime, encounter, source.actor_id, target_id)
    else:
        # B106 uses clear perception and range, not a conventional shot's firing arc.
        if acute_blindness(state.resources, source.actor_id):
            proof = evidence(state, encounter, source.actor_id, target_id)
            if proof is None or proof.exact_location is None:
                raise ValidationError("Malediction requires a clearly perceived target")
        actor_pose = next(p for p in encounter.participants if p.actor_id == source.actor_id)
        target_pose = next(p for p in encounter.participants if p.actor_id == target_id)
        distance = (
            basic_distance(encounter, source.actor_id, target_id)
            if encounter.spatial_kind == "basic"
            else float(
                ranged_distance(
                    runtime.require_hex(encounter),
                    pose(actor_pose).position,
                    pose(target_pose).position,
                )
            )
        )
        scene = RangedSituation(
            attacker_id=source.actor_id,
            defender_id=target_id,
            distance_yards=distance,
            speed_yards_per_second=0,
            size_modifier=0,
        )
    if profile.malediction_range == "none":
        if acute_blindness(state.resources, source.actor_id):
            raise ValidationError("Blind ordinary composed delivery requires a supported consumer")
        if scene.distance > profile.max_range:
            raise ValidationError("Target exceeds approved composed attack range")
    return scene


def _scene_location(state: PlayState, source: ComposedSource, target_id: str) -> str:
    attacker_entity = next(e for e in state.world.entities if e.id == source.actor_id)
    target_entity = next(e for e in state.world.entities if e.id == target_id)
    if (
        attacker_entity.location_id is None
        or attacker_entity.location_id != target_entity.location_id
    ):
        raise ValidationError("Composed attack requires current shared scene location")
    return attacker_entity.location_id


def _vision_contact(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    source: ComposedSource,
    target_id: str,
) -> bool:
    vision_contact = not acute_blindness(state.resources, target_id) and targetable(
        encounter, target_id, source.actor_id, state=state
    )
    if encounter.spatial_kind == "hex" and vision_contact:
        vision_contact = source.actor_id in visible_actors(
            state, encounter, target_id, board=runtime.hex_map(encounter)
        )
    if source.profile.penetration_sense == "vision" and not vision_contact:
        raise ValidationError(
            "Vision-Based Malediction requires the victim's current vision contact"
        )
    return vision_contact


def current_attack(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    source: ComposedSource,
    target_id: str,
) -> CurrentAttack:
    current, attacker = current_source(runtime, state, source.actor_id, source.id)
    if current.source_revision != source.source_revision:
        raise ConflictError("Pending composed attack source changed; abandon the spent use")
    target = _require_subjects(runtime, state, encounter, source, target_id)
    _require_perception(runtime, state, encounter, source, target_id)
    scene = _delivery_scene(runtime, state, encounter, source, target_id)
    location_id = _scene_location(state, source, target_id)
    participant = next(p for p in encounter.participants if p.actor_id == source.actor_id)
    aim = participant.maneuver_state
    profile = source.profile
    vision_contact = _vision_contact(runtime, state, encounter, source, target_id)
    sensory = combat_visibility(encounter, source.actor_id, target_id, state=state)
    context = AttackCompositionContext(
        channel_id=source.id,
        target_id=target_id,
        location_id=location_id,
        distance_yards=scene.distance,
        maneuver="attack" if profile.malediction_range == "none" else "concentrate",
        specialty=source.specialty,
        aim_seconds=aim.aim_seconds
        if (aim.aim_item_id, aim.aim_target_id, aim.aim_mode_id)
        == (source.id, target_id, source.source_revision)
        else 0,
        defense="none",
        target_perceived=True,
        vision_contact=vision_contact,
        contagion_vector=source.contagion_vector,
        incubation_seconds=source.incubation_seconds,
        defender_attack_awareness="current authoritative perception" if sensory.defenses else None,
    )
    natural = attack_defense_traits(
        target, runtime.reviewer.compiler.definitions
    ).damage_resistance()
    return CurrentAttack(
        source,
        attacker,
        target,
        context,
        natural + armor(runtime, state, target_id),
        scene.speed_yards_per_second,
        scene.size_modifier,
        sensory.attack_penalty,
    )


def preflight_pending(
    runtime: RulesContext, state: PlayState, encounter: Encounter
) -> CurrentAttack:
    pending = encounter.pending_defense
    if pending is None:
        raise ConflictError("No composed attack awaits a response")
    binding = pending_binding(state.resources, encounter, pending)
    if binding.campaign_id != state.campaign_id:
        raise ValidationError("Composed pending campaign changed")
    current = current_attack(runtime, state, encounter, binding.source, binding.target_id)
    if binding.stage == "defense":
        attack_target(runtime, state, encounter, current)
    return current


def _guard_turn(
    runtime: RulesContext, state: PlayState, encounter: Encounter, command: UseComposedAttack
) -> None:
    if state.lifecycle != "active" or state.revision != command.expected_revision:
        raise ConflictError("Composed attack requires the current active play revision")
    if (
        encounter.status != "active"
        or encounter.current_actor_id != command.actor_id
        or encounter.pending_defense
        or encounter.pending_unarmed
        or encounter.blocked_reason
        or encounter.wait_interrupt
    ):
        raise ConflictError("Encounter cannot accept this composed maneuver")
    if encounter.spatial_kind not in ("basic", "hex"):
        raise ValidationError("Composed host requires Basic or hex spatial context")
    actor = next(a for a in state.actors if a.actor_id == command.actor_id)
    if actor.available_at > state.resources.game_time:
        raise ValidationError("Actor is recovering from injury")
    participant = next(p for p in encounter.participants if p.actor_id == command.actor_id)
    if (
        participant.high_speed
        or participant.pinned
        or any(command.actor_id in (g.holder_id, g.target_id) for g in encounter.grips)
    ):
        raise ValidationError("Composed maneuver requires a free actor without unfinished movement")
    if any(p.maneuver_state.wait for p in encounter.participants):
        raise ValidationError(
            "Composed attack with a pending Wait requires a supported reaction adapter"
        )
    guard(state, command.actor_id, "take_combat_turn", allow_fright=True)
    require_settled(
        state.resources.recovery_tasks,
        frozenset({command.actor_id, command.target_id}),
        state.resources.game_time,
    )
    require_hazards_settled(
        state.resources.hazards,
        frozenset({command.actor_id, command.target_id}),
        state.resources.game_time,
    )
    require_not_dazed(state.resources, command.actor_id)


def declare(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    command: UseComposedAttack,
) -> tuple[PlayState, Encounter, CombatResult]:
    engine = runtime.combat
    if not isinstance(engine, CombatEngine) or engine.rules.gurps_equipment is None:
        raise ValidationError("Composed attack requires Basic Set combat")
    _guard_turn(runtime, state, encounter, command)
    source, _ = current_source(runtime, state, command.actor_id, command.source_id)
    current = current_attack(runtime, state, encounter, source, command.target_id)
    maneuver: Maneuver = (
        "aim"
        if command.kind == "aim"
        else "concentrate"
        if source.profile.malediction_range != "none"
        else "attack"
    )
    require_innate_action(state.resources, encounter, command.actor_id, source_id=source.id)
    if maneuver == "attack":
        attack_target(runtime, state, encounter, current)
    if maneuver == "aim" and source.profile.malediction_range != "none":
        raise ValidationError("Malediction cannot Aim")
    if not maneuver_allowed(state.resources, command.actor_id, maneuver):
        raise ValidationError("Fright condition prevents this composed maneuver")
    if maneuver == "concentrate":
        require_idle_concentration(state.resources, command.actor_id)
    else:
        state = state.model_copy(
            update={
                "resources": interrupt_concentration(state.resources, command.actor_id, command.id)
            }
        )
    participant = next(p for p in encounter.participants if p.actor_id == command.actor_id)
    forced = participant.forced_do_nothing
    hp = next(p for p in state.resources.pools if p.id == "hp:" + command.actor_id)
    state = injury_turn(
        runtime,
        state,
        command.actor_id,
        command.id,
        start=True,
        do_nothing=forced or bool(hp.injury and hp.injury.stunned),
    )
    hp = next(p for p in state.resources.pools if p.id == hp.id)
    allowed = not (forced or hp.injury and (hp.injury.incapacitated or hp.injury.stunned))
    state, encounter = worn_stress(runtime, state, encounter, command.actor_id, command.id)
    if allowed:
        state, allowed = exertion(runtime, state, command.actor_id, command.id)
    if not allowed:
        maneuver = "do_nothing"
    participant = next(p for p in encounter.participants if p.actor_id == command.actor_id)
    participant = prepare(
        engine,
        encounter,
        participant,
        state.resources,
        actor_id=command.actor_id,
        maneuver=maneuver,
        item_id=source.id,
        target_id=command.target_id,
        attack_option=None,
        defense_option=None,
        wait_trigger=None,
        second_item_id=None,
        second_target_id=None,
        second_mode_id=None,
        basic=encounter.spatial_kind == "basic",
        composed_source_id=source.id,
    )
    if maneuver == "aim":
        participant = participant.model_copy(
            update={
                "maneuver_state": participant.maneuver_state.model_copy(
                    update={
                        "aim_accuracy": source.profile.accuracy,
                        "aim_mode_id": source.source_revision,
                        "aim_seconds": participant.maneuver_state.aim_seconds
                        if next(
                            p
                            for e in state.encounters
                            if e.id == encounter.id
                            for p in e.participants
                            if p.actor_id == command.actor_id
                        ).maneuver_state.aim_mode_id
                        == source.source_revision
                        else 1,
                    }
                )
            }
        )
    participant = participant.model_copy(
        update={
            "last_maneuver": maneuver,
            "last_attack_item_id": source.id
            if maneuver == "attack"
            else participant.last_attack_item_id,
            "movement_allowance": movement(runtime, state, command.actor_id) if allowed else 0,
        }
    )
    encounter = engine._replace(encounter, participant)
    if maneuver in ("aim", "do_nothing"):
        state = injury_turn(
            runtime,
            state,
            command.actor_id,
            command.id,
            start=False,
            do_nothing=maneuver == "do_nothing",
        )
        encounter = engine._advance(encounter)
        return (
            state,
            encounter,
            CombatResult(
                encounter_id=encounter.id,
                code="combat." + maneuver,
                round=encounter.round,
                current_actor_id=encounter.current_actor_id,
                available=engine.available(encounter, encounter.current_actor_id),
            ),
        )
    identifier = identity(PENDING_PREFIX, command.id)
    pending = PendingDefense(
        id=identity("defense:", command.id),
        composed_attack_id=identifier,
        attacker_id=command.actor_id,
        defender_id=command.target_id,
        weapon_id=source.id,
        allowed=("none",),
        opened_round=encounter.round,
        opened_turn=encounter.turn_index,
    )
    binding = ComposedPending(
        id=identifier,
        command_id=command.id,
        campaign_id=state.campaign_id,
        encounter_id=encounter.id,
        pending_id=pending.id,
        attacker_id=command.actor_id,
        target_id=command.target_id,
        source=source,
        stage="defense" if maneuver == "attack" else "resistance",
        opened_round=encounter.round,
        opened_turn=encounter.turn_index,
    )
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "events": state.resources.events
                    + (
                        ResourceEvent(
                            id=identifier,
                            at=state.resources.game_time,
                            target_id=command.actor_id,
                            kind=binding.model_dump_json(),
                        ),
                    )
                }
            )
        }
    )
    encounter = encounter.model_copy(update={"pending_defense": pending})
    if maneuver == "attack":
        sensory = combat_visibility(encounter, command.actor_id, command.target_id, state=state)
        physical = physical_defenses(runtime, state, encounter, pending, None)
        pending = pending.model_copy(
            update={
                "allowed": tuple(d for d in physical if d == "none" or d in sensory.defenses),
                "visibility_attack_penalty": sensory.attack_penalty,
                "visibility_defense_penalty": sensory.defense_penalty,
            }
        )
        encounter = encounter.model_copy(update={"pending_defense": pending})
    return (
        state,
        encounter,
        CombatResult(
            encounter_id=encounter.id,
            code="combat.defense_required",
            round=encounter.round,
            current_actor_id=encounter.current_actor_id,
            pending_defense_id=pending.id,
            available=pending.allowed if maneuver == "attack" else (),
        ),
    )
