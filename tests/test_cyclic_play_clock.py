"""Real play clocks refresh full body authority between B103 damage deadlines."""

import secrets
from pathlib import Path

import pytest
from support.runtime import build_play, played, seed_campaign
from test_actions import campaign
from test_cyclic_host import current, hp, prepare
from test_harmful_physiology_persistence import clock
from test_harmful_physiology_transformations import prepare_form, start, transform

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.cyclic import CyclicAttack, CyclicOccurrence
from wayfarer.engine.simulation.actions import ActorSetup, PlayState, Wait
from wayfarer.engine.simulation.health.cyclic import save
from wayfarer.engine.simulation.health.cyclic_host_state import CyclicPolicy, bind_occurrence
from wayfarer.engine.simulation.resources import Advance, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_ordinary_wait_settles_every_cycle_once_and_seed_reexecutes(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await prepare(tmp_path, backend)
    initial = await play.store.read(cid)
    with pytest.raises(ConflictError, match="Resource revision changed"):
        play.advance_clock(
            play._load(initial),
            Advance(id="stale-inner-clock", actor_id="a", expected_revision=0, to=20),
            RecordedDice(()),
        )
    play.rng = secrets
    command = Wait(id="two-deadlines", actor_id="a", expected_revision=1, ticks=20)
    result = await play.execute(cid, command, principal_id="a")
    final = await play.store.read(cid)
    state = play._load(final)
    assert state.revision == state.resources.revision == 2
    assert state.resources.game_time == 20
    assert not state.resources.cyclic_attacks[0].active
    assert state.resources.cyclic_attacks[0].cycle == 3
    records = await played(play.store, cid)
    replayed, checks = await verify_commands(
        initial,
        records,
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecution"),
    )
    assert all(c.folded and c.reexecuted for c in checks)
    assert replayed == final
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    assert await restarted.execute(cid, command, principal_id="a") == result
    assert await restarted.store.read(cid) == final == await restarted.store.replay(cid)
    assert len(await played(restarted.store, cid)) == 1
    assert (
        restarted.advance_clock(
            state,
            Advance(id="two-deadlines:time", actor_id="a", expected_revision=1, to=20),
            RecordedDice(()),
        )
        == state
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("coincident", [False, True])
async def test_wait_checkpoints_automatic_form_reversion_before_later_damage(
    tmp_path: Path, backend: str, coincident: bool
) -> None:
    setup_path = tmp_path / "setup"
    setup_path.mkdir()
    cid, original = await prepare_form(setup_path, native_st=20, target_st=10)
    approved = await start(original, cid)
    await clock(original, cid, 10)
    await transform(
        original, cid, "resolve", proposal_id=approved.proposal_id, resolution="complete"
    )
    envelope = await original.store.read(cid)
    state = original._load(envelope)
    assert state.resources.game_time == 10 and hp(state, "a") == 10
    # Restore an already-delivered private occurrence, keeping the real approved
    # active form. The composed-host suite separately exercises delivery binding.
    occurrence = CyclicAttack(
        id="delivered-burn",
        attack_id="captured-burn",
        attacker_id="a",
        actor_id="a",
        basic_damage=1,
        damage_dice=1,
        damage_type="burn",
        resistance=0,
        ht=10,
        interval=10,
        remaining=1 if coincident else 2,
        due=20,
        stop_condition="wash",
    )
    resources = bind_occurrence(
        save(state.resources, occurrence),
        occurrence.id,
        source_id=occurrence.attack_id,
        source_revision="captured-approved-source",
        policy=CyclicPolicy(condition="wash"),
    )
    if coincident:
        other = occurrence.model_copy(update={"id": "second-delivered-burn"})
        resources = bind_occurrence(
            save(resources, other),
            other.id,
            source_id=other.attack_id,
            source_revision="captured-approved-source",
            policy=CyclicPolicy(condition="wash"),
        )
    original.commit(envelope, state.model_copy(update={"resources": resources}))
    play = build_play(tmp_path, original.engine, backend=backend, rng=RecordedDice((6, 6, 6, 6, 2)))
    await seed_campaign(play.store, envelope)
    command = Wait(id="form-clock", actor_id="a", expected_revision=state.revision, ticks=20)
    await play.execute(cid, command, principal_id="a")
    final = await current(play, cid)
    pool = next(p for p in final.resources.pools if p.id == "hp:a")
    # B83: 6 injury leaves4/10; unconsciousness returns the native20HP body
    # with8/20. The next due2 then leaves6, rather than scaling both old-body hits.
    assert (pool.maximum, pool.current) == (20, 6)
    assert pool.injury is not None and pool.injury.unconscious
    assert final.transformations.records[-1].status == "reverted"
    assert (
        next(
            p.amount
            for p in final.actors[0].proposal.draft.purchases
            if p.definition_id == "attribute:st"
        )
        == 20
    )
    events = [
        CyclicOccurrence.model_validate_json(e.kind)
        for e in final.resources.events
        if e.id.startswith("cyclic:")
    ]
    assert [(e.at, e.hp_lost) for e in events] == [(20, 6), (20 if coincident else 30, 2)]
    assert final.revision == final.resources.revision == state.revision + 1
    assert await play.store.read(cid) == await play.store.replay(cid)
    play.rng = RecordedDice(())
    await play.execute(cid, command, principal_id="a")
    assert await current(play, cid) == final


@pytest.mark.parametrize(
    "prefix",
    [
        "composed-source:",
        "composed-pending:",
        "composed-finished:",
        "composed-result:",
        "composed-resolution:",
        "cyclic-host-binding:",
        "cyclic-host:",
        "innate-critical:",
        "innate-critical-outcome:",
        "fatigue-critical-knockdown:",
    ],
)
async def test_genesis_cannot_forge_host_source_authority_or_execution(
    tmp_path: Path, prefix: str
) -> None:
    cid, play, _ = await prepare(tmp_path)
    state = await current(play, cid)
    with pytest.raises(ValidationError, match="cannot seed supernatural execution receipts"):
        play.initial_state(
            campaign(play.engine),
            state.world,
            ResourceState(
                events=(ResourceEvent(id=prefix + "forged", at=0, target_id="a", kind="{}"),)
            ),
            (ActorSetup(actor_id="a", proposal=state.actors[0].proposal),),
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_combat_round_cyclic_drop_reconciles_real_ready_and_hand_projections(
    tmp_path: Path, backend: str
) -> None:
    from test_combat_sensory_authority import change
    from test_composed_attack_host import fixture, idle

    cid, play, source_id = await fixture(tmp_path, backend)

    def prior_occurrence(state: PlayState) -> PlayState:
        occurrence = CyclicAttack(
            id="previous-arm-hit",
            attack_id=source_id,
            attacker_id="a",
            actor_id="b",
            basic_damage=1,
            damage_dice=1,
            damage_type="burn",
            resistance=0,
            ht=10,
            interval=1,
            remaining=1,
            due=state.resources.game_time + 1,
            stop_condition="wash",
        )
        resources = bind_occurrence(
            save(state.resources, occurrence),
            occurrence.id,
            source_id=source_id,
            source_revision="captured-source",
            policy=CyclicPolicy(condition="wash"),
            location="right-arm",
        )
        return state.model_copy(update={"resources": resources})

    await change(play, cid, prior_occurrence)
    await idle(play, cid, "a")
    play.rng = RecordedDice((6, 3, 3, 3))
    await idle(play, cid, "b")
    state = await current(play, cid)
    assert state.resources.game_time == 1 and hp(state) == 4
    sword = next(i for i in state.resources.items if i.id == "sword-b")
    assert not sword.equipped and not sword.ready
    assert sword.world_ground_location_id == "dock" and sword.owner_id == "b"
    actor = next(a for a in state.actors if a.actor_id == "b")
    fighter = next(p for p in state.encounters[0].participants if p.actor_id == "b")
    assert "sword-b" not in fighter.ready_item_ids
    assert all(i != "sword-b" for i, _ in fighter.hand_bindings + actor.held_item_hands)
    assert ("shield-b", "left-hand") in fighter.hand_bindings
    play.engine.validate(state)
    assert await play.store.read(cid) == await play.store.replay(cid)
