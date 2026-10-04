"""Replay supported typed command families with recorded inputs and no providers."""

import json
from collections.abc import Awaitable, Callable, Mapping

from wayfarer import validation
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.abandon import AbandonPendingAttack
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.combat.sensory_host import ADAPTER as SENSORY_ADAPTER
from wayfarer.engine.simulation.equipment.world_ground import WorldGroundCommand
from wayfarer.engine.simulation.health.cyclic_host_state import ADAPTER as CYCLIC_HOST_ADAPTER
from wayfarer.engine.simulation.health.hazard_records import HazardCommand
from wayfarer.engine.simulation.magic.analyze_magic_state import ADAPTER as ANALYZE_MAGIC_ADAPTER
from wayfarer.engine.simulation.magic.apportation_state import ADAPTER as APPORTATION_ADAPTER
from wayfarer.engine.simulation.magic.aura_state import ADAPTER as AURA_ADAPTER
from wayfarer.engine.simulation.magic.detect_magic_state import ADAPTER as DETECT_MAGIC_ADAPTER
from wayfarer.engine.simulation.magic.enchanting_transitions import (
    COMMAND_ADAPTER as ENCHANTMENT_ADAPTER,
)
from wayfarer.engine.simulation.magic.great_haste_named import (
    HOST_ADAPTER as GREAT_HASTE_ADAPTER,
)
from wayfarer.engine.simulation.magic.hand_melee_spell_state import ADAPTER as HAND_MELEE_ADAPTER
from wayfarer.engine.simulation.magic.haste_host import ADAPTER as HASTE_ADAPTER
from wayfarer.engine.simulation.magic.haste_manufacture import ObserveHasteManufacture
from wayfarer.engine.simulation.magic.haste_wearable_construction import (
    DeclareHasteWearableConstruction,
)
from wayfarer.engine.simulation.magic.identify_spell_admission import AcceptedSpellProducer
from wayfarer.engine.simulation.magic.identify_spell_state import ADAPTER as IDENTIFY_SPELL_ADAPTER
from wayfarer.engine.simulation.magic.limb_spell_commands import ADAPTER as LIMB_SPELL_ADAPTER
from wayfarer.engine.simulation.magic.lock_host import ADAPTER as LOCK_ADAPTER
from wayfarer.engine.simulation.magic.melee_spell_state import ADAPTER as MELEE_SPELL_ADAPTER
from wayfarer.engine.simulation.magic.ritual_state import DeclareRitualCapability
from wayfarer.engine.simulation.magic.rooted_feet_state import ADAPTER as ROOTED_FEET_ADAPTER
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand, SpellCommand
from wayfarer.engine.simulation.magic.staff_casting_state import ADAPTER as STAFF_CASTING_ADAPTER
from wayfarer.engine.simulation.magic.staff_state import DeclareStaffConstruction
from wayfarer.engine.simulation.magic.wither_spell_commands import ADAPTER as WITHER_SPELL_ADAPTER
from wayfarer.engine.simulation.social.social import SocialCommand
from wayfarer.engine.simulation.traits.composed_host import ADAPTER as COMPOSED_ADAPTER
from wayfarer.errors import ValidationError
from wayfarer.orchestration.analyze_magic import AnalyzeMagicService
from wayfarer.orchestration.apportation import ApportationService
from wayfarer.orchestration.armoury import ADAPTER as ARMOURY_ADAPTER
from wayfarer.orchestration.armoury import ArmouryService
from wayfarer.orchestration.aura import AuraService
from wayfarer.orchestration.combat import COMBAT_ADAPTER, CombatService
from wayfarer.orchestration.combat.abandon import AbandonPendingAttackService
from wayfarer.orchestration.combat.unarmed_host import RandomUnarmedService, RandomUnarmedStrike
from wayfarer.orchestration.combat_senses import CombatSensesService
from wayfarer.orchestration.composed_attacks import ComposedAttackService
from wayfarer.orchestration.cyclic import CyclicService
from wayfarer.orchestration.detect_magic import DetectMagicService
from wayfarer.orchestration.enchantments import EnchantmentService
from wayfarer.orchestration.great_haste import GreatHasteService
from wayfarer.orchestration.hand_melee_spells import HandMeleeSpellService
from wayfarer.orchestration.harmful_physiology import HarmfulPhysiologyService
from wayfarer.orchestration.haste import HasteService
from wayfarer.orchestration.hazard_resume import recorded_resume
from wayfarer.orchestration.hazards import HazardContext, HazardService
from wayfarer.orchestration.identify_spell import IdentifySpellService
from wayfarer.orchestration.limb_spells import LimbSpellService
from wayfarer.orchestration.locks import LockService, LockSpellService
from wayfarer.orchestration.melee_spells import MeleeSpellService
from wayfarer.orchestration.party import PartyCommand, PartyService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.recovery import RecoveryCommand, RecoveryService
from wayfarer.orchestration.replay_inputs import replay_inputs
from wayfarer.orchestration.rooted_feet import RootedFeetService
from wayfarer.orchestration.scenes import SCENE_ADAPTER, SceneService
from wayfarer.orchestration.size_forms import SizeFormService
from wayfarer.orchestration.social import ResolvedInteraction, SocialService
from wayfarer.orchestration.social_generations import replay_payload
from wayfarer.orchestration.spell_backfires import ResolveSpellBackfire, SpellBackfireService
from wayfarer.orchestration.spell_rituals import SpellRitualService
from wayfarer.orchestration.spells import SpellService
from wayfarer.orchestration.staff_casting import StaffCastingService
from wayfarer.orchestration.task_records import ADAPTER as TASK_ADAPTER
from wayfarer.orchestration.tasks import TaskService
from wayfarer.orchestration.transformations import TransformationService
from wayfarer.orchestration.water import ADAPTER as WATER_ADAPTER
from wayfarer.orchestration.water import WaterService
from wayfarer.orchestration.wither_spells import WitherSpellService
from wayfarer.persistence.events import CommandInput, CommandRecord
from wayfarer.persistence.replay import command_text, unavailable_reason


async def _haste_manufacture(play: PlayService, record: CommandRecord, encoded: str) -> None:

    await EnchantmentService(play).observe_haste_manufacture(
        record.campaign_id,
        ObserveHasteManufacture.model_validate_json(encoded),
        principal_id=record.actor_id,
    )


async def _haste_wearable_construction(
    play: PlayService, record: CommandRecord, encoded: str
) -> None:
    await EnchantmentService(play).declare_haste_wearable(
        record.campaign_id,
        DeclareHasteWearableConstruction.model_validate_json(encoded),
        principal_id=record.actor_id,
    )


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


async def _armoury(play: PlayService, record: CommandRecord, encoded: str) -> None:
    await ArmouryService(play).execute(
        record.campaign_id,
        ARMOURY_ADAPTER.validate_json(encoded),
        principal_id=record.actor_id,
    )


async def _great_haste(play: PlayService, record: CommandRecord, encoded: str) -> None:
    await GreatHasteService(play).execute(
        record.campaign_id,
        GREAT_HASTE_ADAPTER.validate_json(encoded),
        principal_id=record.actor_id,
    )


async def _water(play: PlayService, record: CommandRecord, encoded: str) -> None:
    await WaterService(play).execute(
        record.campaign_id,
        WATER_ADAPTER.validate_json(encoded),
        principal_id=record.actor_id,
    )


async def _apportation(play: PlayService, record: CommandRecord, encoded: str) -> None:
    await ApportationService(play).execute(
        record.campaign_id,
        APPORTATION_ADAPTER.validate_json(encoded),
        principal_id=record.actor_id,
    )


async def _haste(play: PlayService, record: CommandRecord, encoded: str) -> None:
    await HasteService(play).execute(
        record.campaign_id,
        HASTE_ADAPTER.validate_json(encoded),
        principal_id=record.actor_id,
    )


async def _world_equipment(play: PlayService, record: CommandRecord, encoded: str) -> None:
    await SizeFormService(play).retrieve(
        record.campaign_id,
        WorldGroundCommand.model_validate_json(encoded),
        principal_id=record.actor_id,
    )


async def _analyze_magic(play: PlayService, record: CommandRecord, encoded: str) -> None:
    await AnalyzeMagicService(play).execute(
        record.campaign_id,
        ANALYZE_MAGIC_ADAPTER.validate_json(encoded),
        principal_id=record.actor_id,
    )


async def _hand_melee_spell(play: PlayService, record: CommandRecord, encoded: str) -> None:
    payload = validation.mapping(validation.decode(record.command_input or "{}"))
    generation = payload.get("generation")
    if type(generation) is not int or generation != 1:
        raise ValidationError("Hand Melee replay requires its captured generation")
    await HandMeleeSpellService(play).execute(
        record.campaign_id, HAND_MELEE_ADAPTER.validate_json(encoded), principal_id=record.actor_id
    )


async def _melee_spell(play: PlayService, record: CommandRecord, encoded: str) -> None:
    payload = validation.mapping(validation.decode(record.command_input or "{}"))
    generation = payload.get("generation")
    if type(generation) is not int or generation != 1:
        raise ValidationError("Melee spell replay requires its captured generation")
    await MeleeSpellService(play).execute(
        record.campaign_id,
        MELEE_SPELL_ADAPTER.validate_json(encoded),
        principal_id=record.actor_id,
    )


async def _aura(play: PlayService, record: CommandRecord, encoded: str) -> None:
    await AuraService(play).execute(
        record.campaign_id, AURA_ADAPTER.validate_json(encoded), principal_id=record.actor_id
    )


async def _rooted_feet(play: PlayService, record: CommandRecord, encoded: str) -> None:
    payload = validation.mapping(validation.decode(record.command_input or "{}"))
    generation = payload.get("generation")
    if type(generation) is not int or generation != 1:
        raise ValidationError("Rooted Feet replay requires its captured generation")
    await RootedFeetService(play).execute(
        record.campaign_id,
        ROOTED_FEET_ADAPTER.validate_json(encoded),
        principal_id=record.actor_id,
    )


async def _wither_spell(play: PlayService, record: CommandRecord, encoded: str) -> None:
    payload = validation.mapping(validation.decode(record.command_input or "{}"))
    generation = payload.get("generation")
    if type(generation) is not int or generation != 3:
        raise ValidationError("Wither spell replay requires its captured generation")
    await WitherSpellService(play).execute(
        record.campaign_id,
        WITHER_SPELL_ADAPTER.validate_json(encoded),
        principal_id=record.actor_id,
    )


async def _limb_spell(play: PlayService, record: CommandRecord, encoded: str) -> None:
    payload = validation.mapping(validation.decode(record.command_input or "{}"))
    generation = payload.get("generation")
    if type(generation) is not int or generation != 2:
        raise ValidationError("Limb spell replay requires its captured generation")
    await LimbSpellService(play).execute(
        record.campaign_id,
        LIMB_SPELL_ADAPTER.validate_json(encoded),
        principal_id=record.actor_id,
    )


async def _identify_spell(play: PlayService, record: CommandRecord, encoded: str) -> None:
    payload = validation.mapping(validation.decode(record.command_input or "{}"))
    if payload.get("producer_generation") != 1:
        raise ValidationError("Identify Spell replay requires captured producer evidence")
    producers = tuple(
        AcceptedSpellProducer.model_validate(value)
        for value in validation.sequence(payload["producer_evidence"])
    )
    await IdentifySpellService(play).execute(
        record.campaign_id,
        IDENTIFY_SPELL_ADAPTER.validate_json(encoded),
        principal_id=record.actor_id,
        replay_producers=producers,
    )


async def _detect_magic(play: PlayService, record: CommandRecord, encoded: str) -> None:
    await DetectMagicService(play).execute(
        record.campaign_id,
        DETECT_MAGIC_ADAPTER.validate_json(encoded),
        principal_id=record.actor_id,
    )


_REGISTERED_FAMILIES: Mapping[str, Callable[[PlayService, CommandRecord, str], Awaitable[None]]] = {
    "rooted-feet": _rooted_feet,
    "limb-spell": _limb_spell,
    "wither-spell": _wither_spell,
    "melee-spell": _melee_spell,
    "hand-melee-spell": _hand_melee_spell,
    "aura": _aura,
    "detect-magic": _detect_magic,
    "identify-spell": _identify_spell,
    "analyze-magic": _analyze_magic,
    "armoury": _armoury,
    "great-haste": _great_haste,
    "water": _water,
    "world-equipment": _world_equipment,
    "apportation": _apportation,
    "haste": _haste,
    "gurps-social": _social,
    "task-host": _task_host,
    "gurps-hazard": _natural_hazard_resume,
    "composed-attack": _composed_attack,
    "composed-defense": _composed_defense,
    "enchantment": _enchantment,
    "staff-construction": _staff_construction,
    "haste-manufacture": _haste_manufacture,
    "haste-wearable-construction": _haste_wearable_construction,
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
