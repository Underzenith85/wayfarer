"""Explicit diagnostic injury reducers test phase guards, not a health host seed."""

from pathlib import Path

import pytest
from support.analyze_magic import revision
from support.detect_magic import complete, fixture, start, work

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.rules.checks import NoRandom, Outcome, RecordedDice
from wayfarer.engine.simulation.health.healing import restore_hp
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.magic.detect_magic_state import (
    DetectSubject,
    ObserveDetectMagicSubject,
    StartDetectMagic,
    WorkDetectMagic,
)
from wayfarer.errors import ConflictError
from wayfarer.orchestration.detect_magic import DetectMagicService
from wayfarer.orchestration.play import PlayService


async def injure_heal_diagnostic(play: PlayService, cid: str) -> None:
    before = play._load(await play.store.read(cid))

    def resolve(campaign: Campaign) -> CommandReceipt:
        original = play._load(campaign)
        resources, result = apply_injury(
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
        assert result.injury == 1
        injured = original.model_copy(update={"resources": resources})
        injured = play.checkpoint(injured, before=original, run_npcs=False)
        hp = next(p for p in injured.resources.pools if p.id == "hp:c")
        restored, amount = restore_hp(injured.resources, hp, 1, kind="magic")
        assert amount == 1 and restored.injury is not None and restored.injury.shock == 1
        healed = injured.model_copy(
            update={
                "revision": original.revision + 1,
                "resources": injured.resources.model_copy(
                    update={
                        "revision": original.revision + 1,
                        "pools": tuple(
                            restored if p.id == hp.id else p for p in injured.resources.pools
                        ),
                    }
                ),
            }
        )
        healed = play.checkpoint(healed, before=injured, run_npcs=False)
        play.commit(campaign, healed)
        return CommandReceipt(action="resource", outcome="diagnostic")

    await play.store.commit_turn(
        cid,
        "diagnostic-injury-heal",
        before.revision,
        "explicit-health-checkpoint-diagnostic",
        resolve,
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("phase", ["start", "work", "ready"])
async def test_current_shock_refuses_start_or_work_but_modifies_actual_ready_check(
    tmp_path: Path,
    backend: str,
    phase: str,
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, "temporary")
    if phase != "start":
        await start(play, cid, carrier="temporary")
    if phase == "ready":
        await work(play, cid)
    await injure_heal_diagnostic(play, cid)
    if phase == "start":
        before = await play.store.read(cid)
        with pytest.raises(ConflictError, match="shock"):
            await start(play, cid, carrier="temporary")
        # Authenticated physical observation is independent, but no cast/roll is committed.
        assert (
            play._load(await play.store.read(cid)).resources.game_time
            == play._load(before).resources.game_time
        )
    elif phase == "work":
        before = await play.store.read(cid)
        play.rng = RecordedDice(())
        with pytest.raises(ConflictError, match="shock"):
            await DetectMagicService(play).execute(
                cid,
                WorkDetectMagic(
                    id="work-after-shock",
                    actor_id="c",
                    expected_revision=await revision(play, cid),
                    cast_id="detection",
                    seconds=5,
                ),
                principal_id="cora",
            )
        assert await play.store.read(cid) == before and play.rng.exhausted()
    else:
        receipt = await complete(play, cid, 57)
        assert receipt.check is not None and receipt.check.base_target == 12
        assert receipt.check.effective_target == 11 and receipt.check.total == 12
        assert receipt.check.outcome == Outcome.FAILURE and receipt.energy_spent == 1


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_maintaining_magelock_caster_refused_before_rng(
    tmp_path: Path,
    backend: str,
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, "temporary")
    service = DetectMagicService(play)
    await service.execute(
        cid,
        ObserveDetectMagicSubject(
            id="maintainer-subject",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            subject=DetectSubject(
                id="maintainer",
                caster_id="a",
                target_id="chest",
                carrier="lock",
                backfire="injury-one",
            ),
        ),
        principal_id="gm",
    )
    saved = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError, match="spells on"):
        await service.execute(
            cid,
            StartDetectMagic(
                id="maintainer-start",
                actor_id="a",
                expected_revision=await revision(play, cid),
                cast_id="maintainer",
                subject_id="maintainer",
            ),
            principal_id="alice",
        )
    assert await play.store.read(cid) == saved and play.rng.exhausted()
