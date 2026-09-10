"""B407 malfunction payloads and B414-415 GM-declared blast resolution."""

import json
from dataclasses import asdict
from decimal import Decimal
from typing import TYPE_CHECKING

from wayfarer.errors import ValidationError
from wayfarer.rules.checks import CheckTrace
from wayfarer.rules.explosion_types import BlastResponse, ExplosionSpec
from wayfarer.rules.firearm_types import FirearmFailure
from wayfarer.rules.gurps_checks import success_roll
from wayfarer.rules.object_types import GroundPosition
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.combat import CombatEngine, Encounter, GridPoint
from wayfarer.simulation.explosions import BlastRecord, blasts, save
from wayfarer.simulation.firearms import spend_rounds
from wayfarer.simulation.gurps_equipment import RangedMode
from wayfarer.simulation.hex_geometry import Hex, distance
from wayfarer.simulation.resources import ResourceState

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService


def schedule_payload(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    weapon: RangedMode,
    *,
    original_resources: ResourceState,
    failure: FirearmFailure | None,
    hits: int,
    shots_fired: int,
    critical: int,
) -> tuple[PlayState, Encounter, bool]:
    from wayfarer.orchestration.gurps_melee import catalog
    from wayfarer.orchestration.weapon_flight import position

    pending = encounter.pending_defense
    assert pending is not None
    entries = {e.definition_id: e for e in catalog(play).entries}
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
        fuse = (play.rng.randbelow(6) + 1,) if delayed else ()
        direct = pending.attacker_id if explodes else pending.defender_id if index < hits else None
        center = (
            position(encounter, attacker)
            if explodes
            else position(encounter, target)
            if direct
            else None
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
                fuse_dice=fuse,
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


def validate_position(play: PlayService, encounter: Encounter, point: GroundPosition) -> None:
    if point.encounter_id != encounter.id:
        raise ValidationError("Blast position belongs to another encounter")
    if encounter.hex_battlefield:
        if point.geometry != "hex" or not any(
            c.position == Hex(q=point.x, r=point.y) and not c.blocked
            for c in encounter.hex_battlefield.cells
        ):
            raise ValidationError("Blast position must be a traversable battlefield hex")
    else:
        rules = play.engine.rules.combat
        assert rules is not None
        field = next(f for f in rules.battlefields if f.id == encounter.battlefield_id)
        if point.geometry != "grid" or not (
            0 <= point.x < field.width and 0 <= point.y < field.height
        ):
            raise ValidationError("Blast position is outside the battlefield")


def resolve_blast(
    play: PlayService,
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
) -> tuple[PlayState, Encounter, int]:
    from wayfarer.orchestration.gurps_melee import catalog, defense_value, movement
    from wayfarer.orchestration.unarmed import hurt
    from wayfarer.orchestration.weapon_flight import position
    from wayfarer.rules.ranged_tables import range_penalty
    from wayfarer.simulation.hit_locations import select_location
    from wayfarer.simulation.objects import DamageObject, apply_object

    if catalog(play).profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Explosions require the exact Basic Set profile")
    blast = next((b for b in blasts(state.resources) if b.id == blast_id), None)
    if (
        blast is None
        or blast.resolved
        or blast.encounter_id != encounter.id
        or blast.due > state.resources.game_time
    ):
        raise ValidationError("No due unresolved blast is available")
    resolved_center = blast.center
    if blast.follow_item:
        item = next(
            (
                i
                for i in (*state.resources.items, *state.resources.expended_items)
                if i.id == blast.source_item_id
            ),
            None,
        )
        if item is not None:
            if item.ground:
                resolved_center = item.ground
            elif item in state.resources.items:
                owner = next(
                    (p for p in encounter.participants if p.actor_id == item.owner_id), None
                )
                if owner is None:
                    raise ValidationError(
                        "Carried grenade holder needs an explicit encounter position"
                    )
                resolved_center = position(encounter, owner)
    if resolved_center is None:
        if center is None:
            raise ValidationError(
                "Unresolved projectile landing requires an explicit GM blast center"
            )
        resolved_center = center
    elif center is not None and center != resolved_center:
        raise ValidationError("A recorded blast center cannot be replaced")
    validate_position(play, encounter, resolved_center)
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
            validate_position(play, encounter, response.dive_to)
            if separation(position(encounter, actor), response.dive_to) not in range(
                1, max(1, (movement(play, state, actor.actor_id) + 9) // 10) + 1
            ):
                raise ValidationError("Diving for cover is limited to one step")
            if actor.posture in ("kneeling", "sitting") or actor.grappled or actor.pinned:
                raise ValidationError("Actor cannot take the declared diving step")
            defense_value(play, state, actor, "dodge")
        elif response.dive_cover_dr:
            raise ValidationError("Destination cover requires a declared dive")
    entries = {e.definition_id: e for e in catalog(play).entries}
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
        return tuple(play.rng.randbelow(6) + 1 for _ in range(count))

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
            value, _ = defense_value(play, state, actor, "dodge")
            assert value is not None
            defense = success_roll("gurps-basic-set-4e-2004", int(value.value) + 3, rng=play.rng)
            if defense.outcome.succeeded:
                point = response.dive_to
                cover = response.dive_cover_dr
                covered = response.dive_covered_locations
        distance_ = separation(point, resolved_center)
        direct = blast.direct_actor_id == actor.actor_id and not blast.follow_item
        amount, dice = (
            blast_damage(distance_, direct, blast.critical if direct else 0)
            if distance_ <= 2 * payload.dice * payload.multiplier
            else (0, ())
        )
        state, encounter, injury = hurt(
            play,
            state,
            encounter,
            actor.actor_id,
            command_id + ":" + actor.actor_id + ":blast",
            max(0, amount - (cover if "torso" in covered else 0)),
            damage_type=payload.damage_type,
            armor_divisor=payload.armor_divisor if direct else Decimal(1),
            critical=blast.critical if direct else 0,
        )
        evidence.append(
            {
                "actor": actor.actor_id,
                "blast_dice": dice,
                "basic": amount,
                "injury": injury,
                "defense": asdict(defense) if defense else None,
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
                else success_roll("gurps-basic-set-4e-2004", target_value, rng=play.rng)
            )
            count = (
                1
                if direct
                else 1 + (target_value - attack.total) // 3
                if attack and attack.outcome.succeeded
                else 0
            )
            for hit in range(count):
                location, location_dice = select_location("random", rng=play.rng)
                fragment_dice = roll(payload.fragmentation_dice)
                state, encounter, injury = hurt(
                    play,
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
            fragment_attack = success_roll("gurps-basic-set-4e-2004", target_value, rng=play.rng)
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
                play.engine.resources,
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
                rng=play.rng,
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
    if blast.follow_item:
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
    from wayfarer.orchestration.object_combat import synchronize

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
