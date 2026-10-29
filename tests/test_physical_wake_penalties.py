"""B421/B428 generic IQ waking, B43 Combat Reflexes and retained cached receipts.

Unqualified waking uses ordinary IQ penalties. This does not implement a special
maneuver-free defensive-waking context or original-seed physical-check replay.
Authentic pre-repair cache preservation is separately evidenced with old stores.
"""

import json
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, seed_campaign
from test_mundane_trait_runtime import prepare

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.toxin import Intoxication
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.physical_checks import (
    PhysicalCheck,
    PhysicalCheckCommand,
    PhysicalCheckService,
)
from wayfarer.orchestration.play import PlayService


class NoDraw:
    def __init__(self) -> None:
        self.calls = 0

    def randbelow(self, upper: int) -> int:
        self.calls += 1
        raise AssertionError("Rejected commands and cached outcomes must not roll")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("level", ["tipsy", "drunk"])
@pytest.mark.parametrize(
    "trait,kind,targets",
    [
        (None, "wake", (9, 8)),
        ("combat-reflexes", "wake", (15, 14)),
        ("high-pain-threshold", "torture", (13, 13)),
    ],
)
async def test_current_waking_penalties_bonuses_and_torture_exemption(
    tmp_path: Path,
    backend: str,
    level: Literal["tipsy", "drunk"],
    trait: str | None,
    kind: Literal["wake", "torture"],
    targets: tuple[int, int],
) -> None:
    purchases = () if trait is None else (Purchase(definition_id="trait:" + trait),)
    cid, original = await prepare(tmp_path / "approved", *purchases)
    initial = await original.store.read(cid)
    state = original._load(initial)
    intoxication = Intoxication(actor_id="a", window_started=0, level=level)
    state = state.model_copy(
        update={"resources": state.resources.model_copy(update={"intoxications": (intoxication,)})}
    )
    initial["play_json"] = state.model_dump_json()
    play = build_play(tmp_path, original.engine, backend=backend, rng=RecordedDice([3, 3, 3]))
    await seed_campaign(play.store, initial)
    calls = 0

    def resolve(
        play: PlayService, current: PlayState, command: PhysicalCheckCommand
    ) -> PhysicalCheck:
        nonlocal calls
        calls += 1
        return PhysicalCheck(kind=kind)

    command = PhysicalCheckCommand(
        id="check", actor_id="a", expected_revision=0, trigger_id="disturbance"
    )
    expected = targets[0 if level == "tipsy" else 1]
    service = PhysicalCheckService(play, resolve)
    assert await service.execute(cid, command, principal_id="gm") is (expected >= 9)
    saved = await play.store.read(cid)
    changed = play._load(saved)
    event = next(e for e in changed.resources.events if e.id.startswith("physical-check:"))
    assert json.loads(event.kind)["effective_target"] == expected
    assert changed.resources.intoxications == (intoxication,)
    assert changed.actors == state.actors
    assert calls == 1
    history = await play.store.history(cid)
    events = await play.store.stream(cid)
    captured = await play.store.command_input(cid, command.id)
    assert saved == await play.store.replay(cid)

    def forbidden(
        play: PlayService, current: PlayState, value: PhysicalCheckCommand
    ) -> PhysicalCheck:
        raise AssertionError("Cached and rejected commands must not resolve the trigger")

    rng = NoDraw()
    restarted = build_play(tmp_path, original.engine, store=play.store, rng=rng)
    retried = PhysicalCheckService(restarted, forbidden)
    assert await retried.execute(cid, command, principal_id="gm") is (expected >= 9)
    with pytest.raises(ValidationError, match="Physical checks require director authority"):
        await retried.execute(cid, command, principal_id="alice")
    with pytest.raises(ConflictError):
        await retried.execute(
            cid, command.model_copy(update={"id": "stale", "trigger_id": "new"}), principal_id="gm"
        )
    assert rng.calls == 0
    assert await play.store.read(cid) == saved
    assert await play.store.history(cid) == history
    assert await play.store.stream(cid) == events
    assert await play.store.command_input(cid, command.id) == captured
