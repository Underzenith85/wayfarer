"""B480-482 item-wide magic authority and permanent loss after physical breakage.

Loss records preserve the original bindings and their completed project evidence.
A fresh binding ID from a later enchantment can work, even in the same second.
"""

import hashlib
from typing import Literal

from wayfarer.engine.rules.magic.protocols import (
    MagicItemBinding,
    ManaLevel,
    effective_item_power,
)
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

PREFIX = "item-magic-loss:"


class ItemMagicLoss(Record):
    kind: Literal["item-magic-loss-v1"] = "item-magic-loss-v1"
    item_id: Id
    binding_ids: tuple[Id, ...]


def losses(resources: ResourceState) -> tuple[ItemMagicLoss, ...]:
    return tuple(
        ItemMagicLoss.model_validate_json(event.kind)
        for event in resources.events
        if event.id.startswith(PREFIX)
    )


def checkpoint(
    resources: ResourceState,
    *,
    before: ResourceState | None = None,
    configured_bindings: tuple[MagicItemBinding, ...] = (),
) -> ResourceState:
    """Persist known magical items' lost bindings without changing mundane history.

    The previous state also covers ordinary repair of an already-broken legacy
    checkpoint. Repeated calls neither duplicate records nor lose new bindings.
    """
    configured_ids = {binding.item_id for binding in configured_bindings}
    records = losses(resources)
    known = {record.item_id for record in records}
    lost = {(record.item_id, binding_id) for record in records for binding_id in record.binding_ids}
    events: list[ResourceEvent] = []
    for snapshot in (before, resources):
        if snapshot is None:
            continue
        for item in snapshot.items:
            if not item.enchantments and item.id not in configured_ids:
                continue
            if item.condition is None or not (item.condition.disabled or item.condition.destroyed):
                continue
            binding_ids = tuple(
                sorted(
                    binding.id for binding in item.enchantments if (item.id, binding.id) not in lost
                )
            )
            if item.id in known and not binding_ids:
                continue
            record = ItemMagicLoss(item_id=item.id, binding_ids=binding_ids)
            events.append(
                ResourceEvent(
                    id=PREFIX + hashlib.sha256(record.model_dump_json().encode()).hexdigest(),
                    at=resources.game_time,
                    target_id=item.id,
                    kind=record.model_dump_json(),
                )
            )
            known.add(item.id)
            lost.update((item.id, binding_id) for binding_id in binding_ids)
    return (
        resources.model_copy(update={"events": resources.events + tuple(events)})
        if events
        else resources
    )


def item_magic_lost(resources: ResourceState, item_id: str, binding_id: str) -> bool:
    """A physical loss invalidates static bindings and the recorded old instances."""
    records = tuple(record for record in losses(resources) if record.item_id == item_id)
    completed = any(
        binding.id == binding_id
        for item in resources.items
        if item.id == item_id
        for binding in item.enchantments
    )
    if completed:
        return any(binding_id in record.binding_ids for record in records)
    # Standalone object damage has no campaign SpellRules. Its existing physical
    # evidence still bars static legacy magic after repair, without adding magic
    # events to every mundane object's historical commands.
    return bool(records) or any(
        result.item_id == item_id and (result.condition.disabled or result.condition.destroyed)
        for result in resources.object_results
    )


def usable_item_enchantment(
    resources: ResourceState, binding: MagicItemBinding, mana: ManaLevel
) -> bool:
    """Each surviving spell's own Power must work in the current mana (B481)."""
    item = next((item for item in resources.items if item.id == binding.item_id), None)
    power = effective_item_power(binding.power, mana)
    return (
        item is not None
        and (item.condition is None or not (item.condition.disabled or item.condition.destroyed))
        and power is not None
        and power >= 15
        and not item_magic_lost(resources, binding.item_id, binding.id)
    )


def _surviving_bindings(
    resources: ResourceState,
    item_id: str,
    configured_bindings: tuple[MagicItemBinding, ...],
) -> tuple[MagicItemBinding, ...]:
    bindings = {
        binding.id: binding for binding in configured_bindings if binding.item_id == item_id
    }
    bindings.update(
        (binding.id, binding)
        for item in resources.items
        if item.id == item_id
        for binding in item.enchantments
    )
    return tuple(
        binding
        for binding in bindings.values()
        if not item_magic_lost(resources, item_id, binding.id)
    )


def has_item_magic(
    resources: ResourceState,
    item_id: str,
    configured_bindings: tuple[MagicItemBinding, ...] = (),
) -> bool:
    """Whether the item retains any completed or configured enchantment."""
    return bool(_surviving_bindings(resources, item_id, configured_bindings))


def item_requires_magery(
    resources: ResourceState,
    item_id: str,
    configured_bindings: tuple[MagicItemBinding, ...] = (),
) -> bool:
    """B482: any surviving mage-only spell restricts the whole physical item."""
    return any(
        binding.requires_magery
        for binding in _surviving_bindings(resources, item_id, configured_bindings)
    )


def require_power_installation(
    resources: ResourceState,
    item_id: str,
    configured_bindings: tuple[MagicItemBinding, ...] = (),
) -> None:
    """Reject unspecified Power composition before committing new costly work."""
    if any(
        binding.spell_id == "power" or binding.power_reduction
        for binding in _surviving_bindings(resources, item_id, configured_bindings)
    ):
        raise ValidationError(
            "Multiple or mixed Power sources require unsupported composition rules"
        )


def item_power_reduction(
    resources: ResourceState,
    item_id: str,
    selected_binding: MagicItemBinding,
    configured_bindings: tuple[MagicItemBinding, ...],
    mana: ManaLevel,
) -> int:
    """Return one raw Power level; item_energy_cost applies mana scaling once.

    Basic Set does not settle permanent Power stacking/upgrading. Keep that
    composition unsupported, including mixing a passive Power with old inline
    discounts. A legacy inline discount retains its original per-binding scope.
    """
    sources = _surviving_bindings(resources, item_id, configured_bindings)
    passive = tuple(binding for binding in sources if binding.spell_id == "power")
    inline = tuple(
        binding for binding in sources if binding.spell_id != "power" and binding.power_reduction
    )
    if len(passive) > 1 or (passive and inline):
        raise ValidationError(
            "Multiple or mixed Power sources require unsupported composition rules"
        )
    source = (
        passive[0]
        if passive
        else next((binding for binding in inline if binding.id == selected_binding.id), None)
    )
    if source is None or not usable_item_enchantment(resources, source, mana):
        return 0
    return source.power_reduction
