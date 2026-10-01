"""Current target authority for newly host-bound Cyclic deadlines (B103/B421).

The callback reopens the current approved body against *each* intermediate
resource state. Source dice and penetration are pinned to the valid exposure;
changing or losing the attacker purchase cannot undo an established occurrence.
"""

from wayfarer.engine.character.traits.attack_defense import attack_defense_traits
from wayfarer.engine.character.traits.physical import physical_traits
from wayfarer.engine.rules.checks import Modifier
from wayfarer.engine.rules.types.cyclic import CyclicAttack
from wayfarer.engine.simulation.abilities import damage_resistance
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.cyclic_host_state import CyclicBinding, binding
from wayfarer.engine.simulation.health.cyclic_types import (
    CyclicContextResolver,
    CyclicTargetContext,
)
from wayfarer.engine.simulation.health.hit_locations import armor_resistance, part, require_location
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def target_context(
    runtime: RulesContext,
    state: PlayState,
    attack: CyclicAttack,
    actor_id: str,
    source: CyclicBinding,
) -> CyclicTargetContext:
    if source.source_id != attack.attack_id or source.occurrence_id != attack.id:
        raise ValidationError("Cyclic source binding does not match the occurrence")
    current = build(runtime, state, actor_id, defensive=True)
    assert current.statistics is not None
    traits = attack_defense_traits(current, runtime.reviewer.compiler.definitions)
    hp = next((p for p in state.resources.pools if p.id == "hp:" + actor_id), None)
    if hp is None or hp.injury is None or hp.injury.profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Cyclic target requires canonical Basic Set physiology")
    immune_to_damage = hp.injury.dead or (
        attack.damage_type in {"fat", "tox"} and hp.injury.machine
    )
    location = None if source.disease else source.location
    if not immune_to_damage:
        require_location(hp.injury, location)
    equipment = runtime.rules.combat.gurps_equipment if runtime.rules.combat else None
    entries = {} if equipment is None else {e.definition_id: e for e in equipment.entries}
    held = tuple(i.id for i in state.resources.items if i.owner_id == actor_id and i.ready)
    participants = tuple(
        p
        for encounter in state.encounters
        if encounter.status == "active"
        for p in encounter.participants
        if p.actor_id == actor_id
    )
    if len(participants) > 1:
        raise ValidationError("Cyclic target has ambiguous current hand bindings")
    hands = (
        ()
        if not participants
        else tuple((item, hand) for item, hand in participants[0].hand_bindings if item in held)
    )
    ordinary = build(runtime, state, actor_id)
    assert ordinary.statistics is not None
    dr = 0
    if not source.bypass_dr and not source.disease:
        dr = traits.damage_resistance(eyes=location is not None and part(location) == "eye")
        if equipment is not None:
            armors = tuple(
                entry.armor
                for item in state.resources.items
                if item.owner_id == actor_id
                and item.equipped
                and (item.condition is None or not item.condition.disabled)
                for entry in (entries.get(item.definition_id),)
                if entry is not None and entry.armor is not None
            )
            dr += armor_resistance(armors, location or "torso", damage_type=attack.damage_type)
        dr += damage_resistance(state.resources, actor_id, build_revision=current.revision)
    modifiers = check_modifiers(state.resources, actor_id, "ht", defensive=True)
    fitness = physical_traits(current, runtime.reviewer.compiler.definitions).fitness
    if fitness:
        modifiers += (Modifier(fitness, "Fitness", "B55", "characters-third"),)
    return CyclicTargetContext(
        ht=current.statistics.ht,
        resistance=dr,
        vulnerability_multiplier=1
        if source.disease
        else traits.injury_multiplier("natural-attacks"),
        resistance_modifiers=modifiers,
        held_item_ids=held,
        held_item_locations=hands,
        shield_item_ids=tuple(
            i.id
            for i in state.resources.items
            if i.id in held and i.definition_id in entries and entries[i.definition_id].shield
        ),
        dx=ordinary.statistics.dx,
        immune_to_damage=immune_to_damage,
    )


def resolver(runtime: RulesContext, state: PlayState) -> CyclicContextResolver:
    def resolve(
        resources: ResourceState, attack: CyclicAttack, actor_id: str
    ) -> CyclicTargetContext:
        source = binding(resources, attack.id)
        if source is None:
            raise ValidationError("Current Cyclic context requires its persisted source binding")
        return target_context(
            runtime, state.model_copy(update={"resources": resources}), attack, actor_id, source
        )

    return resolve
