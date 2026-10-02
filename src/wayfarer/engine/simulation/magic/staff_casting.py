"""B240 Staff benefits joined to personal, approved Regular spell targeting."""

from math import ceil

from wayfarer.engine.rules.magic.gurps_magic import magery_level
from wayfarer.engine.rules.magic.protocols import AreaSelection, ManaLevel, staff_casting_benefit
from wayfarer.engine.rules.types.location import disabled_locations
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.campaign.party import synchronous
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.objects.locations import item_hands, unavailable_hand
from wayfarer.engine.simulation.health.hit_locations import disabled
from wayfarer.engine.simulation.health.symptom_state import acute_blindness
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.engine.simulation.magic.binding_context import SpellEnvironment
from wayfarer.engine.simulation.magic.binding_context import approved_context as build_context
from wayfarer.engine.simulation.magic.item_state import usable_item_enchantment
from wayfarer.engine.simulation.magic.lock_channel_state import channels as lock_channels
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.engine.simulation.magic.spells import (
    RuntimeSpellCommand,
    SpellContext,
    _executable_spec,
)
from wayfarer.engine.simulation.magic.staff_casting_state import (
    DeclareStaffIntent,
    ObserveStaffTouch,
    StaffIntent,
    StaffTouch,
    intents,
    invalidated,
    observations,
    record,
    scope_digest,
)
from wayfarer.engine.simulation.magic.staff_state import StaffConstruction, constructions
from wayfarer.engine.simulation.resources import is_carried
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.models import Record


class _Channel(Record):
    target_id: str
    location_id: str
    mana: ManaLevel
    distance: int
    encounter_id: str | None = None
    position: tuple[int, int] | None = None
    geometry: str = "square"
    area: AreaSelection | None = None


def _channel(
    runtime: RulesContext, state: PlayState, actor_id: str, spell_id: str, channel_id: str
) -> _Channel:
    if spell_id in ("lockmaster", "magelock"):
        channel = next((c for c in lock_channels(state.resources) if c.id == channel_id), None)
        if channel is None or (channel.actor_id, channel.spell_id) != (actor_id, spell_id):
            raise AuthorizationError("Staff intent requires the caster's approved spell channel")
        return _Channel(
            target_id=channel.target_id,
            location_id=channel.location_id,
            mana=channel.mana,
            distance=channel.distance_yards,
            encounter_id=channel.encounter_id,
            position=channel.position,
            geometry=channel.geometry,
        )
    rules = runtime.rules.spells
    selected = next((c for c in rules.channels if c.id == channel_id), None) if rules else None
    if selected is None or (selected.actor_id, selected.spell_id) != (actor_id, spell_id):
        raise AuthorizationError("Staff intent requires the caster's approved spell channel")
    if selected.magic_item_id is not None:
        raise ValidationError("Staff targeting belongs to personal spellcasting")
    return _Channel(
        target_id=selected.target_id,
        location_id=selected.location_id,
        mana=selected.mana,
        distance=selected.distance_yards,
        area=selected.area,
    )


def bound_intent(state: PlayState, command: RuntimeSpellCommand) -> StaffIntent | None:
    intent = next((i for i in intents(state.resources) if i.cast_id == command.cast_id), None)
    if intent is not None and (intent.actor_id, intent.spell_id, intent.channel_id) != (
        command.actor_id,
        command.spell_id,
        command.channel_id,
    ):
        raise ConflictError("Staff casting intent is bound to another actor, spell or channel")
    return intent


def usable_staff(
    state: PlayState, intent: StaffIntent, mana: ManaLevel, magery: int
) -> StaffConstruction | None:
    item = next((i for i in state.resources.items if i.id == intent.item_id), None)
    construction = next(
        (c for c in constructions(state.resources) if c.item_id == intent.item_id), None
    )
    if (
        magery < 0
        or item is None
        or construction is None
        or item.definition_id != construction.definition_id
        or item.quantity != 1
        or item.owner_id != intent.actor_id
        or not item.ready
        or not item.equipped
        or item.container_id is not None
        or not is_carried(state.resources, item)
    ):
        return None
    hands = item_hands(state, intent.actor_id, intent.item_id)
    unavailable = disabled(state.resources, intent.actor_id)
    if not hands or any(unavailable_hand(unavailable, h) for h in hands):
        return None
    actor = next(a for a in state.actors if a.actor_id == intent.actor_id)
    if any(
        p.definition_id == "trait:disadvantage:no-manipulators"
        for p in actor.proposal.draft.purchases
    ):
        return None
    for encounter in state.encounters:
        if encounter.status != "active":
            continue
        participant = next(
            (p for p in encounter.participants if p.actor_id == intent.actor_id), None
        )
        if participant is None:
            continue
        if intent.item_id not in participant.ready_item_ids or participant.pinned:
            return None
        occupied = {h for g in encounter.grips if g.holder_id == intent.actor_id for h in g.hands}
        occupied.update(
            "left-hand" if g.location == "left-arm" else "right-hand"
            for g in encounter.grips
            if g.target_id == intent.actor_id and g.location in ("left-arm", "right-arm")
        )
        if occupied.intersection(hands):
            return None
    completed = next(
        (
            i
            for i in item.enchantments
            if i.spell_id == "staff"
            and i.runtime_family == "staff"
            and i.activation == "always-on"
            and i.always_on
            and usable_item_enchantment(state.resources, i, mana)
        ),
        None,
    )
    return construction if completed is not None else None


def touching(runtime: RulesContext, state: PlayState, intent: StaffIntent) -> bool:
    observation = next(
        (o for o in reversed(observations(state.resources)) if o.cast_id == intent.cast_id), None
    )
    return bool(
        observation
        and observation.touching
        and observation.command_id not in invalidated(state.resources)
        and observation.observed_by in runtime.reviewer.gm_ids
        and any(m.principal_id == observation.observed_by and m.role == "gm" for m in state.members)
        and observation.declared_revision <= state.revision
        and observation.scope_digest == scope_digest(state, intent)
    )


def staff_touching(
    runtime: RulesContext, state: PlayState, command: RuntimeSpellCommand, context: SpellContext
) -> bool:
    intent = bound_intent(state, command)
    return bool(
        intent
        and intent.target_id == context.target_id
        and usable_staff(state, intent, context.mana, context.magery)
        and touching(runtime, state, intent)
    )


def _blind(state: PlayState, actor_id: str) -> bool:
    hp = next(p for p in state.resources.pools if p.id == "hp:" + actor_id)
    # Use the current injury projection, not eye-free Injury Tolerance anatomy.
    eyes = (
        disabled_locations(
            hp.injury.lasting_injuries,
            now=state.resources.game_time,
            full_hp=hp.current >= hp.maximum,
        )
        if hp.injury is not None
        else frozenset()
    )
    return acute_blindness(state.resources, actor_id) or {"left-eye", "right-eye"} <= eyes


def _current_sight(
    state: PlayState, command: RuntimeSpellCommand, context: SpellContext
) -> SpellContext:
    if (
        context.execution_version != 2
        and not context.item_cast
        or context.target_id == command.actor_id
    ):
        return context
    known = {e.id for e in state.world.perspective(command.actor_id).entities}
    if command.kind == "start" and context.target_id not in known:
        return context  # Sight loss cannot authorize an otherwise unknown target.
    direct_touch = command.spell_id in ("lockmaster", "magelock") and any(
        c.id == command.channel_id and c.touching and context.distance == 0
        for c in lock_channels(state.resources)
    )
    if _blind(state, command.actor_id) and not direct_touch:
        # B239: remembering the subject locates it; that does not provide sight.
        return context.model_copy(update={"unseen": True})
    return context


def apply_targeting(
    runtime: RulesContext,
    state: PlayState,
    command: RuntimeSpellCommand,
    context: SpellContext,
    *,
    item_sight: bool = True,
) -> SpellContext:
    intent = bound_intent(state, command)
    if command.kind not in ("start", "concentrate", "complete"):
        return context
    regular = _executable_spec(command.spell_id).kind in ("regular", "resisted")
    if context.item_cast or not (regular or context.area_targeting):
        if intent is not None:
            raise ValidationError("Staff targeting belongs to personal Regular spells")
        if (
            context.item_cast
            and item_sight
            and _executable_spec(command.spell_id).kind in ("regular", "resisted")
        ):
            # B482 retains the contained spell's targeting rules. Power replaces
            # personal skill, not the B239 penalty for an unseen subject.
            return _current_sight(state, command, context)
        return context
    if regular:
        context = _current_sight(state, command, context)
    if intent is None:
        return context
    if intent.target_id != context.target_id or (
        context.area_targeting and (intent.area != context.area or intent.radius != context.radius)
    ):
        raise ConflictError("Staff casting target changed")
    if (
        regular
        and command.kind != "start"
        and command.cast_id in latest(state.resources)
        and command.spell_id not in ("lockmaster", "magelock")
        and context.target_id
        not in {e.id for e in state.world.perspective(command.actor_id).entities}
    ):
        # A subject initially located by trusted contact remains the committed
        # subject; losing that contact restores B239's unseen penalty.
        context = context.model_copy(update={"unseen": True})
    staff = usable_staff(state, intent, context.mana, context.magery)
    if staff is None:
        return context
    contact = touching(runtime, state, intent)
    benefit = staff_casting_benefit(
        staff,
        context.distance,
        touching_with_staff=contact,
        pointing_declared_at_start=intent.pointing,
    )
    # B9 floors negative modifiers: -(distance - exact length), rounded down.
    return context.model_copy(
        update={
            "distance": ceil(benefit.effective_distance_yards),
            "unseen": False if benefit.touch_without_distance_penalty else context.unseen,
        }
    )


def existing_context(
    runtime: RulesContext, state: PlayState, command: RuntimeSpellCommand
) -> SpellContext | None:
    intent = bound_intent(state, command)
    if intent is None or command.kind not in ("cancel", "maintain", "remember"):
        return None
    channel = _channel(runtime, state, intent.actor_id, intent.spell_id, intent.channel_id)
    effect = latest(state.resources).get(command.cast_id)
    if (
        effect is None
        or (effect.actor_id, effect.spell_id, effect.target_id)
        != (intent.actor_id, intent.spell_id, intent.target_id)
        or channel.target_id != intent.target_id
    ):
        raise ConflictError("Staff lifecycle does not identify the caster's existing effect")
    context = build_context(
        runtime, state, command, SpellEnvironment(target_id=effect.target_id, mana=channel.mana)
    )
    return context.model_copy(
        update={
            "execution_version": effect.execution_version,
            "execute_effects": effect.execute_effects,
            "location_id": effect.location_id,
            "encounter_id": effect.encounter_id,
            "position": effect.position,
            "geometry": effect.geometry,
        }
    )


def declare_intent(
    runtime: RulesContext, state: PlayState, command: DeclareStaffIntent
) -> StaffIntent:
    synchronous(state, command.actor_id)
    if any(i.cast_id == command.cast_id for i in intents(state.resources)):
        raise ConflictError("Staff intent cannot be replaced or switched")
    if command.cast_id in latest(state.resources):
        raise ConflictError("Declare Staff pointing before the accepted casting start")
    kind = _executable_spec(command.spell_id).kind
    if kind not in ("regular", "resisted", "area"):
        raise ValidationError("Staff targeting belongs to personal Regular or Area spells")
    channel = _channel(runtime, state, command.actor_id, command.spell_id, command.channel_id)
    if kind == "area" and (channel.area is None or command.radius is None):
        raise ValidationError(
            "Staff targeting requires a Regular spell or an explicit Area and radius"
        )
    if kind != "area" and command.radius is not None:
        raise ValidationError("A Staff Area radius requires an Area spell")
    build(runtime, state, command.actor_id)
    item = next((i for i in state.resources.items if i.id == command.item_id), None)
    if item is None or item.owner_id != command.actor_id:
        raise ValidationError("Staff intent must name the caster's actual item")
    return StaffIntent(
        command_id=command.id,
        actor_id=command.actor_id,
        cast_id=command.cast_id,
        spell_id=command.spell_id,
        channel_id=command.channel_id,
        target_id=channel.target_id,
        item_id=command.item_id,
        pointing=command.pointing,
        area=channel.area if kind == "area" else None,
        radius=command.radius,
    )


def observe_touch(
    runtime: RulesContext, state: PlayState, command: ObserveStaffTouch, principal_id: str
) -> StaffTouch:
    intent = next((i for i in intents(state.resources) if i.cast_id == command.cast_id), None)
    if intent is None or intent.actor_id != command.actor_id:
        raise ValidationError("Staff touch requires its caster's declared intent")
    effect = latest(state.resources).get(command.cast_id)
    if effect is not None and effect.phase != "casting":
        raise ConflictError("Staff touch observation requires an upcoming or concentrating cast")
    channel = _channel(runtime, state, intent.actor_id, intent.spell_id, intent.channel_id)
    if channel.target_id != intent.target_id or channel.area != intent.area:
        raise ConflictError("Staff casting target changed")
    compiled = build(runtime, state, command.actor_id)
    magery = magery_level({p.definition_id: p.amount for p in compiled.purchases})
    staff = usable_staff(state, intent, channel.mana, magery)
    if command.touching and staff is None:
        raise ValidationError("Staff contact requires a currently wielded usable enchanted Staff")
    entities = {e.id: e for e in state.world.entities}
    if command.touching and any(
        entities[a].location_id != channel.location_id
        for a in (
            (intent.actor_id,) if intent.area is not None else (intent.actor_id, intent.target_id)
        )
        if a in entities
    ):
        raise ValidationError("Staff contact requires the same current location")
    encounter = next(
        (e for e in state.encounters if e.status == "active" and intent.actor_id in e.turn_order),
        None,
    )
    distance = channel.distance
    if encounter is not None:
        participants = {p.actor_id: p for p in encounter.participants}
        if intent.area is not None:
            # deferred: fixed Area geometry shares the canonical casting types.
            from wayfarer.engine.simulation.magic.area_targeting import cells

            assert intent.radius is not None
            points = cells(intent.area, intent.radius, encounter.spatial_kind)
            distance = min(
                CombatEngine.distance(
                    participants[intent.actor_id].position,
                    Hex(q=x, r=y) if encounter.spatial_kind == "hex" else GridPoint(x=x, y=y),
                )
                for x, y in points
            )
        elif intent.target_id in participants:
            distance = CombatEngine.distance(
                participants[intent.actor_id].position, participants[intent.target_id].position
            )
        elif channel.encounter_id == encounter.id and channel.position is not None:
            point = (
                Hex(q=channel.position[0], r=channel.position[1])
                if channel.geometry == "hex"
                else GridPoint(x=channel.position[0], y=channel.position[1])
            )
            distance = CombatEngine.distance(participants[intent.actor_id].position, point)
        else:
            raise ValidationError("Staff contact requires the current encounter target")
    if intent.area is not None and encounter is None:
        raise ValidationError("Staff Area contact requires its current mapped encounter")
    if command.touching and staff is not None and distance > ceil(staff.length_yards):
        raise ValidationError("Observed Staff contact exceeds its physical extent")
    return StaffTouch(
        command_id=command.id,
        cast_id=intent.cast_id,
        actor_id=intent.actor_id,
        target_id=intent.target_id,
        item_id=intent.item_id,
        encounter_id=encounter.id if encounter else None,
        observed_by=principal_id,
        declared_revision=state.revision + 1,
        scope_digest=scope_digest(state, intent),
        touching=command.touching,
    )


def apply_command(
    runtime: RulesContext,
    state: PlayState,
    command: DeclareStaffIntent | ObserveStaffTouch,
    *,
    principal_id: str,
) -> tuple[PlayState, StaffIntent | StaffTouch]:
    if state.revision != command.expected_revision:
        raise ConflictError("Staff casting revision changed")
    result = (
        declare_intent(runtime, state, command)
        if isinstance(command, DeclareStaffIntent)
        else observe_touch(runtime, state, command, principal_id)
    )
    resources = record(state.resources, result).model_copy(update={"revision": state.revision + 1})
    return state.model_copy(update={"revision": state.revision + 1, "resources": resources}), result
