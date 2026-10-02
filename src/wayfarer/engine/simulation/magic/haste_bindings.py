"""B251 regular Haste and B482 self-only item channels on approved current state."""

from wayfarer.engine.rules.magic.protocols import MagicItemBinding
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.combat.special_melee import actor_size_modifier
from wayfarer.engine.simulation.magic.binding_context import SpellEnvironment
from wayfarer.engine.simulation.magic.binding_context import approved_context as build_context
from wayfarer.engine.simulation.magic.haste_state import channels, environments, items
from wayfarer.engine.simulation.magic.item_state import (
    _surviving_bindings,
    item_power_reduction,
    item_requires_magery,
    usable_item_enchantment,
)
from wayfarer.engine.simulation.magic.power_lifecycle import remember, require_origin
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand, SpellContext
from wayfarer.engine.simulation.resources import ResourceState, is_carried
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError


def approved_context(
    runtime: RulesContext, state: PlayState, command: RuntimeSpellCommand
) -> SpellContext:
    channel = next((c for c in channels(state.resources) if c.id == command.channel_id), None)
    if channel is None or channel.actor_id != command.actor_id:
        raise AuthorizationError("Haste channel does not authorize this caster")
    if (
        command.target_item_id is not None
        or command.hit_location is not None
        or command.position is not None
    ):
        raise ValidationError("Haste affects the channel's living subject")
    build(runtime, state, channel.target_id)
    size_scale = 1 + max(0, actor_size_modifier(runtime, state, channel.target_id))
    effect = latest(state.resources).get(command.cast_id)
    lifecycle = command.kind in ("cancel", "maintain", "remember")
    if lifecycle and (
        effect is None
        or (effect.actor_id, effect.spell_id, effect.target_id)
        != (command.actor_id, "haste", channel.target_id)
    ):
        raise ConflictError("Haste lifecycle does not identify its existing effect")
    entities = {e.id: e for e in state.world.entities}
    if not lifecycle and any(
        entities[a].location_id != channel.location_id
        for a in (command.actor_id, channel.target_id)
    ):
        raise ValidationError("Haste channel location changed")
    encounter = next(
        (e for e in state.encounters if e.status == "active" and command.actor_id in e.turn_order),
        None,
    )
    position: tuple[int, int] | None = None
    geometry, distance = "square", channel.distance_yards
    if encounter is not None and not lifecycle:
        # deferred: the shared spell dispatcher invokes this private channel adapter.
        from wayfarer.engine.simulation.magic.spell_transitions import _participant_target

        position, geometry, distance = _participant_target(
            encounter, command.actor_id, channel.target_id
        )
    touching = channel.touching and distance <= 1
    mana = channel.mana
    magic: MagicItemBinding | None = None
    if channel.magic_item_id is not None:
        item = next((i for i in state.resources.items if i.id == channel.magic_item_id), None)
        metadata = next(
            (v for v in reversed(items(state.resources)) if v.item_id == channel.magic_item_id),
            None,
        )
        if (
            item is None
            or metadata is None
            or item.owner_id != command.actor_id
            or not item.equipped
            or item.container_id is not None
            or not is_carried(state.resources, item)
        ):
            raise ValidationError("Haste item must be worn by its current owner")
        if command.kind == "start" and command.energy != metadata.levels:
            raise ValidationError("Item Haste magnitude is fixed by its explicit binding")
        location = entities[command.actor_id].location_id
        observed = environments(state.resources).get(location or "")
        if observed is None:
            raise ValidationError("Haste item requires current location mana evidence")
        mana = observed
        configured = runtime.rules.spells.magic_items if runtime.rules.spells else ()
        selected = next(
            (
                v
                for v in _surviving_bindings(state.resources, item.id, configured)
                if v.id == metadata.binding_id and v.spell_id == "haste"
            ),
            None,
        )
        if selected is None or not usable_item_enchantment(state.resources, selected, mana):
            raise ValidationError("Haste item has no usable enchantment")
        if selected.always_on:
            raise ValidationError("Always-on Haste items use wearer activation")
        if command.kind == "start" and any(
            v.id == selected.id and v.charges == 0 for v in item.enchantments
        ):
            raise ValidationError("Haste item has no charges")
        magic = MagicItemBinding.model_validate(
            {
                **selected.model_dump(include=set(MagicItemBinding.model_fields)),
                "requires_magery": item_requires_magery(state.resources, item.id, configured),
                "power_reduction": item_power_reduction(
                    state.resources, item.id, selected, configured, mana
                ),
            }
        )

        require_origin(state.resources, command, selected)
    context = build_context(
        runtime,
        state,
        command,
        SpellEnvironment(
            target_id=channel.target_id,
            mana=mana,
            distance=0
            if lifecycle or touching or command.actor_id == channel.target_id
            else distance,
            energy=command.energy,
            radius=command.radius,
            unseen=not lifecycle and not channel.visible and not touching,
            magic_item=magic,
        ),
    )
    return context.model_copy(
        update={
            "execution_version": 2,
            "haste_size_scale": size_scale,
            "execute_effects": True,
            "location_id": effect.location_id if lifecycle and effect else channel.location_id,
            "encounter_id": effect.encounter_id
            if lifecycle and effect
            else encounter.id
            if encounter
            else None,
            "position": effect.position if lifecycle and effect else position,
            "geometry": effect.geometry if lifecycle and effect else geometry,
        }
    )


def remember_item(
    runtime: RulesContext, before: PlayState, resources: ResourceState, command: RuntimeSpellCommand
) -> ResourceState:
    """One ordinary paid activation, with the existing Power origin and charge path."""
    channel = next(c for c in channels(before.resources) if c.id == command.channel_id)
    if channel.magic_item_id is None:
        return resources

    configured = runtime.rules.spells.magic_items if runtime.rules.spells else ()
    resources = remember(
        resources,
        command,
        channel,
        configured,
        haste_size_scale=1 + max(0, actor_size_modifier(runtime, before, channel.target_id)),
    )
    metadata = next(v for v in reversed(items(resources)) if v.item_id == channel.magic_item_id)
    return resources.model_copy(
        update={
            "items": tuple(
                item.model_copy(
                    update={
                        "enchantments": tuple(
                            b.model_copy(update={"charges": b.charges - 1})
                            if b.id == metadata.binding_id and b.charges is not None
                            else b
                            for b in item.enchantments
                        )
                    }
                )
                if item.id == metadata.item_id
                else item
                for item in resources.items
            )
        }
    )
