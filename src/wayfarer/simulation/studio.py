"""Author-facing scenario graph built from the runtime contracts, never prose facts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from wayfarer.simulation.actions import ActionRules, ActorSetup
from wayfarer.simulation.noncombat import NoncombatRules
from wayfarer.simulation.npcs import NPCRules
from wayfarer.simulation.objectives import ObjectiveRules
from wayfarer.simulation.party import PartyRules
from wayfarer.simulation.recovery import RecoveryRules
from wayfarer.simulation.resources import Id, Record, ResourceState
from wayfarer.simulation.scenes import SceneRules
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


class ScenarioGraph(Record):
    id: Id
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    brief: GenerationBrief
    opening_scene_id: Id
    opening_action: str = Field(min_length=1, max_length=1000)
    failure_consequence: str = Field(min_length=1, max_length=1000)
    world: World
    resources: ResourceState
    actors: tuple[ActorSetup, ...] = Field(min_length=1, max_length=30)
    npc_actor_ids: tuple[Id, ...] = ()
    actions: ActionRules
    scenes: SceneRules
    objectives: ObjectiveRules
    noncombat: NoncombatRules | None = None
    npcs: NPCRules | None = None
    recovery: RecoveryRules | None = None
    party: PartyRules | None = None
    approaches: tuple[ApproachSupport, ...] = ()

    def runtime_rules(self) -> ActionRules:
        return self.actions.model_copy(
            update={
                "scenes": self.scenes,
                "objectives": self.objectives,
                "noncombat": self.noncombat,
                "npcs": self.npcs,
                "recovery": self.recovery,
                "party": self.party,
            }
        )


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
