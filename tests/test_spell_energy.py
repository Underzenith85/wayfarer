"""Independent numeric B235-237 cases, Characters 4e third printing (2008)."""

from pathlib import Path

import pytest
from test_spell_bindings import command as player_command
from test_spell_bindings import setup
from test_spells import command, context, state

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.combat import GridPoint, Placement
from wayfarer.engine.simulation.magic.backfires import backfires, refund_due
from wayfarer.engine.simulation.magic.spells import apply_spell, latest
from wayfarer.orchestration.combat import CombatService, StartEncounter
from wayfarer.orchestration.spells import SpellService


@pytest.mark.parametrize(
    "roll,energy,hp", [([3, 3, 3], 3, 2), ([1, 1, 1], 0, 0), ([5, 5, 3], 1, 1)]
)
def test_hp_contribution_cost_and_retry(roll: list[int], energy: int, hp: int) -> None:
    ctx = context()
    start = command(spell="daze").model_copy(update={"hp_energy": 2})
    resources, _ = apply_spell(state(), start, ctx, rng=RecordedDice([]), system=True)
    assert latest(resources)["cast"].skill == 12
    complete = command(1, kind="complete", spell="daze")
    # Resistance fails when the casting succeeds normally.
    resources, result = apply_spell(
        resources.model_copy(update={"game_time": 2}),
        complete,
        ctx,
        rng=RecordedDice(roll + ([6, 6, 5] if roll == [3, 3, 3] else [])),
        system=True,
    )
    assert (result.energy_spent, result.hp_spent) == (energy, hp)
    assert resources.pools[0].current == 10 - hp
    assert resources.pools[0].injury and resources.pools[0].injury.shock == 0
    assert resources.pools[1].current == 10 - (energy - hp)
    assert apply_spell(resources, complete, ctx, rng=RecordedDice([]), system=True) == (
        resources,
        result,
    )


@pytest.mark.parametrize("skill,cost,ready", [(19, 1, 1), (14, 1, 2), (20, 0, 1)])
def test_low_mana_applies_to_all_skill_benefits(skill: int, cost: int, ready: int) -> None:
    resources, _ = apply_spell(
        state(),
        command(),
        context().model_copy(update={"mana": "low", "skill": skill}),
        rng=RecordedDice([]),
        system=True,
    )
    effect = latest(resources)["cast"]
    assert (effect.cost, effect.ready_at) == (cost, ready)


def test_very_high_mana_refund_is_once_and_not_hp() -> None:
    ctx = context().model_copy(update={"mana": "very-high", "execution_version": 2})
    resources, _ = apply_spell(state(), command(), ctx, rng=RecordedDice([]), system=True)
    resources, _ = apply_spell(
        resources.model_copy(update={"game_time": 1}),
        command(1, kind="complete"),
        ctx,
        rng=RecordedDice([3, 3, 3]),
        system=True,
    )
    assert resources.pools[1].current == 9
    assert refund_due(resources, "a") == resources
    resources = refund_due(resources.model_copy(update={"game_time": 2}), "a")
    assert resources.pools[1].current == 10
    assert refund_due(resources, "a") == resources


def test_normal_failure_in_very_high_mana_rolls_backfire() -> None:
    ctx = context().model_copy(update={"mana": "very-high", "execution_version": 2})
    resources, _ = apply_spell(state(), command(), ctx, rng=RecordedDice([]), system=True)
    resources, result = apply_spell(
        resources.model_copy(update={"game_time": 1}),
        command(1, kind="complete"),
        ctx,
        rng=RecordedDice([5, 5, 5, 3, 3, 2]),
        system=True,
    )
    assert result.outcome == "critical-failure"
    assert backfires(resources)[0].row == 8
    assert resources.pools[0].current == 9


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_one_second_cast_completes_before_other_actor_turn(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await setup(tmp_path, combat=True, backend=backend, execution_version=2)
    await CombatService(play).execute(
        cid,
        StartEncounter(
            id="fight",
            actor_id="gm",
            expected_revision=0,
            encounter_id="fight",
            battlefield_id="room",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=1, y=1)),
                Placement(actor_id="b", position=GridPoint(x=2, y=1)),
            ),
        ),
        authenticated_actor_id="gm",
    )
    play.rng = RecordedDice([3, 3, 3])
    cmd = player_command(1)
    result = await SpellService(play).execute(cid, cmd, principal_id="a")
    assert result.outcome == "active"
    saved = play._load(await play.store.read(cid))
    assert saved.encounters[0].current_actor_id == "b"
    assert saved.resources.game_time == 0
    assert next(p.current for p in saved.resources.pools if p.id == "fp:a") == 9
    play.rng = RecordedDice([])
    assert await SpellService(play).execute(cid, cmd, principal_id="a") == result
    assert await play.store.read(cid) == await play.store.replay(cid)


def test_corrected_prerequisites_accept_one_purchased_point_without_changing_legacy() -> None:
    from dataclasses import replace

    from test_spell_bindings import compiler, draft
    from test_statistics import profile_compiler, profile_package

    from wayfarer.engine.character.compiler import CharacterCompiler
    from wayfarer.engine.rules.catalog import RulesCatalog
    from wayfarer.engine.rules.magic.gurps_magic import definitions
    from wayfarer.engine.rules.magic.spell_catalog import projectile_definition
    from wayfarer.engine.simulation.magic.spells import PROFILE

    package = profile_package(PROFILE, *definitions(2), projectile_definition())
    base = profile_compiler(PROFILE, package=package)
    corrected = CharacterCompiler(
        RulesCatalog((package,)),
        base.rules,
        replace(base.policy, allow_supernatural=True),
        statistics_profile=PROFILE,
    )
    result = corrected.compile(draft(magery=1, points=1))
    assert result.build is not None, result.diagnostics
    assert {int(v.value) for v in result.build.sheet.values if v.target.startswith("spell:")} == {
        11
    }
    assert compiler().compile(draft(magery=1, points=1)).build is None
