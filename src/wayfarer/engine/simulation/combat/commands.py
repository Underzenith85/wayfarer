"""Transactional adapter for the explicit combat encounter state machine."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer.engine.rules.types.explosion import BlastResponse
from wayfarer.engine.rules.types.location import Hand, HitLocation
from wayfarer.engine.rules.types.object import GroundPosition
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.encounter import (
    CombatAllegiance,
    RangedSituation,
    SideOpposition,
)
from wayfarer.engine.simulation.combat.maneuvers import (
    AttackOption,
    CrouchAction,
    DefenseOption,
    WaitTrigger,
)
from wayfarer.engine.simulation.combat.spatial import BasicSpatialFact, Placement
from wayfarer.engine.simulation.combat.suppression import SprayTarget, SuppressionZone
from wayfarer.engine.simulation.combat.unarmed.records import (
    GrappleLocation,
    UnarmedAction,
    UnarmedSkill,
)
from wayfarer.engine.simulation.combat.vocabulary import Defense, Facing, Maneuver, Posture
from wayfarer.engine.simulation.hex_geometry import Hex, HexBattlefield, HexFacing, Pose
from wayfarer.models import Id, Record


class CombatCommand(Record):
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)


class StartEncounter(CombatCommand):
    kind: Literal["start_encounter"] = "start_encounter"
    encounter_id: Id
    battlefield_id: Id
    scene_id: Id | None = Field(default=None, exclude_if=lambda v: v is None)
    placements: tuple[Placement, ...] = Field(min_length=2)
    ranged_situations: tuple[RangedSituation, ...] = ()
    allegiances: tuple[CombatAllegiance, ...] = ()
    oppositions: tuple[SideOpposition, ...] = ()
    automatic_completion: bool = False
    reinforcements_expected: bool = False


class StartBasicEncounter(CombatCommand):
    kind: Literal["start_basic_encounter"] = "start_basic_encounter"
    encounter_id: Id
    scene_id: Id
    participant_ids: tuple[Id, ...] = Field(min_length=2, max_length=100)
    facts: tuple[BasicSpatialFact, ...] = Field(default=(), max_length=10000)
    ranged_situations: tuple[RangedSituation, ...] = ()
    allegiances: tuple[CombatAllegiance, ...] = ()
    oppositions: tuple[SideOpposition, ...] = ()
    automatic_completion: bool = False
    reinforcements_expected: bool = False


class BasicMove(Record):
    reference_actor_id: Id
    direction: Literal["approach", "withdraw"]


class DeclareBasicSpatialFacts(CombatCommand):
    kind: Literal["declare_basic_spatial_facts"] = "declare_basic_spatial_facts"
    encounter_id: Id
    facts: tuple[BasicSpatialFact, ...] = Field(min_length=1, max_length=1000)


class TakeCombatTurn(CombatCommand):
    kind: Literal["take_combat_turn"] = "take_combat_turn"
    encounter_id: Id
    maneuver: Maneuver
    destination: GridPoint | None = None
    facing: Facing | None = None
    posture: Posture | None = None
    crouch: CrouchAction | None = Field(default=None, exclude_if=lambda value: value is None)
    item_id: str | None = None
    target_id: str | None = None
    mode_id: str | None = None
    shots: int = Field(default=1, ge=1)
    spray_targets: tuple[SprayTarget, ...] = Field(
        default=(), max_length=19, exclude_if=lambda value: not value
    )
    suppression_zones: tuple[SuppressionZone, ...] = Field(
        default=(), max_length=20, exclude_if=lambda value: not value
    )
    laser_sight: bool = Field(default=False, exclude_if=lambda value: not value)
    reload_ammunition_id: str | None = None
    unload_ammunition: bool = Field(default=False, exclude_if=lambda v: not v)
    fast_draw: bool = Field(default=False, exclude_if=lambda v: not v)
    cocking_aid_id: str | None = Field(default=None, exclude_if=lambda v: v is None)
    let_down_bow: bool = Field(default=False, exclude_if=lambda v: not v)
    recover_thrown_item: bool = Field(default=False, exclude_if=lambda v: not v)
    escape_entanglement: bool = Field(default=False, exclude_if=lambda v: not v)
    mount_crew: tuple[Id, ...] = Field(default=(), exclude_if=lambda v: not v)
    transport_id: Id | None = Field(default=None, exclude_if=lambda v: v is None)
    firearm_service: Literal["diagnose", "clear", "repair"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    firearm_service_skill: Literal["weapon", "armoury"] = Field(
        default="weapon", exclude_if=lambda v: v == "weapon"
    )
    hit_location: HitLocation | None = None
    target_item_id: Id | None = Field(default=None, exclude_if=lambda v: v is None)
    ready_hand: Hand | Literal["both"] | None = None
    attack_option: AttackOption | None = None
    defense_option: DefenseOption | None = None
    wait_trigger: WaitTrigger | None = None
    step_timing: Literal["before", "after"] = "before"
    second_item_id: str | None = None
    second_target_id: str | None = None
    second_mode_id: str | None = None
    braced: bool = Field(default=False, exclude_if=lambda value: not value)
    hex_path: tuple[Hex, ...] = Field(default=(), max_length=100)
    hex_facing: HexFacing | None = None
    basic_move: BasicMove | None = Field(default=None, exclude_if=lambda value: value is None)


class TakeUnarmedTurn(CombatCommand):
    kind: Literal["take_unarmed_turn"] = "take_unarmed_turn"
    encounter_id: Id
    action: UnarmedAction
    foot: Literal["left-foot", "right-foot"] = "right-foot"
    target_id: Id
    skill: UnarmedSkill = "attribute:dx"
    hands: tuple[Hand, ...] = ()
    location: GrappleLocation = "torso"
    grip_id: Id | None = None
    enter_close_combat: bool = False
    choke_hold: bool = Field(default=False, exclude_if=lambda value: not value)
    maneuver: Literal["attack", "all_out_attack", "move_and_attack"] = Field(
        default="attack", exclude_if=lambda value: value == "attack"
    )
    attack_option: AttackOption | None = Field(default=None, exclude_if=lambda value: value is None)


class ResolveChokeEffects(CombatCommand):
    kind: Literal["resolve_choke_effects"] = "resolve_choke_effects"
    encounter_id: Id
    grip_id: Id


class ResumeInterruptedTurn(CombatCommand):
    kind: Literal["resume_interrupted_turn"] = "resume_interrupted_turn"
    encounter_id: Id
    cancel: bool = False


class ChooseDefense(CombatCommand):
    kind: Literal["choose_defense"] = "choose_defense"
    encounter_id: Id
    defense: Defense
    item_id: str | None = None
    second_defense: Defense | None = None
    second_item_id: str | None = None
    catch_thrown: bool = Field(default=False, exclude_if=lambda v: not v)
    retreat: Hex | None = None
    basic_retreat: bool = Field(default=False, exclude_if=lambda value: not value)
    parry_mode_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    second_parry_mode_id: str | None = Field(default=None, exclude_if=lambda value: value is None)


class ResolveWeaponExplosion(CombatCommand):
    kind: Literal["resolve_weapon_explosion"] = "resolve_weapon_explosion"
    encounter_id: Id
    blast_id: Id
    responses: tuple[BlastResponse, ...]
    object_cover: dict[str, int]
    object_sizes: dict[str, int] = Field(default_factory=dict)
    center: GroundPosition | None = None
    environment: Literal["air", "water", "vacuum"]


class DeclareThrownLanding(CombatCommand):
    kind: Literal["declare_thrown_landing"] = "declare_thrown_landing"
    encounter_id: Id
    item_id: Id
    landing: GroundPosition


class HexPlacement(Record):
    actor_id: Id
    pose: Pose


class MigrateEncounterHex(CombatCommand):
    kind: Literal["migrate_encounter_hex"] = "migrate_encounter_hex"
    encounter_id: Id
    battlefield: HexBattlefield
    placements: tuple[HexPlacement, ...] = Field(min_length=2, max_length=100)


class MigrateEncounterBasic(CombatCommand):
    kind: Literal["migrate_encounter_basic"] = "migrate_encounter_basic"
    encounter_id: Id


class WithdrawEncounter(CombatCommand):
    kind: Literal["withdraw_encounter"] = "withdraw_encounter"
    encounter_id: Id
    new_group_id: Id


class BasicJoinPlacement(Record):
    kind: Literal["basic"] = "basic"
    facts: tuple[BasicSpatialFact, ...] = Field(min_length=1, max_length=1000)


class SquareJoinPlacement(Record):
    kind: Literal["square"] = "square"
    position: GridPoint
    facing: Facing = "north"


class HexJoinPlacement(Record):
    kind: Literal["hex"] = "hex"
    position: Hex
    facing: HexFacing


JoinPlacement = Annotated[
    BasicJoinPlacement | SquareJoinPlacement | HexJoinPlacement,
    Field(discriminator="kind"),
]


class JoinEncounter(CombatCommand):
    kind: Literal["join_encounter"] = "join_encounter"
    encounter_id: Id
    joining_actor_id: Id | None = Field(default=None, exclude_if=lambda value: value is None)
    placement: JoinPlacement | None = Field(default=None, exclude_if=lambda value: value is None)
    # Retained square input for existing callers and the frozen v1 adventure.
    position: GridPoint | None = Field(default=None, exclude_if=lambda value: value is None)
    facing: Facing = "north"
    side_id: Id | None = Field(default=None, exclude_if=lambda value: value is None)
    neutral: bool = Field(default=False, exclude_if=lambda value: not value)


class SetEncounterOpposition(CombatCommand):
    kind: Literal["set_encounter_opposition"] = "set_encounter_opposition"
    encounter_id: Id
    allegiances: tuple[CombatAllegiance, ...]
    oppositions: tuple[SideOpposition, ...]
    completion_policy: Literal["gm", "automatic"] = "gm"
    reinforcements_expected: bool = False


class EndEncounter(CombatCommand):
    kind: Literal["end_encounter"] = "end_encounter"
    encounter_id: Id
    reason: str = Field(min_length=1, max_length=1000)


class RepairEquipment(CombatCommand):
    kind: Literal["repair_equipment"] = "repair_equipment"
    encounter_id: Id
    item_id: Id
    stage: Literal["start", "finish", "cancel"]
    task_id: Id | None = None


class RetrieveEquipment(CombatCommand):
    kind: Literal["retrieve_equipment"] = "retrieve_equipment"
    encounter_id: Id
    item_id: Id
    stage: Literal["start", "finish", "cancel"]
    task_id: Id | None = None


class ContinueCriticalMiss(CombatCommand):
    kind: Literal["continue_critical_miss"] = "continue_critical_miss"
    encounter_id: Id
    critical_id: Id
    stage: Literal["migrate", "resume"]


TypedCombatCommand = Annotated[
    StartEncounter
    | StartBasicEncounter
    | DeclareBasicSpatialFacts
    | TakeCombatTurn
    | ChooseDefense
    | JoinEncounter
    | SetEncounterOpposition
    | EndEncounter
    | ResumeInterruptedTurn
    | TakeUnarmedTurn
    | ResolveChokeEffects
    | RepairEquipment
    | RetrieveEquipment
    | ContinueCriticalMiss
    | DeclareThrownLanding
    | ResolveWeaponExplosion
    | MigrateEncounterHex
    | MigrateEncounterBasic
    | WithdrawEncounter,
    # Migration is explicit and uses the same receipt and CAS as combat commands.
    Field(discriminator="kind"),
]
COMBAT_ADAPTER: TypeAdapter[TypedCombatCommand] = TypeAdapter(TypedCombatCommand)
