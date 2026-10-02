"""The private Acrobatic generation preserves absent and older feature histories."""

import secrets
from pathlib import Path

import pytest
from test_acrobatic_trait_bonuses import reaction_fixture

from scripts.replay_fixtures import FixtureExecutor
from wayfarer import validation
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.orchestration.combat import ChooseDefense, CombatService, generations
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "features",
    [
        frozenset(),
        frozenset({"grenade-fuse"}),
        frozenset({"grenade-fuse", "acrobatic-trait-bonuses"}),
    ],
)
async def test_exact_generation_seed_reexecutes_and_retries_after_activation(
    tmp_path: Path,
    backend: str,
    features: frozenset[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid, play = await reaction_fixture(tmp_path, backend, False, ("perfect-balance",))
    initial = await play.store.read(cid)
    state = play._load(initial)
    count = len(await play.store.history(cid))
    for number in range(10000):
        seed = f"{number:064x}"
        rng = SeededRandom(seed)
        acrobatics = sum(rng.randbelow(6) + 1 for _ in range(3))
        attack = sum(rng.randbelow(6) + 1 for _ in range(3))
        dodge = sum(rng.randbelow(6) + 1 for _ in range(3))
        if acrobatics == 9 and 7 <= attack <= 13 and dodge == 9:
            break
    else:
        raise AssertionError("Missing actual host consequence oracle seed")
    command = ChooseDefense(
        id="reaction",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="dodge",
        acrobatic_dodge=True,
    )
    play.rng, play.seeds = secrets, lambda: seed
    with monkeypatch.context() as generation:
        generation.setattr(generations, "ACTIVE", features)
        result = await CombatService(play).execute(cid, command, principal_id="b")
    final = await play.store.read(cid)
    correct = "acrobatic-trait-bonuses" in features
    trace = play._load(final).encounters[0].defense_history[-1].acrobatic_dodge_trace
    assert trace is not None and trace.effective_target == (9 if correct else 8)
    assert trace.outcome.succeeded == correct
    assert result.injury is not None and result.injury.defense is not None
    assert result.injury.defense.effective_target == (11 if correct else 7)
    assert (result.injury.injury == 0) == correct
    records = (await play.store.history(cid))[count:]
    assert records[0].command_input is not None
    payload = validation.mapping(replay_payload(records[0].command_input))
    recorded = payload.get(generations.KEY, [])
    assert isinstance(recorded, list) and frozenset(recorded) == features
    # Activation changes defaults, but reexecution/retry must use recorded inputs.
    monkeypatch.setattr(
        generations, "ACTIVE", frozenset({"grenade-fuse", "acrobatic-trait-bonuses"})
    )
    events = [e for e in await play.store.stream(cid) if e.command_id == command.id]
    replayed, checks = await verify_commands(
        initial,
        records,
        events,
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "seed-only"),
    )
    assert len(checks) == 1 and checks[0].folded and checks[0].reexecuted
    assert replayed == final
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, command, principal_id="b") == result
    assert await play.store.read(cid) == final and play.rng.exhausted()
