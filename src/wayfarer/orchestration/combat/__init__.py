"""The transactional adapter for combat: one module per step of a command.

The command types this service accepts are re-exported here, because callers
name the service and the command together and the engine owns both.
"""

from wayfarer.engine.simulation.combat.commands import COMBAT_ADAPTER as COMBAT_ADAPTER
from wayfarer.engine.simulation.combat.commands import BasicJoinPlacement as BasicJoinPlacement
from wayfarer.engine.simulation.combat.commands import BasicMove as BasicMove
from wayfarer.engine.simulation.combat.commands import ChooseDefense as ChooseDefense
from wayfarer.engine.simulation.combat.commands import CombatCommand as CombatCommand
from wayfarer.engine.simulation.combat.commands import ContinueCriticalMiss as ContinueCriticalMiss
from wayfarer.engine.simulation.combat.commands import (
    DeclareBasicSpatialFacts as DeclareBasicSpatialFacts,
)
from wayfarer.engine.simulation.combat.commands import DeclareThrownLanding as DeclareThrownLanding
from wayfarer.engine.simulation.combat.commands import EndEncounter as EndEncounter
from wayfarer.engine.simulation.combat.commands import HexJoinPlacement as HexJoinPlacement
from wayfarer.engine.simulation.combat.commands import HexPlacement as HexPlacement
from wayfarer.engine.simulation.combat.commands import JoinEncounter as JoinEncounter
from wayfarer.engine.simulation.combat.commands import (
    MigrateEncounterBasic as MigrateEncounterBasic,
)
from wayfarer.engine.simulation.combat.commands import MigrateEncounterHex as MigrateEncounterHex
from wayfarer.engine.simulation.combat.commands import RepairEquipment as RepairEquipment
from wayfarer.engine.simulation.combat.commands import ResolveChokeEffects as ResolveChokeEffects
from wayfarer.engine.simulation.combat.commands import (
    ResolveWeaponExplosion as ResolveWeaponExplosion,
)
from wayfarer.engine.simulation.combat.commands import (
    ResumeInterruptedTurn as ResumeInterruptedTurn,
)
from wayfarer.engine.simulation.combat.commands import RetrieveEquipment as RetrieveEquipment
from wayfarer.engine.simulation.combat.commands import (
    SetEncounterOpposition as SetEncounterOpposition,
)
from wayfarer.engine.simulation.combat.commands import SquareJoinPlacement as SquareJoinPlacement
from wayfarer.engine.simulation.combat.commands import StartBasicEncounter as StartBasicEncounter
from wayfarer.engine.simulation.combat.commands import StartEncounter as StartEncounter
from wayfarer.engine.simulation.combat.commands import TakeCombatTurn as TakeCombatTurn
from wayfarer.engine.simulation.combat.commands import TakeUnarmedTurn as TakeUnarmedTurn
from wayfarer.engine.simulation.combat.commands import TypedCombatCommand as TypedCombatCommand
from wayfarer.engine.simulation.combat.commands import WithdrawEncounter as WithdrawEncounter
from wayfarer.orchestration.combat.context import CombatContext as CombatContext
from wayfarer.orchestration.combat.context import CombatStep as CombatStep
from wayfarer.orchestration.combat.roster import preview_withdrawal as preview_withdrawal
from wayfarer.orchestration.combat.service import CombatService as CombatService
from wayfarer.orchestration.combat.steps import reduce_combat as reduce_combat
