"""State transitions for campaign-authored B236 backfire consequences."""

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import Field

from wayfarer.character.compiler import ValidatedBuild
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id
from wayfarer.rules.checks import draw_dice, draw_index
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.combat import Battlefield, Combatant, Encounter, GridPoint
from wayfarer.simulation.condition_checks import check_modifiers
from wayfarer.simulation.hex_geometry import Hex
from wayfarer.simulation.injury import Wound, apply_injury
from wayfarer.simulation.mechanics.gurps_melee import build
from wayfarer.simulation.mechanics.spell_effects import armor
from wayfarer.simulation.resources import Command, ResourceEvent
from wayfarer.simulation.rules_context import RulesContext
from wayfarer.simulation.spell_backfires import Backfire, apply_backfire, backfires, save
from wayfarer.simulation.spell_bindings import BackfireAlternative
from wayfarer.simulation.spell_effects import break_daze
from wayfarer.simulation.spells import (
    SPELLS,
    SpellEffect,
    SpellEvent,
    SpellResult,
    active_spells,
    event_id,
    latest,
)


class ResolveSpellBackfire(Command):
    backfire_id: Id
    alternative_id: Id
    good_intent: bool = Field(default=False)


def resolve(
    runtime: RulesContext, state: PlayState, command: ResolveSpellBackfire
) -> tuple[PlayState, Backfire]:
    return reduce_backfire(state, command, BackfireContext(runtime))


@dataclass(frozen=True)
class BackfireContext:
    runtime: RulesContext


@dataclass(frozen=True)
class BackfireSelection:
    item: Backfire
    choice: BackfireAlternative
    effect: SpellEffect
    encounter: Encounter | None
    target_id: str
    compiled: ValidatedBuild


def _select_backfire(
    runtime: RulesContext, state: PlayState, command: ResolveSpellBackfire
) -> BackfireSelection:
    item = next((b for b in backfires(state.resources) if b.id == command.backfire_id), None)
    rules = runtime.rules.spells
    choice = (
        next((a for a in rules.backfire_alternatives if a.id == command.alternative_id), None)
        if rules
        else None
    )
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
    if choice.effect == "reroll" and choice.target_ids != (item.actor_id,):
        raise ValidationError("Reroll belongs to the original caster")
    if choice.effect == "waive":
        if item.row != 18 or item.severity != "normal" or not command.good_intent:
            raise ValidationError("Only the good-intent exception permits waiving a demon")
    elif choice.effect == "reroll":
        if item.severity != "normal":
            raise ValidationError("A spectacular disaster cannot be replaced by a normal roll")
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
    encounter = next((e for e in state.encounters if e.id == effect.encounter_id), None)
    candidates = {}
    for target_id in choice.target_ids:
        compiled = build(runtime, state, target_id)
        assert compiled.statistics
        entity = next(e for e in state.world.entities if e.id == target_id)
        if entity.location_id != effect.location_id:
            raise ValidationError("Backfire target is not nearby")
        if choice.effect == "summon":
            if encounter is None or target_id in encounter.turn_order or choice.position is None:
                raise ValidationError(
                    "Summoning requires an approved reserve combatant and placement"
                )
            point = (
                Hex(q=choice.position[0], r=choice.position[1])
                if encounter.spatial_kind == "hex"
                else GridPoint(x=choice.position[0], y=choice.position[1])
            )
            if any(p.position == point for p in encounter.participants):
                raise ValidationError("Summoned combatant placement is occupied")
            if encounter.spatial_kind == "hex":
                if runtime.require_hex(encounter).cell(point).blocked:  # type: ignore[arg-type]
                    raise ValidationError("Summoned combatant placement is blocked")
            else:
                board = (
                    next(
                        b
                        for b in runtime.rules.combat.battlefields
                        if b.id == encounter.battlefield_id
                    )
                    if runtime.rules.combat
                    else None
                )
                assert isinstance(point, GridPoint)
                if (
                    not isinstance(board, Battlefield)
                    or point.x >= board.width
                    or point.y >= board.height
                    or point in board.blocked
                ):
                    raise ValidationError("Summoned combatant placement is outside the battlefield")
        elif encounter and target_id not in encounter.turn_order:
            raise ValidationError("Backfire target is outside the encounter")
        if choice.effect == "retarget" and target_id == effect.target_id:
            raise ValidationError("An intended table result must be rerolled")
        if choice.relationship in ("foe", "companion") and target_id == item.actor_id:
            raise ValidationError("A companion or foe cannot be the caster")
        candidates[target_id] = compiled
    target_id = (
        choice.target_ids[draw_index(runtime.rng, len(choice.target_ids))]
        if len(choice.target_ids) > 1
        else choice.target_ids[0]
    )
    compiled = candidates[target_id]
    assert compiled.statistics
    return BackfireSelection(item, choice, effect, encounter, target_id, compiled)


def _reroll(
    state: PlayState,
    command: ResolveSpellBackfire,
    context: BackfireContext,
    selection: BackfireSelection,
) -> PlayState:
    runtime = context.runtime
    item = selection.item
    compiled = selection.compiled
    assert compiled.statistics
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
    choice = selection.choice
    encounter, target_id, compiled = selection.encounter, selection.target_id, selection.compiled
    assert compiled.statistics
    resources = state.resources
    assert encounter and choice.position
    point = (
        Hex(q=choice.position[0], r=choice.position[1])
        if encounter.spatial_kind == "hex"
        else GridPoint(x=choice.position[0], y=choice.position[1])
    )
    actor = next(a for a in state.actors if a.actor_id == target_id)
    participant = Combatant(
        actor_id=target_id,
        initiative=compiled.statistics.dx,
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
    encounter = encounter.model_copy(
        update={
            "participants": encounter.participants + (participant,),
            "turn_order": encounter.turn_order + (target_id,),
        }
    )
    state = state.model_copy(
        update={
            "encounters": tuple(encounter if e.id == encounter.id else e for e in state.encounters)
        }
    )
    from dataclasses import replace

    from wayfarer.world import Fact

    fact = Fact("summon:" + command.id, target_id, "visible", "A summoned presence arrives.")
    world = replace(state.world, facts=state.world.facts + (fact,))
    for observer in encounter.participants:
        world = world.learn(observer.actor_id, fact.id)
    state = state.model_copy(update={"world": world})
    return state.model_copy(update={"resources": resources})


def _effect(
    state: PlayState,
    command: ResolveSpellBackfire,
    context: BackfireContext,
    selection: BackfireSelection,
) -> PlayState:
    runtime = context.runtime
    choice, effect = selection.choice, selection.effect
    encounter, target_id, compiled = selection.encounter, selection.target_id, selection.compiled
    assert compiled.statistics
    resources = state.resources
    if choice.effect == "damage" or effect.spell_id == "fireball":
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
                resistance=armor(runtime, state, target_id),
                damage_type="burn" if choice.effect == "retarget" else choice.damage_type,
            ),
            ht=compiled.statistics.ht,
            rng=runtime.rng,
            system=True,
        )
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
    selection = _select_backfire(context.runtime, state, command)
    state = _BACKFIRES[selection.choice.effect](state, command, context, selection)
    item = selection.item.model_copy(
        update={
            "pending": False,
            "resolution_id": selection.choice.id,
            "target_id": selection.target_id,
        }
    )
    resources = save(state.resources, item, command.id).model_copy(
        update={"revision": state.revision + 1}
    )
    return state.model_copy(update={"revision": state.revision + 1, "resources": resources}), item


def perceive(state: PlayState) -> PlayState:
    """Publish observable appearances without exposing private table outcomes."""
    from dataclasses import replace

    from wayfarer.world import Fact

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
    from wayfarer.rules.gurps_checks import success_roll
    from wayfarer.simulation.spells import PROFILE

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
