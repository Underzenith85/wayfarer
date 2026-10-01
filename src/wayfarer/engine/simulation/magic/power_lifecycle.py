"""B238/B480-482 paid item casts with free, awake-only maintenance.

Private origin evidence never turns an outward cast into a wearer effect. Each
renewal updates the existing spell projection and preserves its normal ending
rules, original subject, and spells-on count.
"""

import hashlib
from typing import TYPE_CHECKING

from wayfarer.engine.rules.magic.gurps_magic import magery_level
from wayfarer.engine.rules.magic.protocols import MagicItemBinding, item_energy_cost
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.combat.special_damage import active_afflictions
from wayfarer.engine.simulation.health.drug_state import drug_unconscious
from wayfarer.engine.simulation.health.injury import InjuryResult
from wayfarer.engine.simulation.health.sleep_state import asleep
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
    parse_event,
)
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState, is_carried
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

if TYPE_CHECKING:
    from wayfarer.engine.simulation.magic.bindings import SpellChannel
    from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand

PREFIX = "power-cast-origin:"


class PowerCastOrigin(Record):
    cast_id: Id
    actor_id: Id
    target_id: Id
    channel_id: Id
    item_id: Id
    binding_id: Id
    binding_digest: str
    source_digest: str
    duration: int
    maintenance: int


def origins(resources: ResourceState) -> tuple[PowerCastOrigin, ...]:
    return tuple(
        PowerCastOrigin.model_validate_json(event.kind)
        for event in resources.events
        if event.id.startswith(PREFIX)
    )


def _digest(binding: MagicItemBinding) -> str:
    # Charges, owner and construction history belong to the instance, not the
    # enchantment's immutable identity. A charge is spent by the ordinary cast.
    data = binding.model_dump_json(
        include=set(MagicItemBinding.model_fields) | {"runtime_family", "activation"}
    )
    return hashlib.sha256(data.encode()).hexdigest()


def _sources(
    resources: ResourceState, binding: MagicItemBinding, configured: tuple[MagicItemBinding, ...]
) -> str:
    sources = _surviving_bindings(resources, binding.item_id, configured)
    selected = tuple(
        b for b in sources if b.spell_id == "power" or (b.id == binding.id and b.power_reduction)
    )
    return hashlib.sha256(":".join(sorted(_digest(b) for b in selected)).encode()).hexdigest()


def remember(
    resources: ResourceState,
    command: RuntimeSpellCommand,
    channel: SpellChannel,
    configured: tuple[MagicItemBinding, ...],
) -> ResourceState:
    """Called once by the canonical cast transition, before combat completion."""
    # deferred: keep spell execution's state vocabulary independent of its host.
    from wayfarer.engine.simulation.magic.spells import _executable_spec

    effect = latest(resources)[command.cast_id]
    spec = _executable_spec(effect.spell_id)
    if effect.cost <= 0 or effect.maintenance != 0 or spec.maintenance <= 0 or not spec.duration:
        return resources
    assert channel.magic_item_id is not None
    bindings = _surviving_bindings(resources, channel.magic_item_id, configured)
    completed = next(i for i in resources.items if i.id == channel.magic_item_id).enchantments
    binding = next(
        (b for b in completed if b.spell_id == command.spell_id and b in bindings),
        next((b for b in bindings if b.spell_id == command.spell_id), None),
    )
    assert binding is not None
    origin = PowerCastOrigin(
        cast_id=effect.cast_id,
        actor_id=effect.actor_id,
        target_id=effect.target_id,
        channel_id=channel.id,
        item_id=binding.item_id,
        binding_id=binding.id,
        binding_digest=_digest(binding),
        source_digest=_sources(resources, binding, configured),
        duration=spec.duration,
        maintenance=spec.maintenance * (effect.radius if spec.kind == "area" else 1),
    )
    return resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=PREFIX + hashlib.sha256(effect.cast_id.encode()).hexdigest(),
                    at=resources.game_time,
                    target_id=effect.actor_id,
                    kind=origin.model_dump_json(),
                ),
            )
        }
    )


def require_origin(
    resources: ResourceState, command: RuntimeSpellCommand, binding: MagicItemBinding
) -> None:
    origin = next((o for o in origins(resources) if o.cast_id == command.cast_id), None)
    if origin is not None and (
        origin.channel_id != command.channel_id
        or origin.actor_id != command.actor_id
        or origin.item_id != binding.item_id
        or origin.binding_id != binding.id
        or origin.binding_digest != _digest(binding)
    ):
        raise ConflictError("Power-maintained cast requires its original item and spell binding")


def _awake(state: PlayState, actor_id: str) -> bool:
    actor = next((a for a in state.actors if a.actor_id == actor_id), None)
    hp = next((p for p in state.resources.pools if p.id == "hp:" + actor_id), None)
    fp = next((p for p in state.resources.pools if p.id == "fp:" + actor_id), None)
    return bool(
        actor is not None
        and "unconscious" not in actor.conditions
        and not asleep(state.resources, actor_id)
        and not drug_unconscious(state.resources, actor_id)
        and hp is not None
        and hp.injury is not None
        and not hp.injury.incapacitated
        and fp is not None
        and fp.fatigue is not None
        and not (fp.fatigue.unconscious or fp.fatigue.heart_attack)
        and not any(
            e.condition in ("sleep", "unconsciousness", "coma")
            for e in active_afflictions(state.resources, actor_id)
        )
    )


def _supported(
    runtime: RulesContext, state: PlayState, origin: PowerCastOrigin, effect: RuntimeSpellEffect
) -> bool:
    rules = runtime.rules.spells
    if (
        rules is None
        or (effect.actor_id, effect.target_id) != (origin.actor_id, origin.target_id)
        or effect.reversed
    ):
        return False
    channel = next((c for c in rules.channels if c.id == origin.channel_id), None)
    if channel is None or (
        channel.actor_id,
        channel.target_id,
        channel.spell_id,
        channel.magic_item_id,
    ) != (origin.actor_id, origin.target_id, effect.spell_id, origin.item_id):
        return False
    item = next((i for i in state.resources.items if i.id == origin.item_id), None)
    if (
        item is None
        or item.owner_id != origin.actor_id
        or not (item.equipped or item.ready)
        or item.container_id is not None
        or not is_carried(state.resources, item)
    ):
        return False
    bindings = _surviving_bindings(state.resources, item.id, rules.magic_items)
    binding = next((b for b in bindings if b.id == origin.binding_id), None)
    if (
        binding is None
        or _digest(binding) != origin.binding_digest
        or _sources(state.resources, binding, rules.magic_items) != origin.source_digest
        or not usable_item_enchantment(state.resources, binding, channel.mana)
    ):
        return False
    try:
        compiled = build(runtime, state, origin.actor_id)
        if compiled.revision != effect.build_revision:
            return False
        if (
            item_requires_magery(state.resources, item.id, rules.magic_items)
            and magery_level({p.definition_id: p.amount for p in compiled.purchases}) < 0
        ):
            return False
        reduction = item_power_reduction(
            state.resources, item.id, binding, rules.magic_items, channel.mana
        )
    except ValidationError, ConflictError:
        return False
    return reduction > 0 and item_energy_cost(origin.maintenance, reduction, channel.mana) == 0


def _broken_subject(state: PlayState, before: PlayState | None, effect: RuntimeSpellEffect) -> bool:
    """Daze still breaks on injury/resistance when an action also crosses expiry."""
    if before is None or effect.spell_id != "daze" or not effect.execute_effects:
        return False
    previous = {event.id for event in before.resources.events}
    for event in state.resources.events:
        if event.id in previous:
            continue
        if event.id.startswith("injury:") and event.target_id == effect.target_id:
            if InjuryResult.model_validate_json(event.kind).injury > 0:
                return True
        if event.id.startswith(("spell:", "runtime-spell:")):
            record = parse_event(event)
            if record.effect.target_id == effect.target_id and record.result.outcome == "resisted":
                return True
    return False


def checkpoint(
    runtime: RulesContext, state: PlayState, *, before: PlayState | None = None
) -> PlayState:
    """Renew elapsed intervals without rolls, energy, retargeting, or resurrection.

    The previous checkpoint proves eligibility throughout an elapsed clock step.
    Sleep does not erase an already-paid interval, but expiry while unable to
    renew is final even if the wearer later wakes. B482 removal and B480-481
    loss of working item support end the always-on effect immediately.
    """
    resources = state.resources
    effects = latest(resources)
    for origin in origins(resources):
        effect = effects.get(origin.cast_id)
        if effect is None or effect.phase != "active" or effect.expires_at is None:
            continue
        boundary = effect.expires_at
        supported = _supported(runtime, state, origin, effect)
        if resources.game_time < boundary and supported:
            continue
        eligible = (
            supported
            and _awake(state, origin.actor_id)
            and not _broken_subject(state, before, effect)
        )
        if resources.game_time < boundary:
            eligible = False
        elif resources.game_time > boundary:
            eligible = (
                eligible
                and before is not None
                and before.resources.game_time < boundary
                and _supported(runtime, before, origin, effect)
                and _awake(before, origin.actor_id)
            )
        expires = (
            boundary + ((resources.game_time - boundary) // origin.duration + 1) * origin.duration
        )
        effect = effect.model_copy(
            update={"expires_at": expires} if eligible else {"phase": "ended"}
        )
        record = RuntimeSpellEvent(
            effect=effect, result=SpellResult(outcome="active" if eligible else "interrupted")
        )
        identifier = event_id(f"power-maintain:{origin.cast_id}:{boundary}", effect.spell_id)
        if not any(e.id == identifier for e in resources.events):
            resources = resources.model_copy(
                update={
                    "events": resources.events
                    + (
                        ResourceEvent(
                            id=identifier,
                            at=resources.game_time,
                            target_id=origin.actor_id,
                            kind=record.model_dump_json(),
                        ),
                    )
                }
            )
    return (
        state.model_copy(update={"resources": resources}) if resources != state.resources else state
    )
