"""B66/B376 weapon-owner Luck changes actual counterdamage once, on both stores."""

import asyncio
import secrets
from pathlib import Path

import pytest
from support.runtime import build_play
from test_combat_sensory_authority import change
from test_gurps_melee import setup as melee_setup
from test_opponent_attack_routes import enroll, luck_source
from test_owner_damage_unarmed import strike

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.commands import ChooseDefense, TakeCombatTurn
from wayfarer.engine.simulation.combat.settlement import reconcile_equipment
from wayfarer.engine.simulation.equipment.basic.armor import ARMOR
from wayfarer.engine.simulation.equipment.catalog import Armor
from wayfarer.engine.simulation.resources import Item
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.clock import CommandInstant
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.owner_damage_records import (
    ChooseOwnerDamage,
    OwnerDamageOutcome,
    PrepareOwnerDamage,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import snapshot
from wayfarer.orchestration.tasks import TaskService, real_play_clock
from wayfarer.orchestration.unarmed_damage_records import ArmedParryDamagePending
from wayfarer.persistence.replay import verify_commands


async def setup(path: Path, backend: str, *, double: bool = False) -> tuple[str, PlayService]:
    definition, purchase = luck_source()
    leg_armor = ARMOR[0].model_copy(
        update={
            "definition_id": "equipment:parry-test-leg-armor",
            "armor": Armor(locations=("right-leg",), dr=3),
        }
    )
    cid, original = await melee_setup(
        path / "source",
        "gurps-basic-set-4e-2004",
        human=True,
        allow_supernatural=True,
        extra_definitions=(definition,),
        extra_purchases=(purchase,),
        trait_runtime_hooks=SUPPORTED_HOOKS,
        extra_equipment=(leg_armor,),
        extra_items=(Item(id="leg-armor", definition_id=leg_armor.definition_id, owner_id="a"),),
    )
    play = await enroll(path, backend, cid, original)
    await strike(play, cid, action="kick", option="double" if double else None)
    return cid, play


async def prepare(
    play: PlayService, cid: str, *, secret: bool = False, identifier: str = "parry-original"
) -> tuple[PrepareOwnerDamage, object]:
    state = play._load(await play.store.read(cid))
    response = ChooseDefense(
        id=identifier,
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="parry",
        item_id="sword-b",
        parry_mode_id="swing",
    )
    command = PrepareOwnerDamage(
        id=response.id,
        actor_id=response.actor_id,
        expected_revision=response.expected_revision,
        response=response,
        secret=secret,
    )
    return command, await TaskService(play).execute(
        cid, command, principal_id="gm" if secret else "b"
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("secret", [False, True])
async def test_selected_counterdamage_persists_once_as_parrying_owner(
    tmp_path: Path, backend: str, secret: bool
) -> None:
    cid, play = await setup(tmp_path, backend)
    before = play._load(await play.store.read(cid))
    play.rng = RecordedDice((2, 2, 2, 2, 3, 3, 3, 3, 3) + (() if secret else (1,)))
    opening, original_result = await prepare(play, cid, secret=secret)
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, ArmedParryDamagePending)
    assert pending.actor_id == "b" and pending.preparation.inputs.target_id == "a"
    assert pending.original == (None if secret else (1,)) and play.rng.exhausted()
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 10
    assert not state.encounters[0].unarmed_history and state.encounters[0].current_actor_id == "a"
    assert state.resources.game_time == before.resources.game_time
    with pytest.raises(AuthorizationError):
        await TaskService(play).pending(cid, principal_id="a")
    command = ChooseOwnerDamage(
        id="selected-parry",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=pending.id,
        choice="use-luck",
    )
    play.rng = RecordedDice(((1,) if secret else ()) + (3, 6, 4, 4, 4))
    result = await TaskService(play).execute(cid, command, principal_id="b")
    after = play._load(await play.store.read(cid))
    hp = next(p for p in after.resources.pools if p.id == "hp:a")
    assert hp.current == 4 and hp.injury and hp.injury.shock == 4
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 10
    trace = after.encounters[0].unarmed_history[0]
    assert not trace.won and trace.damage_dice == () and trace.effect_dice == (6,)
    assert trace.checks == pending.preparation.trace.checks
    assert trace.effect_checks == (pending.preparation.inputs.check,)
    assert (
        after.encounters[0].current_actor_id == "b" and after.encounters[0].pending_unarmed is None
    )
    saved = snapshot(after)
    assert saved.pending is None and len(saved.luck.receipts) == 1
    assert saved.luck.rolls[-1].actor_id == "b"
    assert tuple(c.actor_id for c in real_play_clock(after).cooldowns) == ("b",)
    assert play.rng.exhausted()
    if secret:
        assert result.damage_json is None and result.luck is None
    else:
        assert result.damage_json and OwnerDamageOutcome.model_validate_json(
            result.damage_json
        ).dice == (6,)
    restarted = build_play(
        tmp_path, play.engine, backend=backend, rng=RecordedDice(()), instants=play.instants
    )
    assert await TaskService(restarted).execute(cid, command, principal_id="b") == result
    assert (
        await TaskService(restarted).execute(cid, opening, principal_id="gm" if secret else "b")
        == original_result
    )
    assert restarted._load(await restarted.store.read(cid)) == after
    assert await restarted.store.read(cid) == await restarted.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_parry_current_victim_hp_and_lost_owner_approval_acceptance(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await setup(tmp_path, backend)
    play.rng = RecordedDice((2, 2, 2, 2, 3, 3, 3, 3, 3, 1))
    await prepare(play, cid)
    await change(
        play,
        cid,
        lambda s: s.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(update={"approval": None}) if a.actor_id == "b" else a
                    for a in s.actors
                ),
                "resources": s.resources.model_copy(
                    update={
                        "pools": tuple(
                            p.model_copy(update={"current": 8}) if p.id == "hp:a" else p
                            for p in s.resources.pools
                        )
                    }
                ),
            }
        ),
    )
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, ArmedParryDamagePending)
    command = ChooseOwnerDamage(
        id="revoked-parrier",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=pending.id,
        choice="use-luck",
    )
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="approval"):
        await TaskService(play).execute(cid, command, principal_id="b")
    result = await TaskService(play).execute(
        cid, command.model_copy(update={"choice": "accept"}), principal_id="gm"
    )
    assert result.damage_json
    after = play._load(await play.store.read(cid))
    assert next(p.current for p in after.resources.pools if p.id == "hp:a") == 5
    assert next(a for a in after.actors if a.actor_id == "b").approval is None
    assert (
        after.encounters[0].unarmed_history[0].effect_checks
        == pending.preparation.trace.effect_checks
    )
    assert not snapshot(after).luck.receipts and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_parry_authority_immediate_gate_rollback_race_and_stale_refusal(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await setup(tmp_path, backend)
    play.rng = RecordedDice((2, 2, 2, 2, 3, 3, 3, 3, 3, 1))
    await prepare(play, cid)
    before = await play.store.read(cid)
    state = play._load(before)
    pending = snapshot(state).pending
    assert isinstance(pending, ArmedParryDamagePending)
    command = ChooseOwnerDamage(
        id="race-parry",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=pending.id,
        choice="use-luck",
    )
    play.rng = RecordedDice(())
    with pytest.raises(AuthorizationError):
        await TaskService(play).execute(cid, command, principal_id="a")
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="interleave",
                actor_id="a",
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="do_nothing",
            ),
            principal_id="a",
        )
    assert await play.store.read(cid) == before and play.rng.exhausted()
    play.rng = RecordedDice((3, 6))
    with pytest.raises(ValidationError):
        await TaskService(play).execute(cid, command, principal_id="b")
    assert (
        await play.store.read(cid) == before and not real_play_clock(play._load(before)).cooldowns
    )
    peers = [
        build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice((3, 6, 4, 4, 4)))
        for _ in range(2)
    ]
    instant = real_play_clock(state).observed_at_us
    assert instant is not None
    for peer in peers:
        peer.instants = lambda: CommandInstant(instant + 1)
    results = await asyncio.gather(
        *(TaskService(peer).execute(cid, command, principal_id="b") for peer in peers)
    )
    assert results[0] == results[1]
    after = await play.store.read(cid)
    assert next(p.current for p in play._load(after).resources.pools if p.id == "hp:a") == 4
    assert len(snapshot(play._load(after)).luck.receipts) == 1
    with pytest.raises(ConflictError):
        await TaskService(play).execute(
            cid, command.model_copy(update={"choice": "accept"}), principal_id="b"
        )
    with pytest.raises(ConflictError):
        await TaskService(play).execute(
            cid,
            command.model_copy(
                update={"id": "late-parry", "expected_revision": state.revision + 1}
            ),
            principal_id="b",
        )
    assert await play.store.read(cid) == after


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("secret", [False, True])
async def test_parry_seed_only_reexecution_and_event_folding(
    tmp_path: Path, backend: str, secret: bool
) -> None:
    cid, play = await setup(tmp_path, backend)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng = secrets
    # Accepted untrained kick8 attack5, weapon parry defense6, Broadsword13 check9.
    play.seeds = lambda: format(11, "064x")
    await prepare(play, cid, secret=secret)
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, ArmedParryDamagePending)
    await TaskService(play).execute(
        cid,
        ChooseOwnerDamage(
            id="seed-parry",
            actor_id="b",
            expected_revision=state.revision,
            pending_id=pending.id,
            choice="use-luck",
        ),
        principal_id="b",
    )
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


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_parry_uses_current_limb_armor_after_accepted_weapon_lost(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await setup(tmp_path, backend)
    play.rng = RecordedDice((2, 2, 2, 2, 3, 3, 3, 3, 3, 1))
    await prepare(play, cid)

    def equip(state: PlayState) -> PlayState:
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(update={"equipped": True})
                            if i.id == "leg-armor"
                            else i.model_copy(update={"equipped": False, "ready": False})
                            if i.id == "sword-b"
                            else i
                            for i in state.resources.items
                        ),
                        "pools": tuple(
                            p.model_copy(update={"current": 8}) if p.id == "hp:a" else p
                            for p in state.resources.pools
                        ),
                    }
                )
            }
        )
        state, encounter = reconcile_equipment(state, state.encounters[0])
        return state.model_copy(update={"encounters": (encounter,)})

    await change(play, cid, equip)
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, ArmedParryDamagePending)
    play.rng = RecordedDice((3, 3))
    result = await TaskService(play).execute(
        cid,
        ChooseOwnerDamage(
            id="current-armor-parry",
            actor_id="b",
            expected_revision=state.revision,
            pending_id=pending.id,
            choice="use-luck",
        ),
        principal_id="b",
    )
    after = play._load(await play.store.read(cid))
    # Captured swing3+1, current right-leg DR3 => penetrating1, cutting injury1.
    assert next(p.current for p in after.resources.pools if p.id == "hp:a") == 7
    assert result.damage_json and OwnerDamageOutcome.model_validate_json(
        result.damage_json
    ).dice == (3,)
    assert (
        after.encounters[0].unarmed_history[0].effect_checks
        == pending.preparation.trace.effect_checks
    )
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_changed_owner_control_and_identity_reject_without_dice(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await setup(tmp_path, backend)
    play.rng = RecordedDice((2, 2, 2, 2, 3, 3, 3, 3, 3, 1))
    await prepare(play, cid)
    await change(
        play,
        cid,
        lambda state: state.model_copy(
            update={
                "members": tuple(
                    m.model_copy(update={"actor_ids": ()}) if m.principal_id == "b" else m
                    for m in state.members
                ),
            }
        ),
    )
    before = await play.store.read(cid)
    state = play._load(before)
    pending = snapshot(state).pending
    assert isinstance(pending, ArmedParryDamagePending)
    command = ChooseOwnerDamage(
        id="lost-control-parry",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=pending.id,
        choice="use-luck",
    )
    play.rng = RecordedDice(())
    with pytest.raises(AuthorizationError):
        await TaskService(play).execute(cid, command, principal_id="b")
    with pytest.raises(ConflictError):
        await TaskService(play).execute(
            cid, command.model_copy(update={"actor_id": "a"}), principal_id="a"
        )
    assert await play.store.read(cid) == before and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_parry_second_original_obeys_shared_owner_cooldown(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await setup(tmp_path, backend)
    play.rng = RecordedDice((2, 2, 2, 2, 3, 3, 3, 3, 3, 1))
    await prepare(play, cid)
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, ArmedParryDamagePending)
    play.rng = RecordedDice((1, 1))
    await TaskService(play).execute(
        cid,
        ChooseOwnerDamage(
            id="first-parry",
            actor_id="b",
            expected_revision=state.revision,
            pending_id=pending.id,
            choice="use-luck",
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="parrier-wait",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    from wayfarer.engine.simulation.combat.commands import TakeUnarmedTurn

    await CombatService(play).execute(
        cid,
        TakeUnarmedTurn(
            id="second-kick",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            action="kick",
            target_id="b",
        ),
        principal_id="a",
    )
    # Shock3 reduces this second untrained kick from8 to5.
    play.rng = RecordedDice((1, 2, 2, 2, 3, 3, 3, 3, 3, 1))
    await prepare(play, cid, identifier="second-parry-original")
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, ArmedParryDamagePending)
    command = ChooseOwnerDamage(
        id="cooldown-parry",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=pending.id,
        choice="use-luck",
    )
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="cooling down"):
        await TaskService(play).execute(cid, command, principal_id="b")
    await TaskService(play).execute(
        cid, command.model_copy(update={"choice": "accept"}), principal_id="b"
    )
    after = play._load(await play.store.read(cid))
    assert len(snapshot(after).luck.receipts) == 1 and len(after.encounters[0].unarmed_history) == 2
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_failed_separate_weapon_check_has_no_owner_damage_choice(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await setup(tmp_path, backend)
    play.rng = RecordedDice((2, 2, 2, 2, 3, 3, 5, 5, 5))
    _, result = await prepare(play, cid)
    after = play._load(await play.store.read(cid))
    assert snapshot(after).pending is None
    assert after.encounters[0].current_actor_id == "b"
    trace = after.encounters[0].unarmed_history[0]
    assert trace.effect_checks[0].total == 15 and not trace.effect_checks[0].outcome.succeeded
    assert (
        trace.effect_dice == ()
        and next(p.current for p in after.resources.pools if p.id == "hp:a") == 10
    )
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_selected_parry_injury_cancels_unavailable_second_kick(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await setup(tmp_path, backend, double=True)
    play.rng = RecordedDice((2, 2, 2, 2, 3, 3, 3, 3, 3, 1))
    await prepare(play, cid)
    state = play._load(await play.store.read(cid))
    pending = snapshot(state).pending
    assert isinstance(pending, ArmedParryDamagePending)
    play.rng = RecordedDice((3, 6, 5, 5, 5, 3, 3, 3))
    await TaskService(play).execute(
        cid,
        ChooseOwnerDamage(
            id="cancel-second-kick",
            actor_id="b",
            expected_revision=state.revision,
            pending_id=pending.id,
            choice="use-luck",
        ),
        principal_id="b",
    )
    after = play._load(await play.store.read(cid))
    encounter = after.encounters[0]
    assert encounter.pending_unarmed is None and encounter.current_actor_id == "b"
    assert len(encounter.unarmed_history) == 1 and encounter.unarmed_history[0].effect_dice == (6,)
    hp = next(p for p in after.resources.pools if p.id == "hp:a")
    assert hp.injury and hp.injury.stunned
    assert play.rng.exhausted()
