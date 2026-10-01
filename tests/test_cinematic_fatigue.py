"""Cinematic power costs use B426-427 fatigue and authority receipts."""

import pytest
from test_cinematic_skills import compiler, world
from test_mastery_combat import purchase
from test_statistics import gurps_draft

from wayfarer.engine.character.compiler import Purchase, ValidatedBuild
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.recovery import FatigueStatus
from wayfarer.engine.simulation.health.fatigue import fatigue_value
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.engine.simulation.skills.cinematic import CinematicSkillCommand, apply_cinematic_skill
from wayfarer.errors import AuthorizationError, ValidationError

PROFILE = "gurps-basic-set-4e-2004"


def power_build() -> ValidatedBuild:
    result = compiler(include_masters=True).compile(
        gurps_draft(
            purchase("trained-by-a-master"),
            Purchase(definition_id="skill:power-blow", amount=4),
        )
    )
    assert result.build is not None, result.diagnostics
    return result.build


def pools(current: int) -> ResourceState:
    return ResourceState(
        pools=(
            Pool(
                id="fp:inventor",
                current=current,
                maximum=10,
                fatigue=FatigueStatus(profile_id=PROFILE),
            ),
            Pool(id="hp:inventor", current=10, maximum=10, injury=InjuryStatus(profile_id=PROFILE)),
        )
    )


def command(build: ValidatedBuild) -> CinematicSkillCommand:
    return CinematicSkillCommand(
        id="power",
        actor_id="inventor",
        expected_revision=0,
        build_revision=build.revision,
        skill_id="skill:power-blow",
        concentration_turns=32,
    )


@pytest.mark.parametrize("dice,outcome", [([2, 2, 2], "success"), ([4, 4, 4], "failure")])
def test_power_cost_updates_actual_fatigue_threshold_and_replays(
    dice: list[int], outcome: str
) -> None:
    build = power_build()
    changed, result = apply_cinematic_skill(
        pools(4),
        world(),
        build,
        command(build),
        authorized_actor_id="inventor",
        rng=RecordedDice(dice),
    )
    fp = next(p for p in changed.pools if p.id == "fp:inventor")
    assert result.outcome == outcome and result.fatigue_spent == 1
    assert fp.current == 3 and fp.fatigue is not None and fp.fatigue.power == 1
    assert fatigue_value(fp, 10) == 5
    restart = ResourceState.model_validate_json(changed.model_dump_json())
    assert apply_cinematic_skill(
        restart,
        world(),
        build,
        command(build),
        authorized_actor_id="inventor",
        rng=RecordedDice([]),
    ) == (restart, result)
    with pytest.raises(AuthorizationError):
        apply_cinematic_skill(
            restart,
            world(),
            build,
            command(build),
            authorized_actor_id="observer",
            rng=RecordedDice([]),
        )


def test_failed_exertion_persists_collapse_and_does_not_roll_the_power() -> None:
    build = power_build()
    changed, result = apply_cinematic_skill(
        pools(-1),
        world(),
        build,
        command(build),
        authorized_actor_id="inventor",
        rng=RecordedDice([4, 4, 4]),
    )
    fp = next(p for p in changed.pools if p.id == "fp:inventor")
    assert not result.exertion_allowed and result.fatigue_spent == 0
    assert fp.current == -1 and fp.fatigue is not None and fp.fatigue.collapsed
    assert apply_cinematic_skill(
        changed,
        world(),
        build,
        command(build),
        authorized_actor_id="inventor",
        rng=RecordedDice([]),
    ) == (changed, result)


def test_missing_canonical_pools_rejected_before_dice() -> None:
    build = power_build()
    with pytest.raises(ValidationError, match="FP and HP"):
        apply_cinematic_skill(
            ResourceState(),
            world(),
            build,
            command(build),
            authorized_actor_id="inventor",
            rng=RecordedDice([]),
        )


@pytest.mark.parametrize("skill", ["breaking-blow", "push"])
def test_weapon_master_does_not_unlock_tbaM_only_skills(skill: str) -> None:
    result = compiler(include_masters=True).compile(
        gurps_draft(purchase("weapon-master"), Purchase(definition_id="skill:" + skill, amount=4))
    )
    assert not result.legal
