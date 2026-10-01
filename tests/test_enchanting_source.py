"""Independent B480-482 construction, ceremony and daily-project consequences."""

from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import Literal

import pytest
from test_enchanting_projects import advance, create, setup
from test_spell_construction import college_fixtures, compile_spells

from wayfarer.engine.rules.checks import Outcome, RecordedDice
from wayfarer.engine.rules.magic.enchantment import package as enchantment_package
from wayfarer.engine.rules.magic.healing import package as healing_package
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.enchanting import EnchantmentRecipe, EnergyContribution
from wayfarer.engine.simulation.magic.enchanting_transitions import (
    CALENDAR_DAY,
    MAGE_DAY,
    BeginEnchanting,
    CreateEnchantment,
    EnchantmentOutcome,
    InterruptEnchanting,
    SettleEnchanting,
    apply_enchantment,
    enchanting_work_active,
    replay_enchantment_check,
    unresolved_enchantment,
)
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError


def reapprove(runtime: RulesContext, state: PlayState, actor_id: str, points: int) -> PlayState:
    actor = next(a for a in state.actors if a.actor_id == actor_id)
    proposal = actor.proposal.model_copy(
        update={
            "draft": actor.proposal.draft.model_copy(
                update={
                    "purchases": tuple(
                        p.model_copy(
                            update={
                                "amount": points - 4
                                if p.definition_id == "spell:light" and points >= 8
                                else points
                            }
                        )
                        if p.definition_id in ("spell:enchant", "spell:light")
                        else p
                        for p in actor.proposal.draft.purchases
                    )
                }
            )
        }
    )
    actor = actor.model_copy(
        update={
            "proposal": proposal,
            "approval": runtime.reviewer.approve(
                proposal,
                campaign_id=state.campaign_id,
                actor_id=actor_id,
                revision=state.revision,
            ),
        }
    )
    return state.model_copy(
        update={
            "actors": tuple(actor if a.actor_id == actor_id else a for a in state.actors),
            "approvals": state.approvals + ((actor.approval,) if actor.approval else ()),
        }
    )


def recipe_runtime(runtime: RulesContext, **changes: object) -> RulesContext:
    rules = runtime.rules.enchanting
    assert rules is not None
    data = rules.recipes[0].model_dump()
    data.update(changes)
    recipe = EnchantmentRecipe.model_validate(data)
    return replace(
        runtime,
        rules=runtime.rules.model_copy(
            update={"enchanting": rules.model_copy(update={"recipes": (recipe,)})}
        ),
    )


def settle(
    runtime: RulesContext, state: PlayState
) -> tuple[PlayState, EnchantmentOutcome, SettleEnchanting]:
    work = state.resources.enchantment_projects[0].active_work
    assert work is not None
    state = state.model_copy(
        update={"resources": state.resources.model_copy(update={"game_time": work.due})}
    )
    command = SettleEnchanting(
        id="settle",
        actor_id="a",
        expected_revision=state.revision,
        project_id="project",
        work_id=work.id,
    )
    state, outcome = apply_enchantment(runtime, state, command, system=True)
    return state, outcome, command


def start(
    runtime: RulesContext,
    state: PlayState,
    contributions: tuple[EnergyContribution, ...] = (),
) -> PlayState:
    state, _ = create(runtime, state)
    state, _ = apply_enchantment(
        runtime,
        state,
        BeginEnchanting(
            id="begin",
            actor_id="a",
            expected_revision=state.revision,
            project_id="project",
            contributions=contributions,
        ),
        system=True,
    )
    return state


QUICK_ENERGY = (EnergyContribution(actor_id="a", fp=2), EnergyContribution(actor_id="b", fp=2))


def test_staff_and_power_learning_require_their_exact_purchased_chain() -> None:
    packages = college_fixtures(10) + (enchantment_package(), healing_package())
    chain = tuple(("spell:test-college-" + str(i), 1) for i in range(10)) + (("spell:enchant", 1),)
    staff = compile_spells(packages, chain + (("spell:staff", 1),), magery=2)
    assert staff.build is not None, staff.diagnostics
    values = {v.target: v.value for v in staff.build.sheet.values}
    assert values["spell:enchant"] == 11  # IQ12 + Magery2 - VH3
    assert values["spell:staff"] == 12  # IQ12 + Magery2 - H2
    assert not compile_spells(packages, chain[:-1] + (("spell:staff", 16),)).legal
    power_chain = chain + (("spell:lend-energy", 1), ("spell:recover-energy", 1))
    power = compile_spells(packages, power_chain + (("spell:power", 1),), magery=2)
    assert power.build is not None, power.diagnostics
    assert next(v.value for v in power.build.sheet.values if v.target == "spell:power") == 12
    assert not compile_spells(packages, chain + (("spell:power", 16),)).legal
    assert not compile_spells(packages, power_chain[0:-1] + (("spell:power", 16),)).legal


def source_recipe(spell: Literal["staff", "power"], points: int = 0) -> EnchantmentRecipe:
    return EnchantmentRecipe(
        id="source-" + spell,
        spell_id="spell:" + spell,
        effect_id="effect:" + spell,
        method="slow-and-sure",
        energy_required=30 if spell == "staff" else 500 * 2 ** (points - 1),
        target_definition_ids=("equipment:sword",),
        workspace_definition_id="equipment:workshop",
        runtime_family=spell,
        activation="always-on",
        requires_magery=spell == "staff",
        power_reduction=points,
    )


def test_named_source_recipe_costs_and_passive_contract() -> None:
    assert source_recipe("staff").energy_required == 30
    assert [source_recipe("power", i).energy_required for i in range(1, 6)] == [
        500,
        1000,
        2000,
        4000,
        8000,
    ]
    for field, value in (
        ("energy_required", 29),
        ("requires_magery", False),
        ("activation", "cast"),
        ("runtime_family", "spell"),
        ("maximum_charges", 1),
    ):
        data = source_recipe("staff").model_dump()
        data[field] = value
        with pytest.raises(ValueError):
            EnchantmentRecipe.model_validate(data)
    data = source_recipe("power", 3).model_dump()
    for cost in (30, 1500, 2001):
        with pytest.raises(ValueError, match="exact purchased-level"):
            EnchantmentRecipe.model_validate(data | {"energy_required": cost})


def test_lead_skill_sets_roll_and_power_not_the_eligible_assistant(tmp_path: Path) -> None:
    reducer, runtime, state = setup(tmp_path)
    state = reapprove(runtime, state, "b", 1)  # Assistant Enchant15, lead Enchant17.
    state, outcome, command = settle(runtime, start(runtime, state))
    assert outcome.check is not None and outcome.check.base_target == 17
    assert next(i for i in state.resources.items if i.id == "blade").enchantments[0].power == 17
    reducer.validate(state)
    restored = PlayState.model_validate_json(state.model_dump_json())
    assert apply_enchantment(runtime, restored, command, system=True) == (restored, outcome)


@pytest.mark.parametrize("mana", ["none", "low"])
def test_unavailable_mana_and_unqualified_low_mana_are_early(tmp_path: Path, mana: str) -> None:
    _, runtime, state = setup(tmp_path)
    runtime = recipe_runtime(runtime, mana=mana)
    with pytest.raises(ValidationError, match="mana|20"):
        create(runtime, state)
    assert state.resources.receipts == () and state.resources.items[-1].quantity == 3


def test_low_mana_penalty_is_once_and_temporary_in_nominal_power(tmp_path: Path) -> None:
    _, runtime, state = setup(tmp_path)
    runtime = recipe_runtime(runtime, mana="low")
    state = reapprove(runtime, state, "a", 16)  # Enchant20
    state = reapprove(runtime, state, "b", 16)  # Enchant20
    state, outcome, _ = settle(runtime, start(runtime, state))
    assert outcome.check is not None and outcome.check.effective_target == 15
    assert next(i for i in state.resources.items if i.id == "blade").enchantments[0].power == 20
    assert [p.fatigue.power for p in state.resources.pools if p.fatigue] == [0, 0]


@pytest.mark.parametrize(
    "dice,expected", [((6, 5, 5), "perverted"), ((6, 6, 5), "critical-failure")]
)
def test_ceremonial_16_fails_17_destroys_even_at_skill_16(
    tmp_path: Path, dice: tuple[int, ...], expected: str
) -> None:
    reducer, runtime, state = setup(tmp_path, method="quick-and-dirty")
    runtime = replace(runtime, rng=RecordedDice(dice))
    state, outcome, command = settle(runtime, start(runtime, state, QUICK_ENERGY))
    assert outcome.status == expected
    assert outcome.check is not None
    assert replay_enchantment_check(outcome.check) == outcome.check
    assert [p.current for p in state.resources.pools if p.id.startswith("fp:")] == [8, 8]
    if expected == "perverted":
        assert unresolved_enchantment(state.resources, "blade")
        assert not next(i for i in state.resources.items if i.id == "blade").enchantments
    else:
        assert outcome.check is not None and outcome.check.outcome == Outcome.CRITICAL_FAILURE
        assert not any(i.id == "blade" for i in state.resources.items)
        assert any(i.id == "blade" for i in state.resources.expended_items)
    if expected == "perverted":
        reducer.validate(state)
    else:
        # The static-channel adapter must also allow its historical expended item
        # reference; integration tests cover that separately from this reducer.
        PlayState.model_validate_json(state.model_dump_json())
    restored = PlayState.model_validate_json(state.model_dump_json())
    assert apply_enchantment(runtime, restored, command, system=True) == (restored, outcome)


def test_critical_six_keeps_high_skill_and_adds_recorded_2d_power(tmp_path: Path) -> None:
    _, runtime, state = setup(tmp_path, method="quick-and-dirty")
    dice = RecordedDice((2, 2, 2, 3, 4))
    runtime = replace(runtime, rng=dice)
    state, outcome, command = settle(runtime, start(runtime, state, QUICK_ENERGY))
    assert outcome.check is not None and outcome.check.outcome == Outcome.CRITICAL_SUCCESS
    assert next(i for i in state.resources.items if i.id == "blade").enchantments[0].power == 23
    assert (
        next(e.kind for e in state.resources.events if e.id == "enchantment-power:settle") == "3,4"
    )
    assert apply_enchantment(runtime, state, command, system=True) == (state, outcome)
    assert dice.exhausted()


def test_extra_energy_raises_item_power_and_is_all_spent(tmp_path: Path) -> None:
    _, runtime, state = setup(tmp_path, method="quick-and-dirty")
    energy = (EnergyContribution(actor_id="a", fp=3), EnergyContribution(actor_id="b", fp=3))
    state, outcome, _ = settle(runtime, start(runtime, state, energy))
    assert outcome.check is not None and outcome.check.effective_target == 18  # 17 - 1 + 2
    assert next(i for i in state.resources.items if i.id == "blade").enchantments[0].power == 18
    assert [p.current for p in state.resources.pools if p.fatigue] == [7, 7]


def test_hp_energy_uses_canonical_life_energy_injury_and_replays(tmp_path: Path) -> None:
    reducer, runtime, state = setup(tmp_path, method="quick-and-dirty")
    energy = (EnergyContribution(actor_id="a", hp=2), EnergyContribution(actor_id="b", fp=2))
    state, outcome, command = settle(runtime, start(runtime, state, energy))
    assert outcome.check is not None and outcome.check.effective_target == 14  # 17 - 1 - 2 HP
    hp = next(p for p in state.resources.pools if p.id == "hp:a")
    assert hp.current == 8 and hp.injury is not None
    assert hp.injury.shock == 0  # B237 life-energy burning does not cause shock.
    assert any(e.id.startswith("injury:") for e in state.resources.events)
    assert next(p for p in state.resources.pools if p.id == "fp:b").fatigue.power == 2  # type: ignore[union-attr]
    reducer.validate(state)
    assert apply_enchantment(runtime, state, command, system=True) == (state, outcome)


def test_daily_work_has_real_nights_and_night_pause_has_no_penalty(tmp_path: Path) -> None:
    reducer, runtime, state = setup(tmp_path)
    state = start(runtime, state)
    work = state.resources.enchantment_projects[0].active_work
    assert work is not None and work.due == CALENDAR_DAY + MAGE_DAY
    state = advance(reducer, state, MAGE_DAY, "first-shift")
    assert not enchanting_work_active(state.resources, state.resources.enchantment_projects[0])
    state, outcome = apply_enchantment(
        runtime,
        state,
        InterruptEnchanting(
            id="night-pause", actor_id="a", expected_revision=state.revision, project_id="project"
        ),
        system=True,
    )
    assert outcome.energy_completed == 2
    assert state.resources.enchantment_projects[0].delay_seconds == 0
    assert [p.current for p in state.resources.pools if p.fatigue] == [10, 10]
    state, _ = apply_enchantment(
        runtime,
        state,
        BeginEnchanting(
            id="resume", actor_id="a", expected_revision=state.revision, project_id="project"
        ),
        system=True,
    )
    work = state.resources.enchantment_projects[0].active_work
    assert work is not None and work.due == CALENDAR_DAY + MAGE_DAY
    assert not enchanting_work_active(state.resources, state.resources.enchantment_projects[0])


def test_active_day_interruption_costs_fatigue_once_and_two_replacement_days(
    tmp_path: Path,
) -> None:
    reducer, runtime, state = setup(tmp_path)
    runtime = replace(runtime, rng=RecordedDice((4, 2, 3, 3, 3)))
    state = start(runtime, state)
    state = advance(reducer, state, CALENDAR_DAY + 3600, "interrupted-day-two")
    interrupt = InterruptEnchanting(
        id="interrupted", actor_id="a", expected_revision=state.revision, project_id="project"
    )
    state, outcome = apply_enchantment(runtime, state, interrupt, system=True)
    assert outcome.energy_completed == 2
    assert [p.current for p in state.resources.pools if p.fatigue] == [6, 8]
    assert apply_enchantment(runtime, state, interrupt, system=True) == (state, outcome)
    state, _ = apply_enchantment(
        runtime,
        state,
        BeginEnchanting(
            id="resume", actor_id="a", expected_revision=state.revision, project_id="project"
        ),
        system=True,
    )
    work = state.resources.enchantment_projects[0].active_work
    assert work is not None and work.due == 3 * CALENDAR_DAY + MAGE_DAY
    state, outcome, _ = settle(runtime, state)
    assert outcome.status == "completed"
    with pytest.raises(ConflictError, match="revision changed"):
        apply_enchantment(
            runtime,
            state,
            CreateEnchantment(
                id="stale",
                actor_id="a",
                expected_revision=0,
                project_id="new",
                recipe_id="light-blade",
                target_item_id="blade",
                enchanter_ids=("a", "b"),
            ),
            system=True,
        )


def test_staff_completion_requires_physical_facts_and_creates_passive_item(tmp_path: Path) -> None:
    from wayfarer.engine.simulation.magic.staff_state import (
        DeclareStaffConstruction,
        StaffConstruction,
        declare,
    )

    _, runtime, state = setup(tmp_path, learn_source_spells=True)
    source = source_recipe("staff")
    runtime = recipe_runtime(runtime, **(source.model_dump() | {"id": "light-blade"}))
    with pytest.raises(ValidationError, match="trusted physical construction"):
        start(runtime, state)
    construction = StaffConstruction(
        item_id="blade",
        definition_id="equipment:sword",
        form="wand",
        length_yards=Fraction(1),
        material="reed",
        once_living=True,
    )
    declared = declare(
        state.resources,
        DeclareStaffConstruction(
            id="physical-staff",
            actor_id="a",
            expected_revision=state.revision,
            construction=construction,
        ),
    )
    state = state.model_copy(update={"resources": declared})
    state = start(runtime, state)
    work = state.resources.enchantment_projects[0].active_work
    assert work is not None and work.due == 14 * CALENDAR_DAY + MAGE_DAY
    state, outcome, command = settle(runtime, state)
    assert outcome.status == "completed"
    item = next(i for i in state.resources.items if i.id == "blade")
    magic = item.enchantments[0]
    assert (magic.spell_id, magic.runtime_family, magic.power) == ("staff", "staff", 17)
    assert magic.always_on and magic.requires_magery and magic.charges is None
    assert [p.current for p in state.resources.pools if p.fatigue] == [10, 10]
    restored = PlayState.model_validate_json(state.model_dump_json())
    assert apply_enchantment(runtime, restored, command, system=True) == (restored, outcome)


def test_power_requires_magic_target_and_preserves_existing_enchantment(tmp_path: Path) -> None:
    _, runtime, state = setup(tmp_path, learn_source_spells=True)
    power = source_recipe("power", 2)
    power_runtime = recipe_runtime(runtime, **(power.model_dump() | {"id": "light-blade"}))
    with pytest.raises(ValidationError, match="already enchanted"):
        create(power_runtime, state)
    # First create an actual working enchantment; Power is a separate project.
    state, _, _ = settle(runtime, start(runtime, state))
    existing = next(i for i in state.resources.items if i.id == "blade").enchantments[0]
    runtime = replace(power_runtime, rng=RecordedDice((3, 3, 3)))
    state, _ = apply_enchantment(
        runtime,
        state,
        CreateEnchantment(
            id="create-power",
            actor_id="a",
            expected_revision=state.revision,
            project_id="power-project",
            recipe_id="light-blade",
            target_item_id="blade",
            enchanter_ids=("a", "b"),
        ),
        system=True,
    )
    state, _ = apply_enchantment(
        runtime,
        state,
        BeginEnchanting(
            id="begin-power",
            actor_id="a",
            expected_revision=state.revision,
            project_id="power-project",
        ),
        system=True,
    )
    work = next(
        p for p in state.resources.enchantment_projects if p.id == "power-project"
    ).active_work
    assert work is not None
    # B481 forbids a second shift after the first item's day-two work. Power's
    # first workday therefore starts at day three, after the remaining rest.
    assert work.start == CALENDAR_DAY + MAGE_DAY
    assert work.due == 501 * CALENDAR_DAY + MAGE_DAY
    state = state.model_copy(
        update={"resources": state.resources.model_copy(update={"game_time": work.due})}
    )
    command = SettleEnchanting(
        id="settle-power",
        actor_id="a",
        expected_revision=state.revision,
        project_id="power-project",
        work_id=work.id,
    )
    state, outcome = apply_enchantment(runtime, state, command, system=True)
    assert outcome.status == "completed"
    item = next(i for i in state.resources.items if i.id == "blade")
    assert item.enchantments[0] == existing
    assert (item.enchantments[1].spell_id, item.enchantments[1].power_reduction) == ("power", 2)
    assert item.enchantments[1].runtime_family == "power" and item.enchantments[1].always_on
    assert apply_enchantment(runtime, state, command, system=True) == (state, outcome)


def test_slow_failure_loses_blank_subject_but_preserves_existing_magic(tmp_path: Path) -> None:
    _, runtime, state = setup(tmp_path)
    failing = replace(runtime, rng=RecordedDice((6, 5, 5)))
    lost, outcome, command = settle(failing, start(failing, state))
    assert outcome.status == "failed"
    assert not any(i.id == "blade" for i in lost.resources.items)
    assert any(i.id == "blade" for i in lost.resources.expended_items)
    assert apply_enchantment(failing, lost, command, system=True) == (lost, outcome)

    state, _, _ = settle(runtime, start(runtime, state))
    prior = next(i for i in state.resources.items if i.id == "blade").enchantments
    # The first project used its materials. An existing magic item survives the
    # failed extra enchantment, independent of any additional supplies.
    failing = recipe_runtime(replace(runtime, rng=RecordedDice((6, 5, 5))), materials=())
    state, _ = apply_enchantment(
        failing,
        state,
        CreateEnchantment(
            id="another-create",
            actor_id="a",
            expected_revision=state.revision,
            project_id="another-project",
            recipe_id="light-blade",
            target_item_id="blade",
            enchanter_ids=("a", "b"),
        ),
        system=True,
    )
    state, _ = apply_enchantment(
        failing,
        state,
        BeginEnchanting(
            id="another-begin",
            actor_id="a",
            expected_revision=state.revision,
            project_id="another-project",
        ),
        system=True,
    )
    work = next(
        p for p in state.resources.enchantment_projects if p.id == "another-project"
    ).active_work
    assert work is not None
    state = state.model_copy(
        update={"resources": state.resources.model_copy(update={"game_time": work.due})}
    )
    state, outcome = apply_enchantment(
        failing,
        state,
        SettleEnchanting(
            id="another-settle",
            actor_id="a",
            expected_revision=state.revision,
            project_id="another-project",
            work_id=work.id,
        ),
        system=True,
    )
    assert outcome.status == "failed"
    assert next(i for i in state.resources.items if i.id == "blade").enchantments == prior


def test_skipping_a_slow_workday_adds_one_replacement_shift(tmp_path: Path) -> None:
    reducer, runtime, state = setup(tmp_path)
    state = start(runtime, state)
    state = advance(reducer, state, MAGE_DAY, "first-shift")
    state, _ = apply_enchantment(
        runtime,
        state,
        InterruptEnchanting(
            id="night-pause",
            actor_id="a",
            expected_revision=state.revision,
            project_id="project",
        ),
        system=True,
    )
    # Day two is skipped entirely. Two later shifts must replace that one day.
    state = advance(reducer, state, 2 * CALENDAR_DAY, "skip-day-two")
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
    assert work is not None and work.due == 3 * CALENDAR_DAY + MAGE_DAY


def test_interrupted_mage_cannot_work_a_second_enchantment(tmp_path: Path) -> None:
    from wayfarer.engine.simulation.resources import Item

    _, runtime, state = setup(tmp_path)
    runtime = recipe_runtime(runtime, materials=())
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": state.resources.items
                    + (Item(id="second-blade", definition_id="equipment:sword", owner_id="a"),)
                }
            )
        }
    )
    state = start(runtime, state)
    # Pause after the complete first shift, when no fatigue is incurred.
    state = state.model_copy(
        update={"resources": state.resources.model_copy(update={"game_time": MAGE_DAY})}
    )
    state, _ = apply_enchantment(
        runtime,
        state,
        InterruptEnchanting(
            id="pause", actor_id="a", expected_revision=state.revision, project_id="project"
        ),
        system=True,
    )
    state, _ = apply_enchantment(
        runtime,
        state,
        CreateEnchantment(
            id="second-create",
            actor_id="a",
            expected_revision=state.revision,
            project_id="second-project",
            recipe_id="light-blade",
            target_item_id="second-blade",
            enchanter_ids=("a", "b"),
        ),
        system=True,
    )
    with pytest.raises(ConflictError, match="another unfinished enchantment"):
        apply_enchantment(
            runtime,
            state,
            BeginEnchanting(
                id="second-begin",
                actor_id="a",
                expected_revision=state.revision,
                project_id="second-project",
            ),
            system=True,
        )


def test_critical_reenchantment_destruction_preserves_completed_project_history(
    tmp_path: Path,
) -> None:
    reducer, runtime, state = setup(tmp_path)
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": tuple(
                        item.model_copy(update={"quantity": 6}) if item.id == "silver" else item
                        for item in state.resources.items
                    )
                }
            )
        }
    )
    state = start(runtime, state)
    state, _, _ = settle(runtime, state)
    old = state.resources.enchantment_projects[0]
    binding_id = old.magic_item_binding_id
    assert old.status == "completed" and binding_id is not None
    assert state.resources.game_time == 115200  # Two full mage-days with two casters.
    # Start the next project on the next day's shift; this case isolates archive integrity.
    state = advance(reducer, state, 172800, "rest-before-second-project")
    create_second = CreateEnchantment(
        id="create-second",
        actor_id="a",
        expected_revision=state.revision,
        project_id="second",
        recipe_id="light-blade",
        target_item_id="blade",
        enchanter_ids=("a", "b"),
    )
    state, _ = apply_enchantment(runtime, state, create_second, system=True)
    state, _ = apply_enchantment(
        runtime,
        state,
        BeginEnchanting(
            id="begin-second", actor_id="a", expected_revision=state.revision, project_id="second"
        ),
        system=True,
    )
    work = state.resources.enchantment_projects[-1].active_work
    assert work is not None and work.due == 288000  # Next two daily shifts; no double shift.
    state = advance(reducer, state, work.due, "second-time")
    finish = SettleEnchanting(
        id="finish-second",
        actor_id="a",
        expected_revision=state.revision,
        project_id="second",
        work_id=work.id,
    )
    state, result = apply_enchantment(
        replace(runtime, rng=RecordedDice((5, 6, 6))), state, finish, system=True
    )
    assert result.status == "critical-failure" and result.check is not None
    assert result.check.outcome is Outcome.CRITICAL_FAILURE
    assert not any(item.id == "blade" for item in state.resources.items)
    archived = next(item for item in state.resources.expended_items if item.id == "blade")
    assert any(binding.id == binding_id for binding in archived.enchantments)
    assert state.resources.enchantment_projects[0] == old
    reducer.validate(state)
    assert apply_enchantment(
        replace(runtime, rng=RecordedDice(())), state, finish, system=True
    ) == (state, result)
