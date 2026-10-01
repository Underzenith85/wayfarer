"""B237 capability observations cannot silently survive approved build replacement."""

from pathlib import Path

import pytest
from test_lock_spell_persistence import declare, prepare, revision
from test_spell_rituals import observation, start_command

from wayfarer.engine.simulation.magic.ritual_state import latest
from wayfarer.errors import ValidationError
from wayfarer.orchestration.advancement import AdvanceCharacter, AdvancementService, GrantPoints
from wayfarer.orchestration.locks import LockSpellService


async def test_approved_training_change_requires_capability_refresh(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    await declare(play, cid)
    await observation(play, cid, speech=True, gesture=True)
    before = play._load(await play.store.read(cid))
    original = latest(before.resources, "a")
    assert original is not None
    service = AdvancementService(play)
    await service.grant(
        cid,
        GrantPoints(
            id="grant",
            actor_id="gm",
            target_actor_id="a",
            expected_revision=before.revision,
            points=20,
            reason="approved study",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    actor = next(a for a in state.actors if a.actor_id == "a")
    assert actor.approval is not None
    draft = actor.proposal.draft.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": 8}) if p.definition_id == "spell:magelock" else p
                for p in actor.proposal.draft.purchases
            )
        }
    )
    await service.advance(
        cid,
        AdvanceCharacter(
            id="learn",
            actor_id="a",
            expected_revision=state.revision,
            expected_build_revision=actor.approval.build_revision,
            draft=draft,
            reason="Magelock practice",
        ),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    assert play.rules_context.approved_build(state, "a").revision != original.build_revision
    command = (await start_command(play, cid)).model_copy(update={"channel_id": "magelock"})
    snapshot = await play.store.read(cid)
    with pytest.raises(ValidationError, match="Refresh ritual capability"):
        await LockSpellService(play).execute(cid, command, principal_id="gm")
    assert await play.store.read(cid) == snapshot
    await observation(play, cid, speech=True, gesture=True, identifier="refresh")
    result = await LockSpellService(play).execute(
        cid,
        command.model_copy(update={"expected_revision": await revision(play, cid)}),
        principal_id="gm",
    )
    assert result.outcome == "casting"
    assert await play.store.read(cid) == await play.store.replay(cid)
