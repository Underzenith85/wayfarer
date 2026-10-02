"""B238/B481 energy and proximity consequences, with frozen historical identity."""

from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import Literal

import pytest
from test_enchanting_projects import advance, create, setup
from test_enchanting_source import QUICK_ENERGY, recipe_runtime, start

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter
from wayfarer.engine.simulation.combat.spatial import (
    BasicSpatialContext,
    DistanceSpatialFact,
    HexActorPlacement,
    HexSpatialContext,
    SpatialProvenance,
    SquareActorPlacement,
    SquareSpatialContext,
)
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.engine.simulation.magic.enchanting import EnergyContribution
from wayfarer.engine.simulation.magic.enchanting_transitions import (
    CALENDAR_DAY,
    MAGE_DAY,
    AbandonEnchantment,
    BeginEnchanting,
    EnchantingNonparticipant,
    InterruptEnchanting,
    SettleEnchanting,
    apply_enchantment,
    command_payload,
)
from wayfarer.engine.world import Entity, EntityKind
from wayfarer.errors import ConflictError, ValidationError


def finish_command(state: PlayState, **changes: object) -> SettleEnchanting:
    work = state.resources.enchantment_projects[0].active_work
    assert work is not None
    data: dict[str, object] = dict(
        id="settle",
        actor_id="a",
        expected_revision=state.revision,
        project_id="project",
        work_id=work.id,
    )
    return SettleEnchanting.model_validate(data | changes)


def due_state(state: PlayState) -> PlayState:
    work = state.resources.enchantment_projects[0].active_work
    assert work is not None
    return state.model_copy(
        update={"resources": state.resources.model_copy(update={"game_time": work.due})}
    )


def add_bystanders(state: PlayState, *names: str) -> PlayState:
    return state.model_copy(
        update={
            "world": replace(
                state.world,
                entities=state.world.entities
                + tuple(
                    Entity(name, EntityKind.ACTOR, "Hidden bystander", "forge") for name in names
                ),
            )
        }
    )


def distances(*values: tuple[str, Fraction | int]) -> tuple[EnchantingNonparticipant, ...]:
    return tuple(
        EnchantingNonparticipant(actor_id=actor, distance_yards=Fraction(distance))
        for actor, distance in values
    )


@pytest.mark.parametrize(
    "extra,bonus", [(0, 0), (1, 0), (2, 1), (3, 1), (4, 2), (6, 3), (9, 3), (10, 4), (20, 5)]
)
def test_extra_energy_buys_mage_days_and_real_power_without_fp_or_hp(
    tmp_path: Path,
    extra: int,
    bonus: int,
) -> None:
    _, runtime, state = setup(tmp_path)
    runtime = recipe_runtime(runtime, energy_required=10)
    state, _ = create(runtime, state)
    original_pools = state.resources.pools
    command = BeginEnchanting(
        id="begin",
        actor_id="a",
        expected_revision=state.revision,
        project_id="project",
        extra_energy=extra,
    )
    state, begun = apply_enchantment(runtime, state, command, system=True)
    work = state.resources.enchantment_projects[0].active_work
    assert work is not None and work.due == ((10 + extra + 1) // 2 - 1) * CALENDAR_DAY + MAGE_DAY
    assert state.resources.pools == original_pools
    assert apply_enchantment(runtime, state, command, system=True) == (state, begun)
    state = due_state(state)
    final, result = apply_enchantment(runtime, state, finish_command(state), system=True)
    assert result.check is not None and result.check.effective_target == 17 + bonus
    item = next(i for i in final.resources.items if i.id == "blade")
    assert item.enchantments[0].power == 17 + bonus
    assert item.enchantments[0].created_at == work.due
    assert final.resources.pools == original_pools
    assert result.energy_completed == 10
    assert not any(e.id.startswith("mana-refund:") for e in final.resources.events)


def test_extra_work_continues_after_full_base_progress_and_two_restarts(tmp_path: Path) -> None:
    reducer, runtime, state = setup(tmp_path)
    state, _ = create(runtime, state)
    state, _ = apply_enchantment(
        runtime,
        state,
        BeginEnchanting(
            id="begin",
            actor_id="a",
            expected_revision=1,
            project_id="project",
            extra_energy=8,
        ),
        system=True,
    )
    for index in (1, 2):
        # Two full days yield 4 energy; a third yields 6. Both exceed/meet base4,
        # but the 12-energy commitment still needs six productive group shifts.
        state = advance(reducer, state, index * CALENDAR_DAY + MAGE_DAY, f"clock{index}")
        state, _ = apply_enchantment(
            runtime,
            state,
            InterruptEnchanting(
                id=f"pause{index}",
                actor_id="a",
                expected_revision=state.revision,
                project_id="project",
            ),
            system=True,
        )
        project = state.resources.enchantment_projects[0]
        assert project.energy_completed == 4 and project.status == "interrupted"
        assert sum(i.credited_energy for i in project.interruptions) == 2 * (index + 1)
        assert not next(i for i in state.resources.items if i.id == "blade").enchantments
        reducer.validate(state)
        state = PlayState.model_validate_json(state.model_dump_json())
        with pytest.raises(ConflictError, match="committed extra energy"):
            apply_enchantment(
                runtime,
                state,
                BeginEnchanting(
                    id="change-goal",
                    actor_id="a",
                    expected_revision=state.revision,
                    project_id="project",
                    extra_energy=0,
                ),
                system=True,
            )
        state, _ = apply_enchantment(
            runtime,
            state,
            BeginEnchanting(
                id=f"resume{index}",
                actor_id="a",
                expected_revision=state.revision,
                project_id="project",
            ),
            system=True,
        )
        work = state.resources.enchantment_projects[0].active_work
        assert work is not None and work.due == 5 * CALENDAR_DAY + MAGE_DAY
    state = due_state(state)
    final, result = apply_enchantment(runtime, state, finish_command(state), system=True)
    assert result.check is not None and result.check.effective_target == 22  # +200% => +5.
    assert next(i for i in final.resources.items if i.id == "blade").enchantments[0].power == 22
    assert [(p.id, p.current) for p in final.resources.pools] == [
        (p.id, p.maximum) for p in final.resources.pools
    ]
    reducer.validate(final)


def test_interrupted_extra_shift_requires_makeup_and_abandon_preserves_rest(tmp_path: Path) -> None:
    reducer, runtime, state = setup(tmp_path)
    state, _ = create(runtime, state)
    state, _ = apply_enchantment(
        runtime,
        state,
        BeginEnchanting(
            id="begin",
            actor_id="a",
            expected_revision=1,
            project_id="project",
            extra_energy=4,
        ),
        system=True,
    )
    state = advance(reducer, state, 2 * CALENDAR_DAY + 100, "during-extra-day")
    runtime = replace(runtime, rng=RecordedDice((2, 3)))
    state, result = apply_enchantment(
        runtime,
        state,
        InterruptEnchanting(
            id="pause",
            actor_id="a",
            expected_revision=state.revision,
            project_id="project",
        ),
        system=True,
    )
    assert result.energy_completed == 4
    assert [p.current for p in state.resources.pools if p.fatigue] == [8, 7]
    state, _ = apply_enchantment(
        runtime,
        state,
        BeginEnchanting(
            id="resume",
            actor_id="a",
            expected_revision=state.revision,
            project_id="project",
        ),
        system=True,
    )
    work = state.resources.enchantment_projects[0].active_work
    assert work is not None and work.due == 5 * CALENDAR_DAY + MAGE_DAY
    state, result = apply_enchantment(
        runtime,
        state,
        AbandonEnchantment(
            id="abandon",
            actor_id="a",
            expected_revision=state.revision,
            project_id="project",
        ),
        system=True,
    )
    assert result.status == "abandoned"
    assert state.resources.enchantment_projects[0].active_work is None
    assert not next(i for i in state.resources.items if i.id == "blade").enchantments
    with pytest.raises(ConflictError, match="not available"):
        apply_enchantment(
            runtime,
            state,
            BeginEnchanting(
                id="resume-abandoned",
                actor_id="a",
                expected_revision=state.revision,
                project_id="project",
            ),
            system=True,
        )


@pytest.mark.parametrize("nearby,target", [(10, 15), (Fraction(101, 10), 16), (0, 15)])
def test_nearby_penalty_is_one_and_changes_the_created_item(
    tmp_path: Path,
    nearby: Fraction | int,
    target: int,
) -> None:
    _, runtime, state = setup(tmp_path, method="quick-and-dirty")
    state = due_state(start(runtime, add_bystanders(state, "c", "d"), QUICK_ENERGY))
    command = finish_command(
        state, nonparticipant_distances=distances(("c", nearby), ("d", nearby))
    )
    final, result = apply_enchantment(runtime, state, command, system=True)
    assert result.check is not None and result.check.effective_target == target
    assert next(i for i in final.resources.items if i.id == "blade").enchantments[0].power == target
    assert [p.current for p in final.resources.pools if p.fatigue] == [8, 8]
    assert [p.current for p in final.resources.pools if p.injury] == [10, 10]
    restarted = PlayState.model_validate_json(final.model_dump_json())
    assert apply_enchantment(
        replace(runtime, rng=RecordedDice(())), restarted, command, system=True
    ) == (restarted, result)
    with pytest.raises(ConflictError, match="different payload"):
        apply_enchantment(
            runtime,
            restarted,
            command.model_copy(
                update={"nonparticipant_distances": distances(("c", 40), ("d", 40))}
            ),
            system=True,
        )


@pytest.mark.parametrize(
    "values",
    [(), (("c", 20),), (("c", 20), ("c", 20)), (("c", 20), ("b", 1)), (("c", 20), ("ghost", 1))],
)
def test_missing_duplicate_or_wrong_current_bystanders_refuse_before_cost_or_roll(
    tmp_path: Path,
    values: tuple[tuple[str, int], ...],
) -> None:
    _, runtime, state = setup(tmp_path, method="quick-and-dirty")
    state = due_state(start(runtime, add_bystanders(state, "c", "d"), QUICK_ENERGY))
    with pytest.raises(ValidationError, match="nonparticipants"):
        apply_enchantment(
            replace(runtime, rng=RecordedDice(())),
            state,
            finish_command(state, nonparticipant_distances=distances(*values)),
            system=True,
        )
    assert [p.current for p in state.resources.pools if p.fatigue] == [10, 10]


def mapped(state: PlayState, geometry: Literal["square", "hex", "basic"], yards: int) -> PlayState:
    spatial: SquareSpatialContext | HexSpatialContext | BasicSpatialContext
    if geometry == "basic":
        spatial = BasicSpatialContext(
            facts=(
                DistanceSpatialFact(
                    subject_id="a",
                    object_id="c",
                    yards=yards,
                    provenance=SpatialProvenance(
                        source="gm-adjudication",
                        source_id="observation",
                        declared_by="gm",
                        declared_revision=0,
                    ),
                ),
            )
        )
    elif geometry == "hex":
        spatial = HexSpatialContext(
            battlefield_id="forge",
            placements=(
                HexActorPlacement(actor_id="a", position=Hex(q=0, r=0), facing=0),
                HexActorPlacement(actor_id="c", position=Hex(q=yards, r=0), facing=0),
            ),
        )
    else:
        spatial = SquareSpatialContext(
            battlefield_id="forge",
            placements=(
                SquareActorPlacement(actor_id="a", position=GridPoint(x=0, y=0)),
                SquareActorPlacement(actor_id="c", position=GridPoint(x=yards, y=0)),
            ),
        )
    encounter = Encounter(
        id="worksite-map",
        spatial_context=spatial,
        participants=tuple(
            Combatant(actor_id=actor, initiative=5, reach=1, movement_allowance=5)
            for actor in ("a", "c")
        ),
        turn_order=("a", "c"),
    )
    return state.model_copy(update={"encounters": (encounter,)})


@pytest.mark.parametrize("geometry", ["square", "hex", "basic"])
@pytest.mark.parametrize("yards,target", [(10, 15), (11, 16)])
def test_current_authoritative_distance_supersedes_observation(
    tmp_path: Path,
    geometry: Literal["square", "hex", "basic"],
    yards: int,
    target: int,
) -> None:
    _, runtime, state = setup(tmp_path, method="quick-and-dirty")
    state = due_state(start(runtime, add_bystanders(state, "c"), QUICK_ENERGY))
    state = mapped(state, geometry, yards)
    with pytest.raises(ValidationError, match="conflicts"):
        apply_enchantment(
            replace(runtime, rng=RecordedDice(())),
            state,
            finish_command(state, nonparticipant_distances=distances(("c", yards + 1))),
            system=True,
        )
    final, result = apply_enchantment(runtime, state, finish_command(state), system=True)
    assert result.check is not None and result.check.effective_target == target
    assert next(i for i in final.resources.items if i.id == "blade").enchantments[0].power == target


def test_new_default_fields_do_not_change_old_command_payloads() -> None:
    begin = BeginEnchanting(id="b", actor_id="a", expected_revision=1, project_id="p")
    settle = SettleEnchanting(
        id="s", actor_id="a", expected_revision=2, project_id="p", work_id="b"
    )
    assert "extra_energy" not in command_payload(begin)
    assert "nonparticipant_distances" not in command_payload(settle)
    assert command_payload(begin) == dict(
        id="b", actor_id="a", expected_revision=1, kind="begin", project_id="p", contributions=[]
    )


def test_fp_hp_contributions_cannot_substitute_for_extra_slow_work(tmp_path: Path) -> None:
    _, runtime, state = setup(tmp_path)
    state, _ = create(runtime, state)
    with pytest.raises(ValidationError, match="does not spend FP or HP"):
        apply_enchantment(
            runtime,
            state,
            BeginEnchanting(
                id="b",
                actor_id="a",
                expected_revision=1,
                project_id="project",
                extra_energy=4,
                contributions=(EnergyContribution(actor_id="a", fp=4),),
            ),
            system=True,
        )


def test_current_map_placement_is_used_after_movement_not_stale_runtime_mirror(
    tmp_path: Path,
) -> None:
    _, runtime, state = setup(tmp_path, method="quick-and-dirty")
    state = mapped(
        due_state(start(runtime, add_bystanders(state, "c"), QUICK_ENERGY)), "square", 11
    )
    encounter = state.encounters[0]
    moved = encounter.replace_placement(
        SquareActorPlacement(actor_id="c", position=GridPoint(x=10, y=0))
    )
    state = state.model_copy(update={"encounters": (moved,)})
    final, result = apply_enchantment(runtime, state, finish_command(state), system=True)
    assert result.check is not None and result.check.effective_target == 15
    assert next(i for i in final.resources.items if i.id == "blade").enchantments[0].power == 15


def test_invalidated_basic_distance_and_completed_map_require_current_observation(
    tmp_path: Path,
) -> None:
    _, runtime, state = setup(tmp_path, method="quick-and-dirty")
    state = due_state(start(runtime, add_bystanders(state, "c"), QUICK_ENERGY))
    mapped_state = mapped(state, "basic", 11)
    encounter = mapped_state.encounters[0]
    context = encounter.spatial
    assert isinstance(context, BasicSpatialContext)
    fact = context.facts[0]
    stale = context.model_copy(
        update={
            "facts": (
                fact.model_copy(
                    update={
                        "provenance": fact.provenance.model_copy(update={"invalidated_revision": 2})
                    }
                ),
            )
        }
    )
    for invalid in (
        encounter.model_copy(update={"spatial_context": stale}),
        mapped(state, "square", 11).encounters[0].model_copy(update={"status": "completed"}),
    ):
        current = state.model_copy(update={"encounters": (invalid,)})
        with pytest.raises(ValidationError, match="current GM distances"):
            apply_enchantment(
                replace(runtime, rng=RecordedDice(())),
                current,
                finish_command(current),
                system=True,
            )
        final, result = apply_enchantment(
            replace(runtime, rng=RecordedDice((3, 3, 3))),
            current,
            finish_command(current, nonparticipant_distances=distances(("c", 10))),
            system=True,
        )
        assert result.check is not None and result.check.effective_target == 15
        assert next(i for i in final.resources.items if i.id == "blade").enchantments[0].power == 15


def test_nearby_penalty_and_lead_hp_change_real_roll_and_injury_independently(
    tmp_path: Path,
) -> None:
    _, runtime, state = setup(tmp_path, method="quick-and-dirty")
    state = due_state(
        start(
            runtime,
            add_bystanders(state, "c"),
            (
                EnergyContribution(actor_id="a", fp=1, hp=1),
                EnergyContribution(actor_id="b", fp=2),
            ),
        )
    )
    final, result = apply_enchantment(
        runtime,
        state,
        finish_command(state, nonparticipant_distances=distances(("c", 10))),
        system=True,
    )
    assert (
        result.check is not None and result.check.effective_target == 14
    )  # 17 - assistant1 - nearby1 - HP1.
    assert next(i for i in final.resources.items if i.id == "blade").enchantments[0].power == 15
    assert [(p.id, p.current) for p in final.resources.pools if p.fatigue] == [
        ("fp:a", 9),
        ("fp:b", 8),
    ]
    assert [(p.id, p.current) for p in final.resources.pools if p.injury] == [
        ("hp:a", 9),
        ("hp:b", 10),
    ]
    # The source interpretation of permanent HP penalties is deliberately unchanged.
