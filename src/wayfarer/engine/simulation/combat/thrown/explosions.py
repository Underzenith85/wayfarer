"""B407 malfunction payloads and B414-415 GM-declared blast resolution."""

import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from wayfarer.engine.rules.checks import CheckTrace, draw_dice
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.tables.ranged import range_penalty
from wayfarer.engine.rules.types.explosion import BlastResponse, ExplosionSpec
from wayfarer.engine.rules.types.firearm import FirearmFailure
from wayfarer.engine.rules.types.object import GroundPosition
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog, movement
from wayfarer.engine.simulation.combat.attack_roll import AttackRollSpec
from wayfarer.engine.simulation.combat.attack_visibility import (
    SecretAttackSource,
    conceal_attack_result,
)
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.blast_phases import (
    BlastProgress,
    ExposedBlastObject,
    FragmentContinuation,
    FragmentTarget,
    PreparedFragmentAttack,
)
from wayfarer.engine.simulation.combat.commands import ResolveWeaponExplosion
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.equipment_effects import synchronize
from wayfarer.engine.simulation.combat.explosions import BlastRecord, blasts, save
from wayfarer.engine.simulation.combat.firearms import spend_rounds
from wayfarer.engine.simulation.combat.fragment_state import (
    active_fragment,
    fragment_attack_id,
    save_fragment,
)
from wayfarer.engine.simulation.combat.generations import (
    preserve_grenade_fuse,
    secondary_object_blasts_enabled,
)
from wayfarer.engine.simulation.combat.melee.defense import defense_value
from wayfarer.engine.simulation.combat.thrown.flight import position
from wayfarer.engine.simulation.combat.thrown.ground_dive import validate_ground_step
from wayfarer.engine.simulation.combat.thrown.interposition import contact_space, intercept
from wayfarer.engine.simulation.combat.thrown.live_grenades import armed_cause
from wayfarer.engine.simulation.combat.thrown.secondary_explosions import (
    schedule_secondary_object_blast,
)
from wayfarer.engine.simulation.combat.unarmed.injury import armor_dr, hurt
from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.engine.simulation.equipment.objects import DamageObject, apply_object
from wayfarer.engine.simulation.health.hit_locations import select_location
from wayfarer.engine.simulation.hex_geometry import DIRECTIONS as HEX_DIRECTIONS
from wayfarer.engine.simulation.hex_geometry import Hex, distance
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ConflictError, ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.rules_context import RulesContext


def schedule_payload(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    weapon: RangedMode,
    *,
    original_resources: ResourceState,
    failure: FirearmFailure | None,
    hits: int,
    shots_fired: int,
    critical: int,
    attack: CheckTrace,
) -> tuple[PlayState, Encounter, bool]:

    pending = encounter.pending_defense
    assert pending is not None
    entries = {e.definition_id: e for e in catalog(runtime).entries}
    source = next(i for i in original_resources.items if i.id == pending.weapon_id)
    ammo = entries.get(weapon.ammunition_id or "")
    payload = (
        entries[source.definition_id].warhead if weapon.thrown else ammo.warhead if ammo else None
    )
    spec = weapon.firearm
    grenade = spec is not None and spec.action == "grenade"
    explodes = failure is not None and failure.kind == "explosion"
    delayed = failure is not None and failure.kind == "delayed"
    if explodes:
        payload = payload or ExplosionSpec(dice=1, adds=2, fragmentation_dice=2)
    if payload is None or failure is not None and not (explodes or delayed) and shots_fired == 0:
        return state, encounter, False
    if not grenade and not explodes and shots_fired == 0:
        return state, encounter, False
    attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    target = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    resources = state.resources
    if explodes and not grenade:
        resources = spend_rounds(resources, source.id, 1)
        resources = resources.model_copy(
            update={
                "items": tuple(
                    i.model_copy(
                        update={
                            "equipped": False,
                            "ready": False,
                            "ground": position(encounter, attacker),
                            "container_id": None,
                        }
                    )
                    if i.id == source.id
                    else i
                    for i in resources.items
                )
            }
        )
    armed = (
        armed_cause(original_resources, source.id, encounter.id)
        if grenade and preserve_grenade_fuse()
        else None
    )
    count = 1 if grenade or explodes else shots_fired
    for index in range(count):
        fuse = (draw_dice(runtime.rng, 1)[0],) if delayed and armed is None else ()
        direct = pending.attacker_id if explodes else pending.defender_id if index < hits else None
        aim_point = pending.area_aim_point
        attack_range = separation(position(encounter, attacker), aim_point) if aim_point else None
        scatter_direction: int | None = None
        scatter_distance = 0
        center = (
            position(encounter, attacker)
            if explodes
            else position(encounter, target)
            if direct
            else None
        )
        if aim_point is not None:
            center = aim_point
            direct = None
            if not attack.outcome.succeeded:
                scatter_direction = draw_dice(runtime.rng, 1)[0]
                margin = max(1, attack.total - attack.effective_target)
                scatter_distance = margin * margin if pending.scatter_squared else margin
                assert attack_range is not None
                scatter_distance = min(scatter_distance, (attack_range + 1) // 2)
                dq, dr = HEX_DIRECTIONS[scatter_direction - 1]
                if aim_point.geometry == "grid":
                    # The six B414 facings map clockwise onto the square adapter.
                    dq, dr = ((0, -1), (1, -1), (1, 0), (0, 1), (-1, 1), (-1, 0))[
                        scatter_direction - 1
                    ]
                center = aim_point.model_copy(
                    update={
                        "x": aim_point.x + dq * scatter_distance,
                        "y": aim_point.y + dr * scatter_distance,
                    }
                )
            resources = resources.model_copy(
                update={
                    "expended_items": tuple(
                        item.model_copy(update={"ground": center}) if item.id == source.id else item
                        for item in resources.expended_items
                    )
                }
            )
        if armed is not None:
            relocated = armed.model_copy(
                update={
                    "center": center,
                    "direct_actor_id": direct,
                    "critical": critical if index == 0 and not failure else 0,
                    "aim_point": aim_point,
                    "attack_range": attack_range,
                    "attack_dice": attack.dice if aim_point is not None else (),
                    "scatter_direction": scatter_direction,
                    "scatter_distance": scatter_distance,
                }
            )
            resources = save(resources, relocated, f"{pending.id}:grenade-rethrow")
            continue
        resources = save(
            resources,
            BlastRecord(
                id=f"{pending.id}:blast:{index}",
                encounter_id=encounter.id,
                source_item_id=source.id,
                payload=payload,
                due=resources.game_time
                + (
                    (spec.fuse_seconds or 0) + sum(fuse) if grenade and not explodes and spec else 0
                ),
                center=center,
                direct_actor_id=direct,
                critical=critical if index == 0 and not failure else 0,
                follow_item=grenade and not explodes,
                destroy_source=grenade and not explodes,
                fuse_dice=fuse,
                aim_point=aim_point,
                attack_range=attack_range,
                attack_dice=attack.dice if aim_point is not None else (),
                scatter_direction=scatter_direction,
                scatter_distance=scatter_distance,
            ),
            f"{pending.id}:blast:{index}:schedule",
        )
    return state.model_copy(update={"resources": resources}), encounter, True


def separation(a: GroundPosition, b: GroundPosition) -> int:
    if a.geometry != b.geometry or a.encounter_id != b.encounter_id:
        raise ValidationError("Blast positions must share one battlefield")
    return (
        distance(Hex(q=a.x, r=a.y), Hex(q=b.x, r=b.y))
        if a.geometry == "hex"
        else max(abs(a.x - b.x), abs(a.y - b.y))
    )


def validate_position(runtime: RulesContext, encounter: Encounter, point: GroundPosition) -> None:
    if point.encounter_id != encounter.id:
        raise ValidationError("Blast position belongs to another encounter")
    if encounter.spatial_kind == "hex":
        if point.geometry != "hex" or not any(
            c.position == Hex(q=point.x, r=point.y) and not c.blocked
            for c in runtime.require_hex(encounter).cells
        ):
            raise ValidationError("Blast position must be a traversable battlefield hex")
    else:
        rules = runtime.rules.combat
        assert rules is not None
        field = next(f for f in rules.battlefields if f.id == encounter.battlefield_id)
        if not isinstance(field, Battlefield):
            raise ValidationError("Square encounter requires a square template")
        if point.geometry != "grid" or not (
            0 <= point.x < field.width and 0 <= point.y < field.height
        ):
            raise ValidationError("Blast position is outside the battlefield")


def _resolved_center(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    blast: BlastRecord,
    supplied: GroundPosition | None,
) -> GroundPosition:
    resolved = blast.center
    if blast.follow_item:
        item = next(
            (
                i
                for i in (*state.resources.items, *state.resources.expended_items)
                if i.id == blast.source_item_id
            ),
            None,
        )
        if item is not None and item.ground:
            resolved = item.ground
        elif item is not None and item in state.resources.items:
            owner = next((p for p in encounter.participants if p.actor_id == item.owner_id), None)
            if owner is None:
                raise ValidationError("Carried grenade holder needs an explicit encounter position")
            resolved = position(encounter, owner)
    if resolved is None:
        if supplied is None:
            raise ValidationError(
                "Unresolved projectile landing requires an explicit GM blast center"
            )
        resolved = supplied
    elif supplied is not None and supplied != resolved:
        raise ValidationError("A recorded blast center cannot be replaced")
    if blast.scatter_direction is None:
        validate_position(runtime, encounter, resolved)
    elif resolved.encounter_id != encounter.id or resolved.geometry != (
        "hex" if encounter.spatial_kind == "hex" else "grid"
    ):
        raise ValidationError("Recorded scatter does not share the encounter geometry")
    return resolved


def _validate_special_explosion(
    encounter: Encounter,
    center: GroundPosition,
    contact_actor_id: str | None,
    internal_actor_id: str | None,
) -> None:
    if contact_actor_id is not None and internal_actor_id is not None:
        raise ValidationError("An explosion cannot be both contact and internal")
    actor_id = contact_actor_id or internal_actor_id
    if actor_id is None:
        return
    actor = next((p for p in encounter.participants if p.actor_id == actor_id), None)
    if actor is None or separation(position(encounter, actor), center):
        raise ValidationError("Contact/internal explosion actor must occupy the blast center")


def _special_blast_adjustment(
    runtime: RulesContext,
    state: PlayState,
    *,
    actor_id: str,
    amount: int,
    contact_actor_id: str | None,
    internal_actor_id: str | None,
) -> tuple[int, int, bool, bool]:
    contact = contact_actor_id == actor_id
    internal = internal_actor_id == actor_id
    contact_cover = 0
    if contact_actor_id is not None and not contact:
        compiled = build(runtime, state, contact_actor_id)
        assert compiled.statistics is not None
        contact_cover = compiled.statistics.hp + armor_dr(runtime, state, contact_actor_id, "torso")
    return amount * (3 if internal else 1), contact_cover, contact, internal


def _validate_actor_response(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    response: BlastResponse,
    environment: str,
) -> None:
    actor = next(p for p in encounter.participants if p.actor_id == response.actor_id)
    if (
        response.cover_dr
        and not response.covered_locations
        or response.dive_cover_dr
        and not response.dive_covered_locations
    ):
        raise ValidationError("Cover requires explicitly protected hit locations")
    if response.dive_to:
        # deferred: private rooting joins the actual displacement admission.
        from wayfarer.engine.simulation.magic.rooted_feet_state import require_locomotion

        require_locomotion(state.resources, actor.actor_id)
        validate_position(runtime, encounter, response.dive_to)
        if separation(position(encounter, actor), response.dive_to) not in range(
            1, max(1, (movement(runtime, state, actor.actor_id) + 9) // 10) + 1
        ):
            raise ValidationError("Diving for cover is limited to one step")
        if actor.posture in ("kneeling", "sitting") or actor.grappled or actor.pinned:
            raise ValidationError("Actor cannot take the declared diving step")
        validate_ground_step(runtime, state, encounter, response, environment)
        defense_value(runtime, state, actor, "dodge")
    elif response.dive_cover_dr:
        raise ValidationError("Destination cover requires a declared dive")


def _prepare_progress(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    *,
    blast_id: str,
    command_id: str,
    responses: tuple[BlastResponse, ...],
    object_cover: dict[str, int],
    object_sizes: dict[str, int],
    center: GroundPosition | None,
    environment: str,
    contact_actor_id: str | None,
    internal_actor_id: str | None,
    stop_for: tuple[str, ...] = (),
    incendiary_objects: bool = False,
) -> BlastProgress:

    if catalog(runtime).profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Explosions require the exact Basic Set profile")
    blast = next((b for b in blasts(state.resources) if b.id == blast_id), None)
    if (
        blast is None
        or blast.resolved
        or blast.encounter_id != encounter.id
        or blast.due > state.resources.game_time
    ):
        raise ValidationError("No due unresolved blast is available")
    resolved_center = _resolved_center(runtime, state, encounter, blast, center)
    _validate_special_explosion(encounter, resolved_center, contact_actor_id, internal_actor_id)
    payload = blast.payload
    radius = max(2 * payload.dice * payload.multiplier, 5 * payload.fragmentation_dice)
    participants = tuple(
        p
        for p in encounter.participants
        if separation(position(encounter, p), resolved_center) <= radius
        and not any(
            pool.id == f"hp:{p.actor_id}" and pool.injury and pool.injury.dead
            for pool in state.resources.pools
        )
    )
    if len(set(stop_for)) != len(stop_for) or set(stop_for) - {p.actor_id for p in participants}:
        raise ValidationError("Fragment Luck owners must be distinct actors exposed to this blast")
    if stop_for and not payload.fragmentation_dice:
        raise ValidationError("This blast has no fragment attack rolls")
    by_actor = {r.actor_id: r for r in responses}
    if len(by_actor) != len(responses) or set(by_actor) != {p.actor_id for p in participants}:
        raise ValidationError(
            "Declare cover, size and chosen defenses for every actor in blast range"
        )
    # Validate all declarations before the first roll, then refresh each still-unrolled actor.
    for response in responses:
        _validate_actor_response(runtime, state, encounter, response, environment)
    entries = {e.definition_id: e for e in catalog(runtime).entries}
    objects = []
    for item in (*state.resources.items, *state.resources.expended_items):
        entry = entries[item.definition_id]
        if entry.durability is None or item.condition is None or item.condition.destroyed:
            continue
        owner = next((p for p in encounter.participants if p.actor_id == item.owner_id), None)
        point = item.ground or (
            position(encounter, owner) if owner and item in state.resources.items else None
        )
        if point is not None and separation(point, resolved_center) <= radius:
            objects.append((item, point))
    if set(object_cover) != {item.id for item, _ in objects} or any(
        v < 0 for v in object_cover.values()
    ):
        raise ValidationError("Declare cover DR for every durable object in blast range")
    if payload.fragmentation_dice and set(object_sizes) != {item.id for item, _ in objects}:
        raise ValidationError(
            "Fragmentation requires explicit size modifiers for every exposed object"
        )
    contact_actor_id, interception = intercept(
        runtime, state, encounter, responses, resolved_center, contact_actor_id, internal_actor_id
    )
    return BlastProgress(
        blast=blast,
        command_id=command_id,
        center=resolved_center,
        responses=responses,
        actor_ids=tuple(actor.actor_id for actor in participants),
        objects=tuple(ExposedBlastObject(item_id=item.id, point=point) for item, point in objects),
        object_cover=tuple(
            sorted(object_cover.items()) if incendiary_objects else object_cover.items()
        ),
        object_sizes=tuple(
            sorted(object_sizes.items()) if incendiary_objects else object_sizes.items()
        ),
        environment=environment,
        contact_actor_id=contact_actor_id,
        internal_actor_id=internal_actor_id,
        interception=tuple(interception.items()),
        stop_for=stop_for,
        incendiary_objects=incendiary_objects,
    )


def _blast_damage(
    runtime: RulesContext, progress: BlastProgress, distance_: int, direct: bool, critical: int = 0
) -> tuple[int, tuple[int, ...]]:
    payload = progress.blast.payload
    dice = () if critical == 6 else draw_dice(runtime.rng, payload.dice)
    amount = (
        max(0, (6 * payload.dice if critical == 6 else sum(dice)) + payload.adds)
        * payload.multiplier
    )
    amount *= 3 if critical in (3, 18) else 2 if critical in (5, 16) else 1
    divisor = (
        1
        if direct or distance_ == 0
        else {"air": 3, "water": 1, "vacuum": 10}[progress.environment] * distance_
    )
    return amount // divisor, dice


def _evidence(progress: BlastProgress, rows: list[dict[str, object]]) -> BlastProgress:
    return progress.model_copy(
        update={
            "evidence": progress.evidence
            + tuple(json.dumps(row, sort_keys=True, default=str) for row in rows)
        }
    )


def _actor_blast(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    progress: BlastProgress,
    actor_id: str,
) -> tuple[PlayState, Encounter, BlastProgress, FragmentTarget]:
    blast, payload = progress.blast, progress.blast.payload
    by_actor = {response.actor_id: response for response in progress.responses}
    interception = dict(progress.interception)
    resolved_center = progress.center
    contact_actor_id, internal_actor_id = progress.contact_actor_id, progress.internal_actor_id
    command_id = progress.command_id
    evidence: list[dict[str, object]] = []
    actor = next(p for p in encounter.participants if p.actor_id == actor_id)
    response = by_actor[actor.actor_id]
    _validate_actor_response(runtime, state, encounter, response, progress.environment)
    point = position(encounter, actor)
    cover = response.cover_dr
    covered = response.covered_locations
    defense: CheckTrace | None = None
    if response.dive_to:
        value, _ = defense_value(runtime, state, actor, "dodge")
        assert value is not None
        defense = interception.get(actor.actor_id) or success_roll(
            "gurps-basic-set-4e-2004", int(value.value) + 3, rng=runtime.rng
        )
        if defense.outcome.succeeded:
            point = response.dive_to
            cover = response.dive_cover_dr
            covered = response.dive_covered_locations
    distance_ = separation(point, resolved_center)
    direct = blast.direct_actor_id == actor.actor_id and not blast.follow_item
    contact = contact_actor_id == actor.actor_id
    amount, dice = (
        (max(0, (6 * payload.dice + payload.adds) * payload.multiplier), ())
        if contact
        else _blast_damage(runtime, progress, distance_, direct, blast.critical if direct else 0)
        if distance_ <= 2 * payload.dice * payload.multiplier
        else (0, ())
    )
    adjusted, contact_cover, contact, internal = _special_blast_adjustment(
        runtime,
        state,
        actor_id=actor.actor_id,
        amount=amount,
        contact_actor_id=contact_actor_id,
        internal_actor_id=internal_actor_id,
    )
    state, encounter, injury = hurt(
        runtime,
        state,
        encounter,
        actor.actor_id,
        command_id + ":" + actor.actor_id + ":blast",
        max(
            0,
            adjusted - contact_cover - (cover if "torso" in covered else 0),
        ),
        damage_type=payload.damage_type,
        armor_divisor=payload.armor_divisor if direct else Decimal(1),
        critical=blast.critical if direct else 0,
        ignore_dr=internal,
    )
    evidence.append(
        {
            "actor": actor.actor_id,
            "blast_dice": dice,
            "basic": amount,
            "injury": injury,
            "defense": asdict(defense) if defense else None,
            "contact": contact,
            "internal": internal,
            "contact_cover_dr": contact_cover,
        }
    )
    target = None
    if payload.fragmentation_dice and distance_ <= 5 * payload.fragmentation_dice:
        target = (
            15
            + range_penalty(distance_)
            + response.size_modifier
            - (
                4
                if actor.posture == "prone" or defense is not None and defense.outcome.succeeded
                else 2
                if actor.posture in ("kneeling", "sitting")
                else 0
            )
        )
    return (
        state,
        encounter,
        _evidence(progress, evidence),
        FragmentTarget(
            actor_id=actor.actor_id,
            response=response,
            cover=cover,
            covered=covered,
            direct=direct,
            target=target,
        ),
    )


def _fragment_injury(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    progress: BlastProgress,
    subject: FragmentTarget,
    attack: CheckTrace | None,
) -> tuple[PlayState, Encounter, BlastProgress]:
    payload, command_id = progress.blast.payload, progress.command_id
    evidence: list[dict[str, object]] = []
    assert subject.target is not None
    count = (
        1
        if subject.direct
        else 1 + (subject.target - attack.total) // 3
        if attack and attack.outcome.succeeded
        else 0
    )
    for hit in range(count):
        location, location_dice = select_location("random", rng=runtime.rng)
        fragment_dice = draw_dice(runtime.rng, payload.fragmentation_dice)
        state, encounter, injury = hurt(
            runtime,
            state,
            encounter,
            subject.actor_id,
            f"{command_id}:{subject.actor_id}:fragment:{hit}",
            max(0, sum(fragment_dice) - (subject.cover if location in subject.covered else 0)),
            location=location,
            damage_type="cut",
        )
        evidence.append(
            {
                "actor": subject.actor_id,
                "fragment_attack": asdict(attack) if attack else None,
                "location": location,
                "location_dice": location_dice,
                "damage_dice": fragment_dice,
                "injury": injury,
            }
        )
    return state, encounter, _evidence(progress, evidence)


def _finish_actor(
    encounter: Encounter, progress: BlastProgress, subject: FragmentTarget
) -> Encounter:
    response = subject.response
    if response.dive_to:
        actor = next(p for p in encounter.participants if p.actor_id == subject.actor_id)
        # B377: on failure the damage precedes the step; both attempts end prone.
        destination = response.dive_to
        encounter = contact_space(encounter, response)
        encounter = CombatEngine._replace(
            encounter,
            actor.model_copy(
                update={
                    "posture": (
                        actor.posture
                        if progress.environment == "water"
                        or actor.personal_flight is not None
                        and actor.personal_flight.altitude > 0
                        else "prone"
                    ),
                    "position": Hex(q=destination.x, r=destination.y)
                    if destination.geometry == "hex"
                    else GridPoint(x=destination.x, y=destination.y),
                }
            ),
        )
    return encounter


def _finish_blast(
    runtime: RulesContext, state: PlayState, encounter: Encounter, progress: BlastProgress
) -> tuple[PlayState, Encounter, int]:
    blast, payload, command_id = progress.blast, progress.blast.payload, progress.command_id
    resolved_center, responses = progress.center, progress.responses
    object_cover, object_sizes = dict(progress.object_cover), dict(progress.object_sizes)
    environment = progress.environment
    contact_actor_id, internal_actor_id = progress.contact_actor_id, progress.internal_actor_id
    entries = {entry.definition_id: entry for entry in catalog(runtime).entries}
    evidence: list[dict[str, object]] = [json.loads(row) for row in progress.evidence]
    for exposed in progress.objects:
        item = next(
            (
                current
                for current in (*state.resources.items, *state.resources.expended_items)
                if current.id == exposed.item_id
            ),
            None,
        )
        if item is None or item.condition is None or item.condition.destroyed:
            continue
        point = exposed.point
        distance_ = separation(point, resolved_center)
        packets: list[tuple[str, int, tuple[int, ...], str]] = []
        if distance_ <= 2 * payload.dice * payload.multiplier:
            amount, dice = _blast_damage(runtime, progress, distance_, False)
            packets.append(("blast", amount, dice, payload.damage_type))
        if payload.fragmentation_dice and distance_ <= 5 * payload.fragmentation_dice:
            target_value = 15 + range_penalty(distance_) + object_sizes[item.id]
            fragment_attack = success_roll("gurps-basic-set-4e-2004", target_value, rng=runtime.rng)
            evidence.append({"item": item.id, "fragment_attack": asdict(fragment_attack)})
            count = (
                1 + (target_value - fragment_attack.total) // 3
                if fragment_attack.outcome.succeeded
                else 0
            )
            for index in range(count):
                dice = draw_dice(runtime.rng, payload.fragmentation_dice)
                packets.append((f"fragment:{index}", sum(dice), dice, "cut"))
        expended = any(i.id == item.id for i in state.resources.expended_items)
        resources = state.resources
        if expended:
            resources = resources.model_copy(
                update={
                    "items": resources.items + (item,),
                    "expended_items": tuple(i for i in resources.expended_items if i.id != item.id),
                }
            )
        for packet, amount, dice, damage_type in packets:
            resources, result = apply_object(
                runtime.resources,
                resources,
                DamageObject.model_validate(
                    {
                        "id": f"{command_id}:object:{item.id}:{packet}",
                        "actor_id": item.owner_id,
                        "expected_revision": resources.revision,
                        "item_id": item.id,
                        "basic_damage": max(0, amount - object_cover[item.id]),
                        "damage_type": damage_type,
                        "explosive": progress.incendiary_objects and packet == "blast",
                    }
                ),
                system=True,
                rng=runtime.rng,
            )
            if secondary_object_blasts_enabled() and result.exploded:
                resources = schedule_secondary_object_blast(resources, encounter, blast, result)
            evidence.append(
                {
                    "item": item.id,
                    "packet": packet,
                    "dice": dice,
                    "result": result.model_dump(mode="json"),
                }
            )
            if secondary_object_blasts_enabled() and result.exploded:
                break
        if expended:
            damaged = next(i for i in resources.items if i.id == item.id)
            resources = resources.model_copy(
                update={
                    "items": tuple(i for i in resources.items if i.id != item.id),
                    "expended_items": resources.expended_items + (damaged,),
                }
            )
        state = state.model_copy(update={"resources": resources})
    resources = save(
        state.resources,
        blast.model_copy(
            update={
                "resolved": True,
                "center": resolved_center,
                "evidence": json.dumps(
                    {
                        "responses": [r.model_dump(mode="json") for r in responses],
                        "object_cover": object_cover,
                        "object_sizes": object_sizes,
                        "environment": environment,
                        "contact_actor_id": contact_actor_id,
                        "internal_actor_id": internal_actor_id,
                        "outcomes": evidence,
                    },
                    sort_keys=True,
                    default=str,
                ),
            }
        ),
        command_id + ":resolve-blast",
    )
    # An exploded grenade remains the same spent instance, permanently unavailable.
    if blast.destroy_source or blast.follow_item:
        resources = resources.model_copy(
            update={
                "items": tuple(i for i in resources.items if i.id != blast.source_item_id),
                "expended_items": tuple(
                    i for i in resources.expended_items if i.id != blast.source_item_id
                )
                + tuple(
                    i.model_copy(
                        update={
                            "ready": False,
                            "equipped": False,
                            "container_id": None,
                            "ground": resolved_center,
                            "firearm_failure": FirearmFailure(
                                mode_id=next(
                                    m.id
                                    for m in entries[i.definition_id].modes
                                    if isinstance(m, RangedMode)
                                    and m.firearm
                                    and m.firearm.action == "grenade"
                                ),
                                cause_id=blast.id,
                                kind="destroyed",
                            ),
                        }
                    )
                    for i in (*resources.items, *resources.expended_items)
                    if i.id == blast.source_item_id
                ),
            }
        )

    state = state.model_copy(update={"resources": resources})
    debt = blast.deferred_ticks
    remaining = next(
        (
            b
            for b in blasts(resources)
            if not b.resolved and b.encounter_id == encounter.id and b.due <= resources.game_time
        ),
        None,
    )
    if debt and remaining is not None:
        resources = save(
            resources,
            remaining.model_copy(update={"deferred_ticks": remaining.deferred_ticks + debt}),
            command_id + ":defer-remaining",
        )
        state = state.model_copy(update={"resources": resources})
        debt = 0
    return state, synchronize(state, encounter), debt


@dataclass(frozen=True)
class BlastPhaseResult:
    state: PlayState
    encounter: Encounter
    pending: PreparedFragmentAttack | None = None
    deferred_ticks: int = 0


def _continue_blast(
    runtime: RulesContext, state: PlayState, encounter: Encounter, progress: BlastProgress
) -> BlastPhaseResult:
    for index in range(progress.actor_index, len(progress.actor_ids)):
        state, encounter, progress, subject = _actor_blast(
            runtime, state, encounter, progress, progress.actor_ids[index]
        )
        if subject.target is not None:
            spec = AttackRollSpec(profile_id="gurps-basic-set-4e-2004", target=subject.target)
            if not subject.direct and subject.actor_id in progress.stop_for:
                return BlastPhaseResult(
                    state,
                    encounter,
                    PreparedFragmentAttack(
                        progress=progress,
                        target=subject,
                        spec=spec,
                    ),
                )
            attack = None if subject.direct else spec.roll(runtime.rng)
            state, encounter, progress = _fragment_injury(
                runtime, state, encounter, progress, subject, attack
            )
        encounter = _finish_actor(encounter, progress, subject)
        progress = progress.model_copy(update={"actor_index": index + 1})
    state, encounter, debt = _finish_blast(runtime, state, encounter, progress)
    return BlastPhaseResult(state, encounter, deferred_ticks=debt)


def validate_fragment_attack(
    state: PlayState, encounter: Encounter, prepared: PreparedFragmentAttack
) -> None:
    current = next((b for b in blasts(state.resources) if b.id == prepared.progress.blast.id), None)
    if (
        current != prepared.progress.blast
        or current.resolved
        or encounter.id != current.encounter_id
    ):
        raise ConflictError("Fragment continuation no longer matches its unresolved blast")


def resume_fragment_attack(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    prepared: PreparedFragmentAttack,
    selected: CheckTrace,
) -> BlastPhaseResult:
    validate_fragment_attack(state, encounter, prepared)
    if prepared.spec.score(selected.dice) != selected:
        raise ValidationError("Fragment selection differs from its captured target")
    state, encounter, progress = _fragment_injury(
        runtime, state, encounter, prepared.progress, prepared.target, selected
    )
    encounter = _finish_actor(encounter, progress, prepared.target)
    progress = progress.model_copy(update={"actor_index": progress.actor_index + 1})
    return _continue_blast(runtime, state, encounter, progress)


def prepare_blast_fragments(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    *,
    blast_id: str,
    command_id: str,
    responses: tuple[BlastResponse, ...],
    object_cover: dict[str, int],
    object_sizes: dict[str, int],
    center: GroundPosition | None,
    environment: str,
    contact_actor_id: str | None,
    internal_actor_id: str | None,
    stop_for: tuple[str, ...],
    incendiary_objects: bool = False,
) -> BlastPhaseResult:

    progress = _prepare_progress(
        runtime,
        state,
        encounter,
        blast_id=blast_id,
        command_id=command_id,
        responses=responses,
        object_cover=object_cover,
        object_sizes=object_sizes,
        center=center,
        environment=environment,
        contact_actor_id=contact_actor_id,
        internal_actor_id=internal_actor_id,
        stop_for=stop_for,
        incendiary_objects=incendiary_objects,
    )
    return _continue_blast(runtime, state, encounter, progress)


def _resume_cancelled_fragment(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    saved: FragmentContinuation,
    request: ResolveWeaponExplosion,
    command_id: str,
) -> tuple[PlayState, Encounter, int]:
    if not saved.cancelled or saved.preparation is None:
        raise ConflictError(
            "Resolve the pending fragment Luck choice before ordinary blast continuation"
        )
    prepared = continuing_fragment_responses(runtime, state, encounter, saved, request)
    prepared = prepared.model_copy(
        update={"progress": prepared.progress.model_copy(update={"stop_for": ()})}
    )
    validate_fragment_attack(state, encounter, prepared)
    validate_later_fragment_responses(runtime, state, encounter, prepared)
    original = prepared.spec.roll(runtime.rng)
    result = resume_fragment_attack(runtime, state, encounter, prepared, original)
    assert result.pending is None
    resources = save_fragment(
        result.state.resources,
        saved.model_copy(
            update={
                "preparation": None,
                "original": None,
                "cancelled": False,
            }
        ),
        command_id + ":complete",
    )
    if saved.secret:
        resources = conceal_attack_result(
            resources,
            SecretAttackSource(
                encounter_id=saved.resolution.encounter_id,
                attack_id=fragment_attack_id(saved.resolution.blast_id, prepared.target.actor_id),
                attacker_id=saved.launch.command.actor_id,
                target_id=prepared.target.actor_id,
            ),
            command_id,
        )
    return (
        result.state.model_copy(update={"resources": resources}),
        result.encounter,
        result.deferred_ticks,
    )


def resolve_blast(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    *,
    blast_id: str,
    command_id: str,
    responses: tuple[BlastResponse, ...],
    object_cover: dict[str, int],
    object_sizes: dict[str, int],
    center: GroundPosition | None,
    environment: Literal["air", "water", "vacuum"],
    contact_actor_id: str | None,
    internal_actor_id: str | None,
) -> tuple[PlayState, Encounter, int]:

    saved = active_fragment(state.resources, blast_id)
    if saved is not None:
        request = ResolveWeaponExplosion(
            id=saved.resolution.id,
            actor_id=saved.resolution.actor_id,
            expected_revision=saved.resolution.expected_revision,
            encounter_id=encounter.id,
            blast_id=blast_id,
            responses=responses,
            object_cover=object_cover,
            object_sizes=object_sizes,
            center=center,
            environment=environment,
            contact_actor_id=contact_actor_id,
            internal_actor_id=internal_actor_id,
        )
        return _resume_cancelled_fragment(runtime, state, encounter, saved, request, command_id)
    result = prepare_blast_fragments(
        runtime,
        state,
        encounter,
        blast_id=blast_id,
        command_id=command_id,
        responses=responses,
        object_cover=object_cover,
        object_sizes=object_sizes,
        center=center,
        environment=environment,
        contact_actor_id=contact_actor_id,
        internal_actor_id=internal_actor_id,
        stop_for=(),
    )
    assert result.pending is None
    return result.state, result.encounter, result.deferred_ticks


def amend_future_fragment_responses(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    prepared: PreparedFragmentAttack,
    responses: tuple[BlastResponse, ...],
) -> PreparedFragmentAttack:
    validate_fragment_attack(state, encounter, prepared)
    remaining = prepared.progress.actor_ids[prepared.progress.actor_index + 1 :]
    by_actor = {response.actor_id: response for response in responses}
    if not responses or len(by_actor) != len(responses) or set(by_actor) - set(remaining):
        raise ValidationError("Only still-unresolved later blast responses may be amended")
    for response in responses:
        _validate_actor_response(runtime, state, encounter, response, prepared.progress.environment)
    updated = tuple(
        by_actor.get(response.actor_id, response) for response in prepared.progress.responses
    )
    return prepared.model_copy(
        update={"progress": prepared.progress.model_copy(update={"responses": updated})}
    )


def validate_later_fragment_responses(
    runtime: RulesContext, state: PlayState, encounter: Encounter, prepared: PreparedFragmentAttack
) -> None:
    remaining = set(prepared.progress.actor_ids[prepared.progress.actor_index + 1 :])
    for response in prepared.progress.responses:
        if response.actor_id in remaining:
            _validate_actor_response(
                runtime, state, encounter, response, prepared.progress.environment
            )


def continuing_fragment_responses(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    saved: FragmentContinuation,
    request: ResolveWeaponExplosion,
) -> PreparedFragmentAttack:
    prepared = saved.preparation
    assert prepared is not None
    if request.model_dump(
        exclude={"id", "expected_revision", "responses"}
    ) != saved.resolution.model_dump(exclude={"id", "expected_revision", "responses"}):
        raise ConflictError("A continuing blast cannot rewrite its settled source")
    previous = {response.actor_id: response for response in saved.resolution.responses}
    if len(request.responses) != len(previous) or {
        response.actor_id for response in request.responses
    } != set(previous):
        raise ValidationError("A blast continuation must retain its original target set")
    changed = tuple(
        response for response in request.responses if response != previous[response.actor_id]
    )
    return (
        amend_future_fragment_responses(runtime, state, encounter, prepared, changed)
        if changed
        else prepared
    )
