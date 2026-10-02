"""Apportation eligibility from canonical items and current approved subjects."""

from fractions import Fraction

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.transformations import current_body_id
from wayfarer.engine.simulation.health.drug_state import drug_unconscious
from wayfarer.engine.simulation.health.sleep_state import asleep
from wayfarer.engine.simulation.magic.apportation_state import ApportationChannel
from wayfarer.engine.simulation.magic.binding_context import SpellEnvironment
from wayfarer.engine.simulation.magic.binding_context import approved_context as build_context
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand, SpellContext
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.traits.size_forms import size_delta
from wayfarer.engine.world import EntityKind
from wayfarer.errors import ConflictError, ValidationError


def energy_for_weight(weight: int | Fraction) -> int:
    """B251: exact millipounds, including the next begun 100-pound increment."""
    if weight <= 0:
        raise ValidationError("Apportation requires positive physical weight")
    for maximum, energy in ((1000, 1), (10000, 2), (50000, 3), (200000, 4)):
        if weight <= maximum:
            return energy
    excess = Fraction(weight - 200000, 100000)
    return 4 + 4 * (-(-excess.numerator // excess.denominator))


def weight(runtime: RulesContext, state: PlayState, channel: ApportationChannel) -> int | Fraction:
    entity = next((e for e in state.world.entities if e.id == channel.target_id), None)
    if entity is None:
        raise ValidationError("Apportation subject no longer exists")
    if entity.kind is EntityKind.ACTOR:
        runtime.approved_build(state, entity.id)
        hp = next(p for p in state.resources.pools if p.id == "hp:" + entity.id)
        if hp.injury is None or hp.injury.dead:
            raise ValidationError("Dead subjects require their physical object adapter")
        if channel.body_weight_millipounds is None:
            raise ValidationError("Living Apportation requires trusted body mass evidence")
        # Shape transformations change mass independently of the approved native body.
        if (
            size_delta(state.resources, entity.id)
            or current_body_id(state.transformations, entity.id) != entity.id
        ):
            raise ValidationError("Transformed living mass requires a separate current observation")
        return channel.body_weight_millipounds + runtime.resources.carried_weight(
            state.resources, entity.id
        )
    if entity.kind is not EntityKind.OBJECT or channel.body_weight_millipounds is not None:
        raise ValidationError("Apportation object weight comes from its canonical equipment")
    item = next((i for i in state.resources.items if i.id == entity.id), None)
    if (
        item is None
        or item.container_id is not None
        or item.ground is not None
        or item.world_ground_location_id != entity.location_id
        or item.equipped
        or item.ready
        or item.stuck_target_id is not None
        or any(i.container_id == item.id for i in state.resources.items)
        or item.condition is not None
        and item.condition.destroyed
    ):
        raise ValidationError("Apportation requires a loose, whole world-ground object")
    spec = runtime.resources.specs.get(item.definition_id)
    if spec is None:
        raise ValidationError("Apportation requires canonical weight")
    return spec.unit_weight * item.quantity


def context(
    runtime: RulesContext,
    state: PlayState,
    channel: ApportationChannel,
    command: RuntimeSpellCommand,
) -> SpellContext:
    effect = latest(state.resources).get(command.cast_id)
    if command.kind == "cancel" and effect is not None:
        if (effect.actor_id, effect.target_id, effect.spell_id) != (
            command.actor_id,
            channel.target_id,
            "apportation",
        ):
            raise ConflictError("Apportation cancellation changed cast identity")
        return SpellContext(
            profile_id="gurps-basic-set-4e-2004",
            build_revision=effect.build_revision,
            skill=max(1, effect.skill),
            target_id=effect.target_id,
            mana=channel.mana,
            energy=effect.energy,
            execution_version=2,
            execute_effects=True,
        )
    actual_energy = energy_for_weight(weight(runtime, state, channel))
    lifecycle = command.kind in ("maintain", "cancel")
    if effect is not None and (
        effect.actor_id != command.actor_id
        or effect.target_id != channel.target_id
        or effect.spell_id != "apportation"
    ):
        raise ConflictError("Apportation cast identity changed")
    if command.kind != "start" and effect is None:
        raise ConflictError("Apportation requires its accepted cast")
    if effect is not None and actual_energy > effect.energy and not lifecycle:
        raise ConflictError("Apportation subject exceeds its accepted weight allowance")
    entities = {e.id: e for e in state.world.entities}
    if not lifecycle and (
        entities[command.actor_id].location_id != channel.location_id
        or entities[channel.target_id].location_id != channel.location_id
    ):
        raise ConflictError("Apportation subject or caster location changed")
    energy = (
        actual_energy if command.kind == "start" else effect.energy if effect else actual_energy
    )
    bound = build_context(
        runtime,
        state,
        command,
        SpellEnvironment(
            target_id=channel.target_id,
            mana=channel.mana,
            distance=channel.distance_yards if not lifecycle else 0,
            unseen=not channel.visible,
            energy=energy,
        ),
    )
    return bound.model_copy(
        update={"execution_version": 2, "execute_effects": True, "location_id": channel.location_id}
    )


def require_concentration(state: PlayState, actor_id: str) -> None:
    actor = next(a for a in state.actors if a.actor_id == actor_id)
    hp = next(p for p in state.resources.pools if p.id == "hp:" + actor_id)
    fp = next(p for p in state.resources.pools if p.id == "fp:" + actor_id)
    if (
        actor.conditions
        or actor.available_at > state.resources.game_time
        or hp.injury is None
        or hp.injury.incapacitated
        or hp.injury.stunned
        or fp.fatigue is None
        or fp.fatigue.collapsed
        or fp.fatigue.unconscious
        or asleep(state.resources, actor_id)
        or drug_unconscious(state.resources, actor_id)
    ):
        raise ValidationError("Caster cannot concentrate on Apportation movement")
