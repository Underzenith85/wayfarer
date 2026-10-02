"""Private Great Haste casting with trusted actor-relative combat execution."""

import math

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import Encounter, basic_distance
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.special_melee import actor_size_modifier
from wayfarer.engine.simulation.combat.visibility import combat_visibility, visible_actors
from wayfarer.engine.simulation.magic.binding_context import SpellEnvironment, approved_context
from wayfarer.engine.simulation.magic.great_haste_casting import enabled as subjective_great_haste
from wayfarer.engine.simulation.magic.great_haste_effects import checkpoint
from wayfarer.engine.simulation.magic.great_haste_state import (
    ACTIVATION,
    CHANNEL,
    RECEIPT,
    CastGreatHaste,
    DeclareGreatHasteChannel,
    GreatHasteActivation,
    GreatHasteChannel,
    GreatHasteCommand,
    GreatHasteReceipt,
    channels,
    save,
)
from wayfarer.engine.simulation.magic.spell_state import RuntimeSpellEffect, SpellResult, latest
from wayfarer.engine.simulation.magic.spells import (
    PROFILE,
    RuntimeSpellCommand,
    SpellContext,
    apply_spell,
)
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.world import EntityKind
from wayfarer.errors import ConflictError, ValidationError


def apply_host(
    runtime: RulesContext,
    state: PlayState,
    command: GreatHasteCommand,
    *,
    combat_encounter: Encounter | None = None,
) -> tuple[PlayState, GreatHasteReceipt]:
    state = checkpoint(runtime, state)
    channel: GreatHasteChannel | None
    spent = 0
    outcome = "declared"
    if isinstance(command, DeclareGreatHasteChannel):
        channel = command.channel
        if any(c.id == channel.id for c in channels(state.resources)):
            raise ConflictError("Great Haste channels cannot be replaced")
        entities = {e.id: e for e in state.world.entities}
        location = entities.get(channel.location_id)
        if location is None or location.kind is not EntityKind.LOCATION:
            raise ValidationError("Great Haste requires a canonical location")
        for actor_id in (channel.actor_id, channel.target_id):
            entity = entities.get(actor_id)
            if entity is None or entity.kind is not EntityKind.ACTOR:
                raise ValidationError("Great Haste subjects are approved actors")
            runtime.approved_build(state, actor_id)
        updated = state.model_copy(
            update={
                "resources": save(state.resources, CHANNEL, command.id, channel.actor_id, channel)
            }
        )
    else:
        channel, spell, bound = bound_cast(runtime, state, command, combat_encounter)
        resources, result = _apply_cast(runtime, state.resources, spell, bound, combat_encounter)
        updated = state.model_copy(update={"resources": resources})
        outcome, spent = result.outcome, result.energy_spent
        if result.outcome == "active" and command.operation != "cancel":
            target = runtime.approved_build(updated, channel.target_id)
            values = {v.target: int(v.value) for v in target.sheet.values}
            active = latest(resources)[command.cast_id]
            assert active.expires_at is not None
            resources = save(
                resources,
                ACTIVATION,
                command.cast_id,
                channel.target_id,
                GreatHasteActivation(
                    cast_id=command.cast_id,
                    actor_id=command.actor_id,
                    target_id=channel.target_id,
                    expires_at=active.expires_at,
                    ht=values["attribute:ht"],
                    will=values["secondary:will"],
                ),
            )
            updated = updated.model_copy(update={"resources": resources})
        updated = checkpoint(runtime, updated)
    receipt = GreatHasteReceipt(
        command_id=command.id,
        outcome=outcome,
        energy_spent=spent,
        game_time=updated.resources.game_time,
    )
    resources = save(updated.resources, RECEIPT, command.id, command.actor_id, receipt).model_copy(
        update={"revision": state.revision + 1}
    )
    return updated.model_copy(
        update={"revision": state.revision + 1, "resources": resources}
    ), receipt


def _combat_distance(
    runtime: RulesContext,
    state: PlayState,
    channel: GreatHasteChannel,
    command: CastGreatHaste,
    encounter: Encounter | None,
    effect: RuntimeSpellEffect | None,
    energy: int,
) -> int:
    if encounter is None:
        return channel.distance_yards
    if channel.target_id == command.actor_id:
        raise ValidationError("Combat self Great Haste requires a mid-turn activation policy")
    if effect is not None and effect.energy != energy:
        raise ConflictError("Great Haste subject size changed during casting")
    if channel.target_id not in encounter.turn_order:
        raise ValidationError("Combat Great Haste requires an encounter subject")
    if channel.target_id not in visible_actors(
        state, encounter, command.actor_id, board=runtime.hex_map(encounter)
    ):
        raise ValidationError("Combat Great Haste requires a currently visible subject")
    visibility = combat_visibility(encounter, command.actor_id, channel.target_id, state=state)
    if visibility.attack_penalty:
        raise ValidationError("Combat Great Haste requires a currently visible subject")
    actor = next(p for p in encounter.participants if p.actor_id == command.actor_id)
    subject = next(p for p in encounter.participants if p.actor_id == channel.target_id)
    return (
        math.ceil(basic_distance(encounter, command.actor_id, channel.target_id))
        if encounter.spatial_kind == "basic"
        else CombatEngine.distance(actor.position, subject.position)
    )


def _apply_cast(
    runtime: RulesContext,
    resources: ResourceState,
    spell: RuntimeSpellCommand,
    bound: SpellContext,
    encounter: Encounter | None,
) -> tuple[ResourceState, SpellResult]:
    revision = resources.revision + 1
    resources, result = apply_spell(resources, spell, bound, system=True, rng=runtime.rng)
    if encounter is not None and subjective_great_haste():
        casting = latest(resources)[spell.cast_id]
        if casting.phase == "casting" and casting.required_turns == casting.concentration_seconds:
            final = spell.model_copy(
                update={
                    "id": spell.id + ":complete",
                    "kind": "complete",
                    "expected_revision": resources.revision,
                }
            )
            resources, result = apply_spell(resources, final, bound, system=True, rng=runtime.rng)
            resources = resources.model_copy(update={"revision": revision})
    return resources, result


def bound_cast(
    runtime: RulesContext,
    state: PlayState,
    command: CastGreatHaste,
    combat_encounter: Encounter | None,
) -> tuple[GreatHasteChannel, RuntimeSpellCommand, SpellContext]:
    channel = next((c for c in channels(state.resources) if c.id == command.channel_id), None)
    if channel is None or channel.actor_id != command.actor_id:
        raise ValidationError("Great Haste requires its current caster channel")
    effect = latest(state.resources).get(command.cast_id)
    if effect is not None and (effect.actor_id, effect.target_id, effect.spell_id) != (
        command.actor_id,
        channel.target_id,
        "great-haste",
    ):
        raise ConflictError("Great Haste cast identity changed")
    cancelling = command.operation == "cancel" and effect is not None
    if not cancelling:
        if any(
            other.cast_id != command.cast_id
            and other.spell_id == "great-haste"
            and other.target_id == channel.target_id
            and other.phase in ("casting", "active")
            for other in latest(state.resources).values()
        ):
            raise ValidationError("Concurrent Great Haste casts on one subject are unsupported")
        if combat_encounter is None and any(
            e.status == "active"
            and any(p.actor_id in (channel.actor_id, channel.target_id) for p in e.participants)
            for e in state.encounters
        ):
            raise ValidationError(
                "Combat Great Haste casting requires the subjective concentration adapter"
            )
        entities = {e.id: e for e in state.world.entities}
        if any(
            entities[a].location_id != channel.location_id
            for a in (channel.actor_id, channel.target_id)
        ):
            raise ConflictError("Great Haste channel location changed")
        runtime.approved_build(state, channel.target_id)
        hp = next(p for p in state.resources.pools if p.id == "hp:" + channel.target_id)
        if hp.injury is None or hp.injury.dead:
            raise ValidationError("Great Haste requires a living subject")
        energy = 5 * (1 + max(0, actor_size_modifier(runtime, state, channel.target_id)))
    else:
        assert effect is not None
        energy = effect.energy
    spell = RuntimeSpellCommand.model_validate(
        dict(
            id=command.id,
            actor_id=command.actor_id,
            expected_revision=state.resources.revision,
            kind=command.operation,
            spell_id="great-haste",
            channel_id=channel.id,
            cast_id=command.cast_id,
            energy=energy,
        )
    )
    if cancelling:
        assert effect is not None
        bound = SpellContext(
            profile_id=PROFILE,
            build_revision=effect.build_revision,
            skill=max(1, effect.skill),
            target_id=effect.target_id,
            mana=channel.mana,
            energy=effect.energy,
            execution_version=2,
            execute_effects=True,
        )
    else:
        distance = _combat_distance(
            runtime, state, channel, command, combat_encounter, effect, energy
        )
        bound = approved_context(
            runtime,
            state,
            spell,
            SpellEnvironment(
                target_id=channel.target_id,
                mana=channel.mana,
                distance=distance,
                unseen=not channel.visible,
                energy=energy,
            ),
        ).model_copy(
            update={
                "execution_version": 2,
                "execute_effects": True,
                "encounter_id": combat_encounter.id if combat_encounter else None,
            }
        )
    return channel, spell, bound
