"""Explicit private Haste declarations and owner-controlled Power switches."""

from typing import Annotated

from pydantic import Field, TypeAdapter

from wayfarer.engine.rules.magic.gurps_magic import magery_level
from wayfarer.engine.rules.magic.movement import package as movement_package
from wayfarer.engine.rules.magic.protocols import item_energy_cost
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.campaign.party import synchronous
from wayfarer.engine.simulation.combat.special_melee import actor_size_modifier
from wayfarer.engine.simulation.magic.haste_state import (
    CHANNEL,
    ITEM,
    MANA,
    RECEIPT,
    SWITCH,
    DeclareHasteChannel,
    DeclareHasteItem,
    HasteReceipt,
    ObserveHasteMana,
    SwitchHasteItem,
    channels,
    environments,
    items,
    record,
)
from wayfarer.engine.simulation.magic.item_state import (
    item_magic_lost,
    item_power_reduction,
    item_requires_magery,
    usable_item_enchantment,
)
from wayfarer.engine.simulation.magic.power_lifecycle import _awake
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand
from wayfarer.engine.simulation.resources import ResourceState, is_carried
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.world import EntityKind
from wayfarer.errors import ConflictError, ValidationError

HasteHostCommand = DeclareHasteChannel | DeclareHasteItem | ObserveHasteMana | SwitchHasteItem
HasteCommand = Annotated[HasteHostCommand | RuntimeSpellCommand, Field(discriminator="kind")]
ADAPTER: TypeAdapter[HasteCommand] = TypeAdapter(HasteCommand)


def apply_host(
    runtime: RulesContext, state: PlayState, command: HasteHostCommand
) -> tuple[PlayState, HasteReceipt]:
    resources = state.resources
    outcome = "declared"
    if isinstance(command, DeclareHasteChannel):
        channel = command.channel
        if any(c.id == channel.id for c in channels(resources)):
            raise ConflictError("Haste channels cannot be replaced")
        entities = {e.id: e for e in state.world.entities}
        if (
            channel.location_id not in entities
            or entities[channel.location_id].kind is not EntityKind.LOCATION
        ):
            raise ValidationError("Haste channel requires a real location")
        for actor_id in (channel.actor_id, channel.target_id):
            build(runtime, state, actor_id)
        if channel.magic_item_id is not None and not any(
            v.item_id == channel.magic_item_id for v in items(resources)
        ):
            raise ValidationError("Haste item channel requires its explicit magnitude and form")
        resources = record(resources, CHANNEL, command.id, channel.actor_id, channel)
    elif isinstance(command, DeclareHasteItem):
        resources = _declare_item(runtime, state, command)
    elif isinstance(command, ObserveHasteMana):
        environment = command.environment
        if not any(
            e.id == environment.location_id and e.kind is EntityKind.LOCATION
            for e in state.world.entities
        ):
            raise ValidationError("Haste mana evidence requires a real location")
        resources = record(resources, MANA, command.id, command.actor_id, environment)
    else:
        synchronous(state, command.actor_id)
        compiled = build(runtime, state, command.actor_id)

        if not _awake(state, command.actor_id):
            raise ValidationError("Owner must be awake to operate the Power item")
        item = next((i for i in resources.items if i.id == command.switch.item_id), None)
        if item is None or item.owner_id != command.actor_id or not is_carried(resources, item):
            raise ValidationError("Only the current holder can operate the Power item")
        if not any(
            (v.item_id, v.binding_id) == (item.id, command.switch.binding_id)
            for v in items(resources)
        ):
            raise ValidationError("Unknown Haste item binding")
        location = next(e.location_id for e in state.world.entities if e.id == command.actor_id)
        if location not in environments(resources):
            raise ValidationError("Power item requires current location mana evidence")

        metadata = next(
            v
            for v in items(resources)
            if (v.item_id, v.binding_id) == (item.id, command.switch.binding_id)
        )
        selected = next((b for b in item.enchantments if b.id == metadata.binding_id), None)
        if selected is None or selected.charges is not None:
            raise ValidationError("Power switches require an unlimited completed Haste item")
        configured = runtime.rules.spells.magic_items if runtime.rules.spells else ()
        mana = environments(resources)[location]
        if not usable_item_enchantment(resources, selected, mana):
            raise ValidationError("Power switch requires its usable Haste enchantment")
        if (
            item_requires_magery(resources, item.id, configured)
            and magery_level({p.definition_id: p.amount for p in compiled.purchases}) < 0
        ):
            raise ValidationError("Power item requires current Magery")
        reduction = item_power_reduction(resources, item.id, selected, configured, mana)
        size_scale = 1 + max(0, actor_size_modifier(runtime, state, item.owner_id))
        if (
            reduction <= 0
            or item_energy_cost(2 * metadata.levels * size_scale, reduction, mana) != 0
        ):
            raise ValidationError("Power switches require zero casting cost in the current mana")
        resources = record(resources, SWITCH, command.id, command.actor_id, command.switch)
        outcome = "on" if command.switch.enabled else "off"
    receipt = HasteReceipt.model_validate(dict(command_id=command.id, outcome=outcome))
    resources = record(resources, RECEIPT, command.id, command.actor_id, receipt)
    resources = resources.model_copy(update={"revision": state.revision + 1})
    return state.model_copy(
        update={"revision": state.revision + 1, "resources": resources}
    ), receipt


def _declare_item(
    runtime: RulesContext, state: PlayState, command: DeclareHasteItem
) -> ResourceState:
    resources = state.resources
    expected = next(d for d in movement_package().definitions if d.id == "spell:haste")
    if runtime.reviewer.compiler.definitions.get(expected.id) != expected:
        raise ValidationError("Haste item requires the pinned source catalog")
    value = command.item
    previous = tuple(v for v in items(resources) if v.item_id == value.item_id)
    if any(
        v.binding_id == value.binding_id or not item_magic_lost(resources, v.item_id, v.binding_id)
        for v in previous
    ):
        raise ConflictError("Haste item magnitude and form cannot be replaced")
    item = next((i for i in resources.items if i.id == value.item_id), None)
    if item is None or item.definition_id != value.definition_id or item.quantity != 1:
        raise ValidationError("Haste declaration requires its unique actual item")
    spec = runtime.resources.specs.get(item.definition_id)
    if spec is None or spec.slot is None or spec.slot == "hand":
        raise ValidationError("Haste requires wearable clothing or jewelry, not a wielded weapon")
    enchantment = next((v for v in item.enchantments if v.id == value.binding_id), None)
    if (
        enchantment is None
        or enchantment.spell_id != "haste"
        or enchantment.runtime_family != "spell"
        or item_magic_lost(resources, value.item_id, value.binding_id)
    ):
        raise ValidationError("Haste declaration requires its completed Haste enchantment")
    return record(resources, ITEM, command.id, item.owner_id, value)
