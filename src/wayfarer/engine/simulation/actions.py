"""Typed action proposals, action rules, results and the canonical play state.

These are the nouns of play. The verb that assesses and resolves them lives in
``wayfarer.engine.simulation.action_engine``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Annotated, Literal, Self

from pydantic import (
    Field,
    ModelWrapValidatorHandler,
    PrivateAttr,
    SerializerFunctionWrapHandler,
    TypeAdapter,
    ValidationInfo,
    model_serializer,
    model_validator,
)

from wayfarer import validation
from wayfarer.engine.character.power import Approval, CharacterProposal
from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.effects import DerivedValue
from wayfarer.engine.rules.types.location import Hand, HumanBody
from wayfarer.engine.simulation.ability_types import AbilityRules
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.campaign.adjudication import Ruling, RulingPolicy
from wayfarer.engine.simulation.campaign.administration import (
    AdministrationRules,
    AdministrationState,
)
from wayfarer.engine.simulation.campaign.advancement import AdvancementEntry, MigrationEntry
from wayfarer.engine.simulation.campaign.director import AuthorDraft, DirectorTurn
from wayfarer.engine.simulation.campaign.economics import EconomicsRules, EconomicsState
from wayfarer.engine.simulation.campaign.law import LawRules, LawState
from wayfarer.engine.simulation.campaign.npcs import NPCRules, NPCState
from wayfarer.engine.simulation.campaign.objectives import ObjectiveRules, ObjectiveState
from wayfarer.engine.simulation.campaign.party import PartyRules, PartyState
from wayfarer.engine.simulation.campaign.scenes import (
    ActorScene,
    JournalEntry,
    SceneEvent,
    SceneRules,
)
from wayfarer.engine.simulation.combat.encounter import CombatResult, Encounter
from wayfarer.engine.simulation.combat.profiles import CombatRules
from wayfarer.engine.simulation.health.recovery import RecoveryRules, RecoveryState
from wayfarer.engine.simulation.magic.bindings import SpellRules
from wayfarer.engine.simulation.magic.enchanting import EnchantingRules
from wayfarer.engine.simulation.projects.inventions import InventionRules
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.social.noncombat import NoncombatEncounter, NoncombatRules
from wayfarer.engine.world import World
from wayfarer.models import Record

Id = Annotated[str, Field(min_length=1, max_length=100)]


class ActionCommand(Record):
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)
    hypothetical: bool = False


class Move(ActionCommand):
    kind: Literal["move"] = "move"
    destination_id: Id | None = None


class Inspect(ActionCommand):
    kind: Literal["inspect"] = "inspect"
    target_id: Id | None = None


class Attack(ActionCommand):
    kind: Literal["attack"] = "attack"
    target_id: Id | None = None
    weapon_id: Id | None = None


class UseItem(ActionCommand):
    kind: Literal["use_item"] = "use_item"
    item_id: Id | None = None
    quantity: int = Field(default=1, ge=1, le=1000000)


class Social(ActionCommand):
    kind: Literal["social"] = "social"
    target_id: Id | None = None
    approach: Literal["diplomacy", "intimidation", "deception"] = "diplomacy"


class Wait(ActionCommand):
    kind: Literal["wait"] = "wait"
    ticks: int = Field(ge=1, le=10000)


class Question(ActionCommand):
    kind: Literal["question"] = "question"
    text: str = Field(min_length=1, max_length=2000)


TypedAction = Annotated[
    Move | Inspect | Attack | UseItem | Social | Wait | Question, Field(discriminator="kind")
]
ACTION_ADAPTER: TypeAdapter[TypedAction] = TypeAdapter(TypedAction)


class ActorSetup(Record):
    actor_id: Id
    proposal: CharacterProposal
    aware_of: tuple[str, ...] = ()
    conditions: tuple[Literal["unconscious", "stunned", "restrained"], ...] = ()
    available_at: int = Field(default=0, ge=0)
    body: HumanBody | None = None
    held_item_hands: tuple[tuple[str, Hand], ...] = ()


class PlayActor(ActorSetup):
    approval: Approval | None = None


class CheckRule(Record):
    """Trusted scenario check with catalog skill and modifier provenance."""

    id: Id
    action: Literal["inspect", "social"]
    target_id: Id
    definition_id: Id
    package_id: str
    package_version: str
    duration: int = Field(default=1, ge=1, le=10000)
    modifier: int = Field(default=0, ge=-20, le=20)
    reveal_fact_ids: tuple[str, ...] = ()
    required_equipment: str | None = None
    not_before: int = Field(default=0, ge=0)
    darkness_penalty: int = Field(default=0, ge=-9, le=0, exclude_if=lambda value: value == 0)


class ActionRules(Record):
    id: Id
    version: int = Field(ge=1)
    movement_ticks: int = Field(default=1, ge=1, le=10000)
    item_ticks: int = Field(default=1, ge=1, le=10000)
    maximum_wait: int = Field(default=100, ge=1, le=10000)
    fatigue_cost: int = Field(default=0, ge=0, le=100)
    checks: tuple[CheckRule, ...] = ()
    consumables: tuple[str, ...] = ()
    adjudication: RulingPolicy | None = None
    combat: CombatRules | None = None
    # Kept out of the legacy ActionRules encoding so disabled scenes preserve
    # existing campaign digests; enabled scene rules are appended explicitly.
    scenes: SceneRules | None = Field(default=None, exclude=True)
    objectives: ObjectiveRules | None = Field(default=None, exclude=True)
    noncombat: NoncombatRules | None = Field(default=None, exclude=True)
    party: PartyRules | None = Field(default=None, exclude=True)
    npcs: NPCRules | None = Field(default=None, exclude=True)
    recovery: RecoveryRules | None = Field(default=None, exclude=True)
    abilities: AbilityRules | None = Field(default=None, exclude=True)
    spells: SpellRules | None = Field(default=None, exclude=True)
    administration: AdministrationRules | None = Field(default=None, exclude=True)
    law: LawRules | None = Field(default=None, exclude=True)
    economics: EconomicsRules | None = Field(default=None, exclude=True)
    inventions: InventionRules | None = Field(default=None, exclude=True)
    enchanting: EnchantingRules | None = Field(default=None, exclude=True)


class ActionResult(Record):
    status: Literal[
        "feasible",
        "clarification",
        "question",
        "rejected",
        "unsupported",
        "adjudication_required",
        "committed",
    ]
    revision: int
    code: str
    command_id: str
    check: CheckTrace | None = None
    derived: DerivedValue | None = None
    dependencies: tuple[DerivedValue, ...] = ()
    revealed_fact_ids: tuple[str, ...] = ()
    rules_digest: str = ""
    ruling_id: str | None = None


class PlayEventProjection(Record):
    """Derived event view, not a field of canonical play state."""

    last_result: ActionResult | None = None
    last_combat_result: CombatResult | None = None
    scene_events: tuple[SceneEvent, ...] = ()


class PlayCheckpoint(Record):
    campaign_id: str
    lifecycle: Literal["active", "paused", "completed", "archived"] = "active"
    revision: int = Field(default=0, ge=0)
    configuration_digest: str
    world: World
    resources: ResourceState
    actors: tuple[PlayActor, ...]
    approvals: tuple[Approval, ...] = ()
    rulings: tuple[Ruling, ...] = ()
    encounters: tuple[Encounter, ...] = ()
    advancement: tuple[AdvancementEntry, ...] = ()
    migrations: tuple[MigrationEntry, ...] = ()
    members: tuple[CampaignMember, ...] = ()
    actor_scenes: tuple[ActorScene, ...] = ()
    journal: tuple[JournalEntry, ...] = ()
    fired_scene_triggers: tuple[str, ...] = ()
    objectives: ObjectiveState = ObjectiveState()
    noncombat: tuple[NoncombatEncounter, ...] = ()
    party: PartyState = PartyState()
    npcs: NPCState = NPCState()
    recovery: RecoveryState = RecoveryState()
    director: tuple[DirectorTurn, ...] = ()
    drafts: tuple[AuthorDraft, ...] = ()
    administration: AdministrationState = Field(
        default=AdministrationState(), exclude_if=lambda value: value == AdministrationState()
    )
    law: LawState = Field(default=LawState(), exclude_if=lambda value: value == LawState())
    economics: EconomicsState = Field(
        default=EconomicsState(), exclude_if=lambda value: value == EconomicsState()
    )


class PlayState(PlayCheckpoint):
    """Play checkpoint with a separately derived compatibility event view."""

    _event_projection: PlayEventProjection = PrivateAttr(default_factory=PlayEventProjection)

    @property
    def last_result(self) -> ActionResult | None:
        return self._event_projection.last_result

    @property
    def last_combat_result(self) -> CombatResult | None:
        return self._event_projection.last_combat_result

    @property
    def scene_events(self) -> tuple[SceneEvent, ...]:
        return self._event_projection.scene_events

    @model_validator(mode="wrap")
    @classmethod
    def read_event_projection(
        cls, value: object, handler: ModelWrapValidatorHandler[Self], info: ValidationInfo
    ) -> Self:
        if not isinstance(value, dict):
            result = handler(value)
            if isinstance(value, PlayState):
                assert result.__pydantic_private__ is not None
                result.__pydantic_private__["_event_projection"] = value._event_projection
            return result
        fields = validation.mapping(value).copy()
        projection = {
            key: fields.pop(key) for key in PlayEventProjection.model_fields if key in fields
        }
        events = (
            PlayEventProjection.model_validate_json(json.dumps(projection))
            if info.mode == "json"
            else PlayEventProjection.model_validate(projection)
        )
        if info.mode == "json":
            # Keep JSON's tuple/dataclass decoding semantics when the wrapper
            # removes legacy projection keys before canonical validation.
            # Legacy square checkpoints carried a null embedded-map slot.
            for encounter in validation.sequence(fields.get("encounters", [])):
                if isinstance(encounter, dict) and encounter.get("hex_battlefield") is None:
                    encounter.pop("hex_battlefield", None)
            checkpoint = PlayCheckpoint.model_validate_json(json.dumps(fields))
            fields = {name: getattr(checkpoint, name) for name in PlayCheckpoint.model_fields}
        result = handler(fields)
        assert result.__pydantic_private__ is not None
        result.__pydantic_private__["_event_projection"] = events
        return result

    @model_serializer(mode="wrap")
    def with_event_projection(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        # Compatibility JSON still carries the separate projection at API/replay
        # boundaries. Snapshot schema 2 splits it out of the play checkpoint.
        result: dict[str, object] = {}
        events = self._event_projection.model_dump(mode="python")
        positions = {
            "approvals": "last_result",
            "encounters": "last_combat_result",
            "actor_scenes": "scene_events",
        }
        for key, value in validation.mapping(handler(self)).items():
            result[key] = value
            if key in positions:
                result[positions[key]] = events[positions[key]]
        return result

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        fields = dict(update or {})
        projection = {
            key: fields.pop(key) for key in PlayEventProjection.model_fields if key in fields
        }
        result = super().model_copy(update=fields, deep=deep)
        if projection:
            assert result.__pydantic_private__ is not None
            result.__pydantic_private__["_event_projection"] = self._event_projection.model_copy(
                update=projection, deep=deep
            )
        return result
