"""B66 predeclared secret opponent checks keep dice and targets GM-only."""

import json
import secrets
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, build_runtime
from test_blindness_combat_consumers import blind
from test_combat_sensory_authority import change
from test_composed_attack_host import declare
from test_gadgeteer_gizmos_persistence import FailingCommitPlay
from test_opponent_attack_host import fixture, next_attack

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.combat.encounter import CombatResult
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.clock import CommandInstant
from wayfarer.orchestration.opponent_attack_records import BeginOpponentAttack, ChooseOpponentAttack
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import RESULT_PREFIX, TaskResult, identity, snapshot
from wayfarer.orchestration.tasks import TaskService, real_play_clock
from wayfarer.persistence.replay import verify_commands


async def prepare(
    play: PlayService,
    cid: str,
    *,
    identifier: str = "secret",
    prepare_owner_damage: bool = False,
) -> tuple[BeginOpponentAttack, TaskResult]:
    state = play._load(await play.store.read(cid))
    incoming = state.encounters[0].pending_defense
    assert incoming
    command = BeginOpponentAttack(
        id=identifier,
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        attack_id=incoming.id,
        visibility="secret",
        prepare_owner_damage=prepare_owner_damage,
    )
    return command, await TaskService(play).execute(cid, command, principal_id="gm")


async def choose_secret(
    play: PlayService,
    cid: str,
    pending: str | None,
    *,
    identifier: str = "secret-choice",
    choice: Literal["use-luck", "accept", "cancel"] = "use-luck",
    principal: str = "bob",
) -> tuple[ChooseOpponentAttack, TaskResult]:
    assert pending
    state = play._load(await play.store.read(cid))
    command = ChooseOpponentAttack(
        id=identifier,
        actor_id="b",
        expected_revision=state.revision,
        pending_id=pending,
        choice=choice,
        response=None
        if choice == "cancel"
        else ChooseDefense(
            id=identifier,
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        ),
    )
    return command, await TaskService(play).execute(cid, command, principal_id=principal)


def private_result(play: PlayService, campaign: Campaign, command_id: str) -> TaskResult:
    state = play._load(campaign)
    event = next(e for e in state.resources.events if e.id == identity(RESULT_PREFIX, command_id))
    return TaskResult.model_validate_json(event.kind)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "choice,faces,damage",
    [
        ("use-luck", (1, 1, 1, 3, 3, 3, 5, 5, 5), 0),
        ("accept", (3, 3, 3, 2, 2), 4),
        ("cancel", (), 0),
    ],
)
async def test_secret_prepare_owner_declaration_and_gm_resolution_keep_actual_outcomes_private(
    tmp_path: Path,
    backend: str,
    choice: Literal["use-luck", "accept", "cancel"],
    faces: tuple[int, ...],
    damage: int,
) -> None:
    cid, play, source = await fixture(tmp_path, backend)
    await declare(play, cid, source)
    await change(play, cid, blind)
    declared = play._load(await play.store.read(cid))
    play.rng = RecordedDice(())
    command, begun = await prepare(play, cid)
    assert begun.secret and begun.check is None and begun.luck is None and begun.pending_id
    owner_view = await TaskService(play).pending(cid, principal_id="bob")
    assert owner_view and owner_view.pending_id == begun.pending_id
    assert owner_view.check is None and owner_view.combat_json is None
    assert play._load(await play.store.read(cid)).encounters == declared.encounters
    assert play.rng.exhausted()
    with pytest.raises(ValidationError):
        await TaskService(play).execute(cid, command, principal_id="bob")
    with pytest.raises(AuthorizationError):
        await choose_secret(play, cid, begun.pending_id, principal="gm")
    with pytest.raises(ValidationError):
        await choose_secret(play, cid, begun.pending_id, choice="accept", principal="bob")
    play.rng = RecordedDice(faces)
    principal = "bob" if choice == "use-luck" else "gm"
    decision, result = await choose_secret(
        play, cid, begun.pending_id, choice=choice, principal=principal
    )
    if choice == "use-luck":
        assert (
            result.secret
            and result.check is None
            and result.luck is None
            and result.combat_json is None
        )
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10 - damage
    assert snapshot(state).pending is None and play.rng.exhausted()
    evidence = private_result(play, await play.store.read(cid), decision.id)
    if choice == "use-luck":
        assert evidence.check and evidence.check.total == 15 and evidence.luck
        assert tuple(map(sum, evidence.luck.attempts)) == (3, 9, 15)
        assert (
            evidence.combat_json and CombatResult.model_validate_json(evidence.combat_json).injury
        )
    elif choice == "cancel":
        assert state.encounters == declared.encounters and real_play_clock(state).cooldowns == ()
    runtime = build_runtime(play)
    view = json.dumps(await runtime.read(cid, principal_id="bob"))
    events = " ".join(e.model_dump_json() for e in await runtime.events(cid, principal_id="bob"))
    for hidden in (
        "effective_target",
        "chosen_index",
        "attempts",
        "context_digest",
        "opponent-attack:",
    ):
        assert hidden not in view and hidden not in events
    restarted = build_play(
        tmp_path, play.engine, backend=backend, rng=RecordedDice(()), instants=play.instants
    )
    assert await TaskService(restarted).execute(cid, decision, principal_id=principal) == result
    assert await restarted.store.read(cid) == await restarted.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_secret_attack_seed_reexecution_and_rollback_include_actual_consequences(
    tmp_path: Path, backend: str
) -> None:
    cid, play, source = await fixture(tmp_path, backend)
    await declare(play, cid, source)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng = secrets
    play.seeds = lambda: "ab" * 32
    _, begun = await prepare(play, cid)
    before = await play.store.read(cid)
    failing = FailingCommitPlay(
        play.store, play.engine, rng=RecordedDice((3, 3, 3) * 3 + (2, 2)), instants=play.instants
    )
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await choose_secret(failing, cid, begun.pending_id)
    assert await play.store.read(cid) == before
    choice, result = await choose_secret(play, cid, begun.pending_id)
    assert result.check is None and result.luck is None
    final = await play.store.read(cid)
    private = private_result(play, final, choice.id)
    assert private.luck and tuple(map(sum, private.luck.attempts)) == (15, 7, 9)
    assert private.check and private.check.total == 15
    records = (await play.store.history(cid))[count:]
    identifiers = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in identifiers],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert len(checks) == 2 and all(c.folded and c.reexecuted for c in checks)
    assert replayed == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("points,seconds", [(15, 3600), (30, 1800), (60, 600)])
async def test_secret_preparation_during_cooldown_can_declare_at_exact_deadline(
    tmp_path: Path,
    backend: str,
    points: int,
    seconds: int,
) -> None:
    cid, play, source = await fixture(tmp_path, backend, points=points)
    origin = real_play_clock(play._load(await play.store.read(cid))).observed_at_us
    assert origin
    now = [origin + 900001]
    play.instants = lambda: CommandInstant(now[0])
    await declare(play, cid, source)
    _, first = await prepare(play, cid)
    play.rng = RecordedDice((5, 5, 5) * 3)
    await choose_secret(play, cid, first.pending_id)
    play.rng = RecordedDice(())
    await next_attack(play, cid, source, "second-attack")
    _, second = await prepare(play, cid, identifier="second-secret")
    deadline = origin + seconds * 1_000_000 + 900001
    now[0] = deadline - 1
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="cooling down"):
        await choose_secret(play, cid, second.pending_id, identifier="second-choice")
    assert await play.store.read(cid) == before
    now[0] = deadline
    play.rng = RecordedDice((5, 5, 5) * 3)
    await choose_secret(play, cid, second.pending_id, identifier="second-choice")
    assert len(snapshot(play._load(await play.store.read(cid))).luck.receipts) == 2


async def test_secret_source_changes_reject_before_dice_and_gm_cancel_preserves_declaration(
    tmp_path: Path,
) -> None:
    from test_symptom_attribute_consumers import attribute_penalty

    cid, play, source = await fixture(tmp_path)
    await declare(play, cid, source)
    _, begun = await prepare(play, cid)
    await change(
        play,
        cid,
        lambda s: s.model_copy(
            update={"resources": attribute_penalty(s.resources, "a", "dx", level=2)}
        ),
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError, match="context changed"):
        await choose_secret(play, cid, begun.pending_id)
    assert await play.store.read(cid) == before
    await choose_secret(play, cid, begun.pending_id, choice="cancel", principal="gm")
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].pending_defense and snapshot(state).pending is None
    assert real_play_clock(state).cooldowns == () and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_secret_opponent_choice_keeps_following_attacker_damage_private(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.orchestration.owner_damage_records import ChooseOwnerDamage

    cid, play, source = await fixture(tmp_path, backend)
    await declare(play, cid, source)
    _, begun = await prepare(play, cid, prepare_owner_damage=True)
    assert begun.pending_id
    state = play._load(await play.store.read(cid))
    command = ChooseOpponentAttack(
        id="two-owners",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=begun.pending_id,
        choice="use-luck",
        response=ChooseDefense(
            id="two-owners",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        ),
    )
    play.rng = RecordedDice((1, 1, 1, 1, 2, 2, 3, 3, 3))
    result = await TaskService(play).execute(cid, command, principal_id="bob")
    assert (
        result.secret
        and result.status == "completed"
        and result.check is None
        and result.luck is None
    )
    assert result.damage_json is None and result.combat_json is None
    assert play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    with pytest.raises(AuthorizationError):
        await TaskService(play).pending(cid, principal_id="bob")
    owner_view = await TaskService(play).pending(cid, principal_id="alice")
    assert (
        owner_view
        and owner_view.secret
        and owner_view.pending_id
        and owner_view.damage_json is None
    )
    play.rng = RecordedDice((1, 1, 2, 2, 3, 3, 2, 2, 2))
    final = await TaskService(play).execute(
        cid,
        ChooseOwnerDamage(
            id="damage",
            actor_id="a",
            expected_revision=state.revision,
            pending_id=owner_view.pending_id,
            choice="use-luck",
        ),
        principal_id="alice",
    )
    assert (
        final.secret
        and final.luck is None
        and final.damage_json is None
        and final.combat_json is None
    )
    assert (
        next(
            p.current
            for p in play._load(await play.store.read(cid)).resources.pools
            if p.id == "hp:b"
        )
        == 4
    )
    assert play.rng.exhausted() and await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_secret_hex_attack_never_projects_original_or_selected_target_totals(
    tmp_path: Path, backend: str
) -> None:
    from dataclasses import replace

    from test_actions import world
    from test_gurps_maneuvers import turn
    from test_gurps_melee import setup
    from test_issue_714_shield_rush import board
    from test_opponent_attack_routes import enroll, luck_source

    from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
    from wayfarer.engine.simulation.combat.spatial import Placement
    from wayfarer.engine.simulation.hex_geometry import Hex
    from wayfarer.engine.world import Fact
    from wayfarer.orchestration.membership import member_for
    from wayfarer.orchestration.tactical_view.projection import project

    definition, purchase = luck_source()
    actual_world = world()
    actual_world = replace(
        actual_world,
        facts=actual_world.facts
        + (Fact("seen-a", "a", "visible", "yes"), Fact("seen-b", "b", "visible", "yes")),
        knowledge=actual_world.knowledge + (("a", "seen-b"), ("b", "seen-a")),
    )
    cid, original = await setup(
        tmp_path / "source",
        "gurps-basic-set-4e-2004",
        human=True,
        runtime_world=actual_world,
        battlefield=board(),
        allow_supernatural=True,
        placements=(
            Placement(actor_id="a", position=Hex(q=0, r=0), hex_facing=0),
            Placement(actor_id="b", position=Hex(q=1, r=0), hex_facing=3),
        ),
        extra_definitions=(definition,),
        extra_purchases=(purchase,),
        trait_runtime_hooks=SUPPORTED_HOOKS,
    )
    play = await enroll(tmp_path, backend, cid, original)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    play.rng = RecordedDice(())
    _, begun = await prepare(play, cid)
    play.rng = RecordedDice((1, 1, 1, 3, 3, 3, 5, 5, 5))
    choice, result = await choose_secret(play, cid, begun.pending_id, principal="b")
    assert result.check is None and result.luck is None and result.combat_json is None
    state = play._load(await play.store.read(cid))
    own = project(play, state, member_for(state, "b"), "b")
    trace = next(
        t for encounter in own.encounters for t in encounter.traces if t.command_id == choice.id
    )
    assert trace.totals == () and trace.targets == ()
    full = private_result(play, await play.store.read(cid), choice.id)
    assert full.check and full.check.total == 15 and full.check.effective_target == 13
    assert full.combat_json and CombatResult.model_validate_json(full.combat_json).injury
    assert play.rng.exhausted()
