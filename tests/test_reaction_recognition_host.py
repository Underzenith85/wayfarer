"""Actor-scoped recognition survives legacy source attribution and GM recovery."""

import json
import secrets
from pathlib import Path

import pytest
from support.runtime import build_runtime
from test_combat_sensory_authority import change
from test_reaction_task_host import choose, fixture, prepare, source

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.social.social_hooks import Reputation, Standing
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.npcs import NPCReputation, NPCSocialStanding
from wayfarer.engine.simulation.social.social import (
    SocialCommand,
    SocialContext,
    SocialDisclosure,
    apply_interaction,
)
from wayfarer.errors import ValidationError
from wayfarer.orchestration.reaction_records import AttributeReactionRecognition
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_ambiguous_legacy_recognition_requires_gm_attribution_then_replays(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend)

    def old_source(state: PlayState) -> PlayState:
        command = SocialCommand(
            id="old-social",
            actor_id="a",
            subject_id="npc",
            kind="reaction",
            trigger_id="old",
            expected_revision=state.resources.revision,
        )
        context = SocialContext(
            "gurps-basic-set-4e-2004",
            0,
            standing=Standing(reputations=(Reputation("hero", 2, "everyone", "occasionally"),)),
        )
        resources, world, _ = apply_interaction(
            state.resources,
            state.world,
            command,
            context,
            SocialDisclosure(),
            rng=RecordedDice((3, 3, 3, 3, 3, 3)),
            system=True,
        )
        return state.model_copy(update={"resources": resources, "world": world})

    await change(play, cid, old_source)
    old = play._load(await play.store.read(cid))
    event = next(event for event in old.resources.events if event.id == "social:reaction:npc:old")
    authored = source(
        standing=NPCSocialStanding(
            reputations=(NPCReputation(id="hero", level=2, recognition="occasionally"),)
        )
    )
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="recorded source command"):
        await prepare(play, cid, authored=authored)
    initial = await play.store.read(cid)
    assert play._load(initial) == old
    count = len(await play.store.history(cid))
    command = AttributeReactionRecognition(
        id="attribute",
        actor_id="a",
        expected_revision=old.revision,
        event_id=event.id,
        reason="The GM identifies this historical interaction as the guard's reaction to A",
    )
    with pytest.raises(ValidationError, match="director"):
        await TaskService(play).execute(cid, command, principal_id="a")
    play.rng = secrets
    play.seeds = lambda: "03" * 32
    await TaskService(play).execute(cid, command, principal_id="gm")
    recovered = play._load(await play.store.read(cid))
    assert next(value for value in recovered.resources.events if value.id == event.id) == event
    runtime = build_runtime(play)
    visible = json.dumps(await runtime.read(cid, principal_id="a"))
    events = json.dumps(
        [value.model_dump(mode="json") for value in await runtime.events(cid, principal_id="a")]
    )
    for hidden in (
        "recognition-recorded",
        command.reason,
        command.event_id,
        "recognition_actor_id",
    ):
        assert hidden not in visible and hidden not in events
    _, opened = await prepare(play, cid, authored=authored)
    await choose(play, cid, opened.pending_id)
    final = await play.store.read(cid)
    records = (await play.store.history(cid))[count:]
    ids = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [value for value in await play.store.stream(cid) if value.command_id in ids],
        configuration_digest=old.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert len(checks) == 3 and all(check.folded and check.reexecuted for check in checks)
    assert replayed == final == await play.store.replay(cid)
