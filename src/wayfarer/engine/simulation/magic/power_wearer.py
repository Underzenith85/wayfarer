"""B480/B482 zero-casting-cost Haste: live wearer effects and owner switches.

Every effect shares the ordinary spell projection. No second clock, energy pool,
movement bonus ledger, or fabricated caster action is introduced.
"""

import hashlib

from wayfarer.engine.rules.magic.gurps_magic import magery_level
from wayfarer.engine.rules.magic.protocols import (
    MagicItemBinding,
    effective_item_power,
    item_energy_cost,
)
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.combat.special_melee import actor_size_modifier
from wayfarer.engine.simulation.magic.haste_state import HasteItem, enabled, environments, items
from wayfarer.engine.simulation.magic.item_state import (
    _surviving_bindings,
    item_power_reduction,
    item_requires_magery,
    usable_item_enchantment,
)
from wayfarer.engine.simulation.magic.spell_state import (
    RuntimeSpellEffect,
    RuntimeSpellEvent,
    SpellResult,
    event_id,
    latest,
)
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState, is_carried
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PREFIX = "power-wearer:"


class WearerOrigin(Record):
    item_id: Id
    binding_id: Id
    wearer_id: Id
    cast_id: Id


def origins(state: PlayState) -> tuple[WearerOrigin, ...]:
    return tuple(
        WearerOrigin.model_validate_json(e.kind)
        for e in state.resources.events
        if e.id.startswith(PREFIX)
    )


def _support(
    runtime: RulesContext, state: PlayState, metadata: HasteItem
) -> tuple[str, str, MagicItemBinding] | None:
    item = next((i for i in state.resources.items if i.id == metadata.item_id), None)
    if (
        item is None
        or item.definition_id != metadata.definition_id
        or item.quantity != 1
        or not item.equipped
        or item.container_id is not None
        or not is_carried(state.resources, item)
        or not enabled(state.resources, metadata)
    ):
        return None
    if not any(a.actor_id == item.owner_id for a in state.actors):
        return None
    location = next((e.location_id for e in state.world.entities if e.id == item.owner_id), None)
    mana = environments(state.resources).get(location or "")
    if mana is None:
        return None
    configured = runtime.rules.spells.magic_items if runtime.rules.spells else ()
    selected = next(
        (
            b
            for b in _surviving_bindings(state.resources, item.id, configured)
            if b.id == metadata.binding_id and b.spell_id == "haste"
        ),
        None,
    )
    if selected is None or not usable_item_enchantment(state.resources, selected, mana):
        return None
    completed = next((b for b in item.enchantments if b.id == selected.id), None)
    # Charges and self-powered continuous use require a separate source ruling.
    if completed is None or completed.runtime_family != "spell" or completed.charges is not None:
        return None
    try:
        compiled = build(runtime, state, item.owner_id)

        size_scale = 1 + max(0, actor_size_modifier(runtime, state, item.owner_id))
        if (
            item_requires_magery(state.resources, item.id, configured)
            and magery_level({p.definition_id: p.amount for p in compiled.purchases}) < 0
        ):
            return None
        reduction = item_power_reduction(state.resources, item.id, selected, configured, mana)
        if (
            reduction <= 0
            or item_energy_cost(2 * metadata.levels * size_scale, reduction, mana) != 0
        ):
            return None
    except ValidationError, ConflictError:
        return None
    return (
        item.owner_id,
        compiled.revision,
        selected.model_copy(update={"power": effective_item_power(selected.power, mana)}),
    )


def checkpoint(runtime: RulesContext, state: PlayState) -> PlayState:
    resources = state.resources
    projected = latest(resources)
    known = origins(state)
    for metadata in items(resources):
        old = tuple(
            o for o in known if (o.item_id, o.binding_id) == (metadata.item_id, metadata.binding_id)
        )
        support = _support(runtime, state, metadata)
        retained = False
        for origin in old:
            effect = projected.get(origin.cast_id)
            if effect is None or effect.phase != "active":
                continue
            if support is not None and (origin.wearer_id, effect.build_revision) == support[:2]:
                retained = True
                continue
            effect = effect.model_copy(update={"phase": "ended"})
            resources = _effect(
                resources, effect, f"end:{effect.cast_id}:{state.revision}", "interrupted"
            )
        if support is None or retained:
            continue
        wearer_id, revision, binding = support
        cast_id = (
            "power-haste:"
            + hashlib.sha256(
                f"{metadata.item_id}:{metadata.binding_id}:{wearer_id}:{len(old)}".encode()
            ).hexdigest()
        )
        origin = WearerOrigin(
            item_id=metadata.item_id,
            binding_id=metadata.binding_id,
            wearer_id=wearer_id,
            cast_id=cast_id,
        )
        resources = resources.model_copy(
            update={
                "events": resources.events
                + (
                    ResourceEvent(
                        id=PREFIX + hashlib.sha256(cast_id.encode()).hexdigest(),
                        at=resources.game_time,
                        target_id=wearer_id,
                        kind=origin.model_dump_json(),
                    ),
                )
            }
        )
        hp = next(p for p in resources.pools if p.id == "hp:" + wearer_id)
        effect = RuntimeSpellEffect(
            cast_id=cast_id,
            actor_id="item:" + metadata.item_id,
            target_id=wearer_id,
            spell_id="haste",
            build_revision=revision,
            phase="active",
            started_at=resources.game_time,
            ready_at=resources.game_time,
            expires_at=None,
            skill=binding.power,
            cost=0,
            maintenance=0,
            hp_at_start=hp.current,
            energy=metadata.levels,
            execute_effects=True,
            execution_version=2,
        )
        resources = _effect(resources, effect, "start:" + cast_id, "active")
    return (
        state.model_copy(update={"resources": resources}) if resources != state.resources else state
    )


def _effect(
    resources: ResourceState, effect: RuntimeSpellEffect, identifier: str, outcome: str
) -> ResourceState:
    result = SpellResult.model_validate({"outcome": outcome})
    return resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=event_id(identifier, "haste"),
                    at=resources.game_time,
                    target_id=effect.target_id,
                    kind=RuntimeSpellEvent(effect=effect, result=result).model_dump_json(),
                ),
            )
        }
    )
