"""B345 final Diagnosis admission at real source defaults and intoxication states."""

import hashlib
import json
import secrets
from pathlib import Path

import pytest
from support.diagnosis_minimum import command, context, fixture, reexecute, unavailable
from support.runtime import build_runtime
from test_lifesaving_minimum import NoDraw

from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.health.hazards import HazardCommand
from wayfarer.engine.simulation.health.toxins import DrinkCommand
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration import hazard_care_generations as generations
from wayfarer.orchestration.drinking import DrinkingEnvironment, DrinkingService
from wayfarer.orchestration.hazard_care import HazardCare, HazardCareService
from wayfarer.orchestration.hazards import HazardContext, HazardService
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.hazard_care_inputs import KEY


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("iq", [7, 8])
async def test_current_final_one_two_refuse_before_original_attempt_or_event(
    tmp_path: Path, backend: str, iq: int
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, iq=iq)
    before = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    play.rng = dice = NoDraw()
    cmd = await command(play, cid)
    with pytest.raises(ValidationError, match="Diagnosis requires effective skill at least 3"):
        await HazardCareService(play, context).execute(cid, cmd, principal_id="a")
    assert dice.calls == 0 and await play.store.read(cid) == before
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    assert await play.store.command_input(cid, cmd.id) is None
    assert not play._load(before).resources.hazards[0].diagnosed_by


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_final_three_actual_diagnosis_exact_retry_privacy_and_original_seed(
    tmp_path: Path, backend: str
) -> None:
    cid, play, initial = await fixture(tmp_path, backend)
    cmd = await command(play, cid)
    result = await HazardCareService(play, context).execute(cid, cmd, principal_id="a")
    assert (
        result.check
        and result.check.effective_target == result.check.total == 3
        and result.succeeded
    )
    state = play._load(await play.store.read(cid))
    assert state.resources.hazards[0].diagnosed_by == ("a",) and state.resources.game_time == 0
    saved = await play.store.command_input(cid, cmd.id)
    assert saved and saved.text
    payload = replay_payload(saved.text)
    assert isinstance(payload, dict) and payload[KEY] == 2
    public = json.dumps({"hazard-care": cmd.model_dump(mode="json")}, sort_keys=True)
    assert await play.store.duplicate(cid, cmd.id, public) == await play.store.read(cid)
    with pytest.raises(ConflictError, match="different input"):
        await play.store.duplicate(cid, cmd.id, public + " ")
    play.rng = dice = NoDraw()
    assert await HazardCareService(play, unavailable).execute(cid, cmd, principal_id="a") == result
    assert dice.calls == 0
    for actor in ("a", "b"):
        assert KEY not in str(await build_runtime(play).read(cid, principal_id=actor))
    await reexecute(play, cid, initial, tmp_path / "original-seed")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("no_edge", [False, True])
async def test_known_cannot_invent_default_and_unknown_stays_unavailable(
    tmp_path: Path, backend: str, no_edge: bool
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, known=no_edge, no_edge=no_edge)
    before = await play.store.read(cid)
    play.rng = dice = NoDraw()
    with pytest.raises(ValidationError, match="society-known"):
        await HazardCareService(play, context).execute(
            cid, await command(play, cid), principal_id="a"
        )
    assert dice.calls == 0 and await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_auth_and_stale_refuse_before_external_context(tmp_path: Path, backend: str) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    before = await play.store.read(cid)
    play.rng = dice = NoDraw()
    cmd = await command(play, cid)
    service = HazardCareService(play, unavailable)
    with pytest.raises(ValidationError, match="authenticated actor"):
        await service.execute(cid, cmd, principal_id="b")
    with pytest.raises(ConflictError, match="revision"):
        await service.execute(
            cid, cmd.model_copy(update={"expected_revision": 0}), principal_id="a"
        )
    assert dice.calls == 0 and await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("version", [0, 1])
async def test_old_zero_one_keep_actual_two_diagnosis_bytes_and_retry(
    tmp_path: Path, backend: str, version: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, initial = await fixture(tmp_path, backend, iq=8)
    cmd = await command(play, cid)
    service = HazardCareService(play, context)
    monkeypatch.setattr(generations, "DIAGNOSIS_MINIMUM_ACTIVE", False)
    if version:
        result = await service.execute(cid, cmd, principal_id="a")
    else:
        result = await submit(
            play, cid, service.plan(play, cmd, principal_id="a"), principal_id="a"
        )
    assert (
        result.check
        and result.check.effective_target == 2
        and result.check.total == 3
        and result.succeeded
    )
    saved = await play.store.command_input(cid, cmd.id)
    assert saved and saved.text
    selected = replay_payload(saved.text)
    assert isinstance(selected, dict) and (
        selected.get(KEY) == 1 if version else KEY not in selected
    )
    monkeypatch.setattr(generations, "DIAGNOSIS_MINIMUM_ACTIVE", True)
    play.rng = dice = NoDraw()
    assert await HazardCareService(play, unavailable).execute(cid, cmd, principal_id="a") == result
    assert await play.store.command_input(cid, cmd.id) == saved and dice.calls == 0
    assert await play.store.read(cid) == await play.store.replay(cid)
    if version:
        await reexecute(play, cid, initial, tmp_path / "old-one-seed")


async def drinking(play: PlayService, cid: str, identifier: str, kind: str, drinks: int = 0) -> str:
    result = await DrinkingService(play, lambda *_: DrinkingEnvironment()).execute(
        cid,
        DrinkCommand.model_validate(
            {
                "id": identifier,
                "actor_id": "a",
                "expected_revision": (await play.store.read(cid))["revision"],
                "kind": kind,
                "drinks": drinks,
            }
        ),
        principal_id="a",
    )
    return result.level


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("drunk", [False, True])
async def test_actual_paid_diagnosis_tipsy_three_drunk_two_and_rejected_id_conservation(
    tmp_path: Path, backend: str, drunk: bool
) -> None:
    cid, play, initial = await fixture(tmp_path, backend, iq=6, paid=True, known=False)
    play.seeds = lambda: f"{0:064x}"
    assert await drinking(play, cid, "third-drink", "drink", 3) == "tipsy"
    if drunk:
        assert await drinking(play, cid, "fourth-drink", "drink", 1) == "drunk"
    cmd = await command(play, cid)
    service = HazardCareService(play, context)
    if drunk:
        before = await play.store.read(cid)
        play.rng = dice = NoDraw()
        with pytest.raises(ValidationError, match="effective skill at least 3"):
            await service.execute(cid, cmd, principal_id="a")
        assert dice.calls == 0 and await play.store.read(cid) == before
        assert await play.store.command_input(cid, cmd.id) is None
        assert play._load(before).resources.intoxications[0].level == "drunk"
        # Both selected drinking originals remain committed. Actual two-hour
        # stop/sober progression makes the same unconsumed Diagnosis ID lawful.
        play.rng = secrets
        await drinking(play, cid, "stop", "stop-drinking")
        await play.execute(
            cid,
            Wait(
                id="two-hours",
                actor_id="b",
                ticks=7200,
                expected_revision=(await play.store.read(cid))["revision"],
            ),
            principal_id="b",
        )
        play.seeds = lambda: f"{270:064x}"
        assert await drinking(play, cid, "sober", "sober") == "tipsy"
        cmd = cmd.model_copy(update={"expected_revision": (await play.store.read(cid))["revision"]})
    play.seeds = lambda: f"{270:064x}"
    result = await service.execute(cid, cmd, principal_id="a")
    assert (
        result.check
        and result.check.effective_target == result.check.total == 3
        and result.succeeded
    )
    assert [m.value for m in result.check.modifiers] == [-1]
    await reexecute(play, cid, initial, tmp_path / "drinking-originals-seed")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_legal_failed_original_consumes_attempt_and_cannot_be_rerolled(
    tmp_path: Path, backend: str
) -> None:
    cid, play, initial = await fixture(tmp_path, backend)
    play.seeds = lambda: f"{3:064x}"
    cmd = await command(play, cid)
    result = await HazardCareService(play, context).execute(cid, cmd, principal_id="a")
    assert (
        result.check
        and result.check.effective_target == 3
        and result.check.total == 11
        and not result.succeeded
    )
    before = await play.store.read(cid)
    assert not play._load(before).resources.hazards[0].diagnosed_by
    play.rng = dice = NoDraw()
    with pytest.raises(ConflictError, match="already attempted"):
        await HazardCareService(play, context).execute(
            cid, await command(play, cid, "second"), principal_id="a"
        )
    assert dice.calls == 0 and await play.store.read(cid) == before
    await reexecute(play, cid, initial, tmp_path / "failed-original-seed")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_diagnosis_enables_bound_no_roll_antibiotic_once(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, initial = await fixture(tmp_path, backend)
    await HazardCareService(play, context).execute(cid, await command(play, cid), principal_id="a")
    cmd = await command(play, cid, "treat")

    def resolver(*_: object) -> HazardCare:
        return HazardCare("antibiotics", "b", "exposure", item_id="dose", technology_level=8)

    dice = NoDraw()

    def no_draw(_rng: SeededRandom, upper: int) -> int:
        return dice.randbelow(upper)

    monkeypatch.setattr(SeededRandom, "randbelow", no_draw)
    result = await HazardCareService(play, resolver).execute(cid, cmd, principal_id="a")
    assert (
        result.succeeded
        and result.check is None
        and result.treatment_bonus == 3
        and dice.calls == 0
    )
    state = play._load(await play.store.read(cid))
    assert (
        state.resources.items[0].quantity == 1 and state.resources.hazards[0].treatment_bonus == 3
    )
    saved = await play.store.command_input(cid, cmd.id)
    assert saved and saved.text
    payload = replay_payload(saved.text)
    assert isinstance(payload, dict) and payload[KEY] == 1
    assert await HazardCareService(play, unavailable).execute(cid, cmd, principal_id="a") == result
    with pytest.raises(ConflictError, match="already active"):
        await HazardCareService(play, resolver).execute(
            cid, await command(play, cid, "stack"), principal_id="a"
        )
    assert (
        play._load(await play.store.read(cid)).resources.items[0].quantity == 1 and dice.calls == 0
    )
    monkeypatch.undo()
    await reexecute(play, cid, initial, tmp_path / "diagnosis-and-dose-seed")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_rejected_voluntary_diagnosis_never_strands_mandatory_patient_ht_two(
    tmp_path: Path, backend: str
) -> None:
    schedule_id = "exposure:" + hashlib.sha256(json.dumps(["b", "illness"]).encode()).hexdigest()
    cid, play, _ = await fixture(
        tmp_path, backend, iq=8, patient_ht=2, due=0, schedule_id=schedule_id
    )

    def care(*_: object) -> HazardCare:
        return HazardCare("diagnosis", "b", schedule_id, technology_level=8)

    before = await play.store.read(cid)
    play.rng = dice = NoDraw()
    with pytest.raises(ValidationError, match="effective skill at least 3"):
        await HazardCareService(play, care).execute(cid, await command(play, cid), principal_id="a")
    assert dice.calls == 0 and await play.store.read(cid) == before
    play.rng = secrets
    spec = play._load(before).resources.hazards[0].spec
    cmd = HazardCommand(
        id="mandatory-ht",
        actor_id="b",
        expected_revision=before["revision"],
        kind="resolve",
        hazard_id="illness",
    )
    service = HazardService(play, lambda *_: HazardContext(spec))
    result = await service.execute(cid, cmd, principal_id="b")
    assert (
        result.check
        and result.check.effective_target == 2
        and result.check.total == 3
        and not result.active
    )
    assert await service.execute(cid, cmd, principal_id="b") == result
    assert await play.store.read(cid) == await play.store.replay(cid)
