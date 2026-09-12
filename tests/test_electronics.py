"""Independent electronics expectations from Campaigns B471-472 and Characters B288-289."""

import pytest
from test_resources import engine as resource_engine

from wayfarer.engine.rules.types.electronics import (
    CommunicatorSpec,
    ComputerProgram,
    ComputerSpec,
    ElectronicsSuite,
    SensorSpec,
)
from wayfarer.engine.simulation.equipment.basic.catalog import BASIC_EQUIPMENT
from wayfarer.engine.simulation.equipment.electronics import (
    Communicate,
    CommunicationLink,
    Detect,
    DetectionTarget,
    ElectronicsContext,
    OperatorSkill,
    RunComputerTask,
    apply_electronics,
)
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import EquipmentSpec, Item, Owner, ResourceState
from wayfarer.engine.world import Entity, EntityKind, Fact, World
from wayfarer.errors import ValidationError


class NoDice:
    def randbelow(self, exclusive_upper_bound: int, /) -> int:
        raise AssertionError(f"unexpected d{exclusive_upper_bound} draw")


class FixedDice:
    def __init__(self, *values: int) -> None:
        self.values = iter(values)

    def randbelow(self, exclusive_upper_bound: int, /) -> int:
        value = next(self.values)
        assert 1 <= value <= exclusive_upper_bound
        return value - 1


def world() -> World:
    return World(
        entities=(
            Entity("a", EntityKind.ACTOR, "Operator"),
            Entity("b", EntityKind.ACTOR, "Recipient"),
            Entity("c", EntityKind.ACTOR, "Interceptor"),
            Entity("target", EntityKind.OBJECT, "Hidden target"),
        ),
        facts=(
            Fact("message", "a", "message", "rendezvous"),
            Fact("presence", "target", "detected", "present"),
            Fact("secret", "target", "contents", "not scanned"),
        ),
        knowledge=(("a", "message"),),
    )


def setup(
    definition_id: str, suite: ElectronicsSuite, *, charges: int = 10
) -> tuple[ResourceEngine, ResourceState]:
    engine = resource_engine()
    engine.specs[definition_id] = EquipmentSpec(
        definition_id=definition_id,
        unit_weight=1,
        stackable=False,
        electronics=suite,
    )
    state = ResourceState(
        items=(Item(id="device", definition_id=definition_id, owner_id="a", charges=charges),),
        owners=(Owner(actor_id="a", capacity=100), Owner(actor_id="b", capacity=100)),
    )
    engine.validate(state)
    return engine, state


def test_basic_catalog_pins_executable_device_profiles_and_operating_time() -> None:
    entries = {entry.definition_id: entry for entry in BASIC_EQUIPMENT.entries}
    radio = entries["equipment:hand-radio"].electronics
    laptop = entries["equipment:laptop"].electronics
    goggles = entries["equipment:night-vision-goggles"].electronics
    assert radio is not None and radio.communicator is not None
    assert (radio.communicator.range_yards, radio.power_capacity_seconds) == (3_520, 43_200)
    assert laptop is not None and laptop.computer is not None
    assert laptop.computer.program_capacity(3) == 2
    assert laptop.computer.program_capacity(2) == 20
    assert goggles is not None and goggles.sensor is not None
    assert (goggles.sensor.form, goggles.sensor.night_vision) == ("hands-free", 9)
    assert not any(
        blocker in {"battery-and-computer-operation", "special-tool-effects"}
        for profile in (
            entries["equipment:hand-radio"],
            entries["equipment:laptop"],
            entries["equipment:night-vision-goggles"],
        )
        for blocker in profile.unsupported_mechanics
    )


def test_no_power_or_operator_procedure_rejects_before_dice() -> None:
    suite = ElectronicsSuite(
        communicator=CommunicatorSpec(media=("voice",), range_yards=100),
        power_capacity_seconds=10,
    )
    engine, empty = setup("radio", suite, charges=0)
    command = Communicate(
        id="call",
        actor_id="a",
        expected_revision=0,
        item_id="device",
        link_id="link",
        fact_ids=("message",),
        medium="voice",
    )
    link = CommunicationLink(
        id="link", device_item_id="device", recipient_ids=("b",), distance_yards=110
    )
    context = ElectronicsContext(
        skills=(OperatorSkill(id="skill:electronics-operation-communications", level=12),),
        links=(link,),
    )
    with pytest.raises(ValidationError, match="power"):
        apply_electronics(engine, empty, world(), command, context, system=True, rng=NoDice())

    engine, powered = setup("radio", suite)
    with pytest.raises(ValidationError, match="operator procedure"):
        apply_electronics(
            engine,
            powered,
            world(),
            command,
            context.model_copy(update={"skills": ()}),
            system=True,
            rng=NoDice(),
        )


def test_communication_visibility_interception_and_retry_conserve_charge() -> None:
    suite = ElectronicsSuite(
        communicator=CommunicatorSpec(media=("voice",), range_yards=100),
        power_capacity_seconds=10,
    )
    engine, state = setup("radio", suite)
    command = Communicate(
        id="call",
        actor_id="a",
        expected_revision=0,
        item_id="device",
        link_id="link",
        fact_ids=("message",),
        medium="voice",
    )
    context = ElectronicsContext(
        skills=(OperatorSkill(id="skill:electronics-operation-communications", level=12),),
        links=(
            CommunicationLink(
                id="link",
                device_item_id="device",
                recipient_ids=("b",),
                interceptor_ids=("c",),
                distance_yards=110,
            ),
        ),
    )
    after, informed, outcome = apply_electronics(
        engine, state, world(), command, context, system=True, rng=FixedDice(3, 3, 3)
    )
    assert (outcome.status, outcome.effective_target) == ("succeeded", 11)
    assert outcome.interceptor_ids == ("c",)
    assert {fact.id for fact in informed.perspective("b").facts} == {"message"}
    assert {fact.id for fact in informed.perspective("c").facts} == {"message"}
    assert next(item for item in after.items if item.id == "device").charges == 9

    restored = ResourceState.model_validate_json(after.model_dump_json())
    replayed, unchanged_world, replay = apply_electronics(
        engine, restored, informed, command, context, system=True, rng=NoDice()
    )
    assert replayed == restored == after and unchanged_world == informed and replay == outcome
    assert next(item for item in replayed.items if item.id == "device").charges == 9


def test_sensor_reveals_only_authored_detection_facts() -> None:
    suite = ElectronicsSuite(
        sensor=SensorSpec(
            form="manual",
            sense="metal-detection",
            range_yards=10,
            active=True,
            new_sense=True,
        ),
        power_capacity_seconds=10,
    )
    engine, state = setup("sensor", suite)
    command = Detect(
        id="scan",
        actor_id="a",
        expected_revision=0,
        item_id="device",
        target_context_id="target-context",
    )
    context = ElectronicsContext(
        skills=(OperatorSkill(id="skill:electronics-operation-sensors", level=12),),
        targets=(
            DetectionTarget(
                id="target-context",
                device_item_id="device",
                target_id="target",
                fact_ids=("presence",),
                distance_yards=20,
            ),
        ),
    )
    after, detected, outcome = apply_electronics(
        engine, state, world(), command, context, system=True, rng=FixedDice(3, 3, 3)
    )
    assert (outcome.status, outcome.effective_target, outcome.dice) == ("succeeded", 10, (3, 3, 3))
    assert {fact.id for fact in detected.perspective("a").facts} == {"message", "presence"}
    assert "secret" not in {fact.id for fact in detected.perspective("a").facts}
    assert after.revision == 1


def test_computer_returns_task_support_without_executing_the_task() -> None:
    suite = ElectronicsSuite(
        computer=ComputerSpec(complexity=2, storage_megabytes=1_000),
        power_capacity_seconds=10,
    )
    engine, state = setup("computer", suite)
    program = ComputerProgram(
        id="accounts",
        complexity=2,
        storage_megabytes=100,
        task_skill_id="skill:accounting",
        mode="bonus",
        bonus=1,
        native_technology_level=8,
    )
    command = RunComputerTask(
        id="compute",
        actor_id="a",
        expected_revision=0,
        item_id="device",
        program_id=program.id,
        task_skill_id=program.task_skill_id,
        active_program_complexities=(2,),
    )
    context = ElectronicsContext(
        skills=(OperatorSkill(id="skill:computer-operation", level=11),), programs=(program,)
    )
    after, unchanged, outcome = apply_electronics(
        engine, state, world(), command, context, system=True, rng=NoDice()
    )
    assert unchanged == world()
    assert (outcome.status, outcome.task_modifier, outcome.dice) == ("supported", 1, None)
    assert next(item for item in after.items if item.id == "device").charges == 9

    with pytest.raises(ValidationError, match="capacity"):
        apply_electronics(
            engine,
            state,
            world(),
            command.model_copy(update={"active_program_complexities": (2, 2)}),
            context,
            system=True,
            rng=NoDice(),
        )
