"""B173 training is independent of lawful default15 and B248 numeric threshold."""

import json
from pathlib import Path

import pytest
from support.healing_physician import Doctor, Healing, complete, fixture, reexecute, revision, start

from wayfarer.engine.simulation.actions import Inspect
from wayfarer.engine.simulation.magic.backfires import backfires
from wayfarer.engine.simulation.magic.physician_training import PREFIX
from wayfarer.engine.simulation.magic.spell_state import SpellEvent, event_id
from wayfarer.engine.simulation.magic.spells import SpellCommand
from wayfarer.errors import AuthorizationError, ConflictError
from wayfarer.orchestration import spell_generations as generations
from wayfarer.orchestration.spells import SpellService, approved_context
from wayfarer.persistence.command_inputs import same_input
from wayfarer.persistence.healing_physician_inputs import KEY


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("spell", ["minor-healing", "major-healing"])
@pytest.mark.parametrize("doctor", ["default", "point", "trained", "low", "unrelated"])
async def test_actual_critical_cost_and_patient_harm_require_paid_physician_fifteen(
    tmp_path: Path, backend: str, spell: Healing, doctor: Doctor
) -> None:
    cid, play = await fixture(tmp_path, backend, doctor)
    before = play._load(await play.store.read(cid))
    actor = next(a for a in before.actors if a.actor_id == "a")
    build, _ = play.engine.reviewer.activate(
        actor.proposal, actor.approval, campaign_id=cid, actor_id="a"
    )
    values = {v.target: int(v.value) for v in build.sheet.values}
    assert values["skill:physician"] == (14 if doctor == "low" else 15)
    command = await start(play, cid, spell)
    pending = play._load(await play.store.read(cid))
    selected = await play.store.read(cid)
    public = json.dumps(
        {
            "operation": "spell-lifecycle",
            "principal_id": "a",
            "command": command.model_dump(mode="json"),
            "targeting_generation": 1,
            "check_generation": 1,
            "concentration_reaction_generation": 1,
            "item_sight_generation": 1,
            "area_targeting_generation": 1,
            "missile_attack_generation": 1,
        },
        sort_keys=True,
    )
    assert await play.store.duplicate(cid, command.id, public) == selected
    with pytest.raises(ConflictError, match="different input"):
        await play.store.duplicate(cid, command.id, public + " ")
    assert any(e.id.startswith(PREFIX) for e in pending.resources.events)
    eligible = doctor in {"point", "trained"}
    assert approved_context(
        play.rules_context, pending, command.model_copy(update={"kind": "complete"})
    ).physician_skill == (15 if eligible else 14 if doctor == "low" else 0)
    result = await complete(play, cid, command)
    state = play._load(await play.store.read(cid))
    assert result.checks[-1].total == 18 and result.hp_restored == 0
    assert result.outcome == ("failed" if eligible else "critical-failure")
    assert result.energy_spent == (1 if eligible else 2)
    assert len(backfires(state.resources)) == (0 if eligible else 1)
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == (
        9 if eligible else 8
    )
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 1
    assert state.resources.game_time == 1
    record = await play.store.command_input(cid, command.id)
    assert record and record.text and KEY in record.text
    assert same_input(record, public) and not same_input(record, public + " ")
    await reexecute(play, cid, tmp_path / "seed-replay")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_default_physician_fifteen_remains_an_ordinary_legal_roll(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend, "default")
    play.seeds = lambda: f"{11:064x}"
    result = await play.execute(
        cid,
        Inspect(
            id="ordinary", actor_id="a", expected_revision=await revision(play, cid), target_id="b"
        ),
        principal_id="a",
    )
    assert result.check and result.check.effective_target == 15 and result.check.total == 5
    await reexecute(play, cid, tmp_path / "ordinary-default-replay")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_origin_preserves_old_or_source_policy_despite_current_flag_flip(
    tmp_path: Path, backend: str, legacy: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await fixture(tmp_path, backend, "default")
    monkeypatch.setattr(generations, "HEALING_PHYSICIAN_ACTIVE", not legacy)
    command = await start(play, cid, "minor-healing")
    saved = await play.store.command_input(cid, command.id)
    assert saved and saved.text and (KEY in saved.text) is not legacy
    monkeypatch.setattr(generations, "HEALING_PHYSICIAN_ACTIVE", legacy)
    result = await complete(play, cid, command)
    assert result.outcome == ("failed" if legacy else "critical-failure")
    assert result.energy_spent == (1 if legacy else 2)
    await reexecute(play, cid, tmp_path / "origin-replay")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("spell", ["minor-healing", "major-healing"])
@pytest.mark.parametrize("doctor", ["default", "point"])
async def test_final_combat_concentrate_uses_the_same_paid_benefit_origin(
    tmp_path: Path, backend: str, spell: Healing, doctor: Doctor
) -> None:
    cid, play = await fixture(tmp_path, backend, doctor, combat=True)
    command = await start(play, cid, spell)
    state = play._load(await play.store.read(cid))
    result = SpellEvent.model_validate_json(
        next(e.kind for e in state.resources.events if e.id == event_id(command.id))
    ).result
    assert result.checks[-1].total == 18
    assert result.outcome == ("failed" if doctor == "point" else "critical-failure")
    assert result.energy_spent == (1 if doctor == "point" else 2)
    assert len(backfires(state.resources)) == (0 if doctor == "point" else 1)
    assert await SpellService(play).execute(cid, command, principal_id="a")
    await reexecute(play, cid, tmp_path / "combat-seed-replay")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_new_source_producer_cannot_bypass_control_or_stale_revision(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend, "point")
    before = await play.store.read(cid)
    command = SpellCommand(
        id="unauthorized",
        actor_id="a",
        expected_revision=await revision(play, cid),
        kind="start",
        spell_id="minor-healing",
        cast_id="cast",
        channel_id="minor-healing",
        energy=2,
    )
    with pytest.raises(AuthorizationError):
        await SpellService(play).execute(cid, command, principal_id="b")
    with pytest.raises(ConflictError):
        await SpellService(play).execute(
            cid,
            command.model_copy(update={"id": "stale", "expected_revision": 0}),
            principal_id="a",
        )
    assert await play.store.read(cid) == before
