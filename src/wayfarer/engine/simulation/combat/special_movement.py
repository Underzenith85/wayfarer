"""Combat integration for creature mounts and personal flight.

Campaigns fourth printing B396-B398. Vehicle operation remains in the transport
package; this module composes its outcomes with encounter identities and the
shared hex geometry.
"""

from __future__ import annotations

from wayfarer.engine.rules.checks import NO_RANDOM, RandomSource
from wayfarer.engine.rules.types.special_combat import (
    FlightStep,
    MountedCombatRelationship,
    PersonalFlightState,
)
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter
from wayfarer.engine.simulation.combat.spatial import HexActorPlacement
from wayfarer.engine.simulation.hex_geometry import Hex, HexBattlefield, distance
from wayfarer.engine.simulation.movement.transport import apply_transport
from wayfarer.engine.simulation.movement.vehicles.commands import ResolveMountSeparation
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError


def _participant(encounter: Encounter, actor_id: str) -> Combatant:
    result = next((entry for entry in encounter.participants if entry.actor_id == actor_id), None)
    if result is None:
        raise ValidationError("Special combat actor is not an encounter participant")
    return result


def _replace(encounter: Encounter, actor: Combatant) -> Encounter:
    updated = encounter.model_copy(
        update={
            "participants": tuple(
                actor if entry.actor_id == actor.actor_id else entry
                for entry in encounter.participants
            )
        }
    )
    if encounter.spatial_kind == "hex":
        placement = encounter.placement(actor.actor_id)
        assert isinstance(placement, HexActorPlacement)
        updated = updated.replace_placement(
            placement.model_copy(update={"position": actor.position, "facing": actor.hex_facing})
        )
    return updated


def bind_mount(
    encounter: Encounter,
    resources: ResourceState,
    *,
    transport_id: str,
    riding_skill: int,
    saddle: bool = False,
    stirrups: bool = False,
) -> Encounter:
    """Bind the existing creature-backed transport to two encounter combatants."""
    transport = next((entry for entry in resources.transports if entry.id == transport_id), None)
    if transport is None or transport.locomotion != "ground-mount":
        raise ValidationError("Mounted combat requires a creature-backed ground mount")
    creature = next(
        (entry for entry in resources.creatures if entry.actor_id == transport.body_id), None
    )
    if creature is None or creature.mount is None or not creature.mount.riding:
        raise ValidationError("Mounted combat transport has no authored riding creature")
    rider = _participant(encounter, transport.operator_id)
    mount = _participant(encounter, transport.body_id)
    if any(
        transport_id == relationship.transport_id
        or rider.actor_id in (relationship.rider_id, relationship.mount_id)
        or mount.actor_id in (relationship.rider_id, relationship.mount_id)
        for relationship in encounter.mounted_combat
    ):
        raise ValidationError("Mounted combat relationship is already bound")
    if transport.occupants != (rider.actor_id,) or transport.status not in {
        "controlled",
        "spooked",
        "lost",
    }:
        raise ValidationError("Mounted combat requires an attached rider and mount")
    if encounter.spatial_kind != "hex" or not isinstance(mount.position, Hex):
        raise ValidationError("Mounted combat requires authoritative hex poses")
    if (mount.position.q, mount.position.r) != (transport.q, transport.r):
        raise ValidationError("Mount combatant and transport positions disagree")
    rider = rider.model_copy(
        update={"position": mount.position, "hex_facing": mount.hex_facing, "posture": "standing"}
    )
    relationship = MountedCombatRelationship(
        rider_id=rider.actor_id,
        mount_id=mount.actor_id,
        transport_id=transport.id,
        riding_skill=riding_skill,
        saddle=saddle,
        stirrups=stirrups,
        war_trained=creature.mount.war_trained,
        control=("controlled" if transport.status == "controlled" else transport.status),
    )
    encounter = _replace(encounter, rider)
    return encounter.model_copy(
        update={"mounted_combat": (*encounter.mounted_combat, relationship)}
    )


def mounted_action_context(
    encounter: Encounter,
    resources: ResourceState,
    actor_id: str,
) -> tuple[tuple[str, ...], int, int]:
    """Return legal maneuvers, attack skill cap, and active-defense modifier."""
    relationship = next(
        (
            entry
            for entry in encounter.mounted_combat
            if actor_id in (entry.rider_id, entry.mount_id) and entry.control != "separated"
        ),
        None,
    )
    if relationship is None:
        raise ValidationError("Actor has no active mounted combat relationship")
    if actor_id == relationship.rider_id:
        penalty = min(0, relationship.riding_skill - 12)
        return (
            (
                "move",
                "attack",
                "all-out-attack",
                "all-out-defense",
                "move-and-attack",
                "ready",
                "do-nothing",
            ),
            relationship.riding_skill,
            penalty,
        )
    creature = next(entry for entry in resources.creatures if entry.actor_id == actor_id)
    moves = tuple(value.replace("-", "_") for value in creature.combat_behavior.maneuvers)
    if not relationship.war_trained:
        moves = tuple(value for value in moves if value in ("move", "do_nothing"))
    return moves, 50, 0


def separate_mount(
    engine: ResourceEngine,
    encounter: Encounter,
    resources: ResourceState,
    *,
    command_id: str,
    expected_revision: int,
    transport_id: str,
    health: dict[str, int],
    collision_speed: int = 0,
    rng: RandomSource = NO_RANDOM,
) -> tuple[Encounter, ResourceState]:
    """Resolve both injury ledgers and both encounter identities as one value."""
    relationship = next(
        (entry for entry in encounter.mounted_combat if entry.transport_id == transport_id), None
    )
    if relationship is None or relationship.control == "separated":
        raise ValidationError("Mount has no attached combat relationship")
    updated_resources = apply_transport(
        engine,
        resources,
        ResolveMountSeparation(
            id=command_id,
            actor_id=relationship.rider_id,
            expected_revision=expected_revision,
            transport_id=transport_id,
            riding_skill=(relationship.riding_skill if not collision_speed else None),
            collision_speed=collision_speed,
        ),
        system=True,
        health=health,
        rng=rng,
    )
    rider = _participant(encounter, relationship.rider_id).model_copy(update={"posture": "prone"})
    encounter = _replace(encounter, rider)
    relationships = tuple(
        entry.model_copy(update={"control": "separated"})
        if entry.transport_id == transport_id
        else entry
        for entry in encounter.mounted_combat
    )
    return encounter.model_copy(update={"mounted_combat": relationships}), updated_resources


def begin_personal_flight(
    encounter: Encounter,
    actor_id: str,
    *,
    altitude: int,
    basic_air_move: int,
    top_air_speed: int,
    winged: bool = False,
    cannot_hover: bool = False,
) -> Encounter:
    actor = _participant(encounter, actor_id)
    if encounter.spatial_kind != "hex" or not isinstance(actor.position, Hex):
        raise ValidationError("Personal flight requires an exact hex battlefield")
    return _replace(
        encounter,
        actor.model_copy(
            update={
                "personal_flight": PersonalFlightState(
                    altitude=altitude,
                    basic_air_move=basic_air_move,
                    top_air_speed=top_air_speed,
                    winged=winged,
                    cannot_hover=cannot_hover,
                )
            }
        ),
    )


def fly(
    encounter: Encounter,
    board: HexBattlefield,
    actor_id: str,
    maneuver: str,
    steps: tuple[FlightStep, ...],
    *,
    dive: bool = False,
) -> Encounter:
    """Apply B398 personal aerial movement in half-yard movement units."""
    actor = _participant(encounter, actor_id)
    flight = actor.personal_flight
    if flight is None:
        raise ValidationError("Actor has no personal flight capability")
    if maneuver not in ("move", "move_and_attack"):
        raise ValidationError("Personal aerial movement requires Move or Move and Attack")
    if flight.status in ("stalled", "falling"):
        raise ValidationError("Stalled flight requires an explicit recovery or fall")
    if flight.cannot_hover and not steps:
        raise ValidationError("This personal flight capability cannot hover")
    current = actor.position
    assert isinstance(current, Hex)
    altitude = flight.altitude
    cost = 0
    for step in steps:
        point = Hex(q=step.q, r=step.r)
        planar = distance(current, point)
        vertical = abs(step.altitude - altitude)
        if planar > 1 or (planar == 0 and vertical == 0):
            raise ValidationError("Flight path requires adjacent planar or vertical steps")
        if planar and vertical:
            if vertical != 1:
                raise ValidationError("A diagonal flight step changes height by one yard")
            cost += 3
        else:
            cost += 2 * (planar + vertical)
        terrain = board.cell(point)
        if step.altitude <= terrain.ground + terrain.opaque_height:
            raise ValidationError("Flight path intersects terrain; resolve a collision")
        current, altitude = point, step.altitude
    allowance = flight.basic_air_move + (10 if dive else 0)
    if cost > allowance * 2:
        raise ValidationError("Personal flight path exceeds basic air Move")
    velocity = len(steps)
    if flight.winged and flight.cannot_hover and velocity * 4 < flight.top_air_speed:
        flight = flight.model_copy(update={"status": "stalled", "velocity": velocity})
    else:
        flight = flight.model_copy(
            update={
                "altitude": altitude,
                "velocity": velocity,
                "status": "diving" if dive else "flying",
            }
        )
    actor = actor.model_copy(update={"position": current, "personal_flight": flight})
    return _replace(encounter, actor)


def flight_attack_defense(
    attacker: Combatant,
    defender: Combatant,
    *,
    retreat_vertical: bool = False,
) -> tuple[int, int]:
    """Return flight-only attack and defense adjustments; height stays in geometry."""
    if attacker.personal_flight is None:
        raise ValidationError("Attacker is not in personal flight")
    if attacker.personal_flight.status in ("stalled", "falling"):
        raise ValidationError("A stalled or falling combatant cannot make an aerial attack")
    bonus = 0
    if retreat_vertical:
        flight = defender.personal_flight
        if flight is None:
            raise ValidationError("Only a flying defender can retreat vertically")
        bonus = 1 if not flight.cannot_hover else 0
    return 0, bonus
