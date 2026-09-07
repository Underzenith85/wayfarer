"""Author-facing scenario graph built from the runtime contracts, never prose facts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from wayfarer.simulation.ability_types import AbilityRules
from wayfarer.simulation.actions import ActionRules, ActorSetup
from wayfarer.simulation.combat import AttackProfile, CombatConsequence, ProtectionProfile
from wayfarer.simulation.gurps_equipment import EquipmentCatalog
from wayfarer.simulation.noncombat import NoncombatRules
from wayfarer.simulation.npcs import NPCRules
from wayfarer.simulation.objectives import ObjectiveRules
from wayfarer.simulation.party import PartyRules
from wayfarer.simulation.recovery import RecoveryRules
from wayfarer.simulation.resources import Id, Record, ResourceState
from wayfarer.simulation.scenes import SceneRules
from wayfarer.simulation.spell_bindings import SpellRules
from wayfarer.world import World


class GenerationBrief(Record):
    premise: str = Field(min_length=1, max_length=4000)
    genre: str = Field(min_length=1, max_length=100)
    tone: str = Field(min_length=1, max_length=100)
    duration_minutes: int = Field(ge=10, le=10000)
    difficulty: Literal["gentle", "standard", "hard"]
    restrictions: tuple[str, ...] = Field(default=(), max_length=30)


class ApproachSupport(Record):
    """Evidence that a declared approach uses a supported runtime check."""

    id: Id
    scene_id: Id
    check_rule_id: Id
    actor_id: Id


class ScenarioContent(Record):
    """Shared authored mechanics; actor builds are supplied by runtime binding."""

    id: Id
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    brief: GenerationBrief
    opening_scene_id: Id
    opening_action: str = Field(min_length=1, max_length=1000)
    failure_consequence: str = Field(min_length=1, max_length=1000)
    world: World
    resources: ResourceState
    npc_actor_ids: tuple[Id, ...] = ()
    combat_consequences: tuple[CombatConsequence, ...] = ()
    combat_attacks: tuple[AttackProfile, ...] = ()
    combat_equipment: EquipmentCatalog | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    combat_protection: tuple[ProtectionProfile, ...] = ()
    actions: ActionRules
    scenes: SceneRules
    objectives: ObjectiveRules
    noncombat: NoncombatRules | None = None
    npcs: NPCRules | None = None
    recovery: RecoveryRules | None = None
    party: PartyRules | None = None
    abilities: AbilityRules | None = Field(default=None, exclude_if=lambda value: value is None)
    spells: SpellRules | None = Field(default=None, exclude_if=lambda value: value is None)
    approaches: tuple[ApproachSupport, ...] = ()

    def runtime_rules(self) -> ActionRules:
        return self.actions.model_copy(
            update={
                "combat": self.actions.combat.model_copy(
                    update={
                        "attacks": self.combat_attacks,
                        "gurps_equipment": self.combat_equipment,
                        "consequences": self.combat_consequences,
                        "protection": self.combat_protection,
                    }
                )
                if self.actions.combat
                else None,
                "scenes": self.scenes,
                "objectives": self.objectives,
                "noncombat": self.noncombat,
                "npcs": self.npcs,
                "recovery": self.recovery,
                "party": self.party,
                "abilities": self.abilities,
                "spells": self.spells,
            }
        )


class ScenarioGraph(ScenarioContent):
    actors: tuple[ActorSetup, ...] = Field(min_length=1, max_length=30)


class StudioFinding(Record):
    code: str
    severity: Literal["error", "warning"]
    reference: str
    message: str


class StudioReport(Record):
    findings: tuple[StudioFinding, ...]
    reachable_scene_ids: tuple[str, ...]
    challenge: dict[str, int]

    @property
    def valid(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)
