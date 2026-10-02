"""Replay supported typed command families with recorded inputs and no providers."""

import json
from collections.abc import Awaitable, Callable, Mapping

from wayfarer import validation
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.abandon import AbandonPendingAttack
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.combat.sensory_host import ADAPTER as SENSORY_ADAPTER
from wayfarer.engine.simulation.health.cyclic_host_state import ADAPTER as CYCLIC_HOST_ADAPTER
from wayfarer.engine.simulation.health.hazard_records import HazardCommand
from wayfarer.engine.simulation.magic.enchanting_transitions import (
    COMMAND_ADAPTER as ENCHANTMENT_ADAPTER,
)
from wayfarer.engine.simulation.magic.lock_host import ADAPTER as LOCK_ADAPTER
from wayfarer.engine.simulation.magic.ritual_state import DeclareRitualCapability
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand, SpellCommand
from wayfarer.engine.simulation.magic.staff_casting_state import ADAPTER as STAFF_CASTING_ADAPTER
from wayfarer.engine.simulation.magic.staff_state import DeclareStaffConstruction
from wayfarer.engine.simulation.social.social import SocialCommand
from wayfarer.engine.simulation.traits.composed_host import ADAPTER as COMPOSED_ADAPTER
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import COMBAT_ADAPTER, CombatService
from wayfarer.orchestration.combat.abandon import AbandonPendingAttackService
from wayfarer.orchestration.combat.unarmed_host import RandomUnarmedService, RandomUnarmedStrike
from wayfarer.orchestration.combat_senses import CombatSensesService
from wayfarer.orchestration.composed_attacks import ComposedAttackService
from wayfarer.orchestration.cyclic import CyclicService
from wayfarer.orchestration.enchantments import EnchantmentService
from wayfarer.orchestration.harmful_physiology import HarmfulPhysiologyService
from wayfarer.orchestration.hazard_resume import recorded_resume
from wayfarer.orchestration.hazards import HazardContext, HazardService
from wayfarer.orchestration.locks import LockService, LockSpellService
from wayfarer.orchestration.party import PartyCommand, PartyService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.recovery import RecoveryCommand, RecoveryService
from wayfarer.orchestration.replay_inputs import replay_inputs
from wayfarer.orchestration.scenes import SCENE_ADAPTER, SceneService
from wayfarer.orchestration.social import ResolvedInteraction, SocialService
from wayfarer.orchestration.social_generations import replay_payload
from wayfarer.orchestration.spell_backfires import ResolveSpellBackfire, SpellBackfireService
from wayfarer.orchestration.spell_rituals import SpellRitualService
from wayfarer.orchestration.spells import SpellService
from wayfarer.orchestration.staff_casting import StaffCastingService
from wayfarer.orchestration.task_records import ADAPTER as TASK_ADAPTER
from wayfarer.orchestration.tasks import TaskService
from wayfarer.orchestration.transformations import TransformationService
from wayfarer.persistence.events import CommandInput, CommandRecord
from wayfarer.persistence.replay import command_text, unavailable_reason


async def _enchantment(play: PlayService, record: CommandRecord, encoded: str) -> None:
    await EnchantmentService(play).execute(
        record.campaign_id,
        ENCHANTMENT_ADAPTER.validate_json(encoded),
        principal_id=record.actor_id,
    )


async def _social(play: PlayService, record: CommandRecord, encoded: str) -> None:
    def unavailable(
        current: PlayService, state: PlayState, command: SocialCommand
    ) -> ResolvedInteraction:
        raise ValidationError("Replay cannot invoke an unrecorded social resolver")

    await SocialService(play, unavailable).execute(
        record.campaign_id,
        SocialCommand.model_validate_json(encoded),
        principal_id=record.actor_id,
    )


async def _task_host(play: PlayService, record: CommandRecord, encoded: str) -> None:
    await TaskService(play).execute(
        record.campaign_id,
        TASK_ADAPTER.validate_json(encoded),
        principal_id=record.actor_id,
    )


async def _natural_hazard_resume(play: PlayService, record: CommandRecord, encoded: str) -> None:
    command = HazardCommand.model_validate_json(encoded)
    if (
        command.kind != "resolve"
        or recorded_resume(CommandInput(record.payload_hash, record.command_input)) is None
    ):
        raise ValidationError("Historical resolver-only hazards retain event-fold replay only")

    def unavailable(
        _play: PlayService, _state: PlayState, _actor: str, _hazard: str
    ) -> HazardContext:
        raise ValidationError("Hazard continuation cannot fetch a new environmental source")

    await HazardService(play, unavailable).execute(
        record.campaign_id, command, principal_id=record.actor_id
    )


async def _staff_construction(play: PlayService, record: CommandRecord, encoded: str) -> None:
    await EnchantmentService(play).declare_staff(
        record.campaign_id,
        DeclareStaffConstruction.model_validate_json(encoded),
        principal_id=record.actor_id,
    )


async def _staff_casting(play: PlayService, record: CommandRecord, encoded: str) -> None:
    await StaffCastingService(play).execute(
        record.campaign_id,
        STAFF_CASTING_ADAPTER.validate_json(encoded),
        principal_id=record.actor_id,
    )


async def _abandon_pending_attack(play: PlayService, record: CommandRecord, encoded: str) -> None:
    await AbandonPendingAttackService(play).execute(
        record.campaign_id,
        AbandonPendingAttack.model_validate_json(encoded),
        principal_id=record.actor_id,
    )


async def _random_unarmed(play: PlayService, record: CommandRecord, encoded: str) -> None:
    await RandomUnarmedService(play).execute(
        record.campaign_id,
        RandomUnarmedStrike.model_validate_json(encoded),
        principal_id=record.actor_id,
    )


async def _combat_senses(play: PlayService, record: CommandRecord, encoded: str) -> None:
    await CombatSensesService(play).execute(
        record.campaign_id, SENSORY_ADAPTER.validate_json(encoded), principal_id=record.actor_id
    )


async def _harmful_physiology(play: PlayService, record: CommandRecord, encoded: str) -> None:
    await HarmfulPhysiologyService(play).execute(
        record.campaign_id,
        validation.mapping(validation.decode(encoded)),
        principal_id=record.actor_id,
    )


async def _cyclic_host(play: PlayService, record: CommandRecord, encoded: str) -> None:
    await CyclicService(play).execute(
        record.campaign_id,
        CYCLIC_HOST_ADAPTER.validate_json(encoded),
        principal_id=record.actor_id,
    )


async def _composed_attack(play: PlayService, record: CommandRecord, encoded: str) -> None:
    await ComposedAttackService(play).execute(
        record.campaign_id, COMPOSED_ADAPTER.validate_json(encoded), principal_id=record.actor_id
    )


async def _composed_defense(play: PlayService, record: CommandRecord, encoded: str) -> None:
    await ComposedAttackService(play).defend(
        record.campaign_id, ChooseDefense.model_validate_json(encoded), principal_id=record.actor_id
    )


_REGISTERED_FAMILIES: Mapping[str, Callable[[PlayService, CommandRecord, str], Awaitable[None]]] = {
    "gurps-social": _social,
    "task-host": _task_host,
    "gurps-hazard": _natural_hazard_resume,
    "composed-attack": _composed_attack,
    "composed-defense": _composed_defense,
    "enchantment": _enchantment,
    "staff-construction": _staff_construction,
    "staff-casting": _staff_casting,
    "combat-abandon-pending-attack": _abandon_pending_attack,
    "combat-random-unarmed": _random_unarmed,
    "combat-senses": _combat_senses,
    "harmful-physiology": _harmful_physiology,
    "cyclic-host": _cyclic_host,
}


def _recorded_payload(record: CommandRecord) -> dict[str, object]:
    decoded = replay_payload(command_text(record))
    if not isinstance(decoded, dict):
        raise ValidationError("No replay handler for non-object command input")
    return validation.mapping(decoded)


async def execute_recorded(play: PlayService, record: CommandRecord) -> None:
    """The caller supplies an isolated store containing the pre-command state.

    Unknown command families fail explicitly; replay never falls back to text
    interpretation, a provider, or a previously stored result.
    """
    reason = unavailable_reason(record)
    if reason:
        raise ValidationError(reason)
    payload = _recorded_payload(record)
    raw = payload.get("command", payload)
    if isinstance(raw, str):
        raw = validation.decode(raw)
    command = validation.mapping(raw)
    encoded = json.dumps(command)
    operation = payload.get("operation")
    handler = _REGISTERED_FAMILIES.get(operation) if isinstance(operation, str) else None
    with replay_inputs(record):
        if handler is not None:
            await handler(play, record, encoded)
        elif operation == "typed-action":
            await play.execute(record.campaign_id, command, principal_id=record.actor_id)
        elif operation == "transformation":
            await TransformationService(play).execute(
                record.campaign_id, command, principal_id=record.actor_id
            )
        elif operation == "morph-memory":
            await TransformationService(play).memorize(
                record.campaign_id, command, principal_id=record.actor_id
            )
        elif operation == "combat":
            await CombatService(play).execute(
                record.campaign_id,
                COMBAT_ADAPTER.validate_json(encoded),
                principal_id=record.actor_id,
            )
        elif operation == "scene":
            await SceneService(play).execute(
                record.campaign_id,
                SCENE_ADAPTER.validate_json(encoded),
                principal_id=record.actor_id,
            )
        elif operation == "party":
            await PartyService(play).execute(
                record.campaign_id,
                PartyCommand.model_validate_json(encoded),
                principal_id=record.actor_id,
            )
        elif operation == "spell-lifecycle":
            # Membership decides the caster's part, so replay names only the principal.
            await SpellService(play).execute(
                record.campaign_id,
                SpellCommand.model_validate_json(encoded),
                principal_id=record.actor_id,
            )
        elif operation == "lock-host":
            await LockService(play).execute(
                record.campaign_id,
                LOCK_ADAPTER.validate_json(encoded),
                principal_id=record.actor_id,
            )
        elif operation == "lock-spell":
            await LockSpellService(play).execute(
                record.campaign_id,
                RuntimeSpellCommand.model_validate_json(encoded),
                principal_id=record.actor_id,
            )
        elif operation == "spell-ritual":
            await SpellRitualService(play).execute(
                record.campaign_id,
                DeclareRitualCapability.model_validate_json(encoded),
                principal_id=record.actor_id,
            )
        elif operation == "spell-backfire":
            await SpellBackfireService(play).execute(
                record.campaign_id,
                ResolveSpellBackfire.model_validate_json(encoded),
                principal_id=record.actor_id,
            )
        elif record.event["action"] == "recovery":
            await RecoveryService(play).execute(
                record.campaign_id,
                RecoveryCommand.model_validate_json(encoded),
                principal_id=record.actor_id,
            )
        else:
            raise ValidationError(f"No replay handler for command family {json.dumps(operation)}")
