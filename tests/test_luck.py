"""Independent B66 outcomes: 2 rerolls, best/worst choice, real-play cooldowns."""

from dataclasses import replace

import pytest
from test_cinematic_skills import compiler as cinematic_compiler
from test_cinematic_skills import world
from test_mundane_traits import runtime_compiler
from test_statistics import gurps_draft, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase, ValidatedBuild
from wayfarer.engine.rules.catalog import PackagePin, RuleDefinition, RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.skills.cinematic import package as cinematic_package
from wayfarer.engine.rules.traits.base import TraitOptions
from wayfarer.engine.rules.traits.mundane import candidate_package
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.skills.cinematic import CinematicSkillCommand, apply_cinematic_skill
from wayfarer.engine.simulation.skills.luck import apply_lucky_cinematic_skill
from wayfarer.engine.simulation.traits.luck import (
    LuckCommand,
    LuckRoll,
    LuckState,
    apply_luck,
    luck_cooldown,
)
from wayfarer.errors import ConflictError, ValidationError

SEED = "ab" * 32


def approved(points: int = 15) -> tuple[ValidatedBuild, dict[str, RuleDefinition]]:
    compiler = runtime_compiler()
    result = compiler.compile(
        gurps_draft(
            Purchase(
                definition_id="trait:advantage:luck",
                trait=TraitOptions(parameters=(("point-cost", points),)),
            )
        )
    )
    assert result.build is not None, result.diagnostics
    return result.build, dict(compiler.definitions)


def pending(**changes: object) -> LuckState:
    roll = LuckRoll(id="check", actor_id="inventor", original=(6, 6, 6))
    roll = roll.model_copy(update=changes)
    return LuckState(rolls=(roll,), pending_roll_id=roll.id)


def command(revision: int = 0, identifier: str = "luck") -> LuckCommand:
    return LuckCommand(
        id=identifier, actor_id="inventor", expected_revision=revision, roll_id="check"
    )


def invoke(
    state: LuckState,
    use: LuckCommand | None = None,
    *,
    points: int = 15,
    now: int = 0,
) -> LuckState:
    build, definitions = approved(points)
    return apply_luck(
        state,
        use or command(),
        build,
        definitions,
        real_time=now,
        seed=SEED,
        authorized_actor_id="inventor",
        system=True,
    )[0]


@pytest.mark.parametrize(("points", "seconds"), [(15, 3600), (30, 1800), (60, 600)])
def test_tiers_use_elapsed_real_play_time_not_clock_hour_or_campaign_time(
    points: int,
    seconds: int,
) -> None:
    build, definitions = approved(points)
    assert luck_cooldown(build, definitions) == seconds
    state = invoke(pending(), points=points, now=3599)
    assert state.receipts[0].available_at == 3599 + seconds
    next_roll = LuckRoll(id="check", actor_id="inventor", original=(6, 6, 6))
    state = state.model_copy(
        update={"rolls": (next_roll,), "pending_roll_id": "check", "game_time": 10000000}
    )
    with pytest.raises(ValidationError, match="cooling"):
        invoke(state, command(1, "second"), points=points, now=3599 + seconds - 1)
    again = invoke(state, command(1, "second"), points=points, now=3599 + seconds)
    assert again.revision == 2
    assert again.receipts[-1].available_at == 3599 + seconds * 2
    # Waiting for many hours never accumulates extra uses.
    later = again.model_copy(update={"rolls": (next_roll,), "pending_roll_id": "check"})
    later = invoke(later, command(2, "third"), points=points, now=100000000)
    with pytest.raises(ValidationError, match="cooling"):
        invoke(
            later.model_copy(update={"rolls": (next_roll,), "pending_roll_id": "check"}),
            command(3, "fourth"),
            points=points,
            now=100000000,
        )


@pytest.mark.parametrize(
    ("kind", "scope", "selection"),
    [
        ("success", "own", min),
        ("damage", "own", max),
        ("reaction", "own", max),
        ("success", "attack", max),
        ("damage", "attack", min),
        ("success", "party-event", min),
    ],
)
def test_correct_dice_and_selection(kind: str, scope: str, selection: object) -> None:
    state = invoke(pending(kind=kind, scope=scope, affected_actor_ids=("inventor",)))
    receipt = state.receipts[0]
    assert len(receipt.attempts) == 3
    assert receipt.attempts[0] == (6, 6, 6)
    assert receipt.attempts[1:] == ((3, 6, 6), (1, 2, 4))
    totals = [sum(dice) for dice in receipt.attempts]
    expected = min(totals) if selection is min else max(totals)
    assert state.rolls[0].chosen_total == expected
    assert state.rolls[0].chosen_dice == receipt.attempts[receipt.chosen_index]
    assert state.pending_roll_id is None


def test_checkpoint_retry_is_exact_and_does_not_roll_or_consume_again() -> None:
    initial = pending()
    once = invoke(initial)
    assert invoke(initial) == once
    restored = LuckState.model_validate_json(once.model_dump_json())
    build, definitions = approved()
    result, receipt = apply_luck(
        restored,
        command(),
        build,
        definitions,
        real_time=0,
        seed="invalid",
        authorized_actor_id="inventor",
        system=True,
    )
    assert result == once
    assert receipt == once.receipts[0]
    with pytest.raises(ConflictError, match="already used"):
        invoke(once, command().model_copy(update={"roll_id": "other"}))
    with pytest.raises(ConflictError, match="revision"):
        invoke(once, command(0, "stale"))


def test_secret_declaration_and_nontransferability() -> None:
    state = invoke(pending(secret=True, original=None))
    assert len(state.receipts[0].attempts) == 3
    assert state.receipts[0].secret
    with pytest.raises(ValidationError, match="before"):
        invoke(pending(secret=True))
    with pytest.raises(ValidationError, match="shared"):
        invoke(pending(actor_id="observer"))
    with pytest.raises(ValidationError, match="shared"):
        invoke(pending(scope="attack", affected_actor_ids=("observer",)))
    with pytest.raises(ValidationError, match="immediately"):
        invoke(pending().model_copy(update={"pending_roll_id": "later"}))


def test_unavailable_unauthorized_and_backwards_clock_reject() -> None:
    compiler = runtime_compiler()
    result = compiler.compile(gurps_draft())
    assert result.build is not None
    with pytest.raises(ValidationError, match="unavailable"):
        apply_luck(
            pending(),
            command(),
            result.build,
            compiler.definitions,
            real_time=0,
            seed=SEED,
            authorized_actor_id="inventor",
            system=True,
        )
    build, definitions = approved()
    with pytest.raises(ValidationError, match="authority"):
        apply_luck(
            pending(),
            command(),
            build,
            definitions,
            real_time=0,
            seed=SEED,
            authorized_actor_id="observer",
            system=True,
        )
    with pytest.raises(ValidationError, match="backwards"):
        invoke(pending().model_copy(update={"real_time": 10}), now=9)


def test_luck_changes_committed_skill_outcome_and_cannot_rewrite_it() -> None:
    compiler = cinematic_compiler()
    package = profile_package("gurps-basic-set-4e-2004")
    mundane = candidate_package()
    combined = replace(
        package,
        sources=package.sources + cinematic_package().sources + mundane.sources,
        definitions=tuple(compiler.definitions.values()) + mundane.definitions,
    )
    compiler = CharacterCompiler(
        RulesCatalog((combined,)),
        replace(
            compiler.rules, packages=(PackagePin(combined.id, combined.version, combined.digest),)
        ),
        replace(
            compiler.policy, permitted_sources=frozenset(source.id for source in combined.sources)
        ),
        statistics_profile="gurps-basic-set-4e-2004",
        trait_runtime_hooks=runtime_compiler().trait_runtime_hooks,
    )
    result = compiler.compile(
        gurps_draft(
            Purchase(definition_id="skill:weird-science", amount=4),
            Purchase(
                definition_id="trait:advantage:luck",
                trait=TraitOptions(parameters=(("point-cost", 15),)),
            ),
        )
    )
    assert result.build is not None, result.diagnostics
    build = result.build
    skill = CinematicSkillCommand(
        id="check",
        actor_id="inventor",
        expected_revision=0,
        build_revision=build.revision,
        skill_id="skill:weird-science",
    )
    initial = ResourceState()
    failed, bad = apply_cinematic_skill(
        initial,
        world(),
        build,
        skill,
        authorized_actor_id="inventor",
        rng=RecordedDice((6, 6, 6)),
    )
    assert bad.outcome == "critical-failure"
    state, luck, outcome = apply_lucky_cinematic_skill(
        initial,
        pending(),
        world(),
        build,
        compiler.definitions,
        skill,
        command(),
        real_time=0,
        seed=SEED,
        authorized_actor_id="inventor",
        system=True,
    )
    assert outcome.outcome == "success"
    assert outcome.margin == 2
    assert state.revision == 1
    assert state.events[0].kind == outcome.model_dump_json()
    assert luck.rolls[0].chosen_total == 7
    assert apply_lucky_cinematic_skill(
        state,
        luck,
        world(),
        build,
        compiler.definitions,
        skill,
        command(),
        real_time=0,
        seed="invalid",
        authorized_actor_id="inventor",
        system=True,
    ) == (state, luck, outcome)
    with pytest.raises(ConflictError, match="already committed"):
        apply_lucky_cinematic_skill(
            failed,
            pending(),
            world(),
            build,
            compiler.definitions,
            skill,
            command(),
            real_time=0,
            seed=SEED,
            authorized_actor_id="inventor",
            system=True,
        )


def test_secret_dice_remain_gm_only() -> None:
    from wayfarer.engine.simulation.traits.luck import visible_history

    hidden = invoke(pending(secret=True, original=None))
    assert visible_history(hidden, viewer_actor_id="inventor") == ()
    assert visible_history(hidden, viewer_actor_id="observer") == ()
    assert visible_history(hidden, viewer_actor_id="gm", gm=True) == hidden.receipts
    ordinary = invoke(pending())
    assert visible_history(ordinary, viewer_actor_id="inventor") == ordinary.receipts
    assert visible_history(ordinary, viewer_actor_id="observer") == ()


def test_damage_uses_entire_authored_expression_and_keeps_original_on_tie() -> None:
    damage = invoke(pending(kind="damage", dice_count=1, original=(6,), modifier=2))
    receipt = damage.receipts[0]
    assert receipt.attempts == ((6,), (3,), (6,))
    assert receipt.chosen_index == 0
    assert damage.rolls[0].chosen_dice == (6,)
    assert damage.rolls[0].chosen_total == 8
