"""B345/B361/B375: actual preliminary reaction checks need effective skill3."""

import secrets
from functools import cache
from pathlib import Path

import pytest
from test_acrobatic_condition_checks import recovered_coma
from test_acrobatic_trait_bonuses import reaction_fixture
from test_combat_sensory_authority import change
from test_mastery_combat import purchase as original_purchase
from test_statistics import gurps_draft as original_draft

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.character.compiler import CharacterDraft, Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.fright import fright_effect
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.rules.traits.base import TraitOptions
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, level
from wayfarer.engine.simulation.health.fright import apply_effect
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration import task_combat_generations
from wayfarer.orchestration.combat import ChooseDefense, CombatService, generations
from wayfarer.orchestration.opponent_attack_records import BeginOpponentAttack, ChooseOpponentAttack
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.task_records import SetRealPlayClock, snapshot
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.replay import verify_commands

FEATURE = "acrobatic-skill-minimum"


def author_dx(monkeypatch: pytest.MonkeyPatch, dx: int) -> None:
    import test_gurps_melee

    def draft(*purchases: Purchase, st_level: int = 10) -> CharacterDraft:
        # This is the proposal submitted to the normal compiler and approval,
        # never a forged live skill/statistic or tactical pose.
        value = original_draft(*purchases, st_level=st_level)
        return value.model_copy(
            update={
                "purchases": tuple(
                    p.model_copy(update={"amount": dx}) if p.definition_id == "attribute:dx" else p
                    for p in value.purchases
                )
            }
        )

    monkeypatch.setattr(test_gurps_melee, "gurps_draft", draft)


async def prepare(
    path: Path,
    backend: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    dx: int = 6,
    balance: bool = False,
    aftermath: bool = True,
    prone: bool = False,
    luck: bool = False,
) -> tuple[str, PlayService]:
    author_dx(monkeypatch, dx)
    import test_acrobatic_trait_bonuses

    if luck:

        def approved_purchase(trait: str) -> Purchase:
            purchase = original_purchase(trait)
            return (
                purchase.model_copy(
                    update={"trait": TraitOptions(parameters=(("point-cost", 15),))}
                )
                if trait == "luck"
                else purchase
            )

        monkeypatch.setattr(test_acrobatic_trait_bonuses, "purchase", approved_purchase)
    if prone:
        from test_gurps_maneuvers import turn

        async def prone_attack(cid: str, play: PlayService) -> None:
            await turn(cid, play, "a", "do_nothing")
            await turn(cid, play, "b", "change_posture", posture="prone")
            await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")

        monkeypatch.setattr(test_acrobatic_trait_bonuses, "attack", prone_attack)
    traits = (("perfect-balance",) if balance else ()) + (("luck",) if luck else ())
    cid, play = await reaction_fixture(path, backend, False, traits)
    if aftermath:
        await change(play, cid, recovered_coma)
    return cid, play


def response(revision: int, *, acrobatic: bool = True, identifier: str = "defend") -> ChooseDefense:
    return ChooseDefense(
        id=identifier,
        actor_id="b",
        expected_revision=revision,
        encounter_id="fight",
        defense="dodge",
        acrobatic_dodge=acrobatic,
    )


@cache
def reaction_seed(succeed: bool) -> str:
    for number in range(1000000):
        seed = f"{number:064x}"
        rng = SeededRandom(seed)
        skill = sum(rng.randbelow(6) + 1 for _ in range(3))
        attack = sum(rng.randbelow(6) + 1 for _ in range(3))
        dodge = sum(rng.randbelow(6) + 1 for _ in range(3))
        if skill == (3 if succeed else 5) and attack == 9 and dodge == 9:
            return seed
    raise AssertionError("No Acrobatic consequence seed")


@cache
def typed_seed(luck: bool, original: bool = False) -> str:
    wanted = [9] if original else ([9, 9] if luck else []) + [3, 9]
    for number in range(1000000):
        seed = f"{number:064x}"
        rng = SeededRandom(seed)
        if [sum(rng.randbelow(6) + 1 for _ in range(3)) for _ in wanted] == wanted:
            return seed
    raise AssertionError("No typed Acrobatic consequence seed")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_condition_final_two_refuses_before_any_dice_but_legacy_original_bytes_replay(
    tmp_path: Path, backend: str, enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await prepare(tmp_path, backend, monkeypatch)
    state = play._load(await play.store.read(cid))
    assert (
        int(level(build(play.rules_context, state, "b", defensive=True), "skill:acrobatics").value)
        == 4
    )
    selected = response(state.revision)
    features = generations.ACTIVE
    monkeypatch.setattr(generations, "ACTIVE", features if enabled else features - {FEATURE})
    before, history = await play.store.read(cid), await play.store.history(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="not authorized"):
        await CombatService(play).execute(cid, selected, principal_id="a")
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid,
            selected.model_copy(update={"expected_revision": state.revision - 1}),
            principal_id="b",
        )
    if enabled:
        with pytest.raises(
            ValidationError, match="skill check requires effective skill at least 3"
        ):
            await CombatService(play).execute(cid, selected, principal_id="b")
        assert play.rng.exhausted()
        assert await play.store.read(cid) == before and await play.store.history(cid) == history
        assert state.encounters[0].participants[1].acrobatic_dodge_trace is None
        # Ordinary Dodge remains available. Its roll is not this skill check.
        play.rng = RecordedDice((3, 3, 3, 3, 3, 3, 1))
        result = await CombatService(play).execute(
            cid, response(state.revision, acrobatic=False), principal_id="b"
        )
        assert result.injury and result.injury.defense
        assert result.injury.defense.effective_target == 8 and result.injury.injury == 3
        assert play.rng.exhausted()
        return
    count = len(history)
    play.rng, play.seeds = secrets, lambda: reaction_seed(True)
    result = await CombatService(play).execute(cid, selected, principal_id="b")
    final = await play.store.read(cid)
    trace = play._load(final).encounters[0].defense_history[-1].acrobatic_dodge_trace
    assert trace and trace.effective_target == 2 and trace.total == 3 and trace.outcome.succeeded
    assert result.injury and result.injury.injury == 0
    recorded = await play.store.command_input(cid, selected.id)
    assert recorded and recorded.text
    payload = replay_payload(recorded.text)
    assert isinstance(payload, dict)
    selected_features = payload[generations.KEY]
    assert isinstance(selected_features, list) and FEATURE not in selected_features
    monkeypatch.setattr(generations, "ACTIVE", features)
    play.rng = RecordedDice(())
    assert await CombatService(play).execute(cid, selected, principal_id="b") == result
    assert play.rng.exhausted()
    records = (await play.store.history(cid))[count:]
    ids = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        before,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "legacy-seed"),
    )
    assert replayed == final and all(check.folded and check.reexecuted for check in checks)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("balance", [False, True])
@pytest.mark.parametrize("succeed", [False, True])
async def test_actual_final_three_includes_trait_and_condition_once_and_changes_hp(
    tmp_path: Path, backend: str, balance: bool, succeed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await prepare(
        tmp_path, backend, monkeypatch, dx=6 if balance else 7, balance=balance
    )
    before = await play.store.read(cid)
    count = len(await play.store.history(cid))
    state = play._load(before)
    play.rng, play.seeds = secrets, lambda: reaction_seed(succeed)
    selected = response(state.revision)
    result = await CombatService(play).execute(cid, selected, principal_id="b")
    final = await play.store.read(cid)
    trace = play._load(final).encounters[0].defense_history[-1].acrobatic_dodge_trace
    assert trace and trace.effective_target == 3 and trace.total == (3 if succeed else 5)
    assert tuple(m.value for m in trace.modifiers) == ((1, -2) if balance else (-2,))
    assert result.injury and result.injury.defense
    assert result.injury.attack.total == 9 and result.injury.defense.total == 9
    assert result.injury.defense.effective_target == (10 if succeed else 6)
    assert (result.injury.injury == 0) is succeed
    assert result.injury.hp_after == (10 if succeed else 10 - result.injury.injury)
    records = (await play.store.history(cid))[count:]
    ids = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        before,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "source-seed"),
    )
    assert replayed == final and all(check.folded and check.reexecuted for check in checks)
    play.rng = RecordedDice(())
    assert await CombatService(play).execute(cid, selected, principal_id="b") == result


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("use_luck", [False, True])
async def test_typed_original_stays_captured_when_current_acrobatic_choice_is_illegal(
    tmp_path: Path, backend: str, enabled: bool, use_luck: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await prepare(tmp_path, backend, monkeypatch, luck=use_luck)
    task = TaskService(play)
    state = play._load(await play.store.read(cid))
    await task.execute(
        cid,
        SetRealPlayClock(id="clock", actor_id="gm", expected_revision=state.revision, running=True),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    incoming = state.encounters[0].pending_defense
    assert incoming
    play.rng = RecordedDice((3, 3, 3))
    opened = await task.execute(
        cid,
        BeginOpponentAttack(
            id="original",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            attack_id=incoming.id,
        ),
        principal_id="gm",
    )
    assert opened.check and opened.check.total == 9 and opened.pending_id
    before, history = await play.store.read(cid), await play.store.history(cid)
    state = play._load(before)
    captured = snapshot(state).pending
    features = task_combat_generations.ACTIVE
    monkeypatch.setattr(
        task_combat_generations, "ACTIVE", features if enabled else features - {FEATURE}
    )
    choice = ChooseOpponentAttack(
        id="choose",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=opened.pending_id,
        choice="use-luck" if use_luck else "accept",
        response=response(state.revision, identifier="choose"),
    )
    if enabled:
        play.rng = RecordedDice(())
        with pytest.raises(
            ValidationError, match="skill check requires effective skill at least 3"
        ):
            await task.execute(cid, choice, principal_id="b")
        assert play.rng.exhausted()
        assert await play.store.read(cid) == before and await play.store.history(cid) == history
        assert snapshot(play._load(before)).pending == captured
        choice = choice.model_copy(
            update={"response": response(state.revision, acrobatic=False, identifier="choose")}
        )
        play.rng = RecordedDice(((3, 3, 3, 3, 3, 3) if use_luck else ()) + (3, 3, 3, 1))
    else:
        play.rng = RecordedDice(((3, 3, 3, 3, 3, 3) if use_luck else ()) + (1, 1, 1, 3, 3, 3))
    result = await task.execute(cid, choice, principal_id="b")
    from wayfarer.engine.simulation.combat.encounter import CombatResult

    assert result.combat_json
    combat = CombatResult.model_validate_json(result.combat_json)
    assert combat.injury and combat.injury.attack == opened.check
    assert (result.luck is not None) is use_luck
    if result.luck:
        assert result.luck.attempts == ((3, 3, 3),) * 3
    assert combat.injury.injury == (3 if enabled else 0) and play.rng.exhausted()
    final = await play.store.read(cid)
    monkeypatch.setattr(task_combat_generations, "ACTIVE", features)
    play.rng = RecordedDice(())
    assert await task.execute(cid, choice, principal_id="b") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_real_prone_and_stunned_ordinary_dodge_one_still_allows_automatic_three(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await prepare(tmp_path, backend, monkeypatch, aftermath=False, prone=True)

    def stunned(state: PlayState) -> PlayState:
        resources = apply_effect(
            state.resources,
            fright_effect(4, 10, rng=RecordedDice(())),
            actor_id="b",
            trigger_id="sudden-fear",
            command_id="stun",
            ht=10,
            will=10,
            modified_will=10,
            rng=RecordedDice(()),
        )
        return state.model_copy(update={"resources": resources})

    await change(play, cid, stunned)
    before = await play.store.read(cid)
    state = play._load(before)
    assert state.encounters[0].participants[1].posture == "prone"
    play.rng = RecordedDice((3, 3, 3, 1, 1, 1, 3, 3, 3))
    result = await CombatService(play).execute(
        cid, response(state.revision, acrobatic=False), principal_id="b"
    )
    assert result.injury and result.injury.defense
    # Basic Dodge7 + DB1 - prone3 - stunned4 = 1. B374 still admits defense3.
    assert result.injury.defense.effective_target == 1
    assert result.injury.defense.total == 3 and result.injury.defense.outcome.succeeded
    assert result.injury.injury == 0 and play.rng.exhausted()
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("use_luck", [False, True])
async def test_typed_perfect_balance_condition_final_three_is_legal_and_seed_reexecutes(
    tmp_path: Path, backend: str, use_luck: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await prepare(tmp_path, backend, monkeypatch, balance=True, luck=use_luck)
    task = TaskService(play)
    state = play._load(await play.store.read(cid))
    await task.execute(
        cid,
        SetRealPlayClock(id="clock", actor_id="gm", expected_revision=state.revision, running=True),
        principal_id="gm",
    )
    before = await play.store.read(cid)
    count = len(await play.store.history(cid))
    state = play._load(before)
    incoming = state.encounters[0].pending_defense
    assert incoming
    play.rng, play.seeds = secrets, lambda: typed_seed(False, original=True)
    opened = await task.execute(
        cid,
        BeginOpponentAttack(
            id="original",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            attack_id=incoming.id,
        ),
        principal_id="gm",
    )
    assert opened.check and opened.check.total == 9 and opened.pending_id
    state = play._load(await play.store.read(cid))
    selected = ChooseOpponentAttack(
        id="choose",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=opened.pending_id,
        choice="use-luck" if use_luck else "accept",
        response=response(state.revision, identifier="choose"),
    )
    play.seeds = lambda: typed_seed(use_luck)
    result = await task.execute(cid, selected, principal_id="b")
    final = await play.store.read(cid)
    trace = play._load(final).encounters[0].defense_history[-1].acrobatic_dodge_trace
    assert trace and trace.effective_target == 3 and trace.total == 3
    assert tuple(m.value for m in trace.modifiers) == (1, -2)
    from wayfarer.engine.simulation.combat.encounter import CombatResult

    assert result.combat_json
    combat = CombatResult.model_validate_json(result.combat_json)
    assert combat.injury and combat.injury.defense and combat.injury.injury == 0
    assert combat.injury.defense.effective_target == 10 and combat.injury.defense.total == 9
    assert (result.luck is not None) is use_luck
    records = (await play.store.history(cid))[count:]
    ids = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        before,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "typed-source-seed"),
    )
    assert replayed == final and all(check.folded and check.reexecuted for check in checks)
    play.rng = RecordedDice(())
    assert await task.execute(cid, selected, principal_id="b") == result


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("use_luck", [False, True])
async def test_secret_unrolled_choice_refuses_skill_two_before_original_or_luck_draws(
    tmp_path: Path, backend: str, use_luck: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await prepare(tmp_path, backend, monkeypatch, luck=use_luck)
    task = TaskService(play)
    state = play._load(await play.store.read(cid))
    await task.execute(
        cid,
        SetRealPlayClock(id="clock", actor_id="gm", expected_revision=state.revision, running=True),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    incoming = state.encounters[0].pending_defense
    assert incoming
    play.rng = RecordedDice(())
    opened = await task.execute(
        cid,
        BeginOpponentAttack(
            id="original",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            attack_id=incoming.id,
            visibility="secret",
        ),
        principal_id="gm",
    )
    assert opened.check is None and opened.pending_id
    before, history = await play.store.read(cid), await play.store.history(cid)
    state = play._load(before)
    with pytest.raises(ValidationError, match="skill check requires effective skill at least 3"):
        await task.execute(
            cid,
            ChooseOpponentAttack(
                id="choose",
                actor_id="b",
                expected_revision=state.revision,
                pending_id=opened.pending_id,
                choice="use-luck" if use_luck else "accept",
                response=response(state.revision, identifier="choose"),
            ),
            principal_id="b" if use_luck else "gm",
        )
    assert play.rng.exhausted()
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    cancelled = await task.execute(
        cid,
        ChooseOpponentAttack(
            id="cancel",
            actor_id="b",
            expected_revision=state.revision,
            pending_id=opened.pending_id,
            choice="cancel",
        ),
        principal_id="gm",
    )
    assert cancelled.status == "cancelled" and cancelled.check is None and play.rng.exhausted()
