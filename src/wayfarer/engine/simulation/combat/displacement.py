"""Selected Campaigns fourth printing B378 knockback; Characters B201 stance."""

from typing import Literal

from wayfarer.engine.character.traits.mastery import trained_by_master
from wayfarer.engine.rules.checks import CheckTrace, Modifier, ModifierKind, Outcome
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, fatigue_ready
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.tactical import transformed_footprint
from wayfarer.engine.simulation.combat.unarmed.fighters import fighter, settle_control
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.fatigue import fatigue_value
from wayfarer.engine.simulation.health.fright_state import can_defend
from wayfarer.engine.simulation.hex_geometry import DIRECTIONS, Hex, Pose
from wayfarer.engine.simulation.magic.effects import require_not_dazed
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError
from wayfarer.models import Record


class DisplacementResult(Record):
    potential_yards: int = 0
    moved_yards: int = 0
    prevented: bool = False
    fell: bool = False
    checks: tuple[CheckTrace, ...] = ()
    adjudication_required: Literal["collision-or-map-edge", "mapless-direction"] | None = None


def _step(source: GridPoint | Hex, current: GridPoint | Hex) -> GridPoint | Hex:
    if source == current:
        raise ValidationError("Knockback from a shared position requires an attack origin")
    if isinstance(source, Hex) and isinstance(current, Hex):
        dq, dr = current.q - source.q, current.r - source.r
        direction = max(
            range(6),
            key=lambda i: (2 * dq + dr) * DIRECTIONS[i][0] + (dq + 2 * dr) * DIRECTIONS[i][1],
        )
        return Hex(q=current.q + DIRECTIONS[direction][0], r=current.r + DIRECTIONS[direction][1])
    if isinstance(source, GridPoint) and isinstance(current, GridPoint):
        dx = (current.x > source.x) - (current.x < source.x)
        dy = (current.y > source.y) - (current.y < source.y)
        if dx and dy:
            raise ValidationError(
                "Square diagonal knockback requires explicit geometric adjudication"
            )
        return GridPoint(x=current.x + dx, y=current.y + dy)
    raise ValidationError("Mapped knockback requires matching coordinate systems")


def _clear(
    runtime: RulesContext, encounter: Encounter, target_id: str, point: GridPoint | Hex
) -> bool:
    target = fighter(encounter, target_id)
    occupied = (
        {
            p
            for actor in encounter.participants
            if actor.actor_id != target_id
            for p in encounter.occupied_hexes(actor.actor_id)
        }
        if isinstance(point, Hex)
        else {actor.position for actor in encounter.participants if actor.actor_id != target_id}
    )
    if isinstance(point, Hex):
        board = runtime.require_hex(encounter)
        assert target.hex_facing is not None
        footprint = transformed_footprint(
            encounter, target_id, Pose(position=point, facing=target.hex_facing)
        )
        try:
            return all(not board.cell(p).blocked and p not in occupied for p in footprint)
        except ValidationError:
            return False
    assert runtime.rules.combat is not None
    square_board = next(
        (b for b in runtime.rules.combat.battlefields if b.id == encounter.battlefield_id), None
    )
    return (
        isinstance(square_board, Battlefield)
        and point.x < square_board.width
        and point.y < square_board.height
        and point not in square_board.blocked
        and point not in occupied
    )


def require_immovable(
    runtime: RulesContext, state: PlayState, encounter: Encounter, actor_id: str
) -> None:
    """Validate an elected B201 reaction before any enclosing command rolls dice."""
    compiled = build(runtime, state, actor_id)
    hp = next(p for p in state.resources.pools if p.id == "hp:" + actor_id)
    actor = fighter(encounter, actor_id)
    setup = next(a for a in state.actors if a.actor_id == actor_id)
    require_not_dazed(state.resources, actor_id)
    if (
        not trained_by_master(compiled)
        or not any(p.definition_id == "skill:immovable-stance" for p in compiled.purchases)
        or not any(v.target == "skill:immovable-stance" for v in compiled.sheet.values)
        or hp.injury is None
        or hp.injury.incapacitated
        or "unconscious" in setup.conditions
        or not can_defend(state.resources, actor_id)
        or not fatigue_ready(state, actor_id)
        or (actor.personal_flight is not None and actor.personal_flight.altitude > 0)
    ):
        raise ValidationError("Immovable Stance requires a conscious grounded trained defender")


def _directed_step(current: GridPoint | Hex, direction: tuple[int, int]) -> GridPoint | Hex:
    dx, dy = direction
    return (
        Hex(q=current.q + dx, r=current.r + dy)
        if isinstance(current, Hex)
        else GridPoint(x=current.x + dx, y=current.y + dy)
    )


def potential_knockback(
    runtime: RulesContext,
    state: PlayState,
    target_id: str,
    basic_damage: int,
    *,
    damage_type: Literal["cr", "cut"] = "cr",
    penetrated: bool = False,
    resisting: bool = True,
) -> int:
    """B378 whole yards before any optional reaction or balance randomness."""
    if basic_damage < 0:
        raise ValidationError("Knockback requires nonnegative basic damage")
    compiled = build(runtime, state, target_id)
    assert compiled.statistics is not None
    hp = next(p for p in state.resources.pools if p.id == "hp:" + target_id)
    fp = next(p for p in state.resources.pools if p.id == "fp:" + target_id)
    assert hp.injury is not None
    strength = (
        fatigue_value(fp, compiled.statistics.st)
        if resisting and not hp.injury.incapacitated and fatigue_ready(state, target_id)
        else hp.maximum
    )
    return basic_damage // max(1, strength - 2) if damage_type == "cr" or not penetrated else 0


def displace(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    *,
    source_id: str,
    target_id: str,
    basic_damage: int,
    damage_type: Literal["cr", "cut"] = "cr",
    penetrated: bool = False,
    resisting: bool = True,
    use_immovable: bool = False,
    attack_origin: GridPoint | Hex | None = None,
    attack_direction: tuple[int, int] | None = None,
) -> tuple[PlayState, Encounter, DisplacementResult]:
    """Move real placements; defer unrepresented collision consequences explicitly."""
    if basic_damage < 0:
        raise ValidationError("Knockback requires nonnegative basic damage")
    source, target = fighter(encounter, source_id), fighter(encounter, target_id)
    if use_immovable:
        require_immovable(runtime, state, encounter, target_id)
    if attack_direction is not None and attack_direction not in (
        DIRECTIONS
        if isinstance(target.runtime_position, Hex)
        else ((1, 0), (-1, 0), (0, 1), (0, -1))
    ):
        raise ValidationError("Knockback direction must be one adjacent mapped step")
    compiled = build(runtime, state, target_id)
    assert compiled.statistics is not None
    hp = next(p for p in state.resources.pools if p.id == "hp:" + target_id)
    assert hp.injury is not None
    yards = potential_knockback(
        runtime,
        state,
        target_id,
        basic_damage,
        damage_type=damage_type,
        penetrated=penetrated,
        resisting=resisting,
    )
    if not yards:
        return state, encounter, DisplacementResult()
    levels = {v.target: int(v.value) for v in compiled.sheet.values}
    balance = (
        4
        if any(p.definition_id == "trait:advantage:perfect-balance" for p in compiled.purchases)
        else 0
    )
    checks: tuple[CheckTrace, ...] = ()
    fallen = False
    if use_immovable:
        roll = success_roll(
            "gurps-basic-set-4e-2004",
            levels["skill:immovable-stance"],
            check_modifiers(state.resources, target_id, "dx")
            + (
                Modifier(
                    balance - yards,
                    "Immovable Stance potential knockback",
                    "skill:immovable-stance",
                    "B201",
                    ModifierKind.SITUATIONAL,
                ),
            ),
            rng=runtime.rng,
        )
        checks += (roll,)
        if roll.outcome.succeeded:
            return (
                state,
                encounter,
                DisplacementResult(potential_yards=yards, prevented=True, checks=checks),
            )
        fallen = roll.outcome is Outcome.CRITICAL_FAILURE
    if not fallen and not hp.injury.incapacitated and fatigue_ready(state, target_id):
        target_level = max(
            levels.get("attribute:dx", compiled.statistics.dx),
            levels.get("skill:acrobatics", 0),
            levels.get("skill:judo", 0),
        )
        roll = success_roll(
            "gurps-basic-set-4e-2004",
            target_level,
            check_modifiers(state.resources, target_id, "dx")
            + (
                Modifier(
                    balance - max(0, yards - 1),
                    "Knockback balance",
                    "combat:knockback",
                    "B378",
                    ModifierKind.SITUATIONAL,
                ),
            ),
            rng=runtime.rng,
        )
        checks += (roll,)
        fallen = not roll.outcome.succeeded
    current = target.runtime_position
    moved = 0
    adjudication: Literal["collision-or-map-edge", "mapless-direction"] | None = None
    origin = attack_origin or source.runtime_position
    if current is None or origin is None:
        adjudication = "mapless-direction"
    else:
        for _ in range(yards):
            try:
                if attack_direction is not None:
                    step = _directed_step(current, attack_direction)
                else:
                    step = _step(origin, current)
                clear = _clear(runtime, encounter, target_id, step)
            except ValueError, ValidationError:
                clear = False
            if not clear:
                adjudication = "collision-or-map-edge"
                break
            current = step
            moved += 1
    target = target.model_copy(
        update={"position": current, "posture": "prone" if fallen else target.posture}
    )
    encounter = settle_control(state, CombatEngine._replace(encounter, target))
    if fallen:
        hp = hp.model_copy(update={"injury": hp.injury.model_copy(update={"prone": True})})
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "pools": tuple(hp if p.id == hp.id else p for p in state.resources.pools)
                    }
                )
            }
        )
    if adjudication:
        encounter = encounter.model_copy(
            update={"blocked_reason": "Knockback requires " + adjudication + " adjudication"}
        )
    return (
        state,
        encounter,
        DisplacementResult(
            potential_yards=yards,
            moved_yards=moved,
            fell=fallen,
            checks=checks,
            adjudication_required=adjudication,
        ),
    )
