"""The encounter aggregate and its participants, pauses and results."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Self

from pydantic import (
    AliasChoices,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from wayfarer.engine.rules.types.entangle import Entanglement
from wayfarer.engine.rules.types.location import HitLocation
from wayfarer.engine.rules.types.spray import Stream
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.maneuvers import ManeuverState, WaitInterrupt
from wayfarer.engine.simulation.combat.profiles import InjuryTrace
from wayfarer.engine.simulation.combat.spatial import (
    BasicSpatialContext,
    BasicSpatialFact,
    DistanceSpatialFact,
    HexActorPlacement,
    HexSpatialContext,
    ObstacleSpatialFact,
    ReachSpatialFact,
    SpatialContext,
    SpatialProvenance,
    SquareActorPlacement,
    SquareSpatialContext,
    VisibilitySpatialFact,
    point_distance,
)
from wayfarer.engine.simulation.combat.suppression import (
    ActiveSuppressionZone,
    PendingSprayTarget,
    PendingSuppressionAttack,
)
from wayfarer.engine.simulation.combat.tactical import TacticalTrace
from wayfarer.engine.simulation.combat.unarmed.records import Grip, PendingUnarmed, UnarmedTrace
from wayfarer.engine.simulation.combat.vocabulary import Defense, Facing, Maneuver, Posture
from wayfarer.engine.simulation.hex_geometry import Hex, HexFacing
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record


class Combatant(Record):
    actor_id: Id
    initiative: int = Field(ge=0, le=100)
    # Runtime mirrors for existing mechanics. Exact spatial state is serialized only
    # through Encounter.spatial_context; _replace keeps these mirrors synchronized.
    runtime_position: GridPoint | Hex | None = Field(
        default=None,
        alias="position",
        validation_alias=AliasChoices("position", "runtime_position"),
    )
    facing: Facing = "north"
    hex_facing: HexFacing | None = None
    retreat_used: bool = False
    retreat_attacker_id: str | None = None
    tactical_defense_bonus: int = 0
    posture: Posture = "standing"
    reach: int = Field(ge=1, le=20)
    movement_allowance: int = Field(ge=0, le=100)
    ready_item_ids: tuple[str, ...] = ()
    reaction_available: bool = True
    parries: tuple[str, ...] = ()
    block_used: bool = False
    defense_penalty: int = Field(default=0, ge=-20, le=0)
    # B557: these last until this actor's next turn, including across a Wait.
    unarmed_balance_lost: bool = Field(default=False, exclude_if=lambda v: not v)
    unarmed_guard_dropped: bool = Field(default=False, exclude_if=lambda v: not v)
    # Attacker, actual parrying skill, and the first following defender turn.
    unarmed_lock_opportunity: tuple[str, str, int] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    last_maneuver: Maneuver | None = None
    last_attack_item_id: str | None = None
    hand_bindings: tuple[tuple[str, Literal["left-hand", "right-hand"]], ...] = ()
    arm_locked: bool = False
    grappled: bool = False
    pinned: bool = False
    entangled: Entanglement | None = Field(default=None, exclude_if=lambda v: v is None)
    stream: Stream | None = Field(default=None, exclude_if=lambda v: v is None)
    forced_do_nothing: bool = False
    maneuver_state: ManeuverState = Field(default_factory=ManeuverState)

    @property
    def position(self) -> GridPoint | Hex:
        if self.runtime_position is None:
            raise ValidationError("Basic combatant has no exact position")
        return self.runtime_position

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        fields = dict(update or {})
        if "position" in fields:
            fields["runtime_position"] = fields.pop("position")
        return super().model_copy(update=fields, deep=deep)

    @model_serializer(mode="wrap")
    def serialize_runtime_pose(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        result = dict(handler(self))
        result["position"] = result.pop("runtime_position")
        return result


class CombatWithdrawal(Record):
    actor: Combatant
    round: int = Field(ge=1)
    turn_index: int = Field(ge=0)
    group_id: Id


class CombatAllegiance(Record):
    """One participant's explicit encounter side; ``None`` is neutral."""

    actor_id: Id
    side_id: Id | None = None


class SideOpposition(Record):
    """An undirected hostile relationship between two encounter sides."""

    side_ids: tuple[Id, Id]

    @model_validator(mode="after")
    def canonical_pair(self) -> SideOpposition:
        if self.side_ids[0] >= self.side_ids[1]:
            raise ValueError("Opposition sides must be distinct and canonically ordered")
        return self


class PendingDefense(Record):
    id: Id
    attacker_id: Id
    defender_id: Id
    weapon_id: Id
    allowed: tuple[Defense, ...] = Field(min_length=1)
    shots: int = Field(default=1, ge=1)
    opened_round: int = Field(ge=1)
    opened_turn: int = Field(ge=0)
    mode_id: str | None = None
    hit_location: HitLocation | None = None
    target_item_id: Id | None = Field(default=None, exclude_if=lambda v: v is None)
    transport_id: Id | None = Field(default=None, exclude_if=lambda v: v is None)
    vehicle_attack_penalty: int = Field(default=0, ge=-30, le=0, exclude_if=lambda v: v == 0)
    vehicle_aim_lost: bool = Field(default=False, exclude_if=lambda v: not v)
    spray_targets: tuple[PendingSprayTarget, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    spray_recoil_penalty: int = Field(default=0, ge=0, exclude_if=lambda value: value == 0)
    traversal_shots: int = Field(default=0, ge=0, exclude_if=lambda value: value == 0)
    suppression_zone_id: Id | None = Field(default=None, exclude_if=lambda value: value is None)
    suppression_attacks: tuple[PendingSuppressionAttack, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    suppression_remaining_hits: int = Field(default=0, ge=0, exclude_if=lambda value: value == 0)
    suppression_aim_bonus: int = Field(default=0, ge=0, exclude_if=lambda value: value == 0)
    suppression_skill_cap: Literal[6, 8] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    interrupted_actor_id: Id | None = Field(default=None, exclude_if=lambda value: value is None)
    laser_sight: bool = Field(default=False, exclude_if=lambda value: not value)
    spell_cast_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    post_attack_destination: GridPoint | None = None
    post_attack_square_facing: Facing | None = None
    post_attack_hex_path: tuple[Hex, ...] = ()
    post_attack_facing: HexFacing | None = None
    post_attack_posture: Posture | None = None
    post_attack_basic_reference_id: Id | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    post_attack_basic_direction: Literal["approach", "withdraw"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class DefenseChoice(Record):
    pending: PendingDefense
    selected: Defense
    chosen_by: Id


class RangedSituation(Record):
    attacker_id: Id
    defender_id: Id
    distance_yards: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    speed_yards_per_second: float = Field(default=0, ge=0, allow_inf_nan=False)
    size_modifier: int = 0
    beam_environment_dr: int = Field(default=0, ge=0, exclude_if=lambda value: value == 0)
    laser_visible_to_firer: bool = Field(default=False, exclude_if=lambda value: not value)
    laser_visible_to_target: bool = Field(default=False, exclude_if=lambda value: not value)

    @property
    def distance(self) -> float:
        if self.distance_yards is None:
            raise ValidationError("Ranged situation requires an authoritative distance")
        return self.distance_yards


class Encounter(Record):
    id: Id
    # Version 1 is retained for unbound legacy snapshots, including scene-less profiles.
    version: Literal[1, 2] = Field(default=1, exclude_if=lambda v: v == 1)
    scene_id: Id | None = Field(default=None, exclude_if=lambda v: v is None)
    spatial_context: SpatialContext | None = Field(
        default=None, validation_alias="spatial_context", serialization_alias="spatial_context"
    )
    legacy_battlefield_id: Id | None = Field(
        default=None,
        validation_alias=AliasChoices("battlefield_id", "legacy_battlefield_id"),
        exclude=True,
    )
    legacy_spatial_kind: Literal["square", "hex"] = Field(
        default="square",
        validation_alias=AliasChoices("spatial_kind", "legacy_spatial_kind"),
        exclude=True,
    )
    darkness_penalty: int = Field(default=0, ge=-10, le=0, exclude_if=lambda v: v == 0)
    status: Literal["active", "completed"] = "active"
    participants: tuple[Combatant, ...] = Field(min_length=1)
    turn_order: tuple[str, ...] = Field(min_length=1)
    round: int = Field(default=1, ge=1)
    turn_index: int = Field(default=0, ge=0)
    pending_defense: PendingDefense | None = None
    defense_history: tuple[DefenseChoice, ...] = ()
    completion_reason: str | None = None
    wounds: tuple[InjuryTrace, ...] = ()
    blocked_reason: str | None = None
    wait_interrupt: WaitInterrupt | None = None
    grips: tuple[Grip, ...] = ()
    close_pairs: tuple[tuple[str, str], ...] = ()
    pending_unarmed: PendingUnarmed | None = None
    unarmed_history: tuple[UnarmedTrace, ...] = ()
    ranged_situations: tuple[RangedSituation, ...] = ()
    tactical_traces: tuple[TacticalTrace, ...] = ()
    withdrawals: tuple[CombatWithdrawal, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    suppression_zones: tuple[ActiveSuppressionZone, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    allegiances: tuple[CombatAllegiance, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    oppositions: tuple[SideOpposition, ...] = Field(default=(), exclude_if=lambda value: not value)
    completion_policy: Literal["legacy", "gm", "automatic"] = Field(
        default="legacy", exclude_if=lambda value: value == "legacy"
    )
    reinforcements_expected: bool = Field(default=False, exclude_if=lambda value: not value)

    @model_validator(mode="after")
    def validate_scene_version(self) -> Encounter:
        if (self.version == 2) != (self.scene_id is not None):
            raise ValueError("Scene-bound encounters require version 2 and a scene ID")
        context_was_serialized = self.spatial_context is not None
        context = self.spatial
        if not isinstance(context, BasicSpatialContext):
            participants = {p.actor_id: p for p in self.participants}
            if {p.actor_id for p in context.placements} != set(participants):
                raise ValueError("Spatial placements must match combat participants")
            for placement in context.placements:
                participant = participants[placement.actor_id]
                if context_was_serialized:
                    object.__setattr__(participant, "runtime_position", placement.position)
                    if isinstance(placement, SquareActorPlacement):
                        object.__setattr__(participant, "facing", placement.facing)
                        object.__setattr__(participant, "hex_facing", None)
                    else:
                        object.__setattr__(participant, "hex_facing", placement.facing)
                if participant.position != placement.position:
                    raise ValueError("Runtime combat position disagrees with spatial context")
                if isinstance(placement, SquareActorPlacement):
                    if participant.facing != placement.facing or participant.hex_facing is not None:
                        raise ValueError("Runtime square facing disagrees with spatial context")
                elif participant.hex_facing != placement.facing:
                    raise ValueError("Runtime hex facing disagrees with spatial context")
        elif any(
            p.runtime_position is not None or p.hex_facing is not None for p in self.participants
        ):
            raise ValueError("Basic combatants cannot retain exact mapped poses")
        actors = {p.actor_id for p in self.participants}
        allegiance_actors = [a.actor_id for a in self.allegiances]
        if self.allegiances and (
            set(allegiance_actors) != actors or len(allegiance_actors) != len(actors)
        ):
            raise ValueError("Explicit allegiance must classify every participant once")
        pairs = [opposition.side_ids for opposition in self.oppositions]
        if len(set(pairs)) != len(pairs):
            raise ValueError("Opposition pairs must be unique")
        if self.completion_policy == "automatic" and not self.oppositions:
            raise ValueError("Automatic completion requires explicit opposition")
        return self

    @property
    def current_actor_id(self) -> str:
        return self.turn_order[self.turn_index]

    @property
    def spatial_kind(self) -> Literal["basic", "square", "hex"]:
        return self.spatial.kind

    @property
    def spatial(self) -> SpatialContext:
        context = self.spatial_context
        if context is not None:
            return context
        battlefield_id = self.legacy_battlefield_id
        if battlefield_id is None:
            raise ValueError("Mapped encounter requires a battlefield or spatial context")
        if self.legacy_spatial_kind == "hex":
            placements: tuple[HexActorPlacement, ...] = tuple(
                HexActorPlacement(actor_id=p.actor_id, position=p.position, facing=p.hex_facing)
                for p in self.participants
                if isinstance(p.position, Hex) and p.hex_facing is not None
            )
            if len(placements) != len(self.participants):
                raise ValueError("Legacy hex encounter requires hex positions and facings")
            context = HexSpatialContext(battlefield_id=battlefield_id, placements=placements)
        else:
            square: tuple[SquareActorPlacement, ...] = tuple(
                SquareActorPlacement(actor_id=p.actor_id, position=p.position, facing=p.facing)
                for p in self.participants
                if isinstance(p.position, GridPoint)
            )
            if len(square) != len(self.participants):
                raise ValueError("Legacy square encounter requires square positions")
            context = SquareSpatialContext(battlefield_id=battlefield_id, placements=square)
        object.__setattr__(self, "spatial_context", context)
        object.__setattr__(self, "legacy_battlefield_id", None)
        object.__setattr__(self, "legacy_spatial_kind", "square")
        return context

    @property
    def battlefield_id(self) -> str:
        context = self.spatial
        if isinstance(context, BasicSpatialContext):
            raise ValidationError("Basic spatial context has no battlefield")
        return context.battlefield_id

    def placement(self, actor_id: str) -> SquareActorPlacement | HexActorPlacement:
        context = self.spatial
        if isinstance(context, BasicSpatialContext):
            raise ValidationError("Basic spatial context has no exact placement")
        placement = next((p for p in context.placements if p.actor_id == actor_id), None)
        if placement is None:
            raise ValidationError("Combatant has no spatial placement")
        return placement

    def replace_placement(self, placement: SquareActorPlacement | HexActorPlacement) -> Encounter:
        context = self.spatial
        if isinstance(context, BasicSpatialContext):
            raise ValidationError("Basic spatial context has no exact placement")
        if (isinstance(context, SquareSpatialContext)) != isinstance(
            placement, SquareActorPlacement
        ):
            raise ValidationError("Coordinate systems require explicit migration")
        if placement.actor_id not in self.turn_order:
            raise ValidationError("Placement actor is not a combat participant")
        return self.model_copy(
            update={
                "spatial_context": context.model_copy(
                    update={
                        "placements": tuple(
                            placement if p.actor_id == placement.actor_id else p
                            for p in context.placements
                        )
                    }
                )
            }
        )

    def add_participant(self, participant: Combatant) -> Encounter:
        """Add a combatant and its context-owned placement atomically."""
        if any(p.actor_id == participant.actor_id for p in self.participants):
            raise ValidationError("Actor already participates")
        context = self.spatial
        if isinstance(context, SquareSpatialContext):
            if (
                not isinstance(participant.position, GridPoint)
                or participant.hex_facing is not None
            ):
                raise ValidationError("Square context requires square coordinates and facing")
            context = context.model_copy(
                update={
                    "placements": context.placements
                    + (
                        SquareActorPlacement(
                            actor_id=participant.actor_id,
                            position=participant.position,
                            facing=participant.facing,
                        ),
                    )
                }
            )
        elif isinstance(context, HexSpatialContext):
            if not isinstance(participant.position, Hex) or participant.hex_facing is None:
                raise ValidationError("Hex context requires hex coordinates and facing")
            context = context.model_copy(
                update={
                    "placements": context.placements
                    + (
                        HexActorPlacement(
                            actor_id=participant.actor_id,
                            position=participant.position,
                            facing=participant.hex_facing,
                        ),
                    )
                }
            )
        return self.model_copy(
            update={
                "participants": self.participants + (participant,),
                "spatial_context": context,
            }
        )

    def set_opposition(
        self,
        *,
        allegiances: tuple[CombatAllegiance, ...],
        oppositions: tuple[SideOpposition, ...],
        completion_policy: Literal["legacy", "gm", "automatic"],
        reinforcements_expected: bool,
    ) -> Encounter:
        """Replace the GM-owned lifecycle policy as one validated transition."""
        updated = self.model_copy(
            update={
                "allegiances": allegiances,
                "oppositions": oppositions,
                "completion_policy": completion_policy,
                "reinforcements_expected": reinforcements_expected,
            }
        )
        return Encounter.model_validate(updated.model_dump(mode="python"))

    @model_serializer(mode="wrap")
    def serialize_context_owned_spatial_state(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, object]:
        result = dict(handler(self))
        participants = result.get("participants")
        if isinstance(participants, (list, tuple)):
            for participant in participants:
                if isinstance(participant, dict):
                    participant.pop("position", None)
                    participant.pop("runtime_position", None)
                    participant.pop("facing", None)
                    participant.pop("hex_facing", None)
        return result


def basic_distance(encounter: Encounter, subject_id: str, object_id: str) -> float:
    context = encounter.spatial
    if not isinstance(context, BasicSpatialContext):
        left = encounter.placement(subject_id).position
        right = encounter.placement(object_id).position
        return float(point_distance(left, right))
    fact = context.active("distance", subject_id, object_id)
    if not isinstance(fact, DistanceSpatialFact):
        raise ValidationError("Basic combat requires an authoritative distance fact")
    return fact.yards


def basic_reachable(encounter: Encounter, subject_id: str, object_id: str) -> bool:
    context = encounter.spatial
    if not isinstance(context, BasicSpatialContext):
        actor = next(p for p in encounter.participants if p.actor_id == subject_id)
        return basic_distance(encounter, subject_id, object_id) <= actor.reach
    fact = context.active("reach", subject_id, object_id)
    if not isinstance(fact, ReachSpatialFact):
        raise ValidationError("Basic combat requires an authoritative reach fact")
    return fact.relation in ("close", "reachable")


def basic_visible(encounter: Encounter, subject_id: str, object_id: str) -> bool:
    context = encounter.spatial
    if not isinstance(context, BasicSpatialContext):
        return True
    fact = context.active("visibility", subject_id, object_id)
    if not isinstance(fact, VisibilitySpatialFact):
        raise ValidationError("Basic combat requires an authoritative visibility fact")
    return fact.visible


def move_basic(
    encounter: Encounter,
    *,
    actor_id: str,
    reference_actor_id: str,
    direction: Literal["approach", "withdraw"],
    yards: int,
    command_id: str,
    revision: int,
    require_obstacle: bool = True,
) -> Encounter:
    context = encounter.spatial
    if not isinstance(context, BasicSpatialContext):
        raise ValidationError("Basic movement requires a basic spatial context")
    if actor_id == reference_actor_id or reference_actor_id not in encounter.turn_order:
        raise ValidationError("Basic movement requires another combatant as its reference")
    obstacle = context.active("obstacle", actor_id, reference_actor_id)
    if require_obstacle and not isinstance(obstacle, ObstacleSpatialFact):
        raise ValidationError("Basic movement requires an authoritative obstacle fact")
    if isinstance(obstacle, ObstacleSpatialFact) and obstacle.blocked:
        raise ValidationError("An authoritative obstacle blocks this movement")
    prior = basic_distance(encounter, actor_id, reference_actor_id)
    distance = max(0.0, prior - yards) if direction == "approach" else prior + yards
    actors = {p.actor_id: p for p in encounter.participants}
    actor = actors[actor_id]
    reference = actors[reference_actor_id]
    revision = max(
        (revision,)
        + tuple(
            fact.provenance.declared_revision
            for fact in context.facts
            if fact.provenance.invalidated_revision is None
            and actor_id in (fact.subject_id, fact.object_id)
        )
    )
    retained: list[BasicSpatialFact] = []
    for fact in context.facts:
        if fact.provenance.invalidated_revision is None and actor_id in (
            fact.subject_id,
            fact.object_id,
        ):
            fact = fact.model_copy(
                update={
                    "provenance": fact.provenance.model_copy(
                        update={"invalidated_revision": revision}
                    )
                }
            )
        retained.append(fact)
    provenance = SpatialProvenance(
        source="engine-derived",
        source_id=command_id,
        declared_by=actor_id,
        declared_revision=revision,
    )
    retained.extend(
        (
            DistanceSpatialFact(
                subject_id=actor_id,
                object_id=reference_actor_id,
                yards=distance,
                provenance=provenance,
            ),
            ReachSpatialFact(
                subject_id=actor_id,
                object_id=reference_actor_id,
                relation=(
                    "close"
                    if distance == 0
                    else "reachable"
                    if distance <= actor.reach
                    else "separated"
                ),
                provenance=provenance,
            ),
            ReachSpatialFact(
                subject_id=reference_actor_id,
                object_id=actor_id,
                relation=(
                    "close"
                    if distance == 0
                    else "reachable"
                    if distance <= reference.reach
                    else "separated"
                ),
                provenance=provenance,
            ),
        )
    )
    return encounter.model_copy(
        update={"spatial_context": context.model_copy(update={"facts": tuple(retained)})}
    )


class CombatResult(Record):
    encounter_id: Id
    code: str
    round: int = Field(ge=1)
    current_actor_id: Id
    pending_defense_id: str | None = None
    available: tuple[str, ...] = ()
    injury: InjuryTrace | None = None
    unarmed: UnarmedTrace | None = None
