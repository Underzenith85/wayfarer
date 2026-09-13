"""B407 malfunction payloads and B414-415 GM-declared blast resolution."""

import json
from dataclasses import asdict
from decimal import Decimal
from typing import TYPE_CHECKING

from wayfarer.engine.rules.checks import CheckTrace, draw_dice
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.tables.ranged import range_penalty
from wayfarer.engine.rules.types.explosion import BlastResponse, ExplosionSpec
from wayfarer.engine.rules.types.firearm import FirearmFailure
from wayfarer.engine.rules.types.object import GroundPosition
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog, movement
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.equipment_effects import synchronize
from wayfarer.engine.simulation.combat.explosions import BlastRecord, blasts, save
from wayfarer.engine.simulation.combat.firearms import spend_rounds
from wayfarer.engine.simulation.combat.melee.defense import defense_value
from wayfarer.engine.simulation.combat.thrown.flight import position
from wayfarer.engine.simulation.combat.unarmed.injury import armor_dr, hurt
from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.engine.simulation.equipment.objects import DamageObject, apply_object
from wayfarer.engine.simulation.health.hit_locations import select_location
from wayfarer.engine.simulation.hex_geometry import DIRECTIONS as HEX_DIRECTIONS
from wayfarer.engine.simulation.hex_geometry import Hex, distance
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError

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
    count = 1 if grenade or explodes else shots_fired
    for index in range(count):
        fuse = (draw_dice(runtime.rng, 1)[0],) if delayed else ()
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
    environment: str,
    contact_actor_id: str | None,
    internal_actor_id: str | None,
) -> tuple[PlayState, Encounter, int]:

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
    by_actor = {r.actor_id: r for r in responses}
    if len(by_actor) != len(responses) or set(by_actor) != {p.actor_id for p in participants}:
        raise ValidationError(
            "Declare cover, size and chosen defenses for every actor in blast range"
        )
    # Validate every response before the first roll, including destination and defense eligibility.
    for actor in participants:
        response = by_actor[actor.actor_id]
        if (
            response.cover_dr
            and not response.covered_locations
            or response.dive_cover_dr
            and not response.dive_covered_locations
        ):
            raise ValidationError("Cover requires explicitly protected hit locations")
        if response.dive_to:
            validate_position(runtime, encounter, response.dive_to)
            if separation(position(encounter, actor), response.dive_to) not in range(
                1, max(1, (movement(runtime, state, actor.actor_id) + 9) // 10) + 1
            ):
                raise ValidationError("Diving for cover is limited to one step")
            if actor.posture in ("kneeling", "sitting") or actor.grappled or actor.pinned:
                raise ValidationError("Actor cannot take the declared diving step")
            defense_value(runtime, state, actor, "dodge")
        elif response.dive_cover_dr:
            raise ValidationError("Destination cover requires a declared dive")
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
    evidence: list[dict[str, object]] = []

    def roll(count: int) -> tuple[int, ...]:
        return draw_dice(runtime.rng, count)

    def blast_damage(
        distance_: int, direct: bool, critical: int = 0
    ) -> tuple[int, tuple[int, ...]]:
        dice = () if critical == 6 else roll(payload.dice)
        amount = (
            max(0, (6 * payload.dice if critical == 6 else sum(dice)) + payload.adds)
            * payload.multiplier
        )
        amount *= 3 if critical in (3, 18) else 2 if critical in (5, 16) else 1
        divisor = (
            1
            if direct or distance_ == 0
            else {"air": 3, "water": 1, "vacuum": 10}[environment] * distance_
        )
        return amount // divisor, dice

    for original_actor in participants:
        actor = next(p for p in encounter.participants if p.actor_id == original_actor.actor_id)
        response = by_actor[actor.actor_id]
        point = position(encounter, actor)
        cover = response.cover_dr
        covered = response.covered_locations
        defense: CheckTrace | None = None
        if response.dive_to:
            value, _ = defense_value(runtime, state, actor, "dodge")
            assert value is not None
            defense = success_roll("gurps-basic-set-4e-2004", int(value.value) + 3, rng=runtime.rng)
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
            else blast_damage(distance_, direct, blast.critical if direct else 0)
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
        if payload.fragmentation_dice and distance_ <= 5 * payload.fragmentation_dice:
            target_value = (
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
            attack = (
                None
                if direct
                else success_roll("gurps-basic-set-4e-2004", target_value, rng=runtime.rng)
            )
            count = (
                1
                if direct
                else 1 + (target_value - attack.total) // 3
                if attack and attack.outcome.succeeded
                else 0
            )
            for hit in range(count):
                location, location_dice = select_location("random", rng=runtime.rng)
                fragment_dice = roll(payload.fragmentation_dice)
                state, encounter, injury = hurt(
                    runtime,
                    state,
                    encounter,
                    actor.actor_id,
                    f"{command_id}:{actor.actor_id}:fragment:{hit}",
                    max(0, sum(fragment_dice) - (cover if location in covered else 0)),
                    location=location,
                    damage_type="cut",
                )
                evidence.append(
                    {
                        "actor": actor.actor_id,
                        "fragment_attack": asdict(attack) if attack else None,
                        "location": location,
                        "location_dice": location_dice,
                        "damage_dice": fragment_dice,
                        "injury": injury,
                    }
                )
        if response.dive_to:
            actor = next(p for p in encounter.participants if p.actor_id == actor.actor_id)
            # B377: on failure the damage precedes the step; both attempts end prone.
            destination = response.dive_to
            encounter = CombatEngine._replace(
                encounter,
                actor.model_copy(
                    update={
                        "posture": "prone",
                        "position": Hex(q=destination.x, r=destination.y)
                        if destination.geometry == "hex"
                        else GridPoint(x=destination.x, y=destination.y),
                    }
                ),
            )
    for item, point in objects:
        distance_ = separation(point, resolved_center)
        packets: list[tuple[str, int, tuple[int, ...], str]] = []
        if distance_ <= 2 * payload.dice * payload.multiplier:
            amount, dice = blast_damage(distance_, False)
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
                dice = roll(payload.fragmentation_dice)
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
                    }
                ),
                system=True,
                rng=runtime.rng,
            )
            evidence.append(
                {
                    "item": item.id,
                    "packet": packet,
                    "dice": dice,
                    "result": result.model_dump(mode="json"),
                }
            )
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
