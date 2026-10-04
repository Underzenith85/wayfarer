"""Engine checkpoint diagnostics followed by real registered analysis commands.

There is no instantaneous injury/healing service in this bounded host. The
explicit diagnostic CAS applies canonical injury/recovery reducers and real
Play checkpoints; it is not a health command or original-genesis seed proof.
"""

import json
from pathlib import Path

import pytest
from support.analyze_magic import fixture, report, revision, start
from support.runtime import build_runtime

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.rules.checks import NoRandom
from wayfarer.engine.simulation.health.healing import restore_hp
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.magic.analyze_magic_state import (
    PREFIX,
    AnalysisConcentrationReceipt,
    CompleteAnalyzeMagic,
    WorkAnalyzeMagic,
    casts,
    discovery_records,
    secret_result,
)
from wayfarer.errors import ConflictError
from wayfarer.orchestration.play import PlayService


async def _injure_heal_checkpoint_diagnostic(play: PlayService, cid: str) -> None:
    """Persist an honestly named diagnostic, never a fabricated health receipt."""

    before = play._load(await play.store.read(cid))

    def resolve(campaign: Campaign) -> CommandReceipt:
        original = play._load(campaign)
        hp = next(p for p in original.resources.pools if p.id == "hp:c")
        resources, injury = apply_injury(
            original.resources,
            Wound(
                id="diagnostic-injury",
                actor_id="c",
                expected_revision=original.resources.revision,
                basic_damage=1,
                resistance=0,
                damage_type="cr",
            ),
            ht=10,
            rng=NoRandom(),
            system=True,
        )
        injured = original.model_copy(
            update={"revision": resources.revision, "resources": resources}
        )
        assert injury.injury == 1
        injured = play.checkpoint(injured, before=original, run_npcs=False)
        marked = casts(injured.resources)["analysis"]
        assert marked.distracted and marked.status == casts(original.resources)["analysis"].status
        patient = next(p for p in injured.resources.pools if p.id == "hp:c")
        restored, amount = restore_hp(injured.resources, patient, 1, kind="magic")
        assert amount == 1 and restored.current == hp.current
        healed = injured.model_copy(
            update={
                "resources": injured.resources.model_copy(
                    update={
                        "pools": tuple(
                            restored if p.id == restored.id else p for p in injured.resources.pools
                        )
                    }
                )
            }
        )
        healed = play.checkpoint(healed, before=injured, run_npcs=False)
        assert casts(healed.resources)["analysis"].distracted
        assert healed.resources.game_time == original.resources.game_time
        play.commit(campaign, healed)
        return CommandReceipt(action="resource", outcome="engine-checkpoint-diagnostic")

    await play.store.commit_turn(
        cid,
        "injury-healing-checkpoint-diagnostic",
        before.revision,
        json.dumps({"operation": "engine-checkpoint-diagnostic"}, sort_keys=True),
        resolve,
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "phase,seed,passed,dice",
    [
        ("casting", None, None, None),
        ("ready", 57, True, (3, 2, 2)),
        ("ready", 48, False, (6, 6, 4)),
    ],
)
async def test_healed_injury_checkpoint_bounds_work_and_ready_completion(
    tmp_path: Path,
    backend: str,
    phase: str,
    seed: int | None,
    passed: bool | None,
    dice: tuple[int, int, int] | None,
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    await start(play, cid)
    runtime = build_runtime(play)
    seconds = 1200 if phase == "casting" else 3600
    await runtime.submit_json(
        cid,
        WorkAnalyzeMagic(
            id="first-study",
            actor_id="c",
            expected_revision=await revision(play, cid),
            cast_id="analysis",
            seconds=seconds,
        ).model_dump(mode="json"),
        principal_id="cora",
    )
    await _injure_heal_checkpoint_diagnostic(play, cid)
    marked = play._load(await play.store.read(cid))
    cast = casts(marked.resources)["analysis"]
    assert cast.status == phase and cast.seconds == seconds and cast.distracted
    assert next(p.current for p in marked.resources.pools if p.id == "hp:c") == cast.hp
    assert next(p.current for p in marked.resources.pools if p.id == "fp:c") == 10
    beginning = marked.resources.game_time
    command = (
        WorkAnalyzeMagic(
            id="continue-study",
            actor_id="c",
            expected_revision=marked.revision,
            cast_id="analysis",
            seconds=2400,
        )
        if phase == "casting"
        else CompleteAnalyzeMagic(
            id="complete-study",
            actor_id="c",
            expected_revision=marked.revision,
            cast_id="analysis",
        )
    )
    if phase == "casting":
        # This host has no authoritative injury-turn expiry during hour work.
        # It must refuse before dice or elapsed-time credit, preserving the
        # durable distraction instead of pretending healing expired shock.
        snapshot = await play.store.read(cid)
        history = await play.store.history(cid)
        stream = await play.store.stream(cid)
        play.rng = NoRandom()
        with pytest.raises(ConflictError, match="temporal injury shock"):
            await runtime.submit_json(cid, command.model_dump(mode="json"), principal_id="cora")
        assert await play.store.read(cid) == snapshot
        assert await play.store.history(cid) == history
        assert await play.store.stream(cid) == stream
        assert secret_result(marked.resources, "analysis") is None
        assert await play.store.read(cid) == await play.store.replay(cid)
        return
    assert seed is not None and passed is not None and dice is not None
    play.seeds = lambda: f"{seed:064x}"
    await runtime.submit_json(cid, command.model_dump(mode="json"), principal_id="cora")
    state = play._load(await play.store.read(cid))
    receipts = tuple(
        AnalysisConcentrationReceipt.model_validate_json(e.kind)
        for e in state.resources.events
        if e.id.startswith(PREFIX + "distraction:")
    )
    assert len(receipts) == 1 and receipts[0].command_id == command.id
    check = receipts[0].check
    assert check.base_target == check.effective_target == 8  # approved Will 11 minus three
    assert check.dice == dice and check.total == sum(dice)
    assert check.outcome.succeeded is passed
    final_cast = casts(state.resources)["analysis"]
    assert not final_cast.distracted
    assert final_cast.seconds == 3600
    assert state.resources.game_time == beginning
    if passed:
        secret = secret_result(state.resources, "analysis")
        assert secret is not None and secret.energy_spent == 8
        # The actual focus check consumes the command's first three dice.
        assert secret.check.dice == (6, 4, 2)
        assert secret.check.effective_target == 11  # B236: current injury shock minus one
        assert not secret.check.outcome.succeeded
        assert next(p.current for p in state.resources.pools if p.id == "fp:c") == 2
        await report(play, cid)
        assert not discovery_records(play._load(await play.store.read(cid)).resources)
    else:
        assert final_cast.status == "cancelled"
        assert secret_result(state.resources, "analysis") is None
        assert next(p.current for p in state.resources.pools if p.id == "fp:c") == 10
        snapshot = await play.store.read(cid)
        await runtime.submit_json(cid, command.model_dump(mode="json"), principal_id="cora")
        assert await play.store.read(cid) == snapshot
    for principal in ("cora", "alice", "bob", "watcher"):
        for name in ("campaign", "stream"):
            view = await runtime.project(name, cid, principal_id=principal)
            assert PREFIX not in json.dumps(view)
            assert "effective_target" not in json.dumps(view)
    assert await play.store.read(cid) == await play.store.replay(cid)
