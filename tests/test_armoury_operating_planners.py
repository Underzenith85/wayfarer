"""Private planning captures operating-tool admission before assessment entropy."""

import json
import secrets
from pathlib import Path

import pytest
from support.armoury_defaults import revision, wait
from support.runtime import played
from test_armoury_operating_policy import required_kit
from test_armoury_operating_tools import charge, prepared
from test_issue_818_armoury_acceptance import begin

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.equipment.repair_defaults import SelectRepairDefault
from wayfarer.engine.simulation.equipment.repair_parts import AssessRepairParts
from wayfarer.engine.simulation.equipment.repair_time import Method, SelectRepairTime
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.errors import ValidationError
from wayfarer.orchestration import armoury_generations as generations
from wayfarer.orchestration.armoury import ArmouryService
from wayfarer.orchestration.combat import CombatService
from wayfarer.persistence.command_inputs import armoury_intent, canonical, same_input, stamp
from wayfarer.persistence.events import CommandInput, payload_digest
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("assessment", [False, True])
async def test_fresh_planner_refuses_unsupported_operating_kit_before_assessment_entropy(
    tmp_path: Path, backend: str, assessment: bool
) -> None:
    cid, play = await required_kit(tmp_path, backend, "skill:armoury", False)
    before = await play.store.read(cid)
    command = (
        AssessRepairParts(
            id="assess",
            actor_id="b",
            item_id="repair-target",
            expected_revision=await revision(play, cid),
        )
        if assessment
        else SelectRepairTime(
            id="time",
            actor_id="b",
            item_id="repair-target",
            start_command_id="repair",
            method="ordinary",
            expected_revision=await revision(play, cid),
        )
    )
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="explicit supported selection"):
        await ArmouryService(play).execute(cid, command, principal_id="b")
    assert await play.store.read(cid) == before and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_absent_planner_policy_retains_old_assessment_time_bytes_and_seed_replay(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await required_kit(tmp_path, backend, "skill:armoury", False)
    initial = (await play.store.history(cid))[0].state_after
    monkeypatch.setattr(generations, "ACTIVE", False)
    assessment = AssessRepairParts(
        id="old-assess",
        actor_id="b",
        item_id="repair-target",
        expected_revision=await revision(play, cid),
    )
    result = await ArmouryService(play).execute(cid, assessment, principal_id="b")
    assert result.die == 1 and result.quantity == 5
    time = SelectRepairTime(
        id="old-time",
        actor_id="b",
        item_id="repair-target",
        start_command_id="repair",
        method="ordinary",
        expected_revision=await revision(play, cid),
    )
    selected = await ArmouryService(play).execute(cid, time, principal_id="b")
    assert selected.duration_seconds == 1800 and selected.modifier == 0
    for command in (assessment, time):
        recorded = await play.store.command_input(cid, command.id)
        assert recorded and recorded.text and generations.KEY not in recorded.text
    monkeypatch.setattr(generations, "ACTIVE", True)
    assert await ArmouryService(play).execute(cid, assessment, principal_id="b") == result
    assert await ArmouryService(play).execute(cid, time, principal_id="b") == selected
    before = await play.store.read(cid)
    state = play._load(before)
    assert tasks(state.resources) == ()
    assert next(item.quantity for item in state.resources.items if item.id == "parts-b") == 30
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="explicit supported selection"):
        await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    assert await play.store.read(cid) == before and play.rng.exhausted()
    # No actual repair/HP occurred: this is historical planning compatibility.
    play.rng = secrets
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "old-planner-seed-replay"),
    )
    assert checks and all(check.folded and check.reexecuted for check in checks)
    assert replayed == await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("generation", [True, 1.0, "1", 0, 2, None])
def test_recorded_planner_generation_rejects_unknown_or_coerced_numbers(generation: object) -> None:
    base = json.dumps(
        {"operation": "armoury", "generation": 1, "principal_id": "b", "command": {"id": "assess"}},
        sort_keys=True,
    )
    valid = generations.stamp(base)
    assert generations.operating_policy(
        CommandInput(payload_digest({"input": valid}), valid), "assess", "b"
    )
    payload = json.loads(valid)
    payload[generations.KEY] = generation
    text = canonical(payload)
    record = CommandInput(payload_digest({"input": text}), text)
    with pytest.raises(ValidationError, match="operating-tool generation"):
        generations.operating_policy(record, "assess", "b")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("combined", [False, True])
@pytest.mark.parametrize(
    "method,budget,accepted",
    [("haste-50", 900, True), ("haste-50", 899, False), ("extra-2", 1800, False)],
)
async def test_planner_uses_canonical_proposed_duration_before_parts_or_committing(
    tmp_path: Path, backend: str, combined: bool, method: Method, budget: int, accepted: bool
) -> None:
    cid, play = await prepared(
        tmp_path, backend, "armor", charge=budget, duration=budget, default_selected=not combined
    )
    initial = (await play.store.history(cid))[0].state_after
    command = (
        SelectRepairDefault(
            id="combined-time",
            actor_id="b",
            item_id="repair-target",
            start_command_id="repair",
            source_id="skill:armoury-melee-weapons",
            repair_time_method=method,
            expected_revision=await revision(play, cid),
        )
        if combined
        else SelectRepairTime(
            id="proposed-time",
            actor_id="b",
            item_id="repair-target",
            start_command_id="repair",
            method=method,
            expected_revision=await revision(play, cid),
        )
    )
    before = await play.store.read(cid)
    if not accepted:
        play.rng = RecordedDice(())
        with pytest.raises(ValidationError, match="operating time or charge"):
            await ArmouryService(play).execute(cid, command, principal_id="b")
        assert await play.store.read(cid) == before and play.rng.exhausted()
        return
    result = await ArmouryService(play).execute(cid, command, principal_id="b")
    assert result
    assert await ArmouryService(play).execute(cid, command, principal_id="b") == result
    assessed = await ArmouryService(play).execute(
        cid,
        AssessRepairParts(
            id="contextual-parts",
            actor_id="b",
            item_id="repair-target",
            repair_start_command_id="repair",
            expected_revision=await revision(play, cid),
        ),
        principal_id="b",
    )
    assert assessed.die == 1 and assessed.quantity == 5
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    state = play._load(await play.store.read(cid))
    task = tasks(state.resources)[0]
    assert task.parts_die == assessed.die and task.parts_quantity == 5
    assert task.due - task.start == 900 and task.skill == 3
    assert charge(play, await play.store.read(cid)) == 900
    await wait(play, cid, 900)
    finish = begin(await revision(play, cid)).model_copy(
        update={"id": "finish", "stage": "finish", "task_id": "repair"}
    )
    await CombatService(play).execute(cid, finish, principal_id="b")
    state = play._load(await play.store.read(cid))
    done = tasks(state.resources)[0]
    assert done.check and done.check.effective_target == 3 and done.check.total == 5
    assert not done.check.outcome.succeeded and done.restored_hp == 0
    assert charge(play, await play.store.read(cid)) == 0
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "current-planner-seed-replay"),
    )
    assert checks and all(check.folded and check.reexecuted for check in checks)
    assert replayed == await play.store.read(cid)


def test_operating_metadata_retains_exact_original_public_intent() -> None:
    base = json.dumps(
        {
            "operation": "armoury",
            "generation": 1,
            "principal_id": "b",
            "command": {"id": "assess", "item_id": "repair-target"},
        },
        sort_keys=True,
    )
    text = generations.stamp(base)
    record = CommandInput(payload_digest({"input": text}), text)
    assert armoury_intent(text) == base and same_input(record, base)
    assert not same_input(record, base + " ")
    assert not same_input(record, base.replace("repair-target", "different-target"))
    assert not same_input(
        record, json.dumps(json.loads(base), separators=(",", ":"), sort_keys=True)
    )
    payload = json.loads(text)
    payload[generations.ORIGINAL_INPUT] = base.replace("repair-target", "different-target")
    altered = canonical(payload)
    with pytest.raises(ValidationError, match="operating-tool generation"):
        same_input(CommandInput(payload_digest({"input": altered}), altered), base)


@pytest.mark.parametrize("key", [generations.KEY, generations.ORIGINAL_INPUT])
@pytest.mark.parametrize("outer", [False, True])
def test_duplicate_private_operating_keys_refuse_even_with_matching_payload_hash(
    key: str, outer: bool
) -> None:
    base = json.dumps(
        {"operation": "armoury", "generation": 1, "principal_id": "b", "command": {"id": "assess"}},
        sort_keys=True,
    )
    valid = generations.stamp(base)
    if outer:
        valid = stamp(valid, version=3)
    payload = json.loads(valid)
    fragment = json.dumps(key) + ":" + json.dumps(payload[key], separators=(",", ":"))
    duplicated = valid.replace(fragment, fragment + "," + fragment)
    assert duplicated != valid
    record = CommandInput(payload_digest({"input": duplicated}), duplicated)
    with pytest.raises(ValidationError, match="operating-tool generation"):
        generations.operating_policy(record, "assess", "b")
    with pytest.raises(ValidationError, match="operating-tool generation"):
        same_input(record, base)


@pytest.mark.parametrize("base_generation", [0, 1])
def test_absent_operating_metadata_preserves_legacy_raw_base_bytes(base_generation: int) -> None:
    base = json.dumps(
        {
            "operation": "armoury",
            "generation": base_generation,
            "principal_id": "b",
            "command": {"id": "assess"},
        },
        sort_keys=True,
    )
    record = CommandInput(payload_digest({"input": base}), base)
    assert not generations.operating_policy(record, "assess", "b")
    assert same_input(record, base) and not same_input(record, base + " ")
