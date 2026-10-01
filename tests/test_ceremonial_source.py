"""Characters third printing B238: full skill, ritual outcomes and pledged energy."""

import pytest
from test_spells import command, context, state

from wayfarer.engine.rules.checks import Outcome, RecordedDice
from wayfarer.engine.rules.magic.ceremonial import replay_ceremonial_check
from wayfarer.engine.rules.magic.protocols import CeremonialContribution, CeremonialPlan
from wayfarer.engine.simulation.magic.spells import SpellContext, SpellId, apply_spell, latest
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ConflictError


def ritual(
    *, spell: SpellId = "light", leader_fp: int = 2, helper_fp: int = 1
) -> tuple[ResourceState, SpellContext]:
    initial = state()
    initial = initial.model_copy(
        update={
            "pools": initial.pools
            + tuple(
                pool.model_copy(update={"id": pool.id.replace(":a", ":" + actor)})
                for actor in ("helper", "b")
                for pool in initial.pools
            )
        }
    )
    plan = CeremonialPlan(
        leader_id="a",
        contributions=(
            CeremonialContribution(actor_id="a", fp=leader_fp, role="leader"),
            CeremonialContribution(actor_id="helper", fp=helper_fp, role="mage"),
        ),
    )
    ctx = context().model_copy(
        update={
            "skill": 20,
            "will": 10,
            "target_ht": 18,
            "ceremonial": plan,
            "ceremonial_ht": (("a", 10), ("helper", 10)),
        }
    )
    casting, _ = apply_spell(initial, command(spell=spell), ctx, rng=RecordedDice(()), system=True)
    return casting.model_copy(update={"game_time": 20 if spell == "daze" else 10}), ctx


@pytest.mark.parametrize(
    ("dice", "outcome", "result"),
    [
        ((3, 3, 3), Outcome.SUCCESS, "active"),
        ((5, 5, 6), Outcome.FAILURE, "failed"),
        ((5, 6, 6), Outcome.CRITICAL_FAILURE, "critical-failure"),
        ((6, 6, 6), Outcome.CRITICAL_FAILURE, "critical-failure"),
        ((1, 1, 1), Outcome.CRITICAL_SUCCESS, "active"),
    ],
)
def test_b238_keeps_actual_skill_and_margin_with_fixed_failure_boundaries(
    dice: tuple[int, int, int], outcome: Outcome, result: str
) -> None:
    casting, ctx = ritual()
    assert latest(casting)["cast"].skill == 25  # 20 +5 for300% of Light's cost.
    done, resolved = apply_spell(
        casting, command(1, kind="complete"), ctx, rng=RecordedDice(dice), system=True
    )
    check = resolved.checks[0]
    assert check.effective_target == 25 and check.margin == 25 - sum(dice)
    assert check.outcome is outcome and resolved.outcome == result
    assert replay_ceremonial_check(check) == check
    assert resolved.energy_spent == 3  # Includes critical success: every pledge is spent.
    assert next(p.current for p in done.pools if p.id == "fp:a") == 8
    assert next(p.current for p in done.pools if p.id == "fp:helper") == 9
    assert apply_spell(
        done, command(1, kind="complete"), ctx, rng=RecordedDice(()), system=True
    ) == (done, resolved)


def test_b238_high_skill_retains_its_real_resisted_contest_margin() -> None:
    casting, ctx = ritual(spell="daze")
    done, result = apply_spell(
        casting,
        command(1, kind="complete", spell="daze"),
        ctx,
        rng=RecordedDice((4, 5, 5, 5, 5, 5)),
        system=True,
    )
    assert [check.effective_target for check in result.checks] == [20, 18]
    # Rule of16 caps caster at targetHT18: margins4 versus3. The old15 cap loses.
    assert result.outcome == "active" and latest(done)["cast"].phase == "active"


@pytest.mark.parametrize(
    ("will_dice", "outcome", "energy"), [((3, 3, 3), "active", 3), ((4, 4, 3), "interrupted", 0)]
)
def test_b238_group_uses_full_will_and_no_cast_roll_means_no_pledge_spend(
    will_dice: tuple[int, int, int], outcome: str, energy: int
) -> None:
    casting, ctx = ritual()
    ctx = ctx.model_copy(update={"distracted": True})
    dice = RecordedDice((*will_dice, *((3, 3, 3) if energy else ())))
    done, result = apply_spell(casting, command(1, kind="complete"), ctx, rng=dice, system=True)
    assert result.checks[0].effective_target == 10  # Ordinary casting would use Will-3.
    assert result.outcome == outcome and result.energy_spent == energy
    assert len(result.checks) == (2 if energy else 1)
    assert next(p.current for p in done.pools if p.id == "fp:a") == (8 if energy else 10)
    assert dice.exhausted()


def test_b238_group_funds_do_not_require_leader_to_hold_the_whole_cost() -> None:
    casting, ctx = ritual(spell="daze", leader_fp=0, helper_fp=3)
    casting = casting.model_copy(
        update={
            "pools": tuple(
                p.model_copy(update={"current": 1}) if p.id == "fp:a" else p for p in casting.pools
            )
        }
    )
    done, result = apply_spell(
        casting,
        command(1, kind="complete", spell="daze"),
        ctx,
        rng=RecordedDice((3, 3, 3, 6, 6, 6)),
        system=True,
    )
    assert result.energy_spent == 3
    assert next(p.current for p in done.pools if p.id == "fp:a") == 1
    assert next(p.current for p in done.pools if p.id == "fp:helper") == 7


def test_b238_unavailable_pledge_rejects_before_any_cast_or_distraction_dice() -> None:
    casting, ctx = ritual()
    casting = casting.model_copy(
        update={
            "pools": tuple(
                p.model_copy(update={"current": 0}) if p.id == "fp:helper" else p
                for p in casting.pools
            )
        }
    )
    dice = RecordedDice(())
    with pytest.raises(ConflictError, match="promised FP"):
        apply_spell(
            casting,
            command(1, kind="complete"),
            ctx.model_copy(update={"distracted": True}),
            rng=dice,
            system=True,
        )
    assert dice.exhausted()
