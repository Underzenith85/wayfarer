"""State transitions for campaign-authored B236 backfire consequences."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.checks import draw_dice, draw_index
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.campaign.reinforcements import admit_actor
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.engine.simulation.magic import lock_backfires
from wayfarer.engine.simulation.magic.area_fire import armor
from wayfarer.engine.simulation.magic.backfires import Backfire, apply_backfire, backfires, save
from wayfarer.engine.simulation.magic.bindings import RuntimeBackfireAlternative
from wayfarer.engine.simulation.magic.effects import break_daze
from wayfarer.engine.simulation.magic.lock_state import latest as lock_states
from wayfarer.engine.simulation.magic.lock_state import validate_fixture
from wayfarer.engine.simulation.magic.spell_state import (
    RuntimeSpellEffect as SpellEffect,
)
from wayfarer.engine.simulation.magic.spell_state import (
    RuntimeSpellEvent as SpellEvent,
)
from wayfarer.engine.simulation.magic.spells import (
    PROFILE,
    SPELLS,
    SpellResult,
    active_spells,
    event_id,
    latest,
)
from wayfarer.engine.simulation.magic.summoning import (
    SummonEncounter,
    hostile_allegiance,
    validate_summon,
)
from wayfarer.engine.simulation.resources import Command, ResourceEvent, ResourceState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.world import Fact
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id


class ResolveSpellBackfire(Command):
    backfire_id: Id
    alternative_id: Id
    good_intent: bool = Field(default=False)
    summon_encounter: SummonEncounter | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


def resolve(
    runtime: RulesContext,
    state: PlayState,
    command: ResolveSpellBackfire,
    *,
    start_summon: Callable[[PlayState, ResolveSpellBackfire, BackfireSelection], PlayState]
    | None = None,
) -> tuple[PlayState, Backfire]:
    return reduce_backfire(state, command, BackfireContext(runtime, start_summon))


@dataclass(frozen=True)
class BackfireContext:
    runtime: RulesContext
    start_summon: (
        Callable[[PlayState, ResolveSpellBackfire, BackfireSelection], PlayState] | None
    ) = None


@dataclass(frozen=True)
class BackfireSelection:
    item: Backfire
    choice: RuntimeBackfireAlternative
    effect: SpellEffect
    encounter: Encounter | None
    target_id: str
    compiled: ValidatedBuild | None


def _select_backfire(
    runtime: RulesContext, state: PlayState, command: ResolveSpellBackfire
) -> BackfireSelection:
    item = next((b for b in backfires(state.resources) if b.id == command.backfire_id), None)
    rules = runtime.rules.spells
    alternatives = (
        lock_backfires.choices(state.resources)
        if item and item.spell_id in ("lockmaster", "magelock")
        else rules.backfire_alternatives
        if rules
        else ()
    )
    choice = next((a for a in alternatives if a.id == command.alternative_id), None)
    if item is None or not item.pending:
        raise ConflictError("Backfire is not awaiting an interpretation")
    if (
        choice is None
        or choice.spell_id != item.spell_id
        or item.row not in choice.rows
        or item.severity != choice.severity
    ):
        raise ValidationError("Backfire alternative does not authorize this result")
    effect = latest(state.resources)[item.cast_id]
    if command.summon_encounter is not None and choice.effect != "summon":
        raise ValidationError("Only a summon consequence may create its encounter")
    if choice.effect == "reroll" and choice.target_ids != (item.actor_id,):
        raise ValidationError("Reroll belongs to the original caster")
    if choice.effect == "waive":
        if item.row != 18 or item.severity != "normal" or not command.good_intent:
            raise ValidationError("Only the good-intent exception permits waiving a demon")
    elif choice.effect == "reroll":
        if item.severity != "normal":
            raise ValidationError("A spectacular disaster cannot be replaced by a normal roll")
    else:
        _validate_consequence(item, choice, effect)
    encounter = next((e for e in state.encounters if e.id == effect.encounter_id), None)
    if choice.effect == "summon":
        encounter = next(
            (e for e in state.encounters if e.status == "active" and item.actor_id in e.turn_order),
            None,
        )
    candidates = {
        target_id: _backfire_candidate(
            runtime, state, item, choice, effect, encounter, target_id, command.summon_encounter
        )
        for target_id in choice.target_ids
    }
    target_id = (
        choice.target_ids[draw_index(runtime.rng, len(choice.target_ids))]
        if len(choice.target_ids) > 1
        else choice.target_ids[0]
    )
    selected_build = candidates[target_id]
    return BackfireSelection(item, choice, effect, encounter, target_id, selected_build)


def _backfire_candidate(
    runtime: RulesContext,
    state: PlayState,
    item: Backfire,
    choice: RuntimeBackfireAlternative,
    effect: SpellEffect,
    encounter: Encounter | None,
    target_id: str,
    summon_encounter: SummonEncounter | None,
) -> ValidatedBuild | None:
    if effect.spell_id in ("lockmaster", "magelock") and choice.effect in (
        "retarget",
        "reverse",
    ):
        _validate_lock_backfire_candidate(state, item, choice, effect, target_id)
        return None
    compiled = build(runtime, state, target_id)
    assert compiled.statistics
    entity = next(e for e in state.world.entities if e.id == target_id)
    if entity.location_id != effect.location_id:
        raise ValidationError("Backfire target is not nearby")
    if choice.effect == "summon":
        validate_summon(
            runtime,
            state,
            caster_id=item.actor_id,
            location_id=effect.location_id,
            target_id=target_id,
            position=choice.position,
            encounter=encounter,
            declaration=summon_encounter,
        )
    elif encounter and target_id not in encounter.turn_order:
        raise ValidationError("Backfire target is outside the encounter")
    if choice.effect == "retarget" and target_id == effect.target_id:
        raise ValidationError("An intended table result must be rerolled")
    if choice.relationship in ("foe", "companion") and target_id == item.actor_id:
        raise ValidationError("A companion or foe cannot be the caster")
    return compiled


def _validate_lock_backfire_candidate(
    state: PlayState,
    item: Backfire,
    choice: RuntimeBackfireAlternative,
    effect: SpellEffect,
    target_id: str,
) -> None:
    lock_backfires.validate_target(
        state.resources, effect.spell_id, target_id, reverse=choice.effect == "reverse"
    )
    fixture = lock_states(state.resources)[target_id].fixture
    validate_fixture(state.world, state.resources, fixture)
    if fixture.location_id != effect.location_id:
        raise ValidationError("Backfire object is not nearby")
    if item.severity == "normal" and item.row in (4, 5, 6):
        raise ValidationError("Person-targeted lock backfires are inappropriate; reroll the table")
    if choice.effect == "retarget" or item.row in (15, 16):
        if target_id == effect.target_id:
            raise ValidationError("Wrong-target lock backfire requires another object")
    elif item.row == 13 and target_id != effect.target_id:
        raise ValidationError("Table row 13 reverses the effect on its original object")


def _reroll(
    state: PlayState,
    command: ResolveSpellBackfire,
    context: BackfireContext,
    selection: BackfireSelection,
) -> PlayState:
    runtime = context.runtime
    item = selection.item
    compiled = selection.compiled
    assert compiled is not None and compiled.statistics
    resources = state.resources
    resources = apply_backfire(
        resources,
        command_id=command.id + ":reroll",
        actor_id=item.actor_id,
        cast_id=item.cast_id,
        spell_id=item.spell_id,
        ht=compiled.statistics.ht,
        severity="normal",
        rng=runtime.rng,
    )
    return state.model_copy(update={"resources": resources})


def _summon(
    state: PlayState,
    command: ResolveSpellBackfire,
    context: BackfireContext,
    selection: BackfireSelection,
) -> PlayState:
    if selection.encounter is None:
        if context.start_summon is None:
            raise ValidationError("Noncombat summoning requires its canonical encounter host")
        state = context.start_summon(state, command, selection)
        assert command.summon_encounter is not None
        encounter = next(
            e for e in state.encounters if e.id == command.summon_encounter.encounter_id
        )
        return _summon_appearance(state, command, encounter, selection.target_id)
    choice = selection.choice
    encounter, target_id, compiled = selection.encounter, selection.target_id, selection.compiled
    assert compiled is not None and compiled.statistics
    resources = state.resources
    assert encounter and choice.position
    point = (
        Hex(q=choice.position[0], r=choice.position[1])
        if encounter.spatial_kind == "hex"
        else GridPoint(x=choice.position[0], y=choice.position[1])
    )
    actor = next(a for a in state.actors if a.actor_id == target_id)
    exact_gurps = bool(
        context.runtime.combat is not None
        and context.runtime.combat.rules.gurps_equipment is not None
    )
    participant = Combatant(
        actor_id=target_id,
        initiative=(compiled.statistics.basic_speed if exact_gurps else compiled.statistics.dx),
        initiative_dx=compiled.statistics.dx if exact_gurps else 0,
        facing="north",
        reach=1,
        movement_allowance=compiled.statistics.basic_move,
        position=point,
        hex_facing=0 if encounter.spatial_kind == "hex" else None,
        hand_bindings=tuple(
            (i, h)
            for i, h in actor.held_item_hands
            if any(item.id == i and item.ready and item.equipped for item in resources.items)
        ),
        ready_item_ids=tuple(
            i.id for i in resources.items if i.owner_id == target_id and i.ready and i.equipped
        ),
    )
    if state.party.groups:
        state = admit_actor(state, target_id, selection.item.actor_id)
    current_actor_id = encounter.current_actor_id
    encounter = encounter.add_participant(participant)
    encounter = hostile_allegiance(encounter, selection.item.actor_id, target_id, selection.item.id)
    order = tuple(
        p.actor_id
        for p in sorted(
            encounter.participants,
            key=lambda p: (-p.initiative, -p.initiative_dx, p.actor_id),
        )
    )
    encounter = encounter.model_copy(
        update={"turn_order": order, "turn_index": order.index(current_actor_id)}
    )
    state = state.model_copy(
        update={
            "encounters": tuple(encounter if e.id == encounter.id else e for e in state.encounters)
        }
    )
    return _summon_appearance(state, command, encounter, target_id)


def _summon_appearance(
    state: PlayState, command: ResolveSpellBackfire, encounter: Encounter, target_id: str
) -> PlayState:
    from dataclasses import replace

    fact = Fact("summon:" + command.id, target_id, "visible", "A summoned presence arrives.")
    world = replace(state.world, facts=state.world.facts + (fact,))
    for observer in encounter.participants:
        world = world.learn(observer.actor_id, fact.id)
    return state.model_copy(update={"world": world})


def _effect(
    state: PlayState,
    command: ResolveSpellBackfire,
    context: BackfireContext,
    selection: BackfireSelection,
) -> PlayState:
    runtime = context.runtime
    choice, effect = selection.choice, selection.effect
    encounter, target_id, compiled = selection.encounter, selection.target_id, selection.compiled
    if effect.spell_id in ("lockmaster", "magelock") and choice.effect in ("retarget", "reverse"):
        return state.model_copy(
            update={
                "resources": lock_backfires.apply_effect(
                    state.resources, effect, choice, command.id, target_id
                )
            }
        )
    assert compiled is not None
    assert compiled.statistics
    resources = state.resources
    if choice.effect == "damage" or effect.spell_id == "fireball":
        resources = _damage_consequence(runtime, state, command, selection)
    elif choice.effect == "reverse" and effect.spell_id == "daze":
        resources = break_daze(resources, target_id, command.id)
    elif choice.effect == "reverse" and effect.spell_id == "create-fire":
        for fire in active_spells(resources):
            if fire.spell_id == "create-fire" and fire.target_id == target_id:
                resources = resources.model_copy(
                    update={
                        "events": resources.events
                        + (
                            ResourceEvent(
                                id=event_id(command.id + ":extinguish:" + fire.cast_id),
                                at=resources.game_time,
                                target_id=target_id,
                                kind=SpellEvent(
                                    effect=fire.model_copy(update={"phase": "ended"}),
                                    result=SpellResult(outcome="cancelled"),
                                ).model_dump_json(),
                            ),
                        )
                    }
                )
    else:
        position = effect.position
        if encounter:
            point = next(p.position for p in encounter.participants if p.actor_id == target_id)
            position = (point.q, point.r) if isinstance(point, Hex) else (point.x, point.y)
        duration = SPELLS[effect.spell_id].duration
        replacement = effect.model_copy(
            update={
                "phase": "active",
                "target_id": target_id,
                "position": position,
                "reversed": choice.effect == "reverse",
                "expires_at": resources.game_time + duration if duration else None,
            }
        )
        resources = resources.model_copy(
            update={
                "events": resources.events
                + (
                    ResourceEvent(
                        id=event_id(command.id),
                        at=resources.game_time,
                        target_id=target_id,
                        kind=SpellEvent(
                            effect=replacement, result=SpellResult(outcome="active")
                        ).model_dump_json(),
                    ),
                )
            }
        )
    return state.model_copy(update={"resources": resources})


def _waive(
    state: PlayState,
    command: ResolveSpellBackfire,
    context: BackfireContext,
    selection: BackfireSelection,
) -> PlayState:
    return state


_BACKFIRES: dict[
    str, Callable[[PlayState, ResolveSpellBackfire, BackfireContext, BackfireSelection], PlayState]
] = {
    "reroll": _reroll,
    "summon": _summon,
    "retarget": _effect,
    "reverse": _effect,
    "damage": _effect,
    "waive": _waive,
}


def reduce_backfire(
    state: PlayState, command: ResolveSpellBackfire, context: BackfireContext
) -> tuple[PlayState, Backfire]:
    revision = state.revision + 1
    selection = _select_backfire(context.runtime, state, command)
    state = _BACKFIRES[selection.choice.effect](state, command, context, selection)
    item = selection.item.model_copy(
        update={
            "pending": False,
            "resolution_id": selection.choice.id,
            "target_id": selection.target_id,
        }
    )
    resources = save(state.resources, item, command.id).model_copy(update={"revision": revision})
    return state.model_copy(update={"revision": revision, "resources": resources}), item


def perceive(state: PlayState) -> PlayState:
    """Publish observable appearances without exposing private table outcomes."""
    from dataclasses import replace

    world = state.world
    for item in backfires(state.resources):
        if item.flavor is None:
            continue
        identifier = "backfire-appearance:" + item.id
        if any(f.id == identifier for f in world.facts):
            continue
        appearance = {
            "noise": "A sudden sound and flash accompany the casting.",
            "shadow": "A faint magical shimmer fades.",
            "illusion": "The spell appears to take effect.",
        }[item.flavor]
        fact = Fact(identifier, item.actor_id, "appearance", appearance)
        world = replace(world, facts=world.facts + (fact,))
        for actor in state.actors:
            if actor.actor_id == item.actor_id or item.actor_id in {
                e.id for e in world.perspective(actor.actor_id).entities
            }:
                world = world.learn(actor.actor_id, identifier)
    return state.model_copy(update={"world": world})


def recover_stuns(runtime: RulesContext, state: PlayState) -> PlayState:
    """Noncombat mental stun recovers once per elapsed second using approved IQ."""

    combatants = {a for e in state.encounters if e.status == "active" for a in e.turn_order}
    resources = state.resources
    for item in backfires(resources):
        if (
            not item.stunned
            or item.actor_id in combatants
            or item.stun_due_at is None
            or resources.game_time < item.stun_due_at
        ):
            continue
        hp = next(p for p in resources.pools if p.id == "hp:" + item.actor_id)
        if hp.injury is None or hp.injury.incapacitated:
            continue
        compiled = build(runtime, state, item.actor_id)
        assert compiled.statistics
        check = success_roll(
            PROFILE,
            compiled.statistics.iq + 6 * int(hp.injury.physical_traits.combat_reflexes),
            check_modifiers(resources, item.actor_id, "iq"),
            rng=runtime.rng,
        )
        if check.outcome.succeeded:
            hp = hp.model_copy(update={"injury": hp.injury.model_copy(update={"stunned": False})})
            resources = resources.model_copy(
                update={"pools": tuple(hp if p.id == hp.id else p for p in resources.pools)}
            )
        item = item.model_copy(
            update={
                "stunned": not check.outcome.succeeded,
                "stun_due_at": None if check.outcome.succeeded else resources.game_time + 1,
                "stun_roll": check.dice,
            }
        )
        resources = save(resources, item, f"recover:{item.id}:{resources.game_time}")
    return state.model_copy(update={"resources": resources})


def _validate_consequence(
    item: Backfire, choice: RuntimeBackfireAlternative, effect: SpellEffect
) -> None:
    if item.row == 0 and item.spell_id in (
        "minor-healing",
        "major-healing",
        "great-healing",
        "lend-energy",
        "lend-vitality",
    ):
        if choice.effect != "damage" or choice.target_ids != (item.target_id,):
            raise ValidationError("Healing critical failure requires an authored patient injury")
        if choice.damage_dice + choice.damage_add <= 0:
            raise ValidationError("Healing critical failure must harm the patient")
    elif item.severity == "normal":
        required = (
            "retarget"
            if item.row in (4, 5, 6, 7)
            else "reverse"
            if item.row in (13, 15, 16)
            else "summon"
        )
        if choice.effect != required:
            raise ValidationError("Alternative does not implement the rolled table consequence")
        if item.row in (4, 5, 6):
            role = (
                "foe" if effect.spell_id == "light" else "caster" if item.row == 4 else "companion"
            )
            if choice.relationship != role or (
                role == "caster" and choice.target_ids != (item.actor_id,)
            ):
                raise ValidationError("Backfire target relationship does not match the table")


def _damage_consequence(
    runtime: RulesContext,
    state: PlayState,
    command: ResolveSpellBackfire,
    selection: BackfireSelection,
) -> ResourceState:
    choice, effect = selection.choice, selection.effect
    target_id, compiled = selection.target_id, selection.compiled
    assert compiled is not None and compiled.statistics
    resources = state.resources
    count = effect.energy if choice.effect == "retarget" else choice.damage_dice
    damage = max(
        0,
        sum(draw_dice(runtime.rng, count))
        + (0 if choice.effect == "retarget" else choice.damage_add),
    )
    if choice.effect != "retarget":
        hp = next(p for p in resources.pools if p.id == "hp:" + target_id)
        # B236 limits improvised consequences: never kill outright.
        damage = min(damage, max(0, hp.current + hp.maximum - 1))
    resources, _ = apply_injury(
        resources,
        Wound(
            id=event_id(command.id) + ":impact",
            actor_id=target_id,
            expected_revision=resources.revision,
            basic_damage=damage,
            resistance=0
            if selection.item.row == 0
            and effect.spell_id
            in ("minor-healing", "major-healing", "great-healing", "lend-energy", "lend-vitality")
            else armor(runtime, state, target_id),
            damage_type="burn" if choice.effect == "retarget" else choice.damage_type,
        ),
        ht=compiled.statistics.ht,
        rng=runtime.rng,
        system=True,
    )
    return resources
