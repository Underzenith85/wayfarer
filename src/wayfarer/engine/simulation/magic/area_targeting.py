"""B239: a mapped Area is a fixed surface, not the actor used to locate it."""

from typing import Literal

from wayfarer.engine.rules.magic.protocols import AreaSelection, hex_area, square_area
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.hex_geometry import Hex, SightPoint, line_of_sight
from wayfarer.engine.simulation.magic.bindings import SpellChannel
from wayfarer.engine.simulation.magic.casting_targeting import targeting
from wayfarer.engine.simulation.magic.spell_state import RuntimeSpellEffect
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand, active_spells, latest
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError
from wayfarer.models import Record


class AreaTarget(Record):
    selection: AreaSelection
    radius: int
    geometry: Literal["square", "hex"]
    distance: int
    unseen: bool


def enabled(state: PlayState, command: RuntimeSpellCommand, generation: bool) -> bool:
    if not generation or command.spell_id not in ("create-fire", "awaken"):
        return False
    effect = latest(state.resources).get(command.cast_id)
    original = targeting(state.resources, command.cast_id)
    return effect is None or bool(original and original.area)


def cells(selection: AreaSelection, radius: int, geometry: str) -> frozenset[tuple[int, int]]:
    full = (hex_area if geometry == "hex" else square_area)(
        selection.model_copy(update={"cells": ()}), radius
    )
    selected = frozenset(selection.cells) if selection.cells else full
    if not selected or not selected <= full or selection.height_yards != 4:
        raise ValidationError("Selected Area must stay within its paid radius and four-yard height")
    return selected


def _lit(state: PlayState, encounter: Encounter, point: GridPoint | Hex, darkness: int) -> bool:
    sources = tuple(
        effect
        for effect in active_spells(state.resources)
        if effect.spell_id == "light"
        and effect.execute_effects
        and effect.encounter_id == encounter.id
        and effect.position is not None
        and CombatEngine.distance(
            Hex(q=effect.position[0], r=effect.position[1])
            if effect.geometry == "hex"
            else GridPoint(x=effect.position[0], y=effect.position[1]),
            point,
        )
        <= effect.light_radius
    )
    if any(effect.reversed for effect in sources):
        return False
    return darkness > -10 or bool(sources)


def _mapped_points(
    runtime: RulesContext, encounter: Encounter, selected: frozenset[tuple[int, int]], geometry: str
) -> tuple[GridPoint | Hex, ...]:
    if encounter.spatial_kind == "basic":
        raise ValidationError("Area surface targeting requires an authoritative square or hex map")
    field = (
        next(
            (f for f in runtime.rules.combat.battlefields if f.id == encounter.battlefield_id), None
        )
        if runtime.rules.combat
        else None
    )
    points: tuple[GridPoint | Hex, ...]
    if geometry == "square":
        if not isinstance(field, Battlefield) or any(
            not (0 <= x < field.width and 0 <= y < field.height) for x, y in selected
        ):
            raise ValidationError("Affected Area surface is outside the battlefield")
        points = tuple(GridPoint(x=x, y=y) for x, y in selected)
    else:
        board = runtime.require_hex(encounter)
        points = tuple(Hex(q=q, r=r) for q, r in selected)
        for point in points:
            assert isinstance(point, Hex)
            board.cell(point)
    return points


def retarget_area(
    runtime: RulesContext,
    state: PlayState,
    effect: RuntimeSpellEffect,
    encounter: Encounter | None,
    target_position: tuple[int, int] | None,
    proposed_center: tuple[int, int] | None,
) -> AreaSelection | None:
    """B236 retargeting moves the real surface and must include the chosen victim."""
    original = targeting(state.resources, effect.cast_id)
    if original is None or not original.area:
        return None
    if effect.area is None or encounter is None or target_position is None:
        raise ValidationError("Area backfire requires its recorded surface and current map")
    center = proposed_center if proposed_center is not None else target_position
    dx, dy = center[0] - effect.area.center[0], center[1] - effect.area.center[1]
    translated = effect.area.model_copy(
        update={
            "center": center,
            "cells": tuple((x + dx, y + dy) for x, y in effect.area.cells),
        }
    )
    if translated == effect.area:
        raise ValidationError("An intended Area result must be rerolled")
    selected = cells(translated, effect.radius, effect.geometry)
    if target_position not in selected:
        raise ValidationError("Area retarget requires a reviewed center that affects its target")
    _mapped_points(runtime, encounter, selected, effect.geometry)
    return translated


def resolve(
    runtime: RulesContext,
    state: PlayState,
    command: RuntimeSpellCommand,
    channel: SpellChannel,
    encounter: Encounter,
) -> AreaTarget:
    actor = next(p for p in encounter.participants if p.actor_id == command.actor_id)
    geometry: Literal["square", "hex"] = "hex" if isinstance(actor.position, Hex) else "square"
    effect = latest(state.resources).get(command.cast_id)
    selection = effect.area if effect is not None else channel.area
    radius = effect.radius if effect is not None else command.radius
    if selection is None:
        if channel.target_id not in {
            e.id for e in state.world.perspective(command.actor_id).entities
        }:
            raise ValidationError("Area anchor is not perceived")
        subject = next((p for p in encounter.participants if p.actor_id == channel.target_id), None)
        if subject is None:
            raise ValidationError("Area casting requires an authored surface or current placement")
        point = subject.position
        selection = AreaSelection(
            center=(point.q, point.r) if isinstance(point, Hex) else (point.x, point.y)
        )
    selected = cells(selection, radius, geometry)
    field = (
        next(
            (f for f in runtime.rules.combat.battlefields if f.id == encounter.battlefield_id), None
        )
        if runtime.rules.combat
        else None
    )
    points = _mapped_points(runtime, encounter, selected, geometry)
    distance = min(CombatEngine.distance(actor.position, point) for point in points)
    # Being on an affected ground cell provides physical contact even without sight.
    touching = distance == 0
    # deferred: share current physical sight with the personal and item cast consumers.
    from wayfarer.engine.simulation.magic.staff_casting import _blind

    visible = not _blind(state, command.actor_id)
    if geometry == "hex" and visible:
        assert isinstance(actor.position, Hex)
        board = runtime.require_hex(encounter)
        visible = any(
            _lit(
                state,
                encounter,
                Hex(q=q, r=r),
                min(board.darkness_penalty, encounter.darkness_penalty),
            )
            and line_of_sight(
                board,
                SightPoint(position=actor.position, height=0 if actor.posture == "prone" else 1),
                SightPoint(position=Hex(q=q, r=r), height=0),
            )
            for q, r in selected
        )
    elif isinstance(field, Battlefield):
        visible = visible and any(
            _lit(state, encounter, point, min(field.darkness_penalty, encounter.darkness_penalty))
            for point in points
        )
    return AreaTarget(
        selection=selection,
        radius=radius,
        geometry=geometry,
        distance=distance,
        unseen=not (visible or touching),
    )
