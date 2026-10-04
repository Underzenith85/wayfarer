"""Original-genesis replay honors each defense's captured composition feature."""

import secrets
from pathlib import Path

import pytest
from support.rooted_dodge_composition import fixture, prepare
from support.rooted_feet import revision
from support.runtime import played
from test_haste_manufacture_power_composition import _canonical_campaign

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.orchestration.combat import ChooseDefense, CombatService, generations
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("case", ["old-plain", "new-cr", "new-after-old-attack"])
async def test_original_genesis_composition_and_old_plain_reexecute(
    tmp_path: Path, backend: str, case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cr = case == "new-cr"
    hp = 3 if case == "new-after-old-attack" else 12
    fp = 3 if case == "new-after-old-attack" else 12
    cid, play, initial = await fixture(
        tmp_path, backend, dodge=8 if cr else 10, hp=hp, fp=fp, cr=cr
    )
    active = generations.ACTIVE
    if case != "new-cr":
        monkeypatch.setattr(
            generations, "ACTIVE", active - {"rooted-dodge-health-trait-composition"}
        )
    await prepare(play, cid, seeded=True)
    if case != "old-plain":
        monkeypatch.setattr(generations, "ACTIVE", active)
    play.rng = secrets
    play.seeds = lambda: f"{1:064x}"
    await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="defense",
            actor_id="b",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            defense="dodge",
        ),
        principal_id="b",
    )
    monkeypatch.setattr(generations, "ACTIVE", active)
    saved = await play.store.read(cid)
    state = play._load(saved)
    records = await played(play.store, cid)
    defense = next(r for r in records if r.command_id == "defense")
    assert ("rooted-dodge-health-trait-composition" in (defense.command_input or "")) == (
        case != "old-plain"
    )
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
