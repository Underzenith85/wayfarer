"""Typed action proposals, action rules, results and the canonical play state.

These are the nouns of play. The verb that assesses and resolves them lives in
``wayfarer.simulation.action_engine``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer.character.power import Approval, CharacterProposal
from wayfarer.models import Record
from wayfarer.rules.checks import CheckTrace
from wayfarer.rules.effects import DerivedValue
from wayfarer.rules.location_types import Hand, HumanBody
from wayfarer.simulation.ability_types import AbilityRules
from wayfarer.simulation.access import CampaignMember
from wayfarer.simulation.adjudication import Ruling, RulingPolicy
from wayfarer.simulation.advancement import AdvancementEntry, MigrationEntry
from wayfarer.simulation.combat import CombatResult, CombatRules, Encounter
from wayfarer.simulation.director import AuthorDraft, DirectorTurn
from wayfarer.simulation.noncombat import NoncombatEncounter, NoncombatRules
from wayfarer.simulation.npcs import NPCRules, NPCState
from wayfarer.simulation.objectives import ObjectiveRules, ObjectiveState
from wayfarer.simulation.party import PartyRules, PartyState
from wayfarer.simulation.recovery import RecoveryRules, RecoveryState
from wayfarer.simulation.resources import ResourceState
from wayfarer.simulation.scenes import ActorScene, JournalEntry, SceneEvent, SceneRules
from wayfarer.simulation.spell_bindings import SpellRules
from wayfarer.world import World

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


class PlayState(Record):
    campaign_id: str
    lifecycle: Literal["active", "paused", "completed", "archived"] = "active"
    revision: int = Field(default=0, ge=0)
    configuration_digest: str
    world: World
    resources: ResourceState
    actors: tuple[PlayActor, ...]
    approvals: tuple[Approval, ...] = ()
    last_result: ActionResult | None = None
    rulings: tuple[Ruling, ...] = ()
    encounters: tuple[Encounter, ...] = ()
    last_combat_result: CombatResult | None = None
    advancement: tuple[AdvancementEntry, ...] = ()
    migrations: tuple[MigrationEntry, ...] = ()
    members: tuple[CampaignMember, ...] = ()
    actor_scenes: tuple[ActorScene, ...] = ()
    scene_events: tuple[SceneEvent, ...] = ()
    journal: tuple[JournalEntry, ...] = ()
    fired_scene_triggers: tuple[str, ...] = ()
    objectives: ObjectiveState = ObjectiveState()
    noncombat: tuple[NoncombatEncounter, ...] = ()
    party: PartyState = PartyState()
    npcs: NPCState = NPCState()
    recovery: RecoveryState = RecoveryState()
    director: tuple[DirectorTurn, ...] = ()
    drafts: tuple[AuthorDraft, ...] = ()
