"""Actual host stateful guards, physical completeness and immutable information."""

from pathlib import Path

import pytest
from support.aura import fixture, observe, revision
from support.runtime import build_play
from test_gadgeteer_gizmos_persistence import FailingCommitPlay

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.magic.aura_state import (
    AuraSubjectFacts,
    CastAura,
    ObserveAuraSubject,
    ReportAura,
    SecretAuraFact,
)
from wayfarer.engine.simulation.magic.ritual_state import DeclareRitualCapability
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.aura import AuraService
from wayfarer.orchestration.spell_rituals import SpellRitualService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_authority_stale_cas_restart_and_changed_retry(tmp_path: Path, backend: str) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await observe(play, cid)
    command = CastAura(
        id="aura",
        actor_id="c",
        expected_revision=await revision(play, cid),
        cast_id="aura",
        subject_id="subject",
    )
    saved = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    for principal in ("alice", "bob", "watcher"):
        with pytest.raises((AuthorizationError, ValidationError)):
            await AuraService(play).execute(cid, command, principal_id=principal)
    with pytest.raises(ConflictError):
        await AuraService(play).execute(
            cid, command.model_copy(update={"expected_revision": 0}), principal_id="cora"
        )
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice((3, 3, 3)))
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await AuraService(failing).execute(cid, command, principal_id="cora")
    assert await play.store.read(cid) == saved
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    play.rng = RecordedDice((3, 3, 3))
    receipt = await AuraService(play).execute(cid, command, principal_id="cora")
    final = await play.store.read(cid)
    restart = build_play(tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(()))
    assert await AuraService(restart).execute(cid, command, principal_id="cora") == receipt
    with pytest.raises(ConflictError):
        await AuraService(restart).execute(
            cid, command.model_copy(update={"subject_id": "changed"}), principal_id="cora"
        )
    assert await play.store.read(cid) == final
    assert isinstance(restart.rng, RecordedDice) and restart.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "problem", ["fake-trait", "nonmage-wording", "incomplete", "speech", "gesture"]
)
async def test_current_complete_facts_and_ritual_guards(
    tmp_path: Path, backend: str, problem: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    facts = AuraSubjectFacts(
        id="subject",
        caster_id="c",
        subject_id="b",
        classification="living-human",
        classification_complete=True,
        personality_complete=True,
        personality="Patient",
        emotion_complete=True,
        secrets_complete=True,
        mage_power="A practiced mage",
    )
    value: object
    if problem in ("speech", "gesture"):
        await observe(play, cid)
        await SpellRitualService(play).execute(
            cid,
            DeclareRitualCapability(
                id="ritual",
                actor_id="c",
                expected_revision=await revision(play, cid),
                speech_available=problem != "speech",
                gesture_available=problem != "gesture",
                reason="Current physical restraint",
            ),
            principal_id="gm",
        )
        value = CastAura(
            id="aura",
            actor_id="c",
            expected_revision=await revision(play, cid),
            cast_id="aura",
            subject_id="subject",
        )
        principal = "cora"
    else:
        changes = (
            {
                "secret_traits": (
                    SecretAuraFact(
                        id="fake", description="A hidden trait", definition_id="trait:not-purchased"
                    ),
                )
            }
            if problem == "fake-trait"
            else {"mage_power": None}
            if problem == "nonmage-wording"
            else {"secrets_complete": False}
        )
        value = {
            "kind": "aura_subject",
            "id": "facts",
            "actor_id": "gm",
            "expected_revision": await revision(play, cid),
            "subject": facts.model_copy(update=changes).model_dump(mode="json"),
        }
        principal = "gm"
    saved = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    with pytest.raises((ValidationError, ValueError)):
        await AuraService(play).execute(cid, value, principal_id=principal)
    assert await play.store.read(cid) == saved
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_critical_secret_trait_cannot_be_replaced_or_omitted(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await observe(play, cid, secret=True)
    play.rng = RecordedDice((1, 1, 1))
    await AuraService(play).execute(
        cid,
        CastAura(
            id="aura",
            actor_id="c",
            expected_revision=await revision(play, cid),
            cast_id="aura",
            subject_id="subject",
        ),
        principal_id="cora",
    )
    saved = await play.store.read(cid)
    with pytest.raises(ValidationError):
        await AuraService(play).execute(
            cid,
            ReportAura(
                id="omit",
                actor_id="gm",
                expected_revision=await revision(play, cid),
                cast_id="aura",
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == saved


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("change", ["move", "new-facts"])
async def test_actual_current_subject_change_refuses_before_dice(
    tmp_path: Path, backend: str, change: str
) -> None:
    from wayfarer.engine.simulation.magic.aura_state import observations
    from wayfarer.orchestration.scenes import SceneService, TravelScene

    cid, play, _ = await fixture(tmp_path, backend)
    await observe(play, cid)
    if change == "move":
        result = await SceneService(play).execute(
            cid,
            TravelScene(
                id="move-subject",
                actor_id="b",
                expected_revision=await revision(play, cid),
                exit_id="to-alley",
            ),
            principal_id="b",
        )
        assert result.kind == "entered" and result.scene_id == "alley"
        state = play._load(await play.store.read(cid))
        assert next(e.location_id for e in state.world.entities if e.id == "b") == "alley"
    else:
        state = play._load(await play.store.read(cid))
        old = observations(state.resources)[0].subject
        await AuraService(play).execute(
            cid,
            ObserveAuraSubject(
                id="new-physical",
                actor_id="gm",
                expected_revision=state.revision,
                subject=old.model_copy(update={"id": "new-subject", "violent_emotion": None}),
            ),
            principal_id="gm",
        )
    saved = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError):
        await AuraService(play).execute(
            cid,
            CastAura(
                id="aura",
                actor_id="c",
                expected_revision=await revision(play, cid),
                cast_id="aura",
                subject_id="subject",
            ),
            principal_id="cora",
        )
    assert await play.store.read(cid) == saved
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    assert play.rng.exhausted()
