"""Explicit combat lifecycle and action economy for the supported prototype subset.

Attack intent persists a defense-choice pause. Configured original prototype
profiles resolve checks, protection and injury atomically when that choice resumes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Annotated, Literal, Self

from pydantic import (
    AliasChoices,
    BeforeValidator,
    Field,
    SerializerFunctionWrapHandler,
    ValidationInfo,
    model_serializer,
    model_validator,
)

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record
from wayfarer.rules.checks import CheckTrace
from wayfarer.rules.effects import DerivedValue
from wayfarer.rules.entangle_types import Entanglement
from wayfarer.rules.location_types import HitLocation, HumanLocation
from wayfarer.rules.spray_types import Stream
from wayfarer.simulation.gurps_equipment import EquipmentCatalog
from wayfarer.simulation.hex_geometry import Hex, HexBattlefield, HexFacing
from wayfarer.simulation.maneuvers import (
    ATTACK_MANEUVERS,
    AttackOption,
    DefenseOption,
    ManeuverState,
    WaitInterrupt,
    WaitTrigger,
)
from wayfarer.simulation.resources import Equip, ResourceEngine, ResourceState
from wayfarer.simulation.tactical import TacticalTrace
from wayfarer.simulation.unarmed import Grip, PendingUnarmed, UnarmedTrace
from wayfarer.world import EntityKind, World

Facing = Literal["north", "east", "south", "west"]
Posture = Literal["standing", "kneeling", "prone"]
Maneuver = Literal[
    "do_nothing",
    "move",
    "ready",
    "change_posture",
    "attack",
    "wait",
    "concentrate",
    "aim",
    "evaluate",
    "feint",
    "all_out_attack",
    "all_out_defense",
    "move_and_attack",
]
Defense = Literal["dodge", "parry", "block", "none"]

if TYPE_CHECKING:
    from wayfarer.simulation.actions import PlayState
    from wayfarer.simulation.combat_commands import BasicMove


class GridPoint(Record):
    x: int = Field(ge=0, le=1000)
    y: int = Field(ge=0, le=1000)


class Battlefield(Record):
    coordinate_system: Literal["square-grid-v1"] = Field(
        default="square-grid-v1", exclude_if=lambda v: True
    )
    id: Id
    location_id: Id
    width: int = Field(ge=1, le=1000)
    height: int = Field(ge=1, le=1000)
    blocked: tuple[GridPoint, ...] = ()
    darkness_penalty: int = Field(default=0, ge=-10, le=0, exclude_if=lambda v: v == 0)

    @model_validator(mode="after")
    def validate_grid(self) -> Battlefield:
        if len(set(self.blocked)) != len(self.blocked):
            raise ValueError("Duplicate blocked position")
        if any(point.x >= self.width or point.y >= self.height for point in self.blocked):
            raise ValueError("Blocked position is outside the battlefield")
        return self


def _tag_template(value: object, info: ValidationInfo) -> object:
    if isinstance(value, dict):
        value = {"coordinate_system": "square-grid-v1", **value}
        if info.mode == "json":
            model = HexBattlefield if value["coordinate_system"] == "hex-axial-v1" else Battlefield
            return model.model_validate_json(json.dumps(value))
    return value


BattlefieldTemplate = Annotated[
    Battlefield | HexBattlefield,
    Field(discriminator="coordinate_system"),
    BeforeValidator(_tag_template),
]


class AttackProfile(Record):
    """Server-authored original subset, bound to an implemented equipment entry."""

    definition_id: Id
    mode: Literal["melee"] = "melee"
    target: Literal["torso"] = "torso"
    attack_target: Literal["attribute:dx"] = "attribute:dx"
    attack_modifier: int = Field(default=0, ge=-20, le=20)
    damage_dice: int = Field(default=1, ge=1, le=10)
    damage_bonus: int = Field(default=0, ge=-10, le=100)
    injury_multiplier: int = Field(default=1, ge=1, le=4)
    stun_ticks: int = Field(default=1, ge=0, le=100)


class ProtectionProfile(Record):
    definition_id: Id
    resistance: int = Field(ge=0, le=100)


class InjuryTrace(Record):
    attack: CheckTrace
    defense: CheckTrace | None = None
    second_defense: CheckTrace | None = None
    attack_value: DerivedValue
    defense_value: DerivedValue | None = None
    damage_dice: tuple[int, ...] = ()
    basic_damage: int = Field(default=0, ge=0)
    resistance: int = Field(default=0, ge=0)
    injury: int = Field(default=0, ge=0)
    hp_before: int
    hp_after: int
    incapacitated: bool = False
    stunned_until: int | None = None
    profile_id: Id
    rules_version: str
    critical_table: tuple[int, ...] = ()
    malfunction_table: tuple[int, ...] = Field(default=(), exclude_if=lambda v: not v)
    malfunction: str | None = Field(default=None, exclude_if=lambda v: v is None)
    adjudication_required: str | None = None
    location: HumanLocation | None = None
    location_dice: tuple[int, ...] = ()
    effect_dice: tuple[int, ...] = ()
    lasting_injury_ids: tuple[str, ...] = ()
    shots_fired: int = Field(default=0, ge=0)
    hits: int = Field(default=0, ge=0)
    per_hit_damage: tuple[int, ...] = ()
    per_hit_injury: tuple[int, ...] = ()
    per_hit_resistance: tuple[int, ...] = Field(default=(), exclude_if=lambda v: not v)
    per_hit_locations: tuple[HumanLocation | None, ...] = Field(
        default=(), exclude_if=lambda v: not v
    )
    per_hit_location_dice: tuple[tuple[int, ...], ...] = Field(
        default=(), exclude_if=lambda v: not v
    )


class CombatConsequence(Record):
    """Authored revelation after recorded incapacitation in a completed encounter."""

    id: Id
    battlefield_id: Id
    defeated_actor_id: Id
    recipient_actor_ids: tuple[Id, ...] = Field(min_length=1)
    fact_ids: tuple[Id, ...] = Field(min_length=1)


class CombatRules(Record):
    id: Id
    version: int = Field(ge=1)
    movement_allowance: int = Field(default=5, ge=1, le=100)
    prone_movement_allowance: int = Field(default=1, ge=0, le=100)
    default_reach: int = Field(default=1, ge=1, le=20)
    max_combatants: int = Field(default=30, ge=2, le=100)
    battlefields: tuple[BattlefieldTemplate, ...] = Field(default=(), max_length=100)

    consequences: tuple[CombatConsequence, ...] = Field(default=(), exclude=True)
    attacks: tuple[AttackProfile, ...] = Field(default=(), exclude=True)
    protection: tuple[ProtectionProfile, ...] = Field(default=(), exclude=True)
    gurps_equipment: EquipmentCatalog | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def validate_unique(self) -> CombatRules:
        if any(b.location_id == "unbound" for b in self.battlefields):
            raise ValueError("Battlefield template requires an authored location")
        if len({b.id for b in self.battlefields}) != len(self.battlefields):
            raise ValueError("Duplicate battlefield ID")
        templates = {b.id: b for b in self.battlefields}
        for board in self.battlefields:
            if isinstance(board, HexBattlefield) and board.source_template_id is not None:
                source = templates.get(board.source_template_id)
                if not isinstance(source, Battlefield) or source.location_id != board.location_id:
                    raise ValueError(
                        "Migrated template requires its original square template at the same location"
                    )
        if len({p.definition_id for p in self.attacks}) != len(self.attacks) or len(
            {p.definition_id for p in self.protection}
        ) != len(self.protection):
            raise ValueError("Duplicate combat profile")
        return self


class Placement(Record):
    actor_id: Id
    position: GridPoint | Hex
    hex_facing: HexFacing | None = None
    facing: Facing = "north"


class SquareActorPlacement(Record):
    actor_id: Id
    position: GridPoint
    facing: Facing = "north"


class HexActorPlacement(Record):
    actor_id: Id
    position: Hex
    facing: HexFacing


class SpatialProvenance(Record):
    """Trusted origin and lifetime for an authoritative mapless assertion."""

    source: Literal["scenario", "gm-adjudication", "engine-derived"]
    source_id: Id
    declared_by: Id
    declared_revision: int = Field(ge=0)
    invalidated_revision: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_lifetime(self) -> SpatialProvenance:
        if (
            self.invalidated_revision is not None
            and self.invalidated_revision < self.declared_revision
        ):
            raise ValueError("Spatial fact cannot be invalidated before it is declared")
        return self


class DistanceSpatialFact(Record):
    kind: Literal["distance"] = "distance"
    subject_id: Id
    object_id: Id
    yards: float = Field(ge=0, le=10000, allow_inf_nan=False)
    provenance: SpatialProvenance


class ReachSpatialFact(Record):
    kind: Literal["reach"] = "reach"
    subject_id: Id
    object_id: Id
    relation: Literal["close", "reachable", "separated"]
    provenance: SpatialProvenance


class VisibilitySpatialFact(Record):
    kind: Literal["visibility"] = "visibility"
    subject_id: Id
    object_id: Id
    visible: bool
    provenance: SpatialProvenance


class CoverSpatialFact(Record):
    kind: Literal["cover"] = "cover"
    subject_id: Id
    object_id: Id
    cover: Literal["none", "partial", "full"]
    provenance: SpatialProvenance


class ObstacleSpatialFact(Record):
    kind: Literal["obstacle"] = "obstacle"
    subject_id: Id
    object_id: Id
    blocked: bool
    provenance: SpatialProvenance


class RetreatSpatialFact(Record):
    kind: Literal["retreat"] = "retreat"
    subject_id: Id
    object_id: Id
    feasible: bool
    provenance: SpatialProvenance


BasicSpatialFact = Annotated[
    DistanceSpatialFact
    | ReachSpatialFact
    | VisibilitySpatialFact
    | CoverSpatialFact
    | ObstacleSpatialFact
    | RetreatSpatialFact,
    Field(discriminator="kind"),
]


class BasicSpatialContext(Record):
    kind: Literal["basic"] = "basic"
    facts: tuple[BasicSpatialFact, ...] = Field(default=(), max_length=10000)

    @model_validator(mode="after")
    def validate_facts(self) -> BasicSpatialContext:
        active = tuple(f for f in self.facts if f.provenance.invalidated_revision is None)
        keys = tuple(
            (
                f.kind,
                min(f.subject_id, f.object_id) if f.kind == "distance" else f.subject_id,
                max(f.subject_id, f.object_id) if f.kind == "distance" else f.object_id,
            )
            for f in active
        )
        if len(set(keys)) != len(keys):
            raise ValueError("Basic spatial facts require one authoritative value per pair")
        histories: dict[tuple[str, str, str], list[BasicSpatialFact]] = {}
        for fact in self.facts:
            key = (
                fact.kind,
                min(fact.subject_id, fact.object_id)
                if fact.kind == "distance"
                else fact.subject_id,
                max(fact.subject_id, fact.object_id) if fact.kind == "distance" else fact.object_id,
            )
            histories.setdefault(key, []).append(fact)
        for history in histories.values():
            ordered = sorted(history, key=lambda f: f.provenance.declared_revision)
            if any(
                left.provenance.invalidated_revision is None
                or left.provenance.invalidated_revision > right.provenance.declared_revision
                for left, right in zip(ordered, ordered[1:], strict=False)
            ):
                raise ValueError("Basic spatial fact lifetimes cannot overlap")
        return self

    def active(self, kind: str, subject_id: str, object_id: str) -> BasicSpatialFact | None:
        return next(
            (
                fact
                for fact in reversed(self.facts)
                if fact.kind == kind
                and fact.provenance.invalidated_revision is None
                and (
                    (fact.subject_id, fact.object_id) == (subject_id, object_id)
                    or kind == "distance"
                    and (fact.subject_id, fact.object_id) == (object_id, subject_id)
                )
            ),
            None,
        )


class SquareSpatialContext(Record):
    kind: Literal["square"] = "square"
    battlefield_id: Id
    placements: tuple[SquareActorPlacement, ...] = Field(min_length=2, max_length=100)


class HexSpatialContext(Record):
    kind: Literal["hex"] = "hex"
    battlefield_id: Id
    placements: tuple[HexActorPlacement, ...] = Field(min_length=1, max_length=100)


SpatialContext = Annotated[
    BasicSpatialContext | SquareSpatialContext | HexSpatialContext,
    Field(discriminator="kind"),
]


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


class SprayTarget(Record):
    """A declared additional target in one B409 spraying-fire sweep."""

    target_id: Id
    shots: int = Field(ge=1)
    hit_location: HitLocation | None = None


class PendingSprayTarget(SprayTarget):
    """Server-derived traversal cost and control penalty for a queued target."""

    recoil_penalty: int = Field(ge=1)
    traversal_shots: int = Field(ge=0)


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
    spray_targets: tuple[PendingSprayTarget, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    spray_recoil_penalty: int = Field(default=0, ge=0, exclude_if=lambda value: value == 0)
    traversal_shots: int = Field(default=0, ge=0, exclude_if=lambda value: value == 0)
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

    @model_validator(mode="after")
    def validate_scene_version(self) -> Encounter:
        if (self.version == 2) != (self.scene_id is not None):
            raise ValueError("Scene-bound encounters require version 2 and a scene ID")
        context_was_serialized = self.spatial_context is not None
        if self.status == "active" and len(self.participants) < 2:
            raise ValueError("Active encounters require at least two participants")
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
        return float(CombatEngine.distance(left, right))
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


class CombatEngine:
    def __init__(self, rules: CombatRules, resources: ResourceEngine) -> None:
        self.rules = CombatRules.model_validate(rules)
        self.resources = resources
        if any(p.definition_id not in resources.specs for p in rules.attacks) or any(
            p.definition_id not in resources.specs for p in rules.protection
        ):
            raise ValidationError("Combat profile requires implemented catalog equipment")
        self.battlefields = {b.id: b for b in self.rules.battlefields}

    @staticmethod
    def distance(left: GridPoint | Hex, right: GridPoint | Hex) -> int:
        if isinstance(left, Hex) and isinstance(right, Hex):
            from wayfarer.simulation.hex_geometry import distance

            return distance(left, right)
        if not isinstance(left, GridPoint) or not isinstance(right, GridPoint):
            raise ValidationError("Coordinate systems require explicit migration")
        return abs(left.x - right.x) + abs(left.y - right.y)

    def validate(
        self,
        encounter: Encounter,
        world: World,
        resources: ResourceState,
        actor_ids: frozenset[str],
    ) -> None:
        spatial = encounter.spatial
        battlefield = (
            None
            if isinstance(spatial, BasicSpatialContext)
            else self.battlefields.get(spatial.battlefield_id)
        )
        if not isinstance(spatial, BasicSpatialContext) and battlefield is None:
            raise ValidationError("Encounter battlefield is not configured")
        entities = {entity.id: entity for entity in world.entities}
        participants = {p.actor_id: p for p in encounter.participants}
        placements = (
            {}
            if isinstance(spatial, BasicSpatialContext)
            else {p.actor_id: p for p in spatial.placements}
        )
        if (
            len(participants) != len(encounter.participants)
            or not 1 <= len(participants) <= self.rules.max_combatants
            or encounter.status == "active"
            and len(participants) < 2
            or set(participants) != set(encounter.turn_order)
            or len(set(encounter.turn_order)) != len(encounter.turn_order)
            or encounter.turn_index >= len(encounter.turn_order)
            or not set(participants) <= actor_ids
            or not isinstance(spatial, BasicSpatialContext)
            and (set(placements) != set(participants) or len(placements) != len(spatial.placements))
        ):
            raise ValidationError("Invalid encounter participants or turn order")
        expected_order = tuple(
            p.actor_id
            for p in sorted(encounter.participants, key=lambda p: (-p.initiative, p.actor_id))
        )
        if encounter.turn_order != expected_order:
            raise ValidationError("Turn order does not match initiative")
        if encounter.spatial_kind == "hex":
            from wayfarer.simulation.tactical import validate_hex_encounter

            validate_hex_encounter(
                encounter, self.rules.gurps_equipment, board=self.hex_map(encounter)
            )
        if isinstance(spatial, BasicSpatialContext):
            for fact in spatial.facts:
                if (
                    fact.subject_id == fact.object_id
                    or fact.subject_id not in participants
                    or fact.object_id not in participants
                ):
                    raise ValidationError("Basic spatial facts must relate encounter participants")
            for actor_id, actor in participants.items():
                for other_id in participants:
                    if actor_id == other_id:
                        continue
                    reach = spatial.active("reach", actor_id, other_id)
                    distance = spatial.active("distance", actor_id, other_id)
                    if isinstance(reach, ReachSpatialFact) and isinstance(
                        distance, DistanceSpatialFact
                    ):
                        expected = (
                            "close"
                            if distance.yards == 0
                            else "reachable"
                            if distance.yards <= actor.reach
                            else "separated"
                        )
                        if reach.relation != expected:
                            raise ValidationError("Basic distance and reach facts conflict")
        occupied: set[GridPoint | Hex] = set()
        blocked = set(battlefield.blocked) if isinstance(battlefield, Battlefield) else set()
        if battlefield is not None:
            self.hex_map(encounter)
        ready = {
            (item.owner_id, item.id) for item in resources.items if item.equipped and item.ready
        }
        for participant in encounter.participants:
            entity = entities.get(participant.actor_id)
            if entity is None or entity.kind is not EntityKind.ACTOR:
                raise ValidationError("Combatant is not at the battlefield location")
            if not isinstance(spatial, BasicSpatialContext):
                assert battlefield is not None
                placement = placements[participant.actor_id]
                if encounter.status == "active" and entity.location_id != battlefield.location_id:
                    raise ValidationError("Combatant is not at the battlefield location")
                if (
                    (
                        isinstance(placement.position, GridPoint)
                        and (
                            not isinstance(battlefield, Battlefield)
                            or (
                                placement.position.x >= battlefield.width
                                or placement.position.y >= battlefield.height
                            )
                        )
                    )
                    or (encounter.spatial_kind != "hex" and isinstance(placement.position, Hex))
                    or placement.position in blocked
                    or (
                        placement.position in occupied
                        and not (
                            self.rules.gurps_equipment is not None
                            and self.rules.gurps_equipment.profile_id == "gurps-basic-set-4e-2004"
                            and all(
                                tuple(sorted((other.actor_id, participant.actor_id)))
                                in encounter.close_pairs
                                for other in encounter.participants
                                if other.actor_id != participant.actor_id
                                and placements[other.actor_id].position == placement.position
                            )
                        )
                    )
                ):
                    raise ValidationError(
                        "Combatant position is blocked, occupied or out of bounds"
                    )
                occupied.add(placement.position)
            if (
                self.rules.gurps_equipment is None
                and participant.movement_allowance != self.rules.movement_allowance
            ):
                raise ValidationError("Combat movement is not rules-authored")
            if self.rules.gurps_equipment is None and participant.reach != self.rules.default_reach:
                raise ValidationError("Combat reach is not rules-authored")
            expected_ready = tuple(
                sorted(item_id for owner_id, item_id in ready if owner_id == participant.actor_id)
            )
            if encounter.status == "active" and participant.ready_item_ids != expected_ready:
                raise ValidationError("Combat readiness disagrees with inventory")
        from wayfarer.simulation.unarmed import validate_control

        validate_control(
            encounter,
            resources,
            basic=(
                self.rules.gurps_equipment is not None
                and self.rules.gurps_equipment.profile_id == "gurps-basic-set-4e-2004"
            ),
        )
        pending = encounter.pending_defense
        if pending is not None:
            spell_weapon = False
            if pending.spell_cast_id is not None:
                from wayfarer.simulation.spells import active_spells

                spell_weapon = any(
                    e.execute_effects
                    and e.spell_id == "fireball"
                    and e.cast_id == pending.spell_cast_id
                    and e.actor_id == pending.attacker_id
                    and pending.weapon_id
                    == "spell:" + hashlib.sha256(e.cast_id.encode()).hexdigest()
                    for e in active_spells(resources)
                )
                if not spell_weapon:
                    raise ValidationError("Pending missile has no held spell")
            if (
                encounter.status != "active"
                or pending.attacker_id != encounter.current_actor_id
                or pending.attacker_id not in participants
                or pending.defender_id not in participants
                or pending.attacker_id == pending.defender_id
                or pending.opened_round != encounter.round
                or pending.opened_turn != encounter.turn_index
                or (
                    not spell_weapon
                    and pending.weapon_id not in participants[pending.attacker_id].ready_item_ids
                )
                or len(set(pending.allowed)) != len(pending.allowed)
                or "none" not in pending.allowed
            ):
                raise ValidationError("Invalid pending defense pause")
        historical_ids: set[str] = set()
        for choice in encounter.defense_history:
            historical = choice.pending
            if (
                historical.id in historical_ids
                or (pending is not None and historical.id == pending.id)
                or historical.attacker_id not in participants
                or historical.defender_id not in participants
                or historical.attacker_id == historical.defender_id
                or choice.chosen_by != historical.defender_id
                or choice.selected not in historical.allowed
                or historical.opened_round > encounter.round
                or historical.opened_turn >= len(encounter.turn_order)
            ):
                raise ValidationError("Invalid defense history")
            historical_ids.add(historical.id)
        if encounter.status == "completed" and (
            pending is not None or not encounter.completion_reason
        ):
            raise ValidationError("Completed encounter has unfinished state")
        if encounter.status == "active" and encounter.completion_reason is not None:
            raise ValidationError("Active encounter cannot have a completion reason")

    def require_hex(self, encounter: Encounter) -> HexBattlefield:
        board = self.hex_map(encounter)
        if board is None:
            raise ValidationError("Encounter requires a hex template")
        return board

    def hex_map(self, encounter: Encounter) -> HexBattlefield | None:
        return hex_template(encounter, self.rules)

    def start(
        self,
        encounter_id: str,
        battlefield_id: str,
        placements: tuple[Placement, ...],
        initiatives: dict[str, int],
        world: World,
        resources: ResourceState,
        actor_ids: frozenset[str],
    ) -> Encounter:
        participants = tuple(
            Combatant(
                actor_id=p.actor_id,
                initiative=initiatives[p.actor_id],
                position=p.position,
                facing=p.facing,
                hex_facing=p.hex_facing,
                reach=self.rules.default_reach,
                movement_allowance=self.rules.movement_allowance,
                ready_item_ids=tuple(
                    sorted(
                        item.id
                        for item in resources.items
                        if item.owner_id == p.actor_id and item.equipped and item.ready
                    )
                ),
            )
            for p in placements
        )
        board = self.battlefields[battlefield_id]
        if isinstance(board, HexBattlefield):
            if any(not isinstance(p.position, Hex) or p.hex_facing is None for p in placements):
                raise ValidationError("Hex encounter requires hex positions and facings")
            spatial_context: SpatialContext = HexSpatialContext(
                battlefield_id=battlefield_id,
                placements=tuple(
                    HexActorPlacement(actor_id=p.actor_id, position=p.position, facing=p.hex_facing)
                    for p in placements
                    if isinstance(p.position, Hex) and p.hex_facing is not None
                ),
            )
        else:
            if any(
                not isinstance(p.position, GridPoint) or p.hex_facing is not None
                for p in placements
            ):
                raise ValidationError("Square encounter requires square positions and facings")
            spatial_context = SquareSpatialContext(
                battlefield_id=battlefield_id,
                placements=tuple(
                    SquareActorPlacement(actor_id=p.actor_id, position=p.position, facing=p.facing)
                    for p in placements
                    if isinstance(p.position, GridPoint)
                ),
            )
        order = tuple(
            p.actor_id for p in sorted(participants, key=lambda p: (-p.initiative, p.actor_id))
        )
        encounter = Encounter(
            darkness_penalty=self.battlefields[battlefield_id].darkness_penalty,
            id=encounter_id,
            spatial_context=spatial_context,
            participants=participants,
            turn_order=order,
        )
        self.validate(encounter, world, resources, actor_ids)
        return encounter

    def start_basic(
        self,
        encounter_id: str,
        participant_ids: tuple[str, ...],
        facts: tuple[BasicSpatialFact, ...],
        initiatives: dict[str, int],
        world: World,
        resources: ResourceState,
        actor_ids: frozenset[str],
    ) -> Encounter:
        participants = tuple(
            Combatant(
                actor_id=actor_id,
                initiative=initiatives[actor_id],
                reach=self.rules.default_reach,
                movement_allowance=self.rules.movement_allowance,
                ready_item_ids=tuple(
                    sorted(
                        item.id
                        for item in resources.items
                        if item.owner_id == actor_id and item.equipped and item.ready
                    )
                ),
            )
            for actor_id in participant_ids
        )
        order = tuple(
            p.actor_id for p in sorted(participants, key=lambda p: (-p.initiative, p.actor_id))
        )
        encounter = Encounter(
            id=encounter_id,
            spatial_context=BasicSpatialContext(facts=facts),
            participants=participants,
            turn_order=order,
        )
        self.validate(encounter, world, resources, actor_ids)
        return encounter

    def available(self, encounter: Encounter, actor_id: str) -> tuple[str, ...]:
        if encounter.status != "active" or encounter.blocked_reason:
            return ()
        if encounter.pending_unarmed is not None:
            pause = encounter.pending_unarmed
            return tuple(pause.allowed) if actor_id == pause.target_id else ()
        pending = encounter.pending_defense
        if pending is not None:
            return tuple(pending.allowed) if actor_id == pending.defender_id else ()
        interrupt = encounter.wait_interrupt
        if interrupt is not None:
            if interrupt.ready:
                return ("resume_interrupted_turn",) if actor_id == interrupt.actor_id else ()
            waiter = next(p for p in encounter.participants if p.actor_id == interrupt.waiter_id)
            reaction = (
                "attack"
                if interrupt.declaration.reaction == "all_out_attack"
                and waiter.maneuver_state.defended
                else interrupt.declaration.reaction
            )
            return (reaction, "do_nothing") if actor_id == interrupt.waiter_id else ()
        if actor_id != encounter.current_actor_id:
            return ()
        base = ("do_nothing", "move", "ready", "change_posture", "attack", "wait")
        if self.rules.gurps_equipment is not None:
            return base + (
                "aim",
                "evaluate",
                "feint",
                "all_out_attack",
                "all_out_defense",
                "move_and_attack",
                "concentrate",
            )
        return base

    @staticmethod
    def _replace(encounter: Encounter, participant: Combatant) -> Encounter:
        updated = encounter.model_copy(
            update={
                "participants": tuple(
                    participant if p.actor_id == participant.actor_id else p
                    for p in encounter.participants
                )
            }
        )
        context = updated.spatial
        if isinstance(context, SquareSpatialContext):
            if not isinstance(participant.position, GridPoint):
                raise ValidationError("Square context cannot contain hex coordinates")
            return updated.replace_placement(
                SquareActorPlacement(
                    actor_id=participant.actor_id,
                    position=participant.position,
                    facing=participant.facing,
                )
            )
        if isinstance(context, HexSpatialContext):
            if not isinstance(participant.position, Hex) or participant.hex_facing is None:
                raise ValidationError("Hex context requires hex coordinates and facing")
            return updated.replace_placement(
                HexActorPlacement(
                    actor_id=participant.actor_id,
                    position=participant.position,
                    facing=participant.hex_facing,
                )
            )
        return updated

    @staticmethod
    def _reachable(
        battlefield: Battlefield | HexBattlefield,
        start: GridPoint,
        destination: GridPoint,
        limit: int,
        occupied: set[GridPoint],
    ) -> bool:
        if not isinstance(battlefield, Battlefield):
            raise ValidationError("Square movement requires a square template")
        blocked = set(battlefield.blocked) | occupied
        frontier = {start}
        visited = {start}
        for _ in range(limit):
            next_frontier: set[GridPoint] = set()
            for point in frontier:
                for x, y in (
                    (point.x - 1, point.y),
                    (point.x + 1, point.y),
                    (point.x, point.y - 1),
                    (point.x, point.y + 1),
                ):
                    if not (0 <= x < battlefield.width and 0 <= y < battlefield.height):
                        continue
                    neighbor = GridPoint(x=x, y=y)
                    if neighbor in blocked or neighbor in visited:
                        continue
                    if neighbor == destination:
                        return True
                    visited.add(neighbor)
                    next_frontier.add(neighbor)
            frontier = next_frontier
        return start == destination

    @staticmethod
    def _advance(encounter: Encounter) -> Encounter:
        interrupt = encounter.wait_interrupt
        if interrupt is not None and interrupt.reacting:
            return encounter.model_copy(
                update={
                    "turn_index": interrupt.turn_index,
                    "wait_interrupt": interrupt.model_copy(
                        update={"reacting": False, "ready": True}
                    ),
                }
            )
        index = encounter.turn_index + 1
        round_number = encounter.round
        if index == len(encounter.turn_order):
            index = 0
            round_number += 1
        return encounter.model_copy(
            update={
                "turn_index": index,
                "round": round_number,
                "participants": tuple(
                    p.model_copy(
                        update={
                            "reaction_available": True,
                            "parries": (),
                            "block_used": False,
                            "defense_penalty": 0,
                            "unarmed_balance_lost": False,
                            "unarmed_guard_dropped": False,
                            "retreat_used": False,
                            "retreat_attacker_id": None,
                            "tactical_defense_bonus": 0,
                            "maneuver_state": p.maneuver_state.new_turn(),
                        }
                    )
                    if p.actor_id == encounter.turn_order[index]
                    else p
                    for p in encounter.participants
                ),
            }
        )

    def take_turn(
        self,
        encounter: Encounter,
        *,
        actor_id: str,
        maneuver: Maneuver,
        resources: ResourceState,
        destination: GridPoint | None = None,
        facing: Facing | None = None,
        posture: Posture | None = None,
        item_id: str | None = None,
        target_id: str | None = None,
        command_id: str,
        attack_option: AttackOption | None = None,
        defense_option: DefenseOption | None = None,
        wait_trigger: WaitTrigger | None = None,
        step_timing: Literal["before", "after"] = "before",
        second_item_id: str | None = None,
        second_target_id: str | None = None,
        second_mode_id: str | None = None,
        command_json: str = "",
        hex_path: tuple[Hex, ...] = (),
        hex_facing: HexFacing | None = None,
        basic_move: BasicMove | None = None,
        spatial_revision: int | None = None,
    ) -> tuple[Encounter, ResourceState, CombatResult]:
        original, original_resources = encounter, resources
        interrupt = encounter.wait_interrupt
        if interrupt is not None:
            if interrupt.ready or interrupt.reacting or actor_id != interrupt.waiter_id:
                raise ConflictError("Resolve the interrupted Wait before another action")
            declaration = interrupt.declaration
            reacting_waiter = next(p for p in encounter.participants if p.actor_id == actor_id)
            if declaration.reaction == "all_out_attack" and reacting_waiter.maneuver_state.defended:
                declaration = declaration.model_copy(
                    update={"reaction": "attack", "attack_option": None}
                )
            if declaration.unarmed is not None and maneuver != "do_nothing":
                raise ValidationError("The declared Wait reaction is an unarmed attack")
            if maneuver != "do_nothing" and (maneuver, item_id, target_id, attack_option) != (
                declaration.reaction,
                declaration.item_id,
                declaration.reaction_target_id,
                declaration.attack_option,
            ):
                raise ValidationError("Wait reaction must match its recorded declaration")
            if maneuver != "do_nothing" and (
                step_timing != "before"
                or any(v is not None for v in (second_item_id, second_target_id, second_mode_id))
            ):
                raise ValidationError("Wait reaction includes undeclared maneuver options")
            encounter = encounter.model_copy(
                update={
                    "turn_index": encounter.turn_order.index(actor_id),
                    "wait_interrupt": interrupt.model_copy(update={"reacting": True}),
                }
            )
        result = self._take_turn(
            encounter,
            actor_id=actor_id,
            maneuver=maneuver,
            resources=resources,
            spatial_revision=spatial_revision or original_resources.revision + 1,
            destination=destination,
            facing=facing,
            posture=posture,
            item_id=item_id,
            target_id=target_id,
            command_id=command_id,
            attack_option=attack_option,
            defense_option=defense_option,
            wait_trigger=wait_trigger,
            step_timing=step_timing,
            second_item_id=second_item_id,
            second_target_id=second_target_id,
            second_mode_id=second_mode_id,
            hex_path=hex_path,
            hex_facing=hex_facing,
            basic_move=basic_move,
        )
        if self.rules.gurps_equipment is not None and interrupt is None and command_json:
            action = "attack" if maneuver in ATTACK_MANEUVERS else maneuver
            waiters = {p.actor_id: p for p in original.participants if p.actor_id != actor_id}
            for waiter_id in original.turn_order:
                waiter = waiters.get(waiter_id)
                trigger = waiter.maneuver_state.wait if waiter else None
                if trigger:
                    assert waiter is not None
                    before_actor = next(p for p in original.participants if p.actor_id == actor_id)
                    after_actor = next(p for p in result[0].participants if p.actor_id == actor_id)
                    zone_hit = not trigger.zone or any(
                        (point.q, point.r) in trigger.zone
                        for point in (
                            hex_path
                            or (
                                (after_actor.position,)
                                if isinstance(after_actor.position, Hex)
                                else ()
                            )
                        )
                    )
                    stop_candidate = trigger.stop_thrust and (
                        original.spatial_kind != "basic"
                        and action == "attack"
                        and target_id == waiter_id
                        and self.distance(before_actor.position, after_actor.position) >= 1
                        and self.distance(after_actor.position, waiter.position)
                        < self.distance(before_actor.position, waiter.position)
                    )
                    if stop_candidate and waiter.reach <= before_actor.reach:
                        raise ValidationError(
                            "Equal or shorter-reach stop-thrust ordering is unsupported"
                        )
                    stop_thrust = stop_candidate and waiter.reach > before_actor.reach
                    observable = True
                    if original.spatial_kind == "hex":
                        from wayfarer.simulation.tactical import sight

                        observable = sight(
                            result[0], waiter, after_actor, board=self.hex_map(result[0])
                        )
                    elif original.spatial_kind == "basic":
                        observable = basic_visible(original, waiter_id, actor_id)
                    matches = (
                        (trigger.actor_id is None or trigger.actor_id == actor_id)
                        and trigger.action == action
                        and (trigger.target_id is None or trigger.target_id == target_id)
                        and zone_hit
                        and observable
                        and (not trigger.stop_thrust or stop_thrust)
                    )
                else:
                    matches = False
                if matches:
                    assert waiter is not None and trigger is not None
                    declaration = trigger
                    bonus = (
                        self.distance(before_actor.position, after_actor.position) // 2
                        if declaration.stop_thrust
                        else 0
                    )
                    moved = (
                        basic_move is not None
                        if original.spatial_kind == "basic"
                        else self.distance(before_actor.position, after_actor.position) >= 1
                    )
                    paused = self._replace(
                        original,
                        waiter.model_copy(
                            update={
                                "maneuver_state": waiter.maneuver_state.model_copy(
                                    update={
                                        "wait": None,
                                        "stop_thrust_damage_bonus": bonus,
                                    }
                                )
                            }
                        ),
                    )
                    resume_json = command_json
                    if moved:
                        if original.spatial_kind == "basic":
                            paused = paused.model_copy(
                                update={"spatial_context": result[0].spatial}
                            )
                        else:
                            paused_actor = before_actor.model_copy(
                                update={
                                    "position": after_actor.position,
                                    "facing": after_actor.facing,
                                    "hex_facing": after_actor.hex_facing,
                                    "posture": after_actor.posture,
                                }
                            )
                            paused = self._replace(paused, paused_actor)
                        saved = json.loads(command_json)
                        saved.update(
                            {
                                "destination": None,
                                "facing": None,
                                "posture": None,
                                "hex_path": [],
                                "hex_facing": None,
                                "basic_move": None,
                                "step_timing": "before",
                            }
                        )
                        if maneuver == "move":
                            saved["maneuver"] = "do_nothing"
                        resume_json = json.dumps(saved, sort_keys=True, separators=(",", ":"))
                    paused = paused.model_copy(
                        update={
                            "wait_interrupt": WaitInterrupt(
                                waiter_id=waiter_id,
                                actor_id=actor_id,
                                turn_index=original.turn_index,
                                command_json=resume_json,
                                declaration=declaration,
                            )
                        }
                    )
                    return (
                        paused,
                        original_resources,
                        CombatResult(
                            encounter_id=paused.id,
                            code="combat.wait_triggered",
                            round=paused.round,
                            current_actor_id=actor_id,
                            available=self.available(paused, waiter_id),
                        ),
                    )
        return result

    def _take_turn(
        self,
        encounter: Encounter,
        *,
        actor_id: str,
        maneuver: Maneuver,
        resources: ResourceState,
        spatial_revision: int,
        command_id: str,
        destination: GridPoint | None = None,
        facing: Facing | None = None,
        posture: Posture | None = None,
        item_id: str | None = None,
        target_id: str | None = None,
        attack_option: AttackOption | None = None,
        defense_option: DefenseOption | None = None,
        wait_trigger: WaitTrigger | None = None,
        step_timing: Literal["before", "after"] = "before",
        second_item_id: str | None = None,
        second_target_id: str | None = None,
        second_mode_id: str | None = None,
        hex_path: tuple[Hex, ...] = (),
        hex_facing: HexFacing | None = None,
        basic_move: BasicMove | None = None,
    ) -> tuple[Encounter, ResourceState, CombatResult]:
        if self.rules.gurps_equipment is not None:
            command_id = "combat:" + hashlib.sha256(command_id.encode()).hexdigest()
        if (
            encounter.status != "active"
            or encounter.pending_defense is not None
            or encounter.blocked_reason
        ):
            raise ConflictError("Encounter cannot accept a maneuver now")
        if actor_id != encounter.current_actor_id:
            raise ConflictError("Combat action is out of turn")
        if maneuver == "concentrate" and self.rules.gurps_equipment is None:
            raise ValidationError("Concentration requires a bound ability command")
        participant = next(p for p in encounter.participants if p.actor_id == actor_id)
        # B205: a stream lasts only while its holder keeps pouring it on the same
        # weapon and mode. Any other maneuver lets go of it (#359).
        if participant.stream is not None and not (
            maneuver in ATTACK_MANEUVERS and item_id == participant.stream.weapon_id
        ):
            participant = participant.model_copy(update={"stream": None})
            encounter = self._replace(encounter, participant)
        basic = isinstance(encounter.spatial, BasicSpatialContext)
        battlefield = None if basic else self.battlefields[encounter.battlefield_id]
        deferred_step = step_timing == "after"
        if deferred_step and maneuver != "attack":
            raise ValidationError("Only Attack permits a step after the attack")
        if deferred_step and not (
            destination is not None
            or hex_path
            or hex_facing is not None
            or posture is not None
            or basic_move is not None
        ):
            raise ValidationError("A post-attack step requires movement, facing, or posture")
        if deferred_step and encounter.spatial_kind == "hex":
            from wayfarer.simulation.tactical import move_hex

            if destination is not None or facing is not None:
                raise ValidationError("Hex encounters require explicit hex paths and facings")
            if posture is not None and (hex_path or hex_facing is not None):
                raise ValidationError("A posture step cannot also move or turn")
            if posture is not None and {posture, participant.posture} != {
                "standing",
                "kneeling",
            }:
                raise ValidationError("A posture step only switches standing and kneeling")
            if posture is None:
                move_hex(
                    encounter,
                    participant,
                    "attack",
                    hex_path,
                    hex_facing,
                    None,
                    board=self.hex_map(encounter),
                )
        elif deferred_step and destination is not None:
            assert battlefield is not None
            if not isinstance(participant.position, GridPoint):
                raise ValidationError("Square step requires square coordinates")
            occupied = {
                p.position
                for p in encounter.participants
                if p.actor_id != actor_id and isinstance(p.position, GridPoint)
            }
            limit = max(1, (participant.movement_allowance + 9) // 10)
            if not self._reachable(battlefield, participant.position, destination, limit, occupied):
                raise ValidationError("Post-attack step exceeds allowance or terrain constraints")
        elif (
            deferred_step
            and posture is not None
            and {
                posture,
                participant.posture,
            }
            != {"standing", "kneeling"}
        ):
            raise ValidationError("A posture step only switches standing and kneeling")
        if encounter.spatial_kind == "hex" and not deferred_step:
            from wayfarer.simulation.tactical import move_hex

            if destination is not None or facing is not None:
                raise ValidationError("Hex encounters require explicit hex paths and facings")
            if posture is not None and hex_path:
                raise ValidationError("A posture step cannot also translate the actor")
            participant = move_hex(
                encounter,
                participant,
                maneuver,
                hex_path,
                hex_facing,
                defense_option,
                board=self.hex_map(encounter),
            )
            encounter = self._replace(encounter, participant)
        elif (hex_path or hex_facing is not None) and not deferred_step:
            raise ValidationError("Hex movement requires explicit battlefield migration")
        if basic_move is not None:
            if not basic or destination is not None or facing is not None or hex_path or hex_facing:
                raise ValidationError("Basic movement cannot mix mapped movement fields")
            allowed_steps = {
                "attack",
                "aim",
                "evaluate",
                "feint",
                "ready",
                "concentrate",
                "all_out_defense",
            }
            if maneuver != "move" and maneuver not in allowed_steps:
                raise ValidationError("Maneuver does not permit basic movement")
            if maneuver in ("attack", "aim", "evaluate", "feint"):
                raise ValidationError(
                    "Basic movement with a spatially dependent maneuver requires "
                    "post-movement GM adjudication"
                )
            if not deferred_step:
                allowance = (
                    self.rules.prone_movement_allowance
                    if maneuver == "move" and participant.posture == "prone"
                    else participant.movement_allowance
                    if maneuver == "move"
                    else max(1, (participant.movement_allowance + 9) // 10)
                )
                encounter = move_basic(
                    encounter,
                    actor_id=actor_id,
                    reference_actor_id=basic_move.reference_actor_id,
                    direction=basic_move.direction,
                    yards=allowance,
                    command_id=command_id,
                    revision=spatial_revision,
                )
        if self.rules.gurps_equipment is None and (
            maneuver not in ("do_nothing", "move", "ready", "change_posture", "attack", "wait")
            or attack_option
            or defense_option
            or wait_trigger
        ):
            raise ValidationError("Maneuver requires exact GURPS profile dispatch")
        if (
            (attack_option is not None and maneuver != "all_out_attack")
            or (defense_option is not None and maneuver != "all_out_defense")
            or (wait_trigger is not None and maneuver != "wait")
        ):
            raise ValidationError("Maneuver options do not match the maneuver")
        if step_timing == "after" and maneuver != "attack":
            raise ValidationError("Post-attack movement requires Attack")
        if self.rules.gurps_equipment is not None:
            old = participant.maneuver_state
            if participant.last_maneuver != "evaluate":
                old = old.model_copy(update={"evaluate_target_id": None, "evaluate_bonus": 0})
            if participant.last_maneuver != "feint":
                old = old.model_copy(update={"feint_target_id": None, "feint_penalty": 0})
            commitment = ManeuverState()
            if maneuver in ATTACK_MANEUVERS or maneuver == "feint":
                commitment = commitment.model_copy(
                    update={
                        "aim_item_id": old.aim_item_id,
                        "aim_target_id": old.aim_target_id,
                        "aim_mode_id": old.aim_mode_id,
                        "aim_seconds": old.aim_seconds if participant.last_maneuver == "aim" else 0,
                        "aim_accuracy": old.aim_accuracy,
                        "aim_braced": old.aim_braced,
                        "aim_sight_bonus": old.aim_sight_bonus,
                        "evaluate_target_id": old.evaluate_target_id,
                        "evaluate_bonus": old.evaluate_bonus,
                        "feint_target_id": old.feint_target_id,
                        "feint_penalty": old.feint_penalty,
                        "stop_thrust_damage_bonus": old.stop_thrust_damage_bonus,
                    }
                )
            if maneuver == "all_out_attack":
                if attack_option is None:
                    raise ValidationError("Choose an All-Out Attack option")
                commitment = commitment.model_copy(
                    update={
                        "defense_forbidden": True,
                        "attack_bonus": 4 if attack_option == "determined" else 0,
                        "strong": attack_option == "strong",
                        "attacks_remaining": int(attack_option == "double"),
                        "second_attack_item_id": second_item_id,
                        "second_attack_target_id": second_target_id,
                        "second_attack_mode_id": second_mode_id,
                    }
                )
                if attack_option == "double":
                    if second_target_id not in (None, target_id):
                        raise ValidationError("All-Out Attack (Double) attacks the same foe")
                    if second_item_id is not None:
                        hands = {item: hand for item, hand in participant.hand_bindings}
                        if (
                            second_item_id == item_id
                            or second_item_id not in participant.ready_item_ids
                            or item_id not in hands
                            or second_item_id not in hands
                            or hands[item_id] == hands[second_item_id]
                        ):
                            raise ValidationError(
                                "Double requires two distinct ready one-hand weapons"
                            )
                        commitment = commitment.model_copy(
                            update={
                                "second_attack_target_id": target_id,
                                "attack_bonus": -4 if hands[item_id] == "left-hand" else 0,
                                "second_attack_penalty": -4
                                if hands[second_item_id] == "left-hand"
                                else 0,
                            }
                        )
                elif any(v is not None for v in (second_item_id, second_target_id, second_mode_id)):
                    raise ValidationError("Second attack choices require All-Out Attack (Double)")
            if maneuver == "move_and_attack":
                commitment = commitment.model_copy(
                    update={"attack_bonus": -4, "attack_cap": 9, "parry_forbidden": True}
                )
            if maneuver in ATTACK_MANEUVERS:
                commitment = commitment.model_copy(
                    update={
                        "aim_item_id": old.aim_item_id,
                        "aim_target_id": old.aim_target_id,
                        "aim_mode_id": old.aim_mode_id,
                        "aim_seconds": old.aim_seconds if participant.last_maneuver == "aim" else 0,
                        "aim_accuracy": old.aim_accuracy,
                        "aim_braced": old.aim_braced,
                        "aim_sight_bonus": old.aim_sight_bonus,
                        "evaluate_target_id": old.evaluate_target_id,
                        "evaluate_bonus": old.evaluate_bonus,
                        "feint_target_id": old.feint_target_id,
                        "feint_penalty": old.feint_penalty,
                        "stop_thrust_damage_bonus": old.stop_thrust_damage_bonus,
                    }
                )
            if maneuver == "concentrate":
                commitment = commitment.model_copy(
                    update={
                        "concentrating": True,
                        "concentration_seconds": old.concentration_seconds + 1
                        if old.concentrating
                        else 1,
                    }
                )
            if maneuver == "all_out_defense":
                if defense_option is None:
                    raise ValidationError("Choose the enhanced active defense")
                commitment = commitment.model_copy(update={"enhanced_defense": defense_option})
            if maneuver == "wait":
                if (
                    wait_trigger is None
                    or wait_trigger.actor_id == actor_id
                    or (
                        wait_trigger.actor_id is not None
                        and wait_trigger.actor_id not in encounter.turn_order
                    )
                ):
                    raise ValidationError("Wait requires an observable other combatant trigger")
                from wayfarer.simulation.spells import active_spells

                held_missile = any(
                    effect.actor_id == actor_id
                    and effect.spell_id == "fireball"
                    and effect.execute_effects
                    and wait_trigger.item_id
                    == "spell:" + hashlib.sha256(effect.cast_id.encode()).hexdigest()
                    for effect in active_spells(resources)
                )
                if held_missile and (
                    wait_trigger.reaction != "attack" or wait_trigger.mode_id is not None
                ):
                    raise ValidationError("Held missile Wait supports its declared release only")
                if (
                    wait_trigger.unarmed is None
                    and wait_trigger.item_id not in participant.ready_item_ids
                    and wait_trigger.reaction != "ready"
                    and not held_missile
                ):
                    raise ValidationError("Wait attack requires a ready weapon")
                if (
                    wait_trigger.reaction != "ready"
                    and wait_trigger.reaction_target_id not in encounter.turn_order
                ):
                    raise ValidationError("Wait attack requires a declared target")
                if (wait_trigger.reaction == "all_out_attack") != (
                    wait_trigger.attack_option is not None
                ):
                    raise ValidationError("Wait All-Out Attack requires its option in advance")
                if wait_trigger.zone:
                    if encounter.spatial_kind != "hex":
                        raise ValidationError("Wait zones require an explicit hex battlefield")
                    cells = {
                        (cell.position.q, cell.position.r)
                        for cell in self.require_hex(encounter).cells
                    }
                    if not set(wait_trigger.zone) <= cells:
                        raise ValidationError("Wait zone is outside the battlefield")
                if wait_trigger.stop_thrust:
                    if basic:
                        raise ValidationError("Basic stop thrust requires explicit GM adjudication")
                    if (
                        wait_trigger.actor_id is None
                        or wait_trigger.reaction_target_id != wait_trigger.actor_id
                    ):
                        raise ValidationError("Stop thrust requires one declared charging foe")
                commitment = commitment.model_copy(update={"wait": wait_trigger})
            if maneuver in ("evaluate", "aim", "feint"):
                target = next((p for p in encounter.participants if p.actor_id == target_id), None)
                if target is None or target.actor_id == actor_id:
                    raise ValidationError("Maneuver requires another combatant target")
                reach = participant.reach + (
                    participant.movement_allowance if maneuver == "evaluate" else 0
                )
                if (
                    maneuver != "aim"
                    and (
                        basic_distance(encounter, participant.actor_id, target.actor_id)
                        if basic
                        else self.distance(participant.position, target.position)
                    )
                    > reach
                ):
                    raise ValidationError("Maneuver target is outside melee reach")
                if maneuver == "evaluate":
                    commitment = commitment.model_copy(
                        update={
                            "evaluate_target_id": target_id,
                            "evaluate_bonus": min(3, old.evaluate_bonus + 1)
                            if old.evaluate_target_id == target_id
                            else 1,
                        }
                    )
                elif maneuver == "aim":
                    if item_id not in participant.ready_item_ids:
                        raise ValidationError("Aim requires a ready ranged weapon")
                    commitment = commitment.model_copy(
                        update={
                            "aim_item_id": item_id,
                            "aim_target_id": target_id,
                            "aim_seconds": min(3, old.aim_seconds + 1)
                            if (old.aim_item_id, old.aim_target_id) == (item_id, target_id)
                            else 1,
                        }
                    )
            participant = participant.model_copy(update={"maneuver_state": commitment})
            step_maneuvers = {
                "attack",
                "aim",
                "evaluate",
                "feint",
                "ready",
                "concentrate",
                "all_out_defense",
            }
            if facing is not None and maneuver in step_maneuvers:
                participant = participant.model_copy(update={"facing": facing})
                facing = None
            if posture is not None and maneuver in step_maneuvers:
                if destination is not None or {posture, participant.posture} != {
                    "standing",
                    "kneeling",
                }:
                    raise ValidationError(
                        "A posture step only switches standing and kneeling in place"
                    )
                participant = participant.model_copy(update={"posture": posture})
                posture = None
            # A single destination is one movement allowance, never a second action.
            if destination is not None and maneuver != "move" and not deferred_step:
                limit = max(1, (participant.movement_allowance + 9) // 10)
                if maneuver == "move_and_attack":
                    limit = participant.movement_allowance
                elif maneuver == "all_out_attack" or (
                    maneuver == "all_out_defense" and defense_option == "dodge"
                ):
                    limit = participant.movement_allowance // 2
                elif maneuver not in (
                    "attack",
                    "aim",
                    "evaluate",
                    "feint",
                    "ready",
                    "concentrate",
                    "all_out_defense",
                ):
                    raise ValidationError("Maneuver does not permit a step")
                if not isinstance(participant.position, GridPoint):
                    raise ValidationError("Square movement requires square coordinates")
                occupied = {
                    p.position
                    for p in encounter.participants
                    if p.actor_id != actor_id and isinstance(p.position, GridPoint)
                }
                assert battlefield is not None
                if not self._reachable(
                    battlefield,
                    participant.position,
                    destination,
                    limit,
                    occupied,
                ):
                    raise ValidationError(
                        "Maneuver movement exceeds allowance or terrain constraints"
                    )
                if maneuver == "all_out_attack":
                    dx, dy = (
                        destination.x - participant.position.x,
                        destination.y - participant.position.y,
                    )
                    forward = {
                        "north": dy < 0 and dx == 0,
                        "south": dy > 0 and dx == 0,
                        "east": dx > 0 and dy == 0,
                        "west": dx < 0 and dy == 0,
                    }
                    if destination != participant.position and not forward[participant.facing]:
                        raise ValidationError("All-Out Attack movement must be forward")
                participant = participant.model_copy(update={"position": destination})
                destination = None
        if maneuver == "move" and encounter.spatial_kind == "hex":
            if any(value is not None for value in (posture, item_id, target_id)):
                raise ValidationError("Move accepts only a path and facing")
            participant = participant.model_copy(update={"last_maneuver": maneuver})
        elif maneuver == "move" and basic:
            if basic_move is None or any(
                value is not None for value in (destination, facing, posture, item_id, target_id)
            ):
                raise ValidationError("Basic Move requires one authoritative relative movement")
            participant = participant.model_copy(update={"last_maneuver": maneuver})
        elif maneuver == "move":
            if destination is None or any(
                value is not None for value in (posture, item_id, target_id)
            ):
                raise ValidationError("Move requires only a destination and optional facing")
            limit = (
                self.rules.prone_movement_allowance
                if participant.posture == "prone"
                else participant.movement_allowance
            )
            if not isinstance(participant.position, GridPoint):
                raise ValidationError("Square movement requires square coordinates")
            occupied = {
                p.position
                for p in encounter.participants
                if p.actor_id != actor_id and isinstance(p.position, GridPoint)
            }
            if (
                not isinstance(battlefield, Battlefield)
                or destination.x >= battlefield.width
                or destination.y >= battlefield.height
                or destination in battlefield.blocked
                or destination in occupied
                or self.distance(participant.position, destination) > limit
                or not self._reachable(
                    battlefield, participant.position, destination, limit, occupied
                )
            ):
                raise ValidationError("Combat movement exceeds allowance or terrain constraints")
            participant = participant.model_copy(
                update={
                    "position": destination,
                    "facing": facing or participant.facing,
                    "last_maneuver": maneuver,
                }
            )
        elif maneuver == "ready":
            # A bound actor may spend its Ready struggling with the binding
            # instead of readying an item (#354).
            struggling = participant.entangled is not None
            if any(value is not None for value in (destination, facing, posture, target_id)) or (
                item_id is None and not struggling
            ):
                raise ValidationError("Ready requires exactly one item")
            if item_id is not None:
                resources = self.resources.apply(
                    resources,
                    Equip(
                        id=f"{command_id}:ready",
                        actor_id=actor_id,
                        expected_revision=resources.revision,
                        item_id=item_id,
                        ready=True,
                    ),
                )
            participant = participant.model_copy(
                update={
                    "ready_item_ids": tuple(
                        sorted(
                            item.id
                            for item in resources.items
                            if item.owner_id == actor_id and item.equipped and item.ready
                        )
                    ),
                    "last_maneuver": maneuver,
                }
            )
        elif maneuver == "change_posture":
            if (
                self.rules.gurps_equipment is not None
                and participant.posture == "prone"
                and posture == "standing"
            ):
                raise ValidationError("Rise from prone to kneeling before standing")
            if posture is None or any(
                value is not None for value in (destination, facing, item_id, target_id)
            ):
                raise ValidationError("Posture maneuver requires exactly one posture")
            participant = participant.model_copy(
                update={"posture": posture, "last_maneuver": maneuver}
            )
        elif maneuver in ATTACK_MANEUVERS:
            if (
                target_id is None
                or item_id is None
                or (
                    not deferred_step
                    and any(value is not None for value in (destination, facing, posture))
                )
            ):
                raise ValidationError("Attack intent requires a target and ready weapon")
            target = next((p for p in encounter.participants if p.actor_id == target_id), None)
            if (
                target is None
                or target.actor_id == actor_id
                or item_id not in participant.ready_item_ids
                or (
                    not basic_reachable(encounter, actor_id, target_id)
                    and not any(
                        s.attacker_id == actor_id and s.defender_id == target_id
                        for s in encounter.ranged_situations
                    )
                )
            ):
                raise ValidationError("Attack target or weapon is unavailable or out of reach")
            participant = participant.model_copy(
                update={"last_maneuver": maneuver, "last_attack_item_id": item_id}
            )
            encounter = self._replace(encounter, participant)
            pending = PendingDefense(
                id=("defense:" + hashlib.sha256(command_id.encode()).hexdigest())
                if self.rules.gurps_equipment
                else f"{command_id}:defense",
                attacker_id=actor_id,
                defender_id=target_id,
                weapon_id=item_id,
                allowed=("dodge", "parry", "none") if target.ready_item_ids else ("dodge", "none"),
                opened_round=encounter.round,
                opened_turn=encounter.turn_index,
                post_attack_destination=destination if deferred_step else None,
                post_attack_square_facing=facing if deferred_step else None,
                post_attack_hex_path=hex_path if deferred_step else (),
                post_attack_facing=hex_facing if deferred_step else None,
                post_attack_posture=posture if deferred_step else None,
                post_attack_basic_reference_id=(
                    basic_move.reference_actor_id if deferred_step and basic_move else None
                ),
                post_attack_basic_direction=(
                    basic_move.direction if deferred_step and basic_move else None
                ),
            )
            encounter = encounter.model_copy(update={"pending_defense": pending})
            return (
                encounter,
                resources,
                CombatResult(
                    encounter_id=encounter.id,
                    code="combat.defense_required",
                    round=encounter.round,
                    current_actor_id=encounter.current_actor_id,
                    pending_defense_id=pending.id,
                    available=tuple(pending.allowed),
                ),
            )
        elif maneuver in ("aim", "evaluate", "feint"):
            if (
                facing is not None
                or posture is not None
                or (maneuver == "evaluate" and item_id is not None)
            ):
                raise ValidationError("Unexpected observation maneuver parameters")
            participant = participant.model_copy(update={"last_maneuver": maneuver})
        else:
            if any(
                value is not None for value in (destination, facing, posture, item_id, target_id)
            ):
                raise ValidationError("Maneuver has unexpected parameters")
            participant = participant.model_copy(update={"last_maneuver": maneuver})
        encounter = self._advance(self._replace(encounter, participant))
        return (
            encounter,
            resources,
            CombatResult(
                encounter_id=encounter.id,
                code=f"combat.{maneuver}",
                round=encounter.round,
                current_actor_id=encounter.current_actor_id,
                available=self.available(encounter, encounter.current_actor_id),
            ),
        )

    def choose_defense(
        self, encounter: Encounter, *, actor_id: str, selected: Defense
    ) -> tuple[Encounter, CombatResult]:
        pending = encounter.pending_defense
        if pending is None:
            raise ConflictError("Encounter is not waiting for a defense")
        if actor_id != pending.defender_id or selected not in pending.allowed:
            raise ValidationError("Defense is not available to this actor")
        defender = next(p for p in encounter.participants if p.actor_id == actor_id)
        if (
            self.rules.gurps_equipment is None
            and not defender.reaction_available
            and selected != "none"
        ):
            raise ValidationError("Defender has no reaction available")
        defender = defender.model_copy(
            update={
                "reaction_available": False if selected != "none" else defender.reaction_available,
                "maneuver_state": defender.maneuver_state.model_copy(update={"defended": True})
                if selected != "none"
                else defender.maneuver_state,
            }
        )
        choice = DefenseChoice(pending=pending, selected=selected, chosen_by=actor_id)
        encounter = self._replace(encounter, defender).model_copy(
            update={
                "pending_defense": None,
                "defense_history": encounter.defense_history + (choice,),
            }
        )
        attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
        if (
            self.rules.gurps_equipment is not None
            and pending.spray_targets
            and not encounter.blocked_reason
        ):
            following, *remaining = pending.spray_targets
            encounter = encounter.model_copy(
                update={
                    "pending_defense": pending.model_copy(
                        update={
                            "id": "spray:" + hashlib.sha256(pending.id.encode()).hexdigest(),
                            "defender_id": following.target_id,
                            "shots": following.shots,
                            "hit_location": following.hit_location,
                            "target_item_id": None,
                            "spray_targets": tuple(remaining),
                            "spray_recoil_penalty": following.recoil_penalty,
                            "traversal_shots": following.traversal_shots,
                            "post_attack_destination": None,
                            "post_attack_square_facing": None,
                            "post_attack_hex_path": (),
                            "post_attack_facing": None,
                            "post_attack_posture": None,
                        }
                    )
                }
            )
        elif (
            self.rules.gurps_equipment is not None
            and attacker.maneuver_state.attacks_remaining
            and not encounter.blocked_reason
        ):
            commitment = attacker.maneuver_state
            second_item = commitment.second_attack_item_id or pending.weapon_id
            second_target = commitment.second_attack_target_id or pending.defender_id
            if second_item not in attacker.ready_item_ids:
                commitment = commitment.model_copy(update={"attacks_remaining": 0})
                attacker = attacker.model_copy(update={"maneuver_state": commitment})
                encounter = self._replace(encounter, attacker)
                encounter = self._advance(encounter)
                return (
                    encounter,
                    CombatResult(
                        encounter_id=encounter.id,
                        code="combat.defense_recorded",
                        round=encounter.round,
                        current_actor_id=encounter.current_actor_id,
                        available=self.available(encounter, encounter.current_actor_id),
                    ),
                )
            attacker = attacker.model_copy(
                update={
                    "maneuver_state": commitment.model_copy(
                        update={
                            "attacks_remaining": 0,
                            "attack_bonus": commitment.second_attack_penalty,
                        }
                    )
                }
            )
            encounter = self._replace(encounter, attacker).model_copy(
                update={
                    "pending_defense": pending.model_copy(
                        update={
                            "id": "second:" + hashlib.sha256(pending.id.encode()).hexdigest(),
                            "weapon_id": second_item,
                            "defender_id": second_target,
                            "mode_id": commitment.second_attack_mode_id or pending.mode_id,
                            "post_attack_destination": None,
                            "post_attack_square_facing": None,
                            "post_attack_hex_path": (),
                            "post_attack_facing": None,
                            "post_attack_posture": None,
                        }
                    )
                }
            )
        else:
            if (
                pending.post_attack_destination is not None
                or pending.post_attack_square_facing is not None
            ):
                if not isinstance(attacker.position, GridPoint):
                    raise ValidationError("Square step requires square coordinates")
                occupied = {
                    p.position
                    for p in encounter.participants
                    if p.actor_id != attacker.actor_id and isinstance(p.position, GridPoint)
                }
                limit = max(1, (attacker.movement_allowance + 9) // 10)
                destination = pending.post_attack_destination or attacker.position
                if not self._reachable(
                    self.battlefields[encounter.battlefield_id],
                    attacker.position,
                    destination,
                    limit,
                    occupied,
                ):
                    raise ValidationError("Post-attack step is no longer available")
                attacker = attacker.model_copy(
                    update={
                        "position": destination,
                        "facing": pending.post_attack_square_facing or attacker.facing,
                    }
                )
                encounter = self._replace(encounter, attacker)
            if pending.post_attack_posture is not None:
                if {attacker.posture, pending.post_attack_posture} != {"standing", "kneeling"}:
                    raise ValidationError("Post-attack posture step is invalid")
                attacker = attacker.model_copy(update={"posture": pending.post_attack_posture})
                encounter = self._replace(encounter, attacker)
            encounter = self._advance(encounter)
        return (
            encounter,
            CombatResult(
                encounter_id=encounter.id,
                code="combat.defense_recorded",
                round=encounter.round,
                current_actor_id=encounter.current_actor_id,
                available=self.available(encounter, encounter.current_actor_id),
            ),
        )


def validate_consequences(rules: CombatRules, state: PlayState) -> None:
    """Authored consequences must name known battlefields, approved actors and facts."""
    actor_ids = {a.actor_id for a in state.actors}
    facts = {f.id for f in state.world.facts}
    fields = {b.id for b in rules.battlefields}
    if len({c.id for c in rules.consequences}) != len(rules.consequences):
        raise ValidationError("Duplicate combat consequence")
    if any(
        c.battlefield_id not in fields
        or c.defeated_actor_id not in actor_ids
        or not set(c.recipient_actor_ids) <= actor_ids
        or not set(c.fact_ids) <= facts
        for c in rules.consequences
    ):
        raise ValidationError("Invalid combat consequence references")


def hex_template(encounter: Encounter, rules: CombatRules | None) -> HexBattlefield | None:
    if rules is None:
        raise ValidationError("Encounter requires combat rules")
    if isinstance(encounter.spatial, BasicSpatialContext):
        return None
    template = next((b for b in rules.battlefields if b.id == encounter.battlefield_id), None)
    if template is None:
        raise ValidationError("Encounter battlefield is not configured")
    if (encounter.spatial_kind == "hex") != isinstance(template, HexBattlefield):
        raise ValidationError("Encounter geometry disagrees with its template")
    return template if isinstance(template, HexBattlefield) else None
