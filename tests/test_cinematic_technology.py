"""Source-defined B184/B228 effects, canonical builds and durable state consumers."""

import pytest
from test_cinematic_skills import compiler
from test_statistics import gurps_draft

from wayfarer.engine.character.compiler import Purchase, ValidatedBuild
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.skills.computer_hacking import (
    ComputerHackingCommand,
    ComputerSystem,
    apply_computer_hacking,
    has_computer_access,
)
from wayfarer.engine.world import Entity, EntityKind, Fact, World
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError


def hacking_build() -> ValidatedBuild:
    result = compiler().compile(
        gurps_draft(
            Purchase(definition_id="skill:computer-programming", amount=4),
            Purchase(definition_id="skill:computer-hacking", amount=4, technology_level=8),
        )
    )
    assert result.build is not None, result.diagnostics
    return result.build


def computer_world() -> World:
    return World(
        entities=(
            Entity("a", EntityKind.ACTOR, "Operator", "room"),
            Entity("b", EntityKind.ACTOR, "Observer", "room"),
            Entity("room", EntityKind.LOCATION, "Room"),
            Entity("server", EntityKind.OBJECT, "Server", "room"),
        ),
        facts=(Fact("secret", "server", "plans", "original"),),
    )


SYSTEM = ComputerSystem(
    id="server", technology_level=8, reachable_actor_ids=("a",), information_fact_ids=("secret",)
)


def hack(
    state: ResourceState, build: ValidatedBuild, identifier: str, **changes: object
) -> ComputerHackingCommand:
    return ComputerHackingCommand.model_validate(
        {
            "id": identifier,
            "actor_id": "a",
            "expected_revision": state.revision,
            "build_revision": build.revision,
            "system_id": "server",
            **changes,
        }
    )


def test_hacking_prerequisite_is_real_programming_purchase() -> None:
    result = compiler().compile(
        gurps_draft(Purchase(definition_id="skill:computer-hacking", amount=4, technology_level=8))
    )
    assert not result.legal
    assert hacking_build().statistics is not None


def test_intrusion_changes_access_information_and_actual_known_world() -> None:
    state, world, build = ResourceState(), computer_world(), hacking_build()
    state, world, _ = apply_computer_hacking(
        state,
        world,
        build,
        hack(state, build, "access"),
        SYSTEM,
        authorized_actor_id="a",
        rng=RecordedDice([2, 2, 2]),
    )
    assert has_computer_access(world, "a", "server") and not has_computer_access(
        world, "b", "server"
    )
    assert "secret" not in {f.id for f in world.perspective("a").facts}
    state, world, _ = apply_computer_hacking(
        state,
        world,
        build,
        hack(state, build, "find", operation="find", information_id="secret"),
        SYSTEM,
        authorized_actor_id="a",
        rng=RecordedDice([2, 2, 2]),
    )
    assert next(f.value for f in world.perspective("a").facts if f.id == "secret") == "original"
    change = hack(
        state,
        build,
        "change",
        operation="change",
        information_id="secret",
        replacement_value="changed",
    )
    state, world, result = apply_computer_hacking(
        state, world, build, change, SYSTEM, authorized_actor_id="a", rng=RecordedDice([2, 2, 2])
    )
    assert next(f.value for f in world.facts if f.id == "secret") == "changed"
    assert "secret" not in {f.id for f in world.perspective("b").facts}
    restored_state = ResourceState.model_validate_json(state.model_dump_json())
    assert apply_computer_hacking(
        restored_state, world, build, change, SYSTEM, authorized_actor_id="a", rng=RecordedDice([])
    ) == (restored_state, world, result)
    with pytest.raises(AuthorizationError):
        apply_computer_hacking(
            state, world, build, change, SYSTEM, authorized_actor_id="b", rng=RecordedDice([])
        )
    with pytest.raises(ConflictError):
        apply_computer_hacking(
            state,
            world,
            build,
            change.model_copy(update={"replacement_value": "other"}),
            SYSTEM,
            authorized_actor_id="a",
            rng=RecordedDice([]),
        )


@pytest.mark.parametrize(("dice", "evidence"), [([4, 4, 4], False), ([6, 6, 6], True)])
def test_failed_intrusion_never_grants_access_and_critical_failure_leaves_evidence(
    dice: list[int], evidence: bool
) -> None:
    state, world, build = ResourceState(), computer_world(), hacking_build()
    changed, after, _ = apply_computer_hacking(
        state,
        world,
        build,
        hack(state, build, "failed"),
        SYSTEM,
        authorized_actor_id="a",
        rng=RecordedDice(dice),
    )
    assert not has_computer_access(after, "a", "server")
    assert (
        any(f.predicate == "intrusion-attempt" and f.value == "a" for f in after.facts) is evidence
    )
    assert changed.pools == state.pools


def test_access_and_information_boundaries_reject_without_dice() -> None:
    state, world, build = ResourceState(), computer_world(), hacking_build()
    with pytest.raises(ValidationError):
        apply_computer_hacking(
            state,
            world,
            build,
            hack(state, build, "no-access", operation="find", information_id="secret"),
            SYSTEM,
            authorized_actor_id="a",
            rng=RecordedDice([]),
        )
    with pytest.raises(ValidationError):
        apply_computer_hacking(
            state,
            world,
            build,
            hack(state, build, "wrong-system", system_id="other"),
            SYSTEM,
            authorized_actor_id="a",
            rng=RecordedDice([]),
        )


def test_weird_science_bonus_changes_actual_invention_eligibility_then_expires() -> None:
    from test_actions import world
    from test_cinematic_skills import approved_weird_science
    from test_invention_projects import complete_phase, create, setup

    from wayfarer.engine.simulation.skills.weird_science import (
        WeirdScienceCommand,
        apply_weird_science,
    )

    reducer, runtime, initial = setup((4, 4, 4))
    state = create(runtime, initial)
    unaided, _ = complete_phase(reducer, runtime, state, "concept-design", "plain")
    assert unaided.resources.inventions[0].phase == "concept-design"
    build = approved_weird_science()
    command = WeirdScienceCommand(
        id="theory",
        actor_id="a",
        expected_revision=state.resources.revision,
        build_revision=build.revision,
        project_id="scanner-project",
    )
    assert runtime.rules.inventions is not None
    blueprint = runtime.rules.inventions.blueprints[0]
    aided, result = apply_weird_science(
        state.resources,
        world(),
        build,
        command,
        blueprint,
        authorized_actor_id="a",
        rng=RecordedDice([2, 2, 2]),
    )
    restored = ResourceState.model_validate_json(aided.model_dump_json())
    assert apply_weird_science(
        restored, world(), build, command, blueprint, authorized_actor_id="a", rng=RecordedDice([])
    ) == (restored, result)
    state = state.model_copy(update={"resources": restored, "revision": state.revision + 1})
    from test_invention_projects import dice

    aided_state, outcome = complete_phase(
        reducer, dice(runtime, (4, 4, 4)), state, "concept-design", "aided"
    )
    assert outcome.check is not None and any(
        m.value == 5 and m.reason == "Weird Science" for m in outcome.check.modifiers
    )
    assert aided_state.resources.inventions[0].phase == "prototype"
    assert aided_state.resources.inventions[0].weird_science is None
    assert aided_state.resources.pools == unaided.resources.pools


@pytest.mark.parametrize(
    ("method", "purpose", "bonus"),
    [("gadgeteer", "invention", 1), ("quick-gadgeteer", "investigation", 2)],
)
def test_weird_science_gadgeteer_and_investigation_bonus_reaches_real_check(
    method: str, purpose: str, bonus: int
) -> None:
    from test_actions import world
    from test_cinematic_skills import approved_weird_science
    from test_invention_projects import complete_phase, create, dice, gadget_setup

    from wayfarer.engine.simulation.skills.weird_science import (
        WeirdScienceCommand,
        apply_weird_science,
    )

    reducer, runtime, state = gadget_setup(
        method=method, activity="analysis" if purpose == "investigation" else "invention"
    )
    state = create(runtime, state)
    assert runtime.rules.inventions is not None
    blueprint = runtime.rules.inventions.blueprints[0]
    build = approved_weird_science()
    command = WeirdScienceCommand.model_validate(
        {
            "id": "theory",
            "actor_id": "a",
            "expected_revision": state.resources.revision,
            "build_revision": build.revision,
            "project_id": "scanner-project",
            "purpose": purpose,
        }
    )
    resources, _ = apply_weird_science(
        state.resources,
        world(),
        build,
        command,
        blueprint,
        authorized_actor_id="a",
        rng=RecordedDice([2, 2, 2]),
    )
    state = state.model_copy(update={"resources": resources, "revision": state.revision + 1})
    state, outcome = complete_phase(
        reducer,
        dice(runtime, (1, 3, 3, 3) if purpose == "investigation" else (3, 3, 3)),
        state,
        "concept-design",
        "aided",
    )
    assert outcome.check is not None and any(
        m.value == bonus and m.reason == "Weird Science" for m in outcome.check.modifiers
    )
    assert state.resources.inventions[0].phase == (
        "complete" if purpose == "investigation" else "prototype"
    )


def test_weird_science_critical_failure_requires_explicit_gm_consequence_resolution() -> None:
    from test_actions import world
    from test_cinematic_skills import approved_weird_science
    from test_invention_projects import complete_phase, create, dice, setup

    from wayfarer.engine.simulation.skills.weird_science import (
        WeirdScienceAdjudication,
        WeirdScienceCommand,
        acknowledge_weird_science_failure,
        apply_weird_science,
    )

    reducer, runtime, state = setup()
    state = create(runtime, state)
    assert runtime.rules.inventions is not None
    blueprint = runtime.rules.inventions.blueprints[0]
    build = approved_weird_science()
    command = WeirdScienceCommand(
        id="spectacular",
        actor_id="a",
        expected_revision=state.resources.revision,
        build_revision=build.revision,
        project_id="scanner-project",
    )
    resources, outcome = apply_weird_science(
        state.resources,
        world(),
        build,
        command,
        blueprint,
        authorized_actor_id="a",
        rng=RecordedDice([6, 6, 6]),
    )
    assert (
        outcome.outcome == "critical-failure" and resources.inventions[0].weird_science is not None
    )
    state = state.model_copy(update={"resources": resources, "revision": state.revision + 1})
    with pytest.raises(ValidationError, match="GM adjudication"):
        complete_phase(reducer, dice(runtime, ()), state, "concept-design", "blocked")
    resolution = WeirdScienceAdjudication(
        id="gm-resolution",
        expected_revision=resources.revision,
        project_id="scanner-project",
        resolution="The laboratory fills with conspicuous but harmless purple smoke.",
    )
    with pytest.raises(AuthorizationError):
        acknowledge_weird_science_failure(resources, resolution, authorized_gm=False)
    acknowledged = acknowledge_weird_science_failure(resources, resolution, authorized_gm=True)
    assert (
        acknowledge_weird_science_failure(acknowledged, resolution, authorized_gm=True)
        == acknowledged
    )
    with pytest.raises(ConflictError):
        acknowledge_weird_science_failure(
            acknowledged,
            resolution.model_copy(update={"resolution": "changed"}),
            authorized_gm=True,
        )
    assert (
        acknowledged.inventions[0].weird_science is not None
        and not acknowledged.inventions[0].weird_science.adjudication_required
    )


def test_weird_science_invalid_actor_missing_skill_and_stale_attempt_reject_before_dice() -> None:
    from test_actions import world
    from test_cinematic_skills import approved_weird_science
    from test_invention_projects import create, setup

    from wayfarer.engine.simulation.skills.weird_science import (
        WeirdScienceCommand,
        apply_weird_science,
    )

    _, runtime, state = setup()
    state = create(runtime, state)
    assert runtime.rules.inventions is not None
    blueprint = runtime.rules.inventions.blueprints[0]
    build = approved_weird_science()
    command = WeirdScienceCommand(
        id="theory",
        actor_id="a",
        expected_revision=state.resources.revision,
        build_revision=build.revision,
        project_id="scanner-project",
    )
    with pytest.raises(AuthorizationError):
        apply_weird_science(
            state.resources,
            world(),
            build,
            command,
            blueprint,
            authorized_actor_id="b",
            rng=RecordedDice([]),
        )
    with pytest.raises(ConflictError):
        apply_weird_science(
            state.resources,
            world(),
            build,
            command.model_copy(update={"expected_revision": 999}),
            blueprint,
            authorized_actor_id="a",
            rng=RecordedDice([]),
        )
    untrained = hacking_build()
    with pytest.raises(ValidationError, match="does not know"):
        apply_weird_science(
            state.resources,
            world(),
            untrained,
            command.model_copy(update={"build_revision": untrained.revision}),
            blueprint,
            authorized_actor_id="a",
            rng=RecordedDice([]),
        )
