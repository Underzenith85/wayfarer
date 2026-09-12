"""Profile-bound migration and defense geometry inside CombatService's transaction."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfarer.engine.simulation.combat import (
    BasicSpatialContext,
    Combatant,
    CombatEngine,
    CoverSpatialFact,
    DistanceSpatialFact,
    Encounter,
    HexActorPlacement,
    HexSpatialContext,
    ObstacleSpatialFact,
    ReachSpatialFact,
    RetreatSpatialFact,
    SpatialProvenance,
    VisibilitySpatialFact,
    move_basic,
)
from wayfarer.engine.simulation.hex_geometry import (
    HexBattlefield,
    Occupant,
    RetreatContext,
    can_retreat,
    distance,
    neighbor,
)
from wayfarer.engine.simulation.tactical import (
    defense_adjustment,
    occupants,
    pose,
    sight,
    validate_hex_encounter,
)
from wayfarer.errors import ConflictError, ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.engine.simulation.combat_commands import (
        ChooseDefense,
        MigrateEncounterBasic,
        MigrateEncounterHex,
    )
    from wayfarer.engine.simulation.rules_context import RulesContext


def migrate(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    command: MigrateEncounterHex,
) -> Encounter:
    if (
        encounter.spatial_kind == "hex"
        or encounter.status != "active"
        or encounter.pending_defense
        or encounter.pending_unarmed
        or encounter.wait_interrupt
        or encounter.grips
        or encounter.close_pairs
        or encounter.blocked_reason
    ):
        raise ConflictError(
            "Migration requires an unmigrated encounter with no pending interaction"
        )
    poses = {p.actor_id: p.pose for p in command.placements}
    if len(poses) != len(command.placements) or set(poses) != set(encounter.turn_order):
        raise ValidationError("Migration requires exactly one explicit pose for every participant")
    if any(p.posture not in ("standing", "kneeling", "lying") for p in poses.values()):
        raise ValidationError("Unsupported combat posture")
    for actor in encounter.participants:
        if poses[actor.actor_id].posture != (
            "lying" if actor.posture == "prone" else actor.posture
        ):
            raise ValidationError("Map migration cannot change posture")
    if any(
        item.ground is not None and item.ground.encounter_id == encounter.id
        for item in state.resources.items
    ):
        raise ValidationError("Map migration cannot reinterpret grounded equipment")
    from wayfarer.engine.simulation.explosions import blasts
    from wayfarer.engine.simulation.spells import active_spells

    if any(
        not blast.resolved and blast.encounter_id == encounter.id
        for blast in blasts(state.resources)
    ):
        raise ValidationError("Resolve spatial explosions before map migration")
    if any(
        effect.encounter_id == encounter.id and effect.position is not None
        for effect in active_spells(state.resources)
    ):
        raise ValidationError("Map migration cannot reinterpret positioned spell effects")
    participants = tuple(
        p.model_copy(
            update={
                "position": poses[p.actor_id].position,
                "hex_facing": poses[p.actor_id].facing,
            }
        )
        for p in encounter.participants
    )
    result = encounter.model_copy(
        update={
            "spatial_context": HexSpatialContext(
                battlefield_id=command.battlefield.id,
                placements=tuple(
                    HexActorPlacement(
                        actor_id=p.actor_id,
                        position=poses[p.actor_id].position,
                        facing=poses[p.actor_id].facing,
                    )
                    for p in participants
                ),
            ),
            "participants": participants,
        }
    )
    rules = runtime.rules.combat
    if isinstance(encounter.spatial, BasicSpatialContext):
        _validate_basic_escalation(state, encounter, result, command)
    validate_hex_encounter(
        result, rules.gurps_equipment if rules else None, board=runtime.hex_map(result)
    )
    return result


def _validate_basic_escalation(
    state: PlayState,
    basic: Encounter,
    mapped: Encounter,
    command: MigrateEncounterHex,
) -> None:
    """Reject placements that contradict still-authoritative Basic relationships."""
    context = basic.spatial
    assert isinstance(context, BasicSpatialContext)
    actors = {p.actor_id: p for p in mapped.participants}
    board = command.battlefield
    occupied = occupants(mapped)
    hp = {p.id.removeprefix("hp:"): p for p in state.resources.pools if p.id.startswith("hp:")}
    for fact in context.facts:
        if fact.provenance.invalidated_revision is not None:
            continue
        subject, object_id = actors[fact.subject_id], actors[fact.object_id]
        separation = distance(pose(subject).position, pose(object_id).position)
        if isinstance(fact, DistanceSpatialFact) and fact.yards != separation:
            raise ValidationError("Hex placement contradicts authoritative Basic distance")
        if isinstance(fact, ReachSpatialFact):
            expected = "reachable" if separation <= subject.reach else "separated"
            if fact.relation != expected:
                raise ValidationError("Hex placement contradicts authoritative Basic reach")
        if isinstance(fact, VisibilitySpatialFact) and fact.visible != sight(
            mapped, subject, object_id, board=board
        ):
            raise ValidationError("Hex placement contradicts authoritative Basic visibility")
        if isinstance(fact, CoverSpatialFact) and fact.cover != "none":
            raise ValidationError("Basic cover requires explicit resolution before hex escalation")
        if isinstance(fact, ObstacleSpatialFact) and fact.blocked:
            raise ValidationError(
                "A blocking Basic obstacle requires explicit resolution before hex escalation"
            )
        if isinstance(fact, RetreatSpatialFact):
            pool = hp.get(subject.actor_id)
            retreat_context = RetreatContext(
                already_retreated=subject.retreat_used,
                stunned=bool(pool and pool.injury and pool.injury.stunned),
                grappled=subject.grappled
                or subject.pinned
                or any(g.holder_id == subject.actor_id for g in basic.grips),
                maneuver_allows_retreat=not subject.maneuver_state.defense_forbidden,
            )
            feasible = False
            for direction in range(6):
                try:
                    destination = neighbor(pose(subject).position, direction)
                    board.cell(destination)
                except ValidationError, ValueError:
                    continue
                if can_retreat(
                    board,
                    pose(subject),
                    pose(object_id).position,
                    destination,
                    context=retreat_context,
                    occupants=occupied,
                ):
                    feasible = True
                    break
            if fact.feasible != feasible:
                raise ValidationError("Hex placement contradicts authoritative Basic retreat")


def migrate_basic(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    command: MigrateEncounterBasic,
) -> Encounter:
    """Derive a lossless Basic projection of a representable hex encounter."""
    if encounter.spatial_kind != "hex" or encounter.status != "active":
        raise ConflictError("Basic conversion requires an active hex encounter")
    scenes = runtime.rules.scenes
    if (
        encounter.scene_id is None
        or scenes is None
        or not any(scene.id == encounter.scene_id for scene in scenes.scenes)
    ):
        raise ValidationError("Basic conversion requires the encounter's authored scene")
    board = runtime.require_hex(encounter)
    rules = runtime.rules.combat
    validate_hex_encounter(encounter, rules.gurps_equipment if rules else None, board=board)
    if (
        board.darkness_penalty
        or board.stairs
        or any(
            cell.blocked
            or cell.extra_cost
            or cell.opaque_height
            or cell.elevation
            or cell.elevation_inches
            for cell in board.cells
        )
    ):
        raise ValidationError(
            "Consequential tactical terrain cannot be represented in Basic combat"
        )
    if encounter.wait_interrupt is not None:
        raise ConflictError("Resolve the interrupted Wait before Basic conversion")
    pending = encounter.pending_defense
    if pending is not None and (
        pending.post_attack_hex_path or pending.post_attack_facing is not None
    ):
        raise ConflictError("Resolve the pending hex movement before Basic conversion")
    if any(
        item.ground is not None and item.ground.encounter_id == encounter.id
        for item in state.resources.items
    ):
        raise ValidationError("Grounded equipment prevents Basic conversion")
    from wayfarer.engine.simulation.explosions import blasts
    from wayfarer.engine.simulation.spells import active_spells

    if any(
        not blast.resolved and blast.encounter_id == encounter.id
        for blast in blasts(state.resources)
    ):
        raise ValidationError("Resolve spatial explosions before Basic conversion")
    if any(
        effect.encounter_id == encounter.id and effect.position is not None
        for effect in active_spells(state.resources)
    ):
        raise ValidationError("Positioned spell effects prevent Basic conversion")
    provenance = SpatialProvenance(
        source="engine-derived",
        source_id=command.id,
        declared_by=command.actor_id,
        declared_revision=command.expected_revision,
    )
    participants = tuple(
        actor.model_copy(update={"position": None, "hex_facing": None})
        for actor in encounter.participants
    )
    actors = {actor.actor_id: actor for actor in encounter.participants}
    close_pairs = {tuple(sorted(pair)) for pair in encounter.close_pairs}
    occupied = occupants(encounter)
    facts: list[
        DistanceSpatialFact
        | ReachSpatialFact
        | VisibilitySpatialFact
        | CoverSpatialFact
        | ObstacleSpatialFact
        | RetreatSpatialFact
    ] = []
    for index, subject_id in enumerate(encounter.turn_order):
        subject = actors[subject_id]
        for object_id in encounter.turn_order[index + 1 :]:
            object_actor = actors[object_id]
            separation = distance(pose(subject).position, pose(object_actor).position)
            facts.append(
                DistanceSpatialFact(
                    subject_id=subject_id,
                    object_id=object_id,
                    yards=separation,
                    provenance=provenance,
                )
            )
            for left, right in ((subject, object_actor), (object_actor, subject)):
                pair = tuple(sorted((left.actor_id, right.actor_id)))
                facts.extend(
                    (
                        ReachSpatialFact(
                            subject_id=left.actor_id,
                            object_id=right.actor_id,
                            relation=(
                                "close"
                                if pair in close_pairs
                                else "reachable"
                                if separation <= left.reach
                                else "separated"
                            ),
                            provenance=provenance,
                        ),
                        VisibilitySpatialFact(
                            subject_id=left.actor_id,
                            object_id=right.actor_id,
                            visible=sight(encounter, left, right, board=board),
                            provenance=provenance,
                        ),
                        CoverSpatialFact(
                            subject_id=left.actor_id,
                            object_id=right.actor_id,
                            cover="none",
                            provenance=provenance,
                        ),
                        ObstacleSpatialFact(
                            subject_id=left.actor_id,
                            object_id=right.actor_id,
                            blocked=False,
                            provenance=provenance,
                        ),
                        RetreatSpatialFact(
                            subject_id=left.actor_id,
                            object_id=right.actor_id,
                            feasible=_hex_retreat_feasible(
                                state, encounter, left, right, board, occupied
                            ),
                            provenance=provenance,
                        ),
                    )
                )
    result = encounter.model_copy(
        update={
            "spatial_context": BasicSpatialContext(facts=tuple(facts)),
            "participants": participants,
        }
    )
    assert runtime.combat is not None
    runtime.combat.validate(
        result,
        state.world,
        state.resources,
        frozenset(actor.actor_id for actor in state.actors),
    )
    return result


def _hex_retreat_feasible(
    state: PlayState,
    encounter: Encounter,
    subject: Combatant,
    object_actor: Combatant,
    board: HexBattlefield,
    occupied: tuple[Occupant, ...],
) -> bool:
    """Return whether the current exact pose has any legal retreat hex."""
    hp = next(p for p in state.resources.pools if p.id == f"hp:{subject.actor_id}")
    context = RetreatContext(
        already_retreated=subject.retreat_used,
        stunned=bool(hp.injury and hp.injury.stunned),
        grappled=subject.grappled
        or subject.pinned
        or any(grip.holder_id == subject.actor_id for grip in encounter.grips),
        maneuver_allows_retreat=not subject.maneuver_state.defense_forbidden,
    )
    for direction in range(6):
        try:
            destination = neighbor(pose(subject).position, direction)
            board.cell(destination)
        except ValidationError, ValueError:
            continue
        if can_retreat(
            board,
            pose(subject),
            pose(object_actor).position,
            destination,
            context=context,
            occupants=occupied,
        ):
            return True
    return False


def prepare_defense(
    runtime: RulesContext, state: PlayState, encounter: Encounter, command: ChooseDefense
) -> Encounter:
    if isinstance(encounter.spatial, BasicSpatialContext):
        if command.retreat is not None:
            raise ValidationError("Basic retreat does not accept a hex destination")
        pending, unarmed = encounter.pending_defense, encounter.pending_unarmed
        attacker_id = pending.attacker_id if pending else unarmed.actor_id if unarmed else None
        defender_id = pending.defender_id if pending else unarmed.target_id if unarmed else None
        if defender_id != command.actor_id or attacker_id is None:
            raise ValidationError("Defense is unavailable")
        actor = next(p for p in encounter.participants if p.actor_id == attacker_id)
        target = next(p for p in encounter.participants if p.actor_id == defender_id)
        bonus = 0
        if command.basic_retreat:
            from wayfarer.engine.simulation.gurps_equipment import RangedMode
            from wayfarer.engine.simulation.mechanics.gurps_melee import mode

            hp = next(p for p in state.resources.pools if p.id == f"hp:{target.actor_id}")
            fact = encounter.spatial.active("retreat", target.actor_id, actor.actor_id)
            if (
                command.defense == "none"
                or command.second_defense is not None
                or target.retreat_used
                or target.posture in ("kneeling",)
                or bool(hp.injury and hp.injury.stunned)
                or target.grappled
                or target.pinned
                or any(g.holder_id == target.actor_id for g in encounter.grips)
                or target.maneuver_state.defense_forbidden
                or not isinstance(fact, RetreatSpatialFact)
                or not fact.feasible
                or pending
                and runtime.rules.combat is not None
                and runtime.rules.combat.gurps_equipment is not None
                and isinstance(
                    mode(runtime, state, actor.actor_id, pending.weapon_id, pending.mode_id),
                    RangedMode,
                )
            ):
                raise ValidationError("Retreat is unavailable")
            bonus = 3 if command.defense == "dodge" else 1
            target = target.model_copy(
                update={"retreat_used": True, "retreat_attacker_id": actor.actor_id}
            )
            encounter = CombatEngine._replace(encounter, target)
            encounter = move_basic(
                encounter,
                actor_id=target.actor_id,
                reference_actor_id=actor.actor_id,
                direction="withdraw",
                yards=max(1, (target.movement_allowance + 9) // 10),
                command_id=command.id,
                revision=state.revision,
                require_obstacle=False,
            )
        elif target.retreat_attacker_id == actor.actor_id and command.defense != "none":
            bonus = 3 if command.defense == "dodge" else 1
        return CombatEngine._replace(
            encounter, target.model_copy(update={"tactical_defense_bonus": bonus})
        )
    if encounter.spatial_kind != "hex":
        if command.retreat is not None or command.basic_retreat:
            raise ValidationError("Retreat requires the matching spatial representation")
        return encounter
    if command.basic_retreat:
        raise ValidationError("Hex retreat requires an explicit destination")
    pending, unarmed = encounter.pending_defense, encounter.pending_unarmed
    attacker_id = pending.attacker_id if pending else unarmed.actor_id if unarmed else None
    defender_id = pending.defender_id if pending else unarmed.target_id if unarmed else None
    if defender_id != command.actor_id or attacker_id is None:
        raise ValidationError("Defense is unavailable")
    actor = next(p for p in encounter.participants if p.actor_id == attacker_id)
    target = next(p for p in encounter.participants if p.actor_id == defender_id)
    bonus = defense_adjustment(encounter, actor, target) if command.defense != "none" else 0
    if command.retreat is not None:
        from wayfarer.engine.simulation.gurps_equipment import RangedMode
        from wayfarer.engine.simulation.mechanics.gurps_melee import mode

        if command.defense == "none" or command.second_defense is not None:
            raise ValidationError("Retreat requires one active defense")
        if pending and isinstance(
            mode(runtime, state, actor.actor_id, pending.weapon_id, pending.mode_id), RangedMode
        ):
            raise ValidationError("Retreat bonus is not available against ranged attacks")
        if unarmed and unarmed.action in ("grapple", "arm_lock"):
            raise ValidationError(
                "Retreat against control attacks requires following-grapple timing"
            )
        hp = next(p for p in state.resources.pools if p.id == f"hp:{target.actor_id}")
        context = RetreatContext(
            already_retreated=target.retreat_used,
            stunned=bool(hp.injury and hp.injury.stunned),
            grappled=target.grappled
            or target.pinned
            or any(g.holder_id == target.actor_id for g in encounter.grips),
            maneuver_allows_retreat=not target.maneuver_state.defense_forbidden,
        )
        if not can_retreat(
            runtime.require_hex(encounter),
            pose(target),
            pose(actor).position,
            command.retreat,
            context=context,
            occupants=occupants(encounter),
        ):
            raise ValidationError("Retreat is unavailable")
        bonus += 3 if command.defense == "dodge" else 1
    elif target.retreat_attacker_id == actor.actor_id and command.defense != "none":
        bonus += 3 if command.defense == "dodge" else 1
    if command.retreat is not None:
        target = target.model_copy(
            update={"retreat_used": True, "retreat_attacker_id": actor.actor_id}
        )
    return CombatEngine._replace(
        encounter, target.model_copy(update={"tactical_defense_bonus": bonus})
    )


def finish_defense(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    command: ChooseDefense,
) -> Encounter:
    if encounter.spatial_kind == "basic":
        target = next(p for p in encounter.participants if p.actor_id == command.actor_id)
        encounter = CombatEngine._replace(
            encounter, target.model_copy(update={"tactical_defense_bonus": 0})
        )
        pending = encounter.defense_history[-1].pending if encounter.defense_history else None
        if pending and pending.post_attack_basic_reference_id:
            attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
            encounter = move_basic(
                encounter,
                actor_id=attacker.actor_id,
                reference_actor_id=pending.post_attack_basic_reference_id,
                direction=pending.post_attack_basic_direction or "approach",
                yards=max(1, (attacker.movement_allowance + 9) // 10),
                command_id=command.id,
                revision=state.revision,
            )
        return encounter
    if encounter.spatial_kind != "hex":
        return encounter
    target = next(p for p in encounter.participants if p.actor_id == command.actor_id)
    target = target.model_copy(update={"tactical_defense_bonus": 0})
    if command.retreat is not None:
        target = target.model_copy(update={"position": command.retreat})
    encounter = CombatEngine._replace(encounter, target)
    pending = encounter.defense_history[-1].pending if encounter.defense_history else None
    if pending and (pending.post_attack_hex_path or pending.post_attack_facing is not None):
        from wayfarer.engine.simulation.tactical import move_hex

        attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
        attacker = move_hex(
            encounter,
            attacker,
            "attack",
            pending.post_attack_hex_path,
            pending.post_attack_facing,
            None,
            board=runtime.hex_map(encounter),
        )
        encounter = CombatEngine._replace(encounter, attacker)
    return encounter
