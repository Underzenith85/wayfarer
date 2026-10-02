"""Private Apportation commands using the ordinary spell and world ledgers."""

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.party import synchronous
from wayfarer.engine.simulation.health.recovery_guard import guard
from wayfarer.engine.simulation.magic.apportation_bindings import (
    context,
    energy_for_weight,
    require_concentration,
    weight,
)
from wayfarer.engine.simulation.magic.apportation_effects import move, resistance
from wayfarer.engine.simulation.magic.apportation_state import (
    CHANNEL,
    RECEIPT,
    ROUTE,
    ApportationChannel,
    ApportationReceipt,
    CastApportation,
    DeclareApportationChannel,
    DeclareApportationRoute,
    MoveApportation,
    channels,
    routes,
    save,
)
from wayfarer.engine.simulation.magic.concentration import require_idle_concentration
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand, apply_spell
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.world import EntityKind
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError

HostCommand = (
    DeclareApportationChannel | DeclareApportationRoute | CastApportation | MoveApportation
)


def _channel(state: PlayState, channel_id: str, actor_id: str) -> ApportationChannel:
    channel = next((c for c in channels(state.resources) if c.id == channel_id), None)
    if channel is None or channel.actor_id != actor_id:
        raise AuthorizationError("Apportation channel does not authorize this caster")
    return channel


def apply_host(
    runtime: RulesContext, state: PlayState, command: HostCommand
) -> tuple[PlayState, ApportationReceipt]:
    updated = state
    outcome, target, spent = "declared", command.actor_id, 0
    if isinstance(command, DeclareApportationChannel):
        channel = command.channel
        if any(c.id == channel.id for c in channels(state.resources)):
            raise ConflictError("Apportation channel cannot be replaced")
        runtime.approved_build(state, channel.actor_id)
        if not any(
            e.id == channel.location_id and e.kind is EntityKind.LOCATION
            for e in state.world.entities
        ):
            raise ValidationError("Apportation channel requires a real location")
        energy_for_weight(weight(runtime, state, channel))
        updated = state.model_copy(
            update={
                "resources": save(state.resources, CHANNEL, command.id, channel.actor_id, channel)
            }
        )
        target = channel.target_id
    elif isinstance(command, DeclareApportationRoute):
        route = command.route
        if any(r.id == route.id for r in routes(state.resources)):
            raise ConflictError("Apportation route cannot be replaced")
        if not any(
            c.source_id == route.source_id and c.destination_id == route.destination_id
            for c in state.world.connections
        ):
            raise ValidationError("Apportation route requires a real directed world connection")
        updated = state.model_copy(
            update={"resources": save(state.resources, ROUTE, command.id, route.source_id, route)}
        )
    else:
        channel = _channel(state, command.channel_id, command.actor_id)
        target = channel.target_id
        synchronous(state, command.actor_id)
        guard(state, command.actor_id, "apportation")
        cancelling = isinstance(command, CastApportation) and command.operation == "cancel"
        if not cancelling and any(
            e.status == "active" and (command.actor_id in e.turn_order or target in e.turn_order)
            for e in state.encounters
        ):
            raise ValidationError(
                "Combat Apportation requires its canonical concentration/movement adapter"
            )
        effect = latest(state.resources).get(command.cast_id)
        tier = (
            effect.energy
            if cancelling and effect is not None
            else energy_for_weight(weight(runtime, state, channel))
        )
        spell = RuntimeSpellCommand.model_validate(
            dict(
                id=command.id,
                actor_id=command.actor_id,
                expected_revision=state.resources.revision,
                kind=command.operation if isinstance(command, CastApportation) else "maintain",
                spell_id="apportation",
                channel_id=channel.id,
                cast_id=command.cast_id,
                energy=tier if effect is None else effect.energy,
            )
        )
        bound = context(runtime, state, channel, spell)
        if isinstance(command, CastApportation):
            resources, result = apply_spell(
                state.resources, spell, bound, system=True, rng=runtime.rng
            )
            updated = state.model_copy(update={"resources": resources})
            updated, result = resistance(runtime, updated, spell, result)
            outcome, spent = result.outcome, result.energy_spent
        else:
            require_idle_concentration(state.resources, command.actor_id)
            require_concentration(state, command.actor_id)
            if bound.unavailable:
                raise ValidationError("Caster cannot concentrate on Apportation movement")
            if effect is None or tier > effect.energy:
                raise ConflictError("Current subject exceeds its accepted weight allowance")
            selected_route = next(
                (r for r in routes(state.resources) if r.id == command.route_id), None
            )
            if selected_route is None:
                raise ValidationError("Unknown Apportation route")
            updated = move(runtime, state, channel, selected_route, command.cast_id, command.id)
            outcome = "moved"
    receipt = ApportationReceipt(
        command_id=command.id,
        outcome=outcome,
        target_id=target,
        energy_spent=spent,
        game_time=updated.resources.game_time,
    )
    resources = save(updated.resources, RECEIPT, command.id, command.actor_id, receipt).model_copy(
        update={"revision": state.revision + 1}
    )
    return updated.model_copy(
        update={"revision": state.revision + 1, "resources": resources}
    ), receipt
