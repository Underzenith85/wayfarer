"""Actual B249 discoveries preserve the latest meaningful observer belief."""

import json
from pathlib import Path

import pytest
from support.analyze_magic import revision
from support.detect_magic import fixture
from support.runtime import build_runtime, played
from test_haste_manufacture_power_composition import _canonical_campaign

from scripts.replay_fixtures import FixtureExecutor
from wayfarer import validation
from wayfarer.engine.simulation.magic.analyze_magic_state import (
    AnalyzeSubject,
    CompleteAnalyzeMagic,
    ObserveAnalyzeMagicSubject,
    ReportAnalyzeMagic,
    StartAnalyzeMagic,
    WorkAnalyzeMagic,
)
from wayfarer.engine.simulation.magic.detect_magic_state import (
    CompleteDetectMagic,
    DetectSubject,
    ObserveDetectMagicSubject,
    StartDetectMagic,
    WorkDetectMagic,
)
from wayfarer.orchestration.analyze_magic import AnalyzeMagicService
from wayfarer.orchestration.detect_magic import DetectMagicService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands


async def analyze(
    play: PlayService, cid: str, seed: int, claimed: int | None
) -> CompleteAnalyzeMagic:
    runtime = build_runtime(play)
    await runtime.submit_json(
        cid,
        ObserveAnalyzeMagicSubject(
            id="analysis-subject",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            subject=AnalyzeSubject(id="analysis-physical", caster_id="c", item_id="cloak"),
        ).model_dump(mode="json"),
        principal_id="gm",
    )
    await runtime.submit_json(
        cid,
        StartAnalyzeMagic(
            id="analysis-start",
            actor_id="c",
            expected_revision=await revision(play, cid),
            cast_id="analysis",
            subject_id="analysis-physical",
        ).model_dump(mode="json"),
        principal_id="cora",
    )
    await runtime.submit_json(
        cid,
        WorkAnalyzeMagic(
            id="analysis-work",
            actor_id="c",
            expected_revision=await revision(play, cid),
            cast_id="analysis",
            seconds=3600,
        ).model_dump(mode="json"),
        principal_id="cora",
    )
    play.seeds = lambda: f"{seed:064x}"
    command = CompleteAnalyzeMagic(
        id="analysis-complete",
        actor_id="c",
        expected_revision=await revision(play, cid),
        cast_id="analysis",
    )
    await runtime.submit_json(cid, command.model_dump(mode="json"), principal_id="cora")
    await runtime.submit_json(
        cid,
        ReportAnalyzeMagic(
            id="analysis-report",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            cast_id="analysis",
            claimed_power=claimed,
        ).model_dump(mode="json"),
        principal_id="gm",
    )
    return command


async def detect(play: PlayService, cid: str) -> None:
    runtime = build_runtime(play)
    await runtime.submit_json(
        cid,
        ObserveDetectMagicSubject(
            id="detect-subject",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            subject=DetectSubject(
                id="detect-physical",
                caster_id="c",
                target_id="cloak",
                carrier="inventory",
                backfire="injury-one",
            ),
        ).model_dump(mode="json"),
        principal_id="gm",
    )
    await runtime.submit_json(
        cid,
        StartDetectMagic(
            id="detect-start",
            actor_id="c",
            expected_revision=await revision(play, cid),
            cast_id="detection",
            subject_id="detect-physical",
        ).model_dump(mode="json"),
        principal_id="cora",
    )
    await runtime.submit_json(
        cid,
        WorkDetectMagic(
            id="detect-work",
            actor_id="c",
            expected_revision=await revision(play, cid),
            cast_id="detection",
            seconds=5,
        ).model_dump(mode="json"),
        principal_id="cora",
    )
    play.seeds = lambda: f"{14:064x}"
    command = CompleteDetectMagic(
        id="detect-complete",
        actor_id="c",
        expected_revision=await revision(play, cid),
        cast_id="detection",
    )
    await runtime.submit_json(cid, command.model_dump(mode="json"), principal_id="cora")
    before = await play.store.read(cid)
    receipt = await DetectMagicService(play).execute(cid, command, principal_id="cora")
    assert receipt.check is not None and receipt.check.base_target == 10
    assert receipt.energy_spent == 0 and receipt.finding is not None
    assert receipt.finding.power == 22
    assert set(receipt.finding.model_dump(mode="json")) == {
        "actor_id",
        "target_id",
        "magical",
        "permanence",
        "spell_id",
        "power",
    }
    assert before == await play.store.read(cid)


async def visible(play: PlayService, cid: str, expected: int) -> None:
    state = play._load(await play.store.read(cid))
    binding = next(i for i in state.resources.items if i.id == "cloak").enchantments[0]
    assert binding.power == 22
    runtime = build_runtime(play)
    for name in ("campaign", "stream"):
        view = validation.mapping(
            validation.decode(json.dumps(await runtime.project(name, cid, principal_id="cora")))
        )
        item = validation.mapping(
            next(
                i
                for i in validation.sequence(view["inventory"])
                if validation.mapping(i)["id"] == "cloak"
            )
        )
        shown = validation.mapping(validation.sequence(item["enchantments"])[0])
        assert shown["power"] == expected
        assert set(shown) == set(binding.model_dump(mode="json"))
        assert "truthful" not in json.dumps(view) and "actual_power" not in json.dumps(view)
        for principal in ("alice", "bob", "watcher"):
            other = validation.mapping(
                validation.decode(
                    json.dumps(await runtime.project(name, cid, principal_id=principal))
                )
            )
            assert "magic_findings" not in other
            assert not any(
                validation.mapping(i)["id"] == "cloak"
                for i in validation.sequence(other["inventory"])
            )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("order", ["false-then-detect", "detect-then-false", "detect-then-failure"])
async def test_actual_discovery_claim_chronology_preserves_canonical_power(
    tmp_path: Path, backend: str, order: str
) -> None:
    cid, play, initial = await fixture(tmp_path, backend)
    before = play._load(await play.store.read(cid))
    assert next(p.current for p in before.resources.pools if p.id == "fp:c") == 10
    if order == "false-then-detect":
        command = await analyze(play, cid, 30, 24)
        await visible(play, cid, 24)
        await detect(play, cid)
        expected = 22
    else:
        await detect(play, cid)
        await visible(play, cid, 22)
        command = await analyze(
            play,
            cid,
            48 if order == "detect-then-failure" else 30,
            None if order == "detect-then-failure" else 24,
        )
        expected = 22 if order == "detect-then-failure" else 24
    await visible(play, cid, expected)
    final = await play.store.read(cid)
    state = play._load(final)
    assert state.resources.game_time == before.resources.game_time + 3605
    assert next(p.current for p in state.resources.pools if p.id == "fp:c") == 2
    receipt = await AnalyzeMagicService(play).execute(cid, command, principal_id="cora")
    assert receipt.model_dump(mode="json") == {"command_id": command.id, "outcome": "finished"}
    assert final == await play.store.read(cid)
    records = await played(play.store, cid)
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert _canonical_campaign(replayed) == _canonical_campaign(final)
