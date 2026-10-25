"""B345 voluntary combat admission uses the actual canonical attack target."""

import secrets
from functools import cache
from pathlib import Path

import pytest
from support.runtime import build_play, open_store, seed_campaign
from test_abandon_pending_attack import abandon_command
from test_gurps_maneuvers import turn
from test_gurps_melee import setup
from test_gurps_ranged import load, scene, weapon

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.simulation.combat.attack_roll import AttackRollSpec
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.encounter import CombatResult
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.health.toxins import DrinkCommand
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    StartEncounter,
    TakeCombatTurn,
    TakeUnarmedTurn,
    generations,
)
from wayfarer.orchestration.combat.abandon import AbandonPendingAttackService
from wayfarer.orchestration.drinking import DrinkingEnvironment, DrinkingService
from wayfarer.orchestration.opponent_attack_records import BeginOpponentAttack, ChooseOpponentAttack
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.tactical_view.preview import preview
from wayfarer.orchestration.task_records import SetRealPlayClock
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.replay import verify_commands


async def prepare(
    path: Path, backend: str, *, darkness: int, ranged: bool = False
) -> tuple[str, PlayService]:
    cid, play = await setup(
        path,
        "gurps-basic-set-4e-2004",
        human=True,
        darkness_penalty=darkness,
        ranged_mode=weapon() if ranged else None,
        ranged_scene=scene() if ranged else (),
    )
    if backend == "postgres":
        store = open_store(path, backend=backend)
        await seed_campaign(store, await play.store.read(cid))
        play = build_play(path, play.engine, store=store)
    play.rng = RecordedDice(())
    if ranged:
        await load(cid, play)
    return cid, play


def attack(revision: int, *, ranged: bool = False) -> TakeCombatTurn:
    return TakeCombatTurn(
        id="source-minimum-attack",
        actor_id="a",
        expected_revision=revision,
        encounter_id="fight",
        maneuver="attack",
        item_id="sword-a",
        target_id="b",
        mode_id="ranged" if ranged else "swing",
        hit_location="neck",
    )


@cache
def source_seed() -> str:
    for index in range(100000):
        seed = f"{index:064x}"
        rng = SeededRandom(seed)
        dice = tuple(rng.randbelow(6) + 1 for _ in range(3))
        if sum(dice) == 4:
            return seed
    raise AssertionError("No ordinary critical-success seed")


@cache
def drinking_seed() -> str:
    for index in range(100000):
        seed = f"{index:064x}"
        rng = SeededRandom(seed)
        if sum(rng.randbelow(6) + 1 for _ in range(3)) == 15:
            return seed
    raise AssertionError("No ordinary failed drinking seed")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("ranged", [False, True])
async def test_actual_skill_two_refuses_before_entropy_cost_and_preview(
    tmp_path: Path,
    backend: str,
    ranged: bool,
) -> None:
    cid, play = await prepare(tmp_path, backend, darkness=-6, ranged=ranged)
    before = await play.store.read(cid)
    state = play._load(before)
    command = attack(state.revision, ranged=ranged)
    # Purchased weapon13, darkness-6 and the selected neck-5: exactly2.
    with pytest.raises(ValidationError, match="effective skill at least 3"):
        preview(play, state, state.encounters[0], command)
    with pytest.raises(ValidationError, match="effective skill at least 3"):
        await CombatService(play).execute(cid, command, principal_id="a")
    assert await play.store.read(cid) == before == await play.store.replay(cid)
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    assert not any(row.command_id == command.id for row in await play.store.history(cid))


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("ranged", [False, True])
async def test_actual_skill_three_legal_seed_retry_and_replay(
    tmp_path: Path,
    backend: str,
    ranged: bool,
) -> None:
    cid, play = await prepare(tmp_path, backend, darkness=-5, ranged=ranged)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    state = play._load(initial)
    command = attack(state.revision, ranged=ranged)
    preview(play, state, state.encounters[0], command)
    play.rng, play.seeds = secrets, source_seed
    await CombatService(play).execute(cid, command, principal_id="a")
    state = play._load(await play.store.read(cid))
    response = ChooseDefense(
        id="minimum-response",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    result = await CombatService(play).execute(cid, response, principal_id="b")
    assert result.injury and result.injury.attack.effective_target == 3
    assert result.injury.attack.total == 4 and result.injury.attack.outcome.succeeded
    assert result.injury.injury > 0
    final = await play.store.read(cid)
    records = (await play.store.history(cid))[count:]
    identifiers = {row.command_id for row in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in identifiers],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "minimum-seed"),
    )
    assert len(checks) == 2 and all(check.folded and check.reexecuted for check in checks)
    assert replayed == final == await play.store.replay(cid)
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice(()))
    assert await CombatService(restarted).execute(cid, response, principal_id="b") == result
    assert await play.store.read(cid) == final


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_real_tipsy_after_declaration_rechecks_before_acrobatics_and_can_abandon(
    tmp_path: Path,
    backend: str,
) -> None:
    cid, play = await prepare(tmp_path, backend, darkness=-5)
    play.rng, play.seeds = secrets, drinking_seed
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    await CombatService(play).execute(cid, attack(play._load(initial).revision), principal_id="a")
    before_drink = play._load(await play.store.read(cid))
    # B440: ordinary failed alcohol check produces Tipsy, a new DX-1 condition.
    play.rng, play.seeds = secrets, drinking_seed
    drink = DrinkCommand(
        id="attacker-drink",
        actor_id="a",
        expected_revision=before_drink.revision,
        kind="drink",
        drinks=3,
    )
    intoxication = await DrinkingService(play, lambda *_: DrinkingEnvironment()).execute(
        cid, drink, principal_id="a"
    )
    assert intoxication.level == "tipsy"
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].pending_defense == before_drink.encounters[0].pending_defense
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    response = ChooseDefense(
        id="now-illegal-response",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="dodge",
        acrobatic_dodge=True,
    )
    with pytest.raises(ValidationError, match="effective skill at least 3"):
        preview(play, state, state.encounters[0], response)
    with pytest.raises(ValidationError, match="effective skill at least 3"):
        await CombatService(play).execute(cid, response, principal_id="b")
    assert play.rng.exhausted() and await play.store.read(cid) == before
    play.rng = secrets
    command = abandon_command(state)
    result = await AbandonPendingAttackService(play).execute(cid, command, principal_id="a")
    assert result.current_actor_id == "b"
    after = play._load(await play.store.read(cid))
    assert after.encounters[0].pending_defense is None
    assert after.resources.items == state.resources.items
    assert [(pool.id, pool.current) for pool in after.resources.pools] == [
        (pool.id, pool.current) for pool in state.resources.pools
    ]
    await turn(cid, play, "b", "do_nothing")
    assert play._load(await play.store.read(cid)).encounters[0].current_actor_id == "a"
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice(()))
    assert (
        await AbandonPendingAttackService(restarted).execute(cid, command, principal_id="a")
        == result
    )
    assert await play.store.read(cid) == await play.store.replay(cid)
    assert len((await play.store.history(cid))[count:]) == 4

    records = (await play.store.history(cid))[count:]
    identifiers = {row.command_id for row in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in identifiers],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "current-condition-seed"),
    )
    assert len(checks) == 4 and all(check.folded and check.reexecuted for check in checks)
    assert replayed == await play.store.read(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_old_pending_keeps_old_minimum_policy_with_fresh_defender(
    tmp_path: Path,
    backend: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid, play = await prepare(tmp_path, backend, darkness=-6)
    current = generations.ACTIVE
    monkeypatch.setattr(generations, "ACTIVE", current - {"voluntary-attack-minimum"})
    await CombatService(play).execute(
        cid, attack(play._load(await play.store.read(cid)).revision), principal_id="a"
    )
    monkeypatch.setattr(generations, "ACTIVE", current)
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((1, 1, 2, 3, 3, 3, 1))
    result = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="legacy-minimum-response",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        ),
        principal_id="b",
    )
    assert result.injury and result.injury.attack.effective_target == 2
    assert result.injury.attack.outcome.succeeded
    assert play.rng.exhausted()
    assert await play.store.read(cid) == await play.store.replay(cid)


def test_canonical_spec_does_not_forbid_mandatory_low_target_rolls() -> None:
    # Fragmentation, stray fire and mandatory defenses share scoring infrastructure.
    spec = AttackRollSpec(profile_id="gurps-basic-set-4e-2004", target=2)
    assert spec.roll(RecordedDice((1, 1, 1))).outcome.succeeded
    with pytest.raises(ValidationError, match="effective skill at least 3"):
        spec.require_voluntary()


async def genesis_health(
    path: Path, backend: str, *, darkness: int, low_pool: str | None = None, bare: bool = False
) -> tuple[str, PlayService]:
    """Author initial health/equipment facts before the first real encounter command."""
    cid, original = await setup(
        path / "source",
        "gurps-basic-set-4e-2004",
        human=True,
        darkness_penalty=darkness,
        start_encounter=False,
    )
    initial = await original.store.read(cid)
    state = original._load(initial)
    resources = state.resources.model_copy(
        update={
            "pools": tuple(
                pool.model_copy(update={"current": 0}) if pool.id == low_pool else pool
                for pool in state.resources.pools
            ),
            "items": tuple(
                item.model_copy(update={"equipped": False, "ready": False}) if bare else item
                for item in state.resources.items
            ),
        }
    )
    state = state.model_copy(
        update={
            "resources": resources,
            "actors": tuple(
                actor.model_copy(update={"held_item_hands": ()}) if bare else actor
                for actor in state.actors
            ),
        }
    )
    initial["play_json"] = state.model_dump_json()
    play = build_play(path / "actual", original.engine, backend=backend, rng=RecordedDice(()))
    await seed_campaign(play.store, initial)
    assert play.engine.combat
    await CombatService(play).execute(
        cid,
        StartEncounter(
            id="initial-health-start",
            actor_id="gm",
            expected_revision=0,
            encounter_id="fight",
            battlefield_id=play.engine.combat.rules.battlefields[0].id,
            placements=(
                Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
                Placement(actor_id="b", position=GridPoint(x=1, y=0), facing="west"),
            ),
        ),
        principal_id="gm",
    )
    return cid, play


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("pool", ["hp:a", "fp:a"])
async def test_invalid_attack_precedes_mandatory_health_draws_and_legal_failure_commits_once(
    tmp_path: Path,
    backend: str,
    pool: str,
) -> None:
    cid, play = await genesis_health(tmp_path / "invalid", backend, darkness=-6, low_pool=pool)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="effective skill at least 3"):
        await CombatService(play).execute(
            cid, attack(play._load(before).revision), principal_id="a"
        )
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    assert await play.store.read(cid) == before
    # FP0 halves usable ST to5, imposing another-5 for this ST10 weapon.
    cid, play = await genesis_health(
        tmp_path / "legal", backend, darkness=0 if pool == "fp:a" else -5, low_pool=pool
    )
    state = play._load(await play.store.read(cid))
    command = attack(state.revision)
    play.rng = RecordedDice((5, 5, 5))
    result = await CombatService(play).execute(cid, command, principal_id="a")
    assert play.rng.exhausted()
    final = await play.store.read(cid)
    after = play._load(final)
    assert after.revision == state.revision + 1 and after.encounters[0].pending_defense is None
    changed = next(p for p in after.resources.pools if p.id == pool)
    if pool == "hp:a":
        assert changed.injury and changed.injury.unconscious
    else:
        assert changed.fatigue and changed.fatigue.collapsed
    play.rng = RecordedDice(())
    assert await CombatService(play).execute(cid, command, principal_id="a") == result
    assert play.rng.exhausted() and await play.store.read(cid) == final == await play.store.replay(
        cid
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("legal", [False, True])
async def test_unarmed_kick_uses_actual_dx_minus_two_and_location_target(
    tmp_path: Path,
    backend: str,
    legal: bool,
) -> None:
    # DX10, kick-2, neck-5 =>3; darkness-1 makes the otherwise same declaration2.
    cid, play = await genesis_health(tmp_path, backend, darkness=0 if legal else -1, bare=True)
    before = await play.store.read(cid)
    state = play._load(before)
    command = TakeUnarmedTurn(
        id="minimum-kick",
        actor_id="a",
        expected_revision=state.revision,
        encounter_id="fight",
        action="kick",
        target_id="b",
        location="neck",
    )
    if not legal:
        with pytest.raises(ValidationError, match="effective skill at least 3"):
            preview(play, state, state.encounters[0], command)
        with pytest.raises(ValidationError, match="effective skill at least 3"):
            await CombatService(play).execute(cid, command, principal_id="a")
        assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
        assert await play.store.read(cid) == before
        return
    preview(play, state, state.encounters[0], command)
    await CombatService(play).execute(cid, command, principal_id="a")
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((1, 1, 2, 3, 3, 3, 1))
    result = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="minimum-kick-response",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        ),
        principal_id="b",
    )
    assert (
        result.unarmed
        and result.unarmed.checks[0]
        and result.unarmed.checks[0].effective_target == 3
    )
    assert result.unarmed.checks[0].outcome.succeeded
    assert play.rng.exhausted() and await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("original_captured", [False, True])
async def test_staged_original_obeys_current_minimum_then_keeps_rolled_context(
    tmp_path: Path,
    backend: str,
    original_captured: bool,
) -> None:
    cid, play = await prepare(tmp_path, backend, darkness=-5)
    tasks = TaskService(play)
    state = play._load(await play.store.read(cid))
    await tasks.execute(
        cid,
        SetRealPlayClock(
            id="minimum-clock", actor_id="gm", expected_revision=state.revision, running=True
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(cid, attack(state.revision), principal_id="a")
    state = play._load(await play.store.read(cid))
    incoming = state.encounters[0].pending_defense
    assert incoming
    pending_id = None
    if original_captured:
        play.rng, play.seeds = secrets, source_seed
        result = await tasks.execute(
            cid,
            BeginOpponentAttack(
                id="minimum-original",
                actor_id="b",
                expected_revision=state.revision,
                encounter_id="fight",
                attack_id=incoming.id,
            ),
            principal_id="gm",
        )
        assert result.check and result.check.effective_target == 3 and result.check.total == 4
        pending_id = result.pending_id
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((5, 5, 5))
    drinks = DrinkingService(play, lambda *_: DrinkingEnvironment())
    drink = DrinkCommand(
        id="staged-attacker-drink",
        actor_id="a",
        expected_revision=state.revision,
        kind="drink",
        drinks=3,
    )
    if original_captured:
        # Existing immediate-choice authority freezes play once the original exists.
        with pytest.raises(ConflictError, match="Accept the pending task roll"):
            await drinks.execute(cid, drink, principal_id="a")
    else:
        drink_result = await drinks.execute(cid, drink, principal_id="a")
        assert drink_result.level == "tipsy"
    before = await play.store.read(cid)
    state = play._load(before)
    play.rng = RecordedDice(())
    if not original_captured:
        with pytest.raises(ValidationError, match="effective skill at least 3"):
            await tasks.execute(
                cid,
                BeginOpponentAttack(
                    id="minimum-original",
                    actor_id="b",
                    expected_revision=state.revision,
                    encounter_id="fight",
                    attack_id=incoming.id,
                ),
                principal_id="gm",
            )
        assert play.rng.exhausted() and await play.store.read(cid) == before
        return
    assert pending_id
    play.rng, play.seeds = secrets, source_seed
    result = await tasks.execute(
        cid,
        ChooseOpponentAttack(
            id="minimum-accept-original",
            actor_id="b",
            expected_revision=state.revision,
            pending_id=pending_id,
            choice="accept",
            response=ChooseDefense(
                id="minimum-accept-original",
                actor_id="b",
                expected_revision=state.revision,
                encounter_id="fight",
                defense="none",
            ),
        ),
        principal_id="b",
    )
    assert result.combat_json
    combat = CombatResult.model_validate_json(result.combat_json)
    assert combat.injury and combat.injury.attack.effective_target == 3
    assert combat.injury.attack.total == 4 and combat.injury.injury > 0
    final = await play.store.read(cid)
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice(()))
    assert await restarted.store.replay(cid) == final


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_very_high_mana_refund_precedes_canonical_attack_admission(
    tmp_path: Path,
    backend: str,
) -> None:
    from wayfarer.engine.character.compiler import Purchase
    from wayfarer.engine.rules.magic.gurps_magic import definitions
    from wayfarer.engine.simulation.actions import ActionRules
    from wayfarer.engine.simulation.magic.bindings import SpellChannel, SpellRules
    from wayfarer.engine.simulation.magic.spells import SpellCommand
    from wayfarer.orchestration.spells import SpellService

    cid, original = await setup(
        tmp_path / "source",
        "gurps-basic-set-4e-2004",
        human=True,
        allow_supernatural=True,
        start_encounter=False,
        extra_definitions=definitions(),
        extra_purchases=(
            Purchase(definition_id="trait:magery-0"),
            Purchase(definition_id="spell:light", amount=4),
        ),
        runtime_rules=ActionRules(
            id="minimum-refund",
            version=1,
            spells=SpellRules(
                id="minimum-light",
                version=1,
                execution_version=2,
                channels=(
                    SpellChannel(
                        id="light",
                        actor_id="a",
                        target_id="a",
                        location_id="dock",
                        spell_id="light",
                        mana="very-high",
                    ),
                ),
            ),
        ),
    )
    initial = await original.store.read(cid)
    state = original._load(initial)
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "pools": tuple(
                        pool.model_copy(update={"current": 4}) if pool.id == "fp:a" else pool
                        for pool in state.resources.pools
                    )
                }
            )
        }
    )
    initial["play_json"] = state.model_dump_json()
    play = build_play(tmp_path / "actual", original.engine, backend=backend, rng=RecordedDice(()))
    await seed_campaign(play.store, initial)
    await CombatService(play).execute(
        cid,
        StartEncounter(
            id="refund-start",
            actor_id="gm",
            expected_revision=0,
            encounter_id="fight",
            battlefield_id="dock",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
                Placement(actor_id="b", position=GridPoint(x=1, y=0), facing="west"),
            ),
        ),
        principal_id="gm",
    )
    play.rng = RecordedDice((3, 3, 3))
    spell_result = await SpellService(play).execute(
        cid,
        SpellCommand(
            id="refund-cast",
            actor_id="a",
            expected_revision=1,
            kind="start",
            spell_id="light",
            channel_id="light",
            cast_id="light-cast",
        ),
        principal_id="a",
    )
    assert spell_result.outcome == "active" and spell_result.energy_spent == 1
    await turn(cid, play, "b", "do_nothing")
    state = play._load(await play.store.read(cid))
    assert next(pool.current for pool in state.resources.pools if pool.id == "fp:a") == 3
    # Current FP3 halves usable ST, making Broadsword13-skull7-ST5=1.
    # The already-earned next-opportunity refund restores ST10 and target6.
    command = attack(state.revision).model_copy(update={"hit_location": "skull"})
    play.rng = RecordedDice(())
    preview(play, state, state.encounters[0], command)
    await CombatService(play).execute(cid, command, principal_id="a")
    after = play._load(await play.store.read(cid))
    assert next(pool.current for pool in after.resources.pools if pool.id == "fp:a") == 4
    assert after.encounters[0].pending_defense
    assert play.rng.exhausted()

    play.rng = RecordedDice((2, 2, 2, 1))
    result = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="refund-attack-response",
            actor_id="b",
            expected_revision=after.revision,
            encounter_id="fight",
            defense="none",
        ),
        principal_id="b",
    )
    assert result.injury and result.injury.attack.effective_target == 6
    assert play.rng.exhausted() and await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("pool", ["hp:a", "fp:a"])
async def test_successful_health_branch_preserves_the_prepared_target(
    tmp_path: Path,
    backend: str,
    pool: str,
) -> None:
    cid, play = await genesis_health(
        tmp_path, backend, darkness=0 if pool == "fp:a" else -5, low_pool=pool
    )
    state = play._load(await play.store.read(cid))
    command = attack(state.revision)
    play.rng = RecordedDice((3, 3, 3))
    await CombatService(play).execute(cid, command, principal_id="a")
    assert play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == pool) == 0
    play.rng = RecordedDice((1, 1, 2, 3, 3, 3, 1))
    result = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="health-target-response",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        ),
        principal_id="b",
    )
    assert result.injury and result.injury.attack.effective_target == 3
    assert play.rng.exhausted() and await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_lite_preserves_native_low_target_and_sitting_arithmetic(
    tmp_path: Path,
    backend: str,
) -> None:
    from wayfarer.engine.simulation.combat.attack_policy import PREFIX

    cid, original = await setup(
        tmp_path / "source", "gurps-lite-4e-2004", human=True, trained=False, darkness_penalty=-3
    )
    play = build_play(tmp_path / "actual", original.engine, backend=backend, rng=RecordedDice(()))
    await seed_campaign(play.store, await original.store.read(cid))
    await turn(cid, play, "a", "change_posture", posture="sitting")
    await turn(cid, play, "b", "do_nothing")
    before = play._load(await play.store.read(cid))
    command = attack(before.revision).model_copy(update={"hit_location": None})
    preview(play, before, before.encounters[0], command)
    await CombatService(play).execute(cid, command, principal_id="a")
    state = play._load(await play.store.read(cid))
    assert not any(event.id.startswith(PREFIX) for event in state.resources.events)
    play.rng = RecordedDice((1, 1, 2, 3, 3, 3))
    result = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="lite-native-response",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        ),
        principal_id="b",
    )
    assert result.injury and result.injury.attack.effective_target == 2
    assert result.injury.attack.outcome.succeeded and play.rng.exhausted()
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_prototype_native_attack_and_wait_never_capture_gurps_policy(
    tmp_path: Path,
    backend: str,
) -> None:
    from test_combat import setup as prototype_setup
    from test_combat import start as prototype_start

    from wayfarer.engine.simulation.combat.attack_policy import PREFIX
    from wayfarer.engine.simulation.combat.attack_targets import require_pending_minimum

    cid, play, service = await prototype_setup(tmp_path, backend=backend)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng, play.seeds = secrets, source_seed
    await service.execute(
        cid,
        prototype_start().model_copy(
            update={
                "placements": (
                    Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
                    Placement(actor_id="b", position=GridPoint(x=1, y=0), facing="west"),
                )
            }
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    await service.execute(
        cid,
        TakeCombatTurn(
            id="native-prototype-attack",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            target_id="b",
        ),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].pending_defense
    assert require_pending_minimum(play.rules_context, state, state.encounters[0]) is None
    assert not any(event.id.startswith(PREFIX) for event in state.resources.events)
    response = ChooseDefense(
        id="native-prototype-response",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    result = await service.execute(cid, response, principal_id="b")
    await turn(cid, play, "b", "wait")
    state = play._load(await play.store.read(cid))
    assert not any(event.id.startswith(PREFIX) for event in state.resources.events)
    final = await play.store.read(cid)
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice(()))
    assert await CombatService(restarted).execute(cid, response, principal_id="b") == result
    replayed, checks = await verify_commands(
        initial,
        (await play.store.history(cid))[count:],
        await play.store.stream(cid),
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "prototype-seed"),
    )
    assert len(checks) == 4 and all(check.folded and check.reexecuted for check in checks)
    assert replayed == final == await play.store.replay(cid)
