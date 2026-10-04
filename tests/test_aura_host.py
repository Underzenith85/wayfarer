"""Actual learned Aura secret outcomes, uniform findings and original-genesis replay."""

import json
from pathlib import Path

import pytest
from support.aura import fixture, observe, revision
from support.runtime import build_runtime, played
from test_haste_manufacture_power_composition import _canonical_campaign

from scripts.replay_fixtures import FixtureExecutor
from wayfarer import validation
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.magic.aura_state import (
    AuraFinding,
    CastAura,
    ReportAura,
    projection,
    secret_result,
)
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.aura import AuraService
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "seed,success,critical",
    [(1, True, False), (1119, True, True), (48, False, False), (30, False, False)],
)
async def test_actual_aura_outcomes_and_private_findings(
    tmp_path: Path, backend: str, seed: int, success: bool, critical: bool
) -> None:
    cid, play, initial = await fixture(tmp_path, backend)
    await observe(play, cid, secret=True)
    before = play._load(await play.store.read(cid))
    command = CastAura(
        id="aura",
        actor_id="c",
        expected_revision=before.revision,
        cast_id="aura",
        subject_id="subject",
    )
    play.seeds = lambda: f"{seed:064x}"
    await build_runtime(play).submit_json(cid, command.model_dump(mode="json"), principal_id="cora")
    service = AuraService(play)
    assert (await service.execute(cid, command, principal_id="cora")).model_dump() == {
        "command_id": "aura",
        "outcome": "finished",
    }
    state = play._load(await play.store.read(cid))
    secret = secret_result(state.resources, "aura")
    assert (
        secret is not None
        and secret.check.effective_target == 10
        and secret.check.outcome.succeeded is success
    )
    assert state.resources.game_time == before.resources.game_time + 1
    assert next(p.current for p in state.resources.pools if p.id == "fp:c") == 7
    assert secret.observation.magery >= 0 and not secret.observation.control_json
    assert not projection(state.resources, ("c",))
    false = (
        AuraFinding(
            personality="Reckless and impatient",
            mage=False,
            mage_power=None,
            controlled=True,
            possessed=False,
            violent_emotion=None,
            secret_traits=("A hidden curse",),
        )
        if seed == 30
        else None
    )
    report = ReportAura(
        id="finding",
        actor_id="gm",
        expected_revision=state.revision,
        cast_id="aura",
        personality="Patient and cautious" if success else None,
        false_finding=false,
    )
    for principal in ("cora", "alice", "bob", "watcher"):
        with pytest.raises((AuthorizationError, ValidationError)):
            await service.execute(cid, report, principal_id=principal)
    await build_runtime(play).submit_json(cid, report.model_dump(mode="json"), principal_id="gm")
    for name in ("campaign", "stream"):
        view = await build_runtime(play).project(name, cid, principal_id="cora")
        rows = validation.sequence(validation.decode(json.dumps(view["aura_findings"])))
        raw_finding = validation.mapping(rows[0])["finding"]
        finding = validation.mapping(raw_finding) if raw_finding is not None else None
        if success:
            assert finding is not None
            assert finding["mage"] is True and finding["controlled"] is False
            assert (
                finding["mage_power"] == "A practiced mage"
                and finding["violent_emotion"] == "Anger"
            )
            assert finding["secret_traits"] == (["A concealed magical talent"] if critical else [])
        elif false is not None:
            assert finding == false.model_dump(mode="json")
        else:
            assert finding is None
        assert "truthful" not in json.dumps(view) and "aura:" not in json.dumps(view)
        for principal in ("alice", "bob", "watcher"):
            assert "aura_findings" not in await build_runtime(play).project(
                name, cid, principal_id=principal
            )
    saved = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError, match="today"):
        await service.execute(
            cid,
            command.model_copy(
                update={
                    "id": "again",
                    "cast_id": "again",
                    "expected_revision": await revision(play, cid),
                }
            ),
            principal_id="cora",
        )
    assert await play.store.read(cid) == saved and play.rng.exhausted()
    records = await played(play.store, cid)
    ids = {r.command_id for r in records}
    replayed, evidence = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert evidence and all(r.folded and r.reexecuted for r in evidence)
    assert _canonical_campaign(replayed) == _canonical_campaign(saved)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_approved_nonmage_has_no_magical_power_finding(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, subject_mage=False)
    await observe(play, cid, mage=False)
    service = AuraService(play)
    play.rng = RecordedDice((3, 3, 3))
    await service.execute(
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
    await service.execute(
        cid,
        ReportAura(
            id="report",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            cast_id="aura",
            personality="Patient",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    finding = projection(state.resources, ("c",))[0]["finding"]
    assert isinstance(finding, dict) and finding["mage"] is False and finding["mage_power"] is None
    assert next(p.current for p in state.resources.pools if p.id == "fp:c") == 7
