"""Full original-genesis Wither reexecution; ordinary care has a separate fold limit."""

import secrets
from pathlib import Path

import pytest
from support.runtime import played
from support.wither_limb import fixture, revision
from test_haste_manufacture_power_composition import _canonical_campaign
from test_wither_limb_host import contact, prepare_contact

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.health.hit_locations import disabled
from wayfarer.engine.simulation.health.medical.commands import BeginRecovery, FinishRecovery
from wayfarer.engine.simulation.magic.wither_spell_state import contact_results
from wayfarer.orchestration.combat import ChooseDefense, CombatService, EndEncounter
from wayfarer.orchestration.medical import CareEnvironment, MedicalService
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_original_genesis_staff_cast_and_permanent_contact_reexecution(
    tmp_path: Path, backend: str
) -> None:
    cid, play, initial = await fixture(tmp_path, backend)
    await prepare_contact(play, cid)
    play.rng = secrets
    play.seeds = lambda: f"{43:064x}"
    await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="response",
            actor_id="b",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            defense="none",
        ),
        principal_id="b",
    )
    saved = await play.store.read(cid)
    state = play._load(saved)
    result = contact_results(state.resources)[0]
    assert (
        result.outcome == "withered"
        and result.injury == 2
        and result.hp_before == 9
        and result.hp_after == 7
    )
    assert "right-arm" in disabled(state.resources, "b")
    records = await played(play.store, cid)
    ids = {r.command_id for r in records}
    replayed, evidence = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert evidence and all(e.folded and e.reexecuted for e in evidence)
    assert _canonical_campaign(replayed) == _canonical_campaign(saved)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_real_ordinary_hp_bandaging_and_elapsed_time_do_not_cure_arm(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    result = await contact(play, cid)
    await CombatService(play).execute(
        cid,
        EndEncounter(
            id="end",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            reason="The single attack has resolved",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    hp = next(p for p in state.resources.pools if p.id == "hp:b")
    assert hp.injury is not None
    permanent = hp.injury.lasting_injuries
    assert hp.current == 8
    wounds = [
        e.id for e in state.resources.events if e.id.startswith("injury:") and e.target_id == "b"
    ]
    assert len(wounds) == 2
    # Existing medical commands persist and fold. Their old gurps-recovery replay
    # family is unregistered, so this oracle does not claim medical reexecution.
    medical = MedicalService(play, lambda _play, _state, _target: CareEnvironment())
    for index, wound in enumerate(wounds):
        identity = "bandage-" + str(index)
        await medical.execute(
            cid,
            BeginRecovery(
                id=identity,
                actor_id="a",
                expected_revision=await revision(play, cid),
                target_id="b",
                kind="bandage",
                wound_id=wound,
            ),
            principal_id="a",
        )
        await play.execute(
            cid,
            Wait(
                id=identity + "-wait",
                actor_id="a",
                expected_revision=await revision(play, cid),
                ticks=60,
            ),
            principal_id="a",
        )
        healed = await medical.execute(
            cid,
            FinishRecovery(
                id=identity + "-finish",
                actor_id="a",
                expected_revision=await revision(play, cid),
                task_id=identity,
            ),
            principal_id="a",
        )
        assert healed.hp_recovered == 1
    after = play._load(await play.store.read(cid))
    healed_hp = next(p for p in after.resources.pools if p.id == "hp:b")
    assert healed_hp.current == 10 and healed_hp.injury is not None
    assert healed_hp.injury.lasting_injuries == permanent and result.recovery_at is None
    assert "right-arm" in disabled(after.resources, "b")
    assert after.resources.game_time >= permanent[0].inflicted_at + 120
    assert await play.store.read(cid) == await play.store.replay(cid)
