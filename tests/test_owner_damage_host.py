"""B66/B378-381 approved owner damage changes actual persisted combat injury."""

import asyncio
import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, seed_campaign
from test_basic_combat import start_basic
from test_combat_sensory_authority import change
from test_composed_attack_host import declare
from test_composed_attacks import pick
from test_gurps_melee import setup
from trait_support import options

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.gurps_characters import source
from wayfarer.engine.rules.traits.attack_defense import RUNTIME_HOOKS, package
from wayfarer.engine.rules.traits.base import TraitOptions
from wayfarer.engine.rules.traits.modifiers import ModifierSelection
from wayfarer.engine.rules.traits.mundane import candidate_package
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.combat.commands import ChooseDefense, TakeCombatTurn
from wayfarer.engine.simulation.events import visible
from wayfarer.engine.simulation.health.cyclic_host_state import CyclicPolicy
from wayfarer.engine.simulation.traits.composed_sources import BindComposedSource
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.clock import CommandInstant
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.composed_attacks import ComposedAttackService
from wayfarer.orchestration.owner_damage_records import (
    ChooseOwnerDamage,
    OwnerDamageOutcome,
    OwnerDamagePending,
    PrepareOwnerDamage,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import SetRealPlayClock, TaskResult, snapshot
from wayfarer.orchestration.tasks import TaskService, real_play_clock
from wayfarer.persistence.replay import verify_commands


async def fixture(
    path: Path,
    backend: str = "sqlite",
    *,
    points: int = 15,
    with_luck: bool = True,
    modifiers: tuple[ModifierSelection, ...] = (),
    dr: int = 0,
    kind: str = "burn",
    luck_modifiers: tuple[str, ...] = (),
    cyclic_policy: CyclicPolicy | None = None,
) -> tuple[str, PlayService]:
    luck = replace(
        next(d for d in candidate_package().definitions if d.id == "trait:advantage:luck"),
        source_id=source("gurps-basic-set-4e-2004").id,
    )
    cid, original = await setup(
        path / "source",
        "gurps-basic-set-4e-2004",
        human=True,
        trained=False,
        allow_supernatural=True,
        scene_bound=True,
        start_encounter=False,
        extra_definitions=package().definitions + (luck,),
        trait_runtime_hooks=RUNTIME_HOOKS | SUPPORTED_HOOKS,
        extra_purchases=(
            Purchase(
                definition_id="advantage:innate-attack",
                amount=2,
                trait=options(**{"damage-type": kind}).model_copy(
                    update={"attack_modifiers": modifiers}
                ),
            ),
        )
        + (
            (
                Purchase(
                    definition_id=luck.id,
                    trait=TraitOptions(
                        parameters=(("point-cost", points),), modifiers=luck_modifiers
                    ),
                ),
            )
            if with_luck
            else ()
        )
        + ((Purchase(definition_id="advantage:damage-resistance", amount=dr),) if dr else ()),
    )
    await CombatService(original).execute(cid, start_basic(2, ranged=True), principal_id="gm")
    play = build_play(path, original.engine, backend=backend, rng=RecordedDice(()))
    await seed_campaign(play.store, await original.store.read(cid))
    await change(
        play,
        cid,
        lambda state: state.model_copy(
            update={
                "members": state.members
                + (
                    CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),
                    CampaignMember(principal_id="bob", role="player", actor_ids=("b",)),
                )
            }
        ),
    )
    state = play._load(await play.store.read(cid))
    bound = await ComposedAttackService(play).execute(
        cid,
        BindComposedSource(
            id="bind",
            actor_id="a",
            expected_revision=state.revision,
            description="Approved directed damage",
            specialty="beam",
            cyclic_policy=cyclic_policy,
        ),
        principal_id="gm",
    )
    assert bound.source_id
    state = play._load(await play.store.read(cid))
    await TaskService(play).execute(
        cid,
        SetRealPlayClock(id="clock", actor_id="gm", expected_revision=state.revision, running=True),
        principal_id="gm",
    )
    await declare(play, cid, bound.source_id)
    return cid, play


async def begin(
    play: PlayService,
    cid: str,
    *,
    secret: bool = False,
    principal: str = "bob",
    defense: str = "none",
    identifier: str = "prepare-damage",
) -> tuple[PrepareOwnerDamage, TaskResult]:
    state = play._load(await play.store.read(cid))
    response = ChooseDefense.model_validate(
        {
            "id": identifier,
            "actor_id": "b",
            "expected_revision": state.revision,
            "encounter_id": "fight",
            "defense": defense,
        }
    )
    command = PrepareOwnerDamage(
        id=response.id,
        actor_id=response.actor_id,
        expected_revision=response.expected_revision,
        response=response,
        secret=secret,
    )
    return command, await TaskService(play).execute(cid, command, principal_id=principal)


async def choose(
    play: PlayService,
    cid: str,
    *,
    luck: bool = True,
    principal: str = "alice",
    identifier: str = "choose-damage",
) -> tuple[ChooseOwnerDamage, TaskResult]:
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, OwnerDamagePending)
    command = ChooseOwnerDamage(
        id=identifier,
        actor_id="a",
        expected_revision=state.revision,
        pending_id=pending.id,
        choice="use-luck" if luck else "accept",
    )
    return command, await TaskService(play).execute(cid, command, principal_id=principal)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_damage_luck_changes_actual_armor_hp_shock_stun_and_posture(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(
        tmp_path, backend, modifiers=(pick("enhancement:armor-divisor", option="2"),), dr=5
    )
    before = play._load(await play.store.read(cid))
    play.rng = RecordedDice((2, 2, 2, 1, 1))
    prepared_command, response = await begin(play, cid)
    assert response.damage_json is None  # The defender does not control the owner's roll.
    pending_state = play._load(await play.store.read(cid))
    pending = snapshot(pending_state).pending
    assert isinstance(pending, OwnerDamagePending) and pending.original == (1, 1)
    assert next(p.current for p in pending_state.resources.pools if p.id == "hp:b") == 10
    assert pending_state.resources.game_time == before.resources.game_time
    assert pending_state.encounters[0].current_actor_id == "a"
    assert play.rng.exhausted()
    displayed = await TaskService(play).pending(cid, principal_id="alice")
    assert (
        displayed
        and displayed.damage_json
        and OwnerDamageOutcome.model_validate_json(displayed.damage_json).dice == (1, 1)
    )
    play.rng = RecordedDice((3, 4, 6, 5, 4, 4, 4))
    command, result = await choose(play, cid)
    assert (
        result.luck
        and result.luck.attempts == ((1, 1), (3, 4), (6, 5))
        and result.luck.chosen_index == 2
    )
    assert result.damage_json
    outcome = OwnerDamageOutcome.model_validate_json(result.damage_json)
    # B378 floor(5/2) DR and B379 burning x1: 11-2=9 HP injury.
    assert outcome.combat and outcome.combat.injury and outcome.combat.injury.injury == 9
    assert outcome.combat.injury.attack.dice == (2, 2, 2)
    assert outcome.combat.injury.resistance == 2 and outcome.dice == (6, 5)
    after = play._load(await play.store.read(cid))
    hp = next(p for p in after.resources.pools if p.id == "hp:b")
    assert (
        hp.current == 1
        and hp.injury
        and hp.injury.shock == 4
        and hp.injury.stunned
        and hp.injury.prone
    )
    assert next(p for p in after.encounters[0].participants if p.actor_id == "b").posture == "prone"
    assert (
        after.encounters[0].current_actor_id == "b" and after.encounters[0].pending_defense is None
    )
    assert snapshot(after).pending is None and len(snapshot(after).luck.receipts) == 1
    assert play.rng.exhausted()
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    assert await TaskService(restarted).execute(cid, command, principal_id="alice") == result
    assert (
        await TaskService(restarted).execute(cid, prepared_command, principal_id="bob") == response
    )
    assert restarted._load(await restarted.store.read(cid)) == after
    assert await restarted.store.read(cid) == await restarted.store.replay(cid)


@pytest.mark.parametrize("secret", [False, True])
async def test_authority_secret_visibility_gate_and_original_acceptance(
    tmp_path: Path, secret: bool
) -> None:
    cid, play = await fixture(tmp_path)
    play.rng = RecordedDice((2, 2, 2) + (() if secret else (1, 2)))
    with pytest.raises((AuthorizationError, ValidationError)):
        await begin(play, cid, secret=secret, principal="alice")
    await begin(play, cid, secret=secret, principal="gm" if secret else "bob")
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, OwnerDamagePending)
    view = await TaskService(play).pending(cid, principal_id="alice")
    assert view and view.pending_id == pending.id and view.check is None
    assert (view.damage_json is None) == secret
    with pytest.raises(AuthorizationError):
        await TaskService(play).pending(cid, principal_id="bob")
    with pytest.raises(ConflictError, match="pending task"):
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="later",
                actor_id="a",
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="do_nothing",
            ),
            principal_id="a",
        )
    with pytest.raises(AuthorizationError):
        await choose(play, cid, principal="gm")
    if secret:
        with pytest.raises(ValidationError, match="director|GM"):
            await choose(play, cid, luck=False)
    play.rng = RecordedDice((1, 2) if secret else ())
    _, result = await choose(play, cid, luck=False, principal="gm" if secret else "alice")
    assert result.luck is None and play.rng.exhausted()
    after = play._load(await play.store.read(cid))
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 7
    assert not real_play_clock(after).cooldowns
    member = next(m for m in after.members if m.principal_id == "alice")
    events = [
        e.event.model_dump_json() for e in await play.store.stream(cid) if visible(e.event, member)
    ]
    assert all("task-host:" not in e and "attempts" not in e for e in events)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_rollback_competing_independent_stores_and_post_commit_refusal(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend)
    play.rng = RecordedDice((2, 2, 2, 1, 1))
    await begin(play, cid)
    before = await play.store.read(cid)
    play.rng = RecordedDice((6, 6))
    with pytest.raises(ValidationError):
        await choose(play, cid)
    assert await play.store.read(cid) == before
    state = play._load(before)
    pending = snapshot(state).pending
    assert isinstance(pending, OwnerDamagePending)
    command = ChooseOwnerDamage(
        id="race",
        actor_id="a",
        expected_revision=state.revision,
        pending_id=pending.id,
        choice="use-luck",
    )
    peers = [
        build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice((2, 2, 2, 3)))
        for _ in range(2)
    ]
    instant = real_play_clock(state).observed_at_us
    assert instant is not None
    for peer in peers:
        peer.instants = lambda: CommandInstant(instant + 1)
    results = await asyncio.gather(
        *(TaskService(peer).execute(cid, command, principal_id="alice") for peer in peers)
    )
    assert results[0] == results[1]
    after = await play.store.read(cid)
    assert next(p.current for p in play._load(after).resources.pools if p.id == "hp:b") == 5
    assert len(snapshot(play._load(after)).luck.receipts) == 1
    with pytest.raises(ConflictError):
        await TaskService(play).execute(
            cid, command.model_copy(update={"choice": "accept"}), principal_id="alice"
        )
    with pytest.raises(ConflictError):
        await TaskService(play).execute(
            cid,
            command.model_copy(update={"id": "too-late", "expected_revision": state.revision + 1}),
            principal_id="alice",
        )
    assert await play.store.read(cid) == after


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("secret", [False, True])
async def test_complete_event_folding_and_seed_only_reexecution(
    tmp_path: Path, backend: str, secret: bool
) -> None:
    cid, play = await fixture(tmp_path, backend)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng = secrets
    # Seed 01 begins 1/3/1: ordinary success at the captured skill 6.
    play.seeds = lambda: "01" * 32
    await begin(play, cid, secret=secret, principal="gm" if secret else "bob")
    pending = snapshot(play._load(await play.store.read(cid))).pending
    assert pending is not None
    await choose(play, cid)
    records = (await play.store.history(cid))[count:]
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert len(checks) == 2 and all(c.folded and c.reexecuted for c in checks)
    assert replayed == await play.store.read(cid) == await play.store.replay(cid)
