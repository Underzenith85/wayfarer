"""Independent printed B253 gallon, stream and material-state expectations."""

import pytest
from pydantic import ValidationError as ModelValidationError

from wayfarer.engine.simulation.magic.water_effects import (
    DISCOVERY_PREFIX,
    WaterDiscovery,
    WaterPlan,
    apply,
    parameters,
    validate_operation,
)
from wayfarer.engine.simulation.magic.water_state import WaterBody, declare, latest, save
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.world import Entity, EntityKind, World
from wayfarer.errors import ValidationError


def body(name: str, gallons: int, pure: int = 0, **extra: object) -> WaterBody:
    return WaterBody.model_validate(
        dict(
            object_id=name,
            location_id="room",
            gallons=gallons,
            pure_gallons=pure,
            nature="fresh water",
            **extra,
        )
    )


def state(*bodies: WaterBody) -> ResourceState:
    result = ResourceState()
    for value in bodies:
        result = save(result, value, "seed:" + value.object_id)
    return result


def test_create_water_fills_actual_container_permanently() -> None:
    initial = state(body("pail", 1, 1, capacity_gallons=5))
    plan = WaterPlan(spell_id="create-water", target_id="pail", gallons=3)
    assert parameters(initial, plan) == (6, 1, 0)
    result = apply(initial, plan, "caster", "create")
    assert (latest(result)["pail"].gallons, latest(result)["pail"].pure_gallons) == (4, 4)
    assert latest(result.model_copy(update={"game_time": 100000}))["pail"].gallons == 4
    assert latest(initial)["pail"].gallons == 1
    assert ResourceState.model_validate_json(result.model_dump_json()) == result


def test_purify_moves_dirty_water_and_removes_impurity() -> None:
    initial = state(body("pond", 8), body("pail", 0, capacity_gallons=4))
    plan = WaterPlan(
        spell_id="purify-water",
        target_id="pail",
        source_id="pond",
        gallons=3,
        seconds_per_gallon=10,
        flowing_through_ring=True,
    )
    assert parameters(initial, plan) == (3, 30, 0)
    result = apply(initial, plan, "caster", "purify")
    assert latest(result)["pond"].gallons == 5
    assert latest(result)["pond"].pure_gallons == 0
    assert latest(result)["pail"].gallons == latest(result)["pail"].pure_gallons == 3
    assert sum(v.gallons for v in latest(result).values()) == 8


@pytest.mark.parametrize(
    "override",
    [
        {"flowing_through_ring": False},
        {"gallons": 9},
        {"source_id": "pail"},
        {"source_id": "absent"},
    ],
)
def test_purification_restrictions_leave_source_unchanged(override: dict[str, object]) -> None:
    initial = state(body("pond", 8), body("pail", 0, capacity_gallons=10))
    plan = WaterPlan(
        spell_id="purify-water", target_id="pail", source_id="pond", flowing_through_ring=True
    ).model_copy(update=override)
    with pytest.raises(ValidationError):
        apply(initial, plan, "caster", "invalid")
    assert latest(initial)["pond"].gallons == 8


def test_seek_nearest_significant_source_exclusions_and_private_discovery() -> None:
    initial = state(
        body("drop", 1, significant=False),
        body("well", 100, significant=True, position=(3, 4)),
        body("lake", 1000, significant=True, position=(240, 0)),
    )
    plan = WaterPlan(spell_id="seek-water", target_id="caster", forked_stick=False)
    assert parameters(initial, plan) == (2, 1, -3)
    result = apply(initial, plan, "caster", "seek")
    discovery = WaterDiscovery.model_validate_json(
        next(e.kind for e in result.events if e.id.startswith(DISCOVERY_PREFIX))
    )
    assert discovery.actor_id == "caster"
    assert (discovery.source_id, discovery.direction, discovery.distance_yards) == (
        "well",
        (3, 4),
        5,
    )
    assert discovery.nature == "fresh water"
    excluded = plan.model_copy(update={"excluded_source_ids": ("well",)})
    assert parameters(initial, excluded) == (2, 1, -4)
    far = apply(initial, excluded, "caster", "far")
    assert WaterDiscovery.model_validate_json(far.events[-1].kind).source_id == "lake"


def test_destroy_actual_water_all_forms_only_selected_isolated_portions() -> None:
    initial = state(
        body("ice", 10, form="ice"),
        body("steam", 4, form="steam"),
        body("outside", 9, position=(2, 0)),
    )
    plan = WaterPlan(
        spell_id="destroy-water", target_id="ice", radius=2, destroyed_ids=("ice", "steam")
    )
    assert parameters(initial, plan) == (6, 1, 0)
    result = apply(initial, plan, "caster", "destroy")
    assert latest(result)["ice"].gallons == latest(result)["steam"].gallons == 0
    assert latest(result)["outside"].gallons == 9


@pytest.mark.parametrize(
    "extra", [{"depth_yards": 3}, {"surrounding_water": True}, {"position": (1, 0)}]
)
def test_destroy_rejects_unbounded_depth_refill_or_area(extra: dict[str, object]) -> None:
    initial = state(WaterBody.model_validate({**body("lake", 1000).model_dump(), **extra}))
    with pytest.raises(ValidationError):
        validate_operation(
            initial, WaterPlan(spell_id="destroy-water", target_id="lake", destroyed_ids=("lake",))
        )


def test_no_living_body_can_be_authored_as_water() -> None:
    world = World(
        entities=(
            Entity("room", EntityKind.LOCATION, "Room"),
            Entity("victim", EntityKind.ACTOR, "Victim", "room"),
        )
    )
    with pytest.raises(ValidationError, match="nonliving"):
        declare(world, ResourceState(), body("victim", 3), "invalid")


def test_material_quantities_reject_invalid_purity_and_capacity() -> None:
    with pytest.raises(ModelValidationError):
        body("pail", 2, 3)
    with pytest.raises(ModelValidationError):
        body("pail", 3, capacity_gallons=2)


def test_destroy_cannot_spare_other_water_in_same_selected_area() -> None:
    initial = state(body("pail", 1), body("steam", 1, form="steam"))
    with pytest.raises(ValidationError, match="all authored water"):
        apply(
            initial,
            WaterPlan(spell_id="destroy-water", target_id="pail", destroyed_ids=("pail",)),
            "caster",
            "invalid",
        )


def test_private_plan_cannot_be_substituted_between_casting_seconds() -> None:
    from wayfarer.engine.simulation.magic.water_cast_state import (
        WaterCastPlan,
        remember,
        require_plan,
    )
    from wayfarer.errors import ConflictError

    initial = ResourceState()
    commitment = WaterCastPlan(
        cast_id="cast",
        actor_id="caster",
        channel_id="original",
        plan=WaterPlan(spell_id="create-water", target_id="pail", gallons=2),
    )
    committed = remember(initial, commitment)
    require_plan(committed, commitment, starting=False)
    with pytest.raises(ConflictError):
        require_plan(
            committed, commitment.model_copy(update={"channel_id": "replacement"}), starting=False
        )
    with pytest.raises(ConflictError):
        require_plan(
            committed,
            commitment.model_copy(
                update={"plan": commitment.plan.model_copy(update={"gallons": 3})}
            ),
            starting=False,
        )
    with pytest.raises(ConflictError):
        require_plan(initial, commitment, starting=False)
    assert remember(committed, commitment) == committed


def test_water_never_runs_without_exact_private_material_protocol() -> None:
    from test_spells import context
    from test_spells import state as casting_state

    from wayfarer.engine.rules.checks import RecordedDice
    from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand, apply_spell

    command = RuntimeSpellCommand(
        id="invalid",
        actor_id="a",
        expected_revision=0,
        kind="start",
        spell_id="create-water",
        cast_id="cast",
        channel_id="water",
    )
    before = casting_state()
    with pytest.raises(ValidationError, match="private material commitment"):
        apply_spell(before, command, context(), system=True, rng=RecordedDice(()))
    assert before.revision == 0
