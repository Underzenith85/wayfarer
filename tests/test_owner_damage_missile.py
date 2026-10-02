"""B66 Fireball damage choices finish one paid held spell and its real injury."""

from pathlib import Path

import pytest
from support.runtime import build_play
from test_opponent_attack_host import begin as begin_attack
from test_opponent_attack_routes import enroll, luck_source, revoke_attacker
from test_owner_damage_host import begin
from test_spell_bindings import command as spell_command
from test_spell_bindings import idle, start_fight
from test_spell_bindings import setup as spell_setup

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.commands import ChooseDefense, StartEncounter
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.magic.missile_damage_records import PreparedMissileDamage
from wayfarer.engine.simulation.magic.spells import latest
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.inventory_damage_records import InventoryDamagePending
from wayfarer.orchestration.opponent_attack_records import ChooseOpponentAttack
from wayfarer.orchestration.owner_damage_records import ChooseOwnerDamage, OwnerDamageOutcome
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spells import SpellService
from wayfarer.orchestration.task_records import snapshot
from wayfarer.orchestration.tasks import TaskService


async def fixture(path: Path, backend: str, *, distance: int = 1) -> tuple[str, PlayService]:
    definition, purchase = luck_source()
    cid, original = await spell_setup(
        path / "source",
        combat=True,
        execution_version=2,
        extra_definitions=(definition,),
        extra_purchases=(purchase,)
        + (
            (Purchase(definition_id="skill:innate-attack-projectile", amount=16),)
            if distance > 1
            else ()
        ),
        battlefield=Battlefield(
            id="room", location_id="room", width=max(5, distance + 2), height=5
        ),
        trait_runtime_hooks=SUPPORTED_HOOKS,
    )
    if distance == 1:
        await start_fight(cid, original)
    else:
        await CombatService(original).execute(
            cid,
            StartEncounter(
                id="fight-start",
                actor_id="gm",
                expected_revision=0,
                encounter_id="fight",
                battlefield_id="room",
                placements=(
                    Placement(actor_id="a", position=GridPoint(x=1, y=1)),
                    Placement(actor_id="b", position=GridPoint(x=1 + distance, y=1)),
                ),
            ),
            principal_id="gm",
        )
    original.rng = RecordedDice((3, 3, 3))
    start = spell_command(1).model_copy(update={"spell_id": "fireball", "channel_id": "fireball"})
    await SpellService(original).execute(cid, start, principal_id="a")
    await idle(cid, original, "b")
    await SpellService(original).execute(
        cid,
        start.model_copy(update={"id": "release", "kind": "release", "expected_revision": 3}),
        principal_id="a",
    )
    return cid, await enroll(path, backend, cid, original)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("secret", [False, True])
async def test_selected_fireball_damage_changes_real_hp_and_ends_paid_spell_once(
    tmp_path: Path, backend: str, secret: bool
) -> None:
    cid, play = await fixture(tmp_path, backend)
    play.rng = RecordedDice((1, 2, 2) + (() if secret else (1,)))
    opening, shown = await begin(play, cid, secret=secret, principal="gm" if secret else "b")
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, InventoryDamagePending)
    assert isinstance(pending.preparation, PreparedMissileDamage)
    assert pending.original == (None if secret else (1,)) and play.rng.exhausted()
    assert latest(state.resources)["cast"].phase == "active"
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 9
    assert state.encounters[0].current_actor_id == "a" and not state.encounters[0].wounds
    command = ChooseOwnerDamage(
        id="selected-fireball",
        actor_id="a",
        expected_revision=state.revision,
        pending_id=pending.id,
        choice="use-luck",
    )
    play.rng = RecordedDice(((1,) if secret else ()) + (3, 6, 4, 4, 4))
    result = await TaskService(play).execute(cid, command, principal_id="a")
    after = play._load(await play.store.read(cid))
    hp = next(p for p in after.resources.pools if p.id == "hp:b")
    assert (
        hp.current == 4
        and hp.injury
        and hp.injury.shock == 4
        and hp.injury.stunned
        and hp.injury.prone
    )
    assert next(p.current for p in after.resources.pools if p.id == "fp:a") == 9
    assert latest(after.resources)["cast"].phase == "ended"
    assert after.encounters[0].current_actor_id == "b" and len(after.encounters[0].wounds) == 1
    injury = after.encounters[0].wounds[0]
    assert injury.attack.dice == (1, 2, 2) and injury.damage_dice == (6,) and injury.injury == 6
    assert play.rng.exhausted() and snapshot(after).pending is None
    assert len(snapshot(after).luck.receipts) == 1
    if secret:
        assert result.damage_json is None and result.luck is None
    else:
        assert result.damage_json and OwnerDamageOutcome.model_validate_json(
            result.damage_json
        ).dice == (6,)
    restarted = build_play(
        tmp_path, play.engine, backend=backend, rng=RecordedDice(()), instants=play.instants
    )
    assert await TaskService(restarted).execute(cid, command, principal_id="a") == result
    assert (
        await TaskService(restarted).execute(cid, opening, principal_id="gm" if secret else "b")
        == shown
    )
    assert restarted._load(await restarted.store.read(cid)) == after
    with pytest.raises(ConflictError):
        await TaskService(restarted).execute(
            cid,
            command.model_copy(update={"id": "late", "expected_revision": after.revision}),
            principal_id="a",
        )
    assert await restarted.store.read(cid) == await restarted.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_captured_spell_source_survives_revoked_approval_through_both_phases(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend)
    play.rng = RecordedDice((1, 2, 2))
    _, original = await begin_attack(play, cid, prepare_owner_damage=True)
    assert original.pending_id
    await revoke_attacker(play, cid)
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((4,))
    response = ChooseDefense(
        id="deliver",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    result = await TaskService(play).execute(
        cid,
        ChooseOpponentAttack(
            id=response.id,
            actor_id="b",
            expected_revision=state.revision,
            pending_id=original.pending_id,
            choice="accept",
            response=response,
        ),
        principal_id="b",
    )
    assert result.status == "completed" and result.pending_id is None
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, InventoryDamagePending) and pending.original == (4,)
    command = ChooseOwnerDamage(
        id="finish",
        actor_id="a",
        expected_revision=state.revision,
        pending_id=pending.id,
        choice="use-luck",
    )
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="approval"):
        await TaskService(play).execute(cid, command, principal_id="a")
    result = await TaskService(play).execute(
        cid, command.model_copy(update={"choice": "accept"}), principal_id="gm"
    )
    after = play._load(await play.store.read(cid))
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 6
    assert next(p.current for p in after.resources.pools if p.id == "fp:a") == 9
    assert next(a for a in after.actors if a.actor_id == "a").approval is None
    assert latest(after.resources)["cast"].phase == "ended" and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_missile_candidate_rollback_keeps_held_source_and_selected_injury_atomic(
    tmp_path: Path, backend: str
) -> None:
    from test_gadgeteer_gizmos_persistence import FailingCommitPlay

    cid, play = await fixture(tmp_path, backend)
    play.rng = RecordedDice((1, 2, 2, 1))
    await begin(play, cid, principal="b")
    before = await play.store.read(cid)
    state = play._load(before)
    pending = snapshot(state).pending
    assert isinstance(pending, InventoryDamagePending)
    command = ChooseOwnerDamage(
        id="select",
        actor_id="a",
        expected_revision=state.revision,
        pending_id=pending.id,
        choice="use-luck",
    )
    history, events = await play.store.history(cid), await play.store.stream(cid)
    failing = FailingCommitPlay(
        play.store, play.engine, rng=RecordedDice((3, 5)), instants=play.instants
    )
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await TaskService(failing).execute(cid, command, principal_id="a")
    assert await play.store.read(cid) == before == await play.store.replay(cid)
    assert await play.store.history(cid) == history and await play.store.stream(cid) == events
    play.rng = RecordedDice((3, 5))
    await TaskService(play).execute(cid, command, principal_id="a")
    after = play._load(await play.store.read(cid))
    assert latest(after.resources)["cast"].phase == "ended"
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 5


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_missile_preparation_and_choice_reexecute_from_seed_and_fold_exactly(
    tmp_path: Path, backend: str
) -> None:
    import secrets

    from scripts.replay_fixtures import FixtureExecutor
    from wayfarer.persistence.replay import verify_commands

    cid, play = await fixture(tmp_path, backend)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng = secrets
    play.seeds = lambda: "01" * 32
    await begin(play, cid, principal="b")
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, InventoryDamagePending)
    await TaskService(play).execute(
        cid,
        ChooseOwnerDamage(
            id="seed-select",
            actor_id="a",
            expected_revision=state.revision,
            pending_id=pending.id,
            choice="use-luck",
        ),
        principal_id="a",
    )
    records = (await play.store.history(cid))[count:]
    ids = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in ids],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert len(checks) == 2 and all(check.folded and check.reexecuted for check in checks)
    assert replayed == await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_changed_held_spell_refuses_before_luck_entropy_or_spending(
    tmp_path: Path, backend: str
) -> None:
    from test_combat_sensory_authority import change

    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.engine.simulation.magic.spell_state import RuntimeSpellEvent, SpellResult
    from wayfarer.engine.simulation.resources import ResourceEvent

    cid, play = await fixture(tmp_path, backend)
    play.rng = RecordedDice((1, 2, 2, 1))
    await begin(play, cid, principal="b")

    def changed_energy(state: PlayState) -> PlayState:
        effect = latest(state.resources)["cast"]
        event = ResourceEvent(
            id="runtime-spell:changed-energy",
            at=state.resources.game_time,
            target_id="a",
            kind=RuntimeSpellEvent(
                effect=effect.model_copy(update={"energy": 2}),
                result=SpellResult(outcome="active"),
            ).model_dump_json(),
        )
        return state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={"events": state.resources.events + (event,)}
                )
            }
        )

    await change(play, cid, changed_energy)
    before = await play.store.read(cid)
    state = play._load(before)
    pending = snapshot(state).pending
    assert isinstance(pending, InventoryDamagePending)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="held spell"):
        await TaskService(play).execute(
            cid,
            ChooseOwnerDamage(
                id="stale-source",
                actor_id="a",
                expected_revision=state.revision,
                pending_id=pending.id,
                choice="use-luck",
            ),
            principal_id="a",
        )
    assert await play.store.read(cid) == before and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("same", [False, True])
async def test_competing_missile_choices_end_and_injure_once(
    tmp_path: Path, backend: str, same: bool
) -> None:
    import asyncio

    cid, play = await fixture(tmp_path, backend)
    play.rng = RecordedDice((1, 2, 2, 1))
    await begin(play, cid, principal="b")
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, InventoryDamagePending)
    peers = [
        build_play(
            tmp_path, play.engine, backend=backend, rng=RecordedDice((3, 5)), instants=play.instants
        )
        for _ in range(2)
    ]
    outcomes = await asyncio.gather(
        *(
            TaskService(peer).execute(
                cid,
                ChooseOwnerDamage(
                    id="choice" if same else "choice-" + str(index),
                    actor_id="a",
                    expected_revision=state.revision,
                    pending_id=pending.id,
                    choice="use-luck",
                ),
                principal_id="a",
            )
            for index, peer in enumerate(peers)
        ),
        return_exceptions=True,
    )
    if same:
        assert outcomes[0] == outcomes[1]
    else:
        assert sum(isinstance(result, ConflictError) for result in outcomes) == 1
    after = play._load(await play.store.read(cid))
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 5
    assert latest(after.resources)["cast"].phase == "ended"
    assert len(after.encounters[0].wounds) == len(snapshot(after).luck.receipts) == 1


@pytest.mark.parametrize(
    "distance,selected,expected",
    [
        (24, 5, 5),
        (25, 5, 2),
        (26, 5, 2),
        (24, 1, 1),
        (25, 1, 0),
        (26, 1, 0),
    ],
)
async def test_b247_range_and_b378_floor_are_independent_selected_damage_oracles(
    tmp_path: Path, distance: int, selected: int, expected: int
) -> None:
    cid, play = await fixture(tmp_path, "sqlite", distance=distance)
    play.rng = RecordedDice((2, 2, 2, 1))
    await begin(play, cid, principal="b")
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, InventoryDamagePending)
    assert isinstance(pending.preparation, PreparedMissileDamage)
    assert pending.preparation.inputs.distance == distance
    play.rng = RecordedDice((selected, selected))
    result = await TaskService(play).execute(
        cid,
        ChooseOwnerDamage(
            id="select-range",
            actor_id="a",
            expected_revision=state.revision,
            pending_id=pending.id,
            choice="use-luck",
        ),
        principal_id="a",
    )
    assert result.damage_json
    outcome = OwnerDamageOutcome.model_validate_json(result.damage_json)
    assert outcome.combat and outcome.combat.injury
    assert outcome.combat.injury.basic_damage == expected
    assert outcome.combat.injury.hp_after == 10 - expected and play.rng.exhausted()
    after = play._load(await play.store.read(cid))
    assert latest(after.resources)["cast"].phase == "ended"
    assert next(p.current for p in after.resources.pools if p.id == "fp:a") == 9


async def test_ordinary_fireball_retains_historical_half_range_generation(tmp_path: Path) -> None:
    cid, play = await fixture(tmp_path, "sqlite", distance=25)
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((2, 2, 2, 5))
    result = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="ordinary-range",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        ),
        principal_id="b",
    )
    assert result.injury and result.injury.basic_damage == 5 and result.injury.hp_after == 5
    assert play.rng.exhausted()
