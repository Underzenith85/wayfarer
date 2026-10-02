"""Luck consumes the command's ongoing RNG stream and preserves seed-only callers."""

import json
from pathlib import Path

import pytest
from support.runtime import build_play, played, seed_campaign
from test_actions import campaign, engine
from test_luck import SEED, approved, command, invoke, pending

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.rules.checks import RecordedDice, draw_dice
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.simulation.traits.luck import apply_luck
from wayfarer.errors import ValidationError
from wayfarer.orchestration.entropy import CommandRandom, commit_command


def test_injected_stream_consumes_only_two_rerolls_and_leaves_consequence_dice() -> None:
    build, definitions = approved()
    rng = RecordedDice((6, 5, 4, 2, 2, 3, 1, 3))
    updated, receipt = apply_luck(
        pending(),
        command(),
        build,
        definitions,
        real_time=0,
        seed="unused because the host supplies its stream",
        rng=rng,
        authorized_actor_id="inventor",
        system=True,
    )
    assert receipt.attempts == ((6, 6, 6), (6, 5, 4), (2, 2, 3))
    assert receipt.chosen_index == 2 and updated.rolls[0].chosen_total == 7
    assert draw_dice(rng, 2) == (1, 3)
    assert rng.exhausted()


def test_injected_stream_continues_after_prior_command_draws() -> None:
    build, definitions = approved()
    actual, expected = SeededRandom(SEED), SeededRandom(SEED)
    assert draw_dice(actual) == draw_dice(expected)
    _, receipt = apply_luck(
        pending(),
        command(),
        build,
        definitions,
        real_time=0,
        rng=actual,
        authorized_actor_id="inventor",
        system=True,
    )
    assert receipt.attempts[1:] == (draw_dice(expected), draw_dice(expected))
    assert draw_dice(actual, 2) == draw_dice(expected, 2)
    assert receipt != invoke(pending()).receipts[0]


def test_exact_retry_needs_no_rng_and_rejected_use_never_consumes_rng() -> None:
    build, definitions = approved()
    spent = invoke(pending())
    replayed, receipt = apply_luck(
        spent,
        command(),
        build,
        definitions,
        real_time=0,
        authorized_actor_id="inventor",
        system=True,
    )
    assert replayed == spent and receipt == spent.receipts[0]
    rng = RecordedDice((2, 3, 4))
    with pytest.raises(ValidationError, match="authority"):
        apply_luck(
            pending(),
            command(),
            build,
            definitions,
            real_time=0,
            rng=rng,
            authorized_actor_id="observer",
            system=True,
        )
    assert draw_dice(rng) == (2, 3, 4)


def test_new_use_requires_entropy_and_seed_only_history_is_unchanged() -> None:
    build, definitions = approved()
    with pytest.raises(ValidationError, match="explicit random source or seed"):
        apply_luck(
            pending(),
            command(),
            build,
            definitions,
            real_time=0,
            authorized_actor_id="inventor",
            system=True,
        )
    assert invoke(pending()).receipts[0].attempts == ((6, 6, 6), (3, 6, 6), (1, 2, 4))


async def test_command_random_shares_recorded_seed_with_prior_and_followup_rolls(
    tmp_path: Path,
) -> None:
    play = build_play(tmp_path, engine(), seeds=lambda: SEED, filename="luck-entropy.sqlite")
    initial = campaign(play.engine)
    await seed_campaign(play.store, initial)
    build, definitions = approved()

    def reduce(state: Campaign) -> CommandReceipt:
        before = draw_dice(CommandRandom())
        _, receipt = apply_luck(
            pending(),
            command(),
            build,
            definitions,
            real_time=0,
            rng=CommandRandom(),
            authorized_actor_id="inventor",
            system=True,
        )
        followup = draw_dice(CommandRandom(), 2)
        state["revision"] += 1
        return CommandReceipt(
            action="legacy", outcome=json.dumps((before, receipt.attempts, followup))
        )

    await commit_command(play, initial["id"], "luck", 0, "luck", reduce)
    record = (await played(play.store, initial["id"]))[0]
    assert record.reexecutable and record.entropy_seed == SEED
    replay = SeededRandom(record.entropy_seed)
    before = draw_dice(replay)
    attempts = ((6, 6, 6), draw_dice(replay), draw_dice(replay))
    followup = draw_dice(replay, 2)
    assert record.event["outcome"] == json.dumps((before, attempts, followup))
