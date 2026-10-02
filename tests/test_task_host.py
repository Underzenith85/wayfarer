"""B66/B346: selected rolls alter actual persisted task and world consequences."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, seed_campaign
from test_actions import world
from test_gurps_melee import setup

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.gurps_characters import source
from wayfarer.engine.rules.traits.base import TraitOptions
from wayfarer.engine.rules.traits.mundane import candidate_package
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.rules.types.skill import ControllingAttribute, Difficulty, SkillSpec
from wayfarer.engine.simulation.actions import ActionRules, CheckRule
from wayfarer.engine.simulation.campaign.activities import LongTaskRule
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import (
    BeginTaskCheck,
    BeginTaskWork,
    BindLongTask,
    ChooseTaskCheck,
    SetRealPlayClock,
    TaskResult,
    snapshot,
)
from wayfarer.orchestration.tasks import TaskService


async def fixture(
    path: Path,
    backend: str = "sqlite",
    *,
    points: int = 15,
    fatigue_cost: int = 0,
    action: Literal["inspect", "social"] = "inspect",
    modifiers: tuple[str, ...] = (),
) -> tuple[str, PlayService]:
    luck = next(
        definition
        for definition in candidate_package().definitions
        if definition.id == "trait:advantage:luck"
    )
    luck = replace(luck, source_id=source("gurps-basic-set-4e-2004").id)
    skills = tuple(
        RuleDefinition(
            "skill:" + key,
            DefinitionKind.SKILL,
            key.title(),
            luck.source_id,
            None,
            ImplementationStatus.IMPLEMENTED,
            hooks=("character.gurps-skill", "check.target"),
            skill=SkillSpec(
                ControllingAttribute.IQ,
                Difficulty.HARD if key == "diplomacy" else Difficulty.AVERAGE,
                page,
            ),
        )
        for key, page in (("carpentry", "B183"), ("administration", "B174"), ("leadership", "B204"))
        + ((("diplomacy", "B187"),) if action == "social" else ())
    )
    cid, original = await setup(
        path / "source",
        "gurps-basic-set-4e-2004",
        human=True,
        trained=False,
        start_encounter=False,
        allow_supernatural=True,
        extra_definitions=(luck,) + skills,
        trait_runtime_hooks=SUPPORTED_HOOKS,
        extra_purchases=(
            Purchase(
                definition_id=luck.id,
                trait=TraitOptions(parameters=(("point-cost", points),), modifiers=modifiers),
            ),
        )
        + tuple(
            Purchase(definition_id=skill.id, amount=12 if skill.id == "skill:diplomacy" else 8)
            for skill in skills
        ),
        runtime_world=world(),
        aware_of=("chest", "b"),
        runtime_rules=ActionRules(
            id="tasks",
            version=1,
            fatigue_cost=fatigue_cost,
            checks=(
                CheckRule(
                    id="carpentry-inspect",
                    action=action,
                    target_id="chest" if action == "inspect" else "b",
                    definition_id="skill:carpentry" if action == "inspect" else "skill:diplomacy",
                    package_id="package:gurps-basic-set-4e-2004-characters",
                    package_version="1.0.0",
                    duration=1,
                    reveal_fact_ids=("clue" if action == "inspect" else "promise",),
                ),
            ),
        ),
    )
    play = build_play(path, original.engine, backend=backend, rng=RecordedDice(()))
    await seed_campaign(play.store, await original.store.read(cid))
    state = play._load(await play.store.read(cid))
    await TaskService(play).execute(
        cid,
        SetRealPlayClock(
            id="resume", actor_id="gm", expected_revision=state.revision, running=True
        ),
        principal_id="gm",
    )
    return cid, play


async def begin_check(
    play: PlayService, cid: str, identifier: str = "inspect", secret: bool = False
) -> tuple[BeginTaskCheck, TaskResult]:
    state = play._load(await play.store.read(cid))
    command = BeginTaskCheck(
        id=identifier,
        actor_id="a",
        expected_revision=state.revision,
        check_id="carpentry-inspect",
        secret=secret,
    )
    return command, await TaskService(play).execute(
        cid, command, principal_id="gm" if secret else "a"
    )


async def choose(
    play: PlayService,
    cid: str,
    pending: str | None,
    *,
    luck: bool = True,
    identifier: str = "choose",
    actor: str = "a",
    principal: str | None = None,
) -> tuple[ChooseTaskCheck, TaskResult]:
    assert pending is not None
    state = play._load(await play.store.read(cid))
    command = ChooseTaskCheck(
        id=identifier,
        actor_id=actor,
        expected_revision=state.revision,
        kind="use-luck" if luck else "accept-check",
        pending_id=pending,
    )
    return command, await TaskService(play).execute(cid, command, principal_id=principal or actor)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_luck_changes_real_world_fact_once_after_restart(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend)
    play.rng = RecordedDice((6, 6, 6))
    _, original = await begin_check(play, cid)
    before = play._load(await play.store.read(cid))
    assert original.check and original.check.total == 18
    assert not before.world.knowledge
    assert before.resources.game_time == 1
    play.rng = RecordedDice((5, 5, 5, 2, 2, 3))
    command, result = await choose(play, cid, original.pending_id)
    after = play._load(await play.store.read(cid))
    assert result.check and result.check.total == 7
    assert result.action and result.action.revealed_fact_ids == ("clue",)
    assert ("a", "clue") in after.world.knowledge
    assert after.resources.game_time == 1 and snapshot(after).pending is None
    assert result.luck and result.luck.attempts == ((6, 6, 6), (5, 5, 5), (2, 2, 3))
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    assert await TaskService(restarted).execute(cid, command, principal_id="a") == result
    assert restarted._load(await restarted.store.read(cid)) == after
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_long_task_selected_roll_changes_actual_progress(tmp_path: Path) -> None:
    cid, play = await fixture(tmp_path)
    state = play._load(await play.store.read(cid))
    await TaskService(play).execute(
        cid,
        BindLongTask(
            id="bind",
            actor_id="a",
            expected_revision=state.revision,
            rule=LongTaskRule(id="bridge", target_id="skill:carpentry", required_man_hours=8),
            location_id="dock",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((6, 6, 6))
    begun = await TaskService(play).execute(
        cid,
        BeginTaskWork(
            id="shift",
            actor_id="a",
            expected_revision=state.revision,
            task_id="bridge",
            seconds=28800,
        ),
        principal_id="a",
    )
    assert begun.check and begun.check.total == 18
    play.rng = RecordedDice((5, 5, 5, 2, 2, 3))
    _, selected = await choose(play, cid, begun.pending_id)
    assert selected.activity and selected.activity.progress == Decimal(8)
    assert selected.activity.total_progress == Decimal(8) and selected.activity.completed
    assert play._load(await play.store.read(cid)).resources.game_time == 28800


async def test_pending_boundary_requires_explicit_continuation_and_cannot_reopen(
    tmp_path: Path,
) -> None:
    from wayfarer.engine.simulation.actions import Wait

    cid, play = await fixture(tmp_path)
    play.rng = RecordedDice((4, 4, 4))
    _, result = await begin_check(play, cid)
    pending = play._load(await play.store.read(cid))
    with pytest.raises(ConflictError, match="pending task roll"):
        await play.execute(
            cid,
            Wait(id="later", actor_id="b", expected_revision=pending.revision, ticks=1),
            principal_id="b",
        )
    assert play._load(await play.store.read(cid)) == pending
    play.rng = RecordedDice(())
    await choose(play, cid, result.pending_id, luck=False, principal="gm")
    with pytest.raises(ConflictError, match="no longer"):
        await choose(play, cid, result.pending_id, identifier="too-late")


async def test_secret_original_is_private_and_cannot_be_rerolled_after_reveal(
    tmp_path: Path,
) -> None:
    cid, play = await fixture(tmp_path)
    play.rng = RecordedDice((6, 6, 6))
    _, result = await begin_check(play, cid, secret=True)
    visible = await TaskService(play).pending(cid, principal_id="a")
    assert visible and visible.check is None and visible.pending_id is None
    with pytest.raises(ValidationError, match="Secret Luck"):
        await choose(play, cid, result.pending_id, principal="gm")
    await choose(play, cid, result.pending_id, luck=False, principal="gm")
