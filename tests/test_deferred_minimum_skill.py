"""B345 gates deferred checks on exact current inputs before their action dice."""

import json
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play
from test_drowsy_toxins import produce
from test_enchanting_noncombat_nighttime import configured as magic_fixture
from test_enchanting_noncombat_nighttime import start_choice
from test_live_skill_prerequisite_context import background_fixture, reexecute, revision

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.action_engine.engine import ActionEngine, TaskCheckPreparation
from wayfarer.engine.simulation.actions import (
    ActionResult,
    ActorSetup,
    Inspect,
    PlayState,
    Social,
    Wait,
)
from wayfarer.engine.simulation.campaign.adjudication import Ruling
from wayfarer.engine.simulation.campaign.minimum_skill_state import PREFIX, enabled
from wayfarer.engine.simulation.campaign.skill_context_state import enabled as context_enabled
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ValidationError
from wayfarer.orchestration.adjudication import (
    AdjudicationService,
    DecideRuling,
    ExecuteRuling,
    RequestRuling,
)
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.noncombat import NoncombatCommand
from wayfarer.orchestration.party import PartyCommand, PartyService
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.views import campaign_view


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("family", ["inspect", "noncombat"])
@pytest.mark.parametrize(
    "equipment,authored,target",
    [(0, -15, 2), (1, -16, 2), (-1, -14, 2), (0, -14, 3), (1, -15, 3), (-1, -13, 3)],
)
async def test_initial_queue_uses_final_minimum_before_time_costs_or_dice(
    tmp_path: Path,
    backend: str,
    family: Literal["inspect", "noncombat"],
    equipment: int,
    authored: int,
    target: int,
) -> None:
    initial, play, _ = await magic_fixture(
        tmp_path,
        backend,
        enroll=False,
        grouped=True,
        retain_source_spells=True,
        equipment_modifier=equipment,
        check_modifier=authored,
    )
    cid = initial["id"]
    inner: Inspect | NoncombatCommand
    if family == "noncombat":
        inner = await start_choice(play, cid)
    else:
        inner = Inspect(id="look", actor_id="a", expected_revision=0, target_id="forge")
    command = PartyCommand(
        id="queued-minimum",
        actor_id="a",
        expected_revision=await revision(play, cid),
        kind="queue_activity",
        activity_json=inner.model_dump_json(),
    )
    before = await play.store.read(cid)
    dice = RecordedDice(()) if target == 2 else RecordedDice((1, 1, 1))
    play.rng = dice
    if target == 2:
        with pytest.raises(ValidationError, match="effective skill must be at least 3"):
            await PartyService(play).execute(cid, command, principal_id="a")
        assert await play.store.read(cid) == before
        assert await play.store.command_input(cid, command.id) is None
    else:
        result = await PartyService(play).execute(cid, command, principal_id="a")
        assert result.party.receipts[-1].status == "committed" and result.resources.game_time == 10
        trace = (
            result.noncombat[-1].checks[-1]
            if family == "noncombat"
            else result.last_result.check
            if result.last_result
            else None
        )
        assert trace is not None and trace.effective_target == trace.total == 3
        assert "night-clue" in {f.id for f in result.world.perspective("a").facts}
        assert next(p.current for p in result.resources.pools if p.id == "fp:a") == (
            9 if family == "noncombat" else 10
        )
        assert enabled(result.resources, "queued-activity", command.id, "a")
        public = json.dumps(campaign_view(result, member_for(result, "a")))
        assert PREFIX not in public and "minimum_effective_skill_generation" not in public
        restarted = build_play(
            tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
        )
        assert await PartyService(restarted).execute(cid, command, principal_id="a") == result
    assert dice.exhausted()


class AuditedEngine(ActionEngine):
    checked: dict[str, int]

    def prepare_task_check(
        self,
        state: PlayState,
        command: Inspect | Social,
        *,
        correct_symptom_attributes: bool = True,
        fatigue_already_paid: bool = False,
        correct_skill_context: bool = False,
        correct_default_knowledge: bool = False,
        require_default_knowledge: bool = False,
        ruling_id: str | None = None,
    ) -> TaskCheckPreparation:
        prepared = super().prepare_task_check(
            state,
            command,
            correct_symptom_attributes=correct_symptom_attributes,
            fatigue_already_paid=fatigue_already_paid,
            correct_skill_context=correct_skill_context,
            correct_default_knowledge=correct_default_knowledge,
            require_default_knowledge=require_default_knowledge,
            ruling_id=ruling_id,
        )
        if state.resources.game_time == 10:
            self.checked[command.actor_id] = prepared.target + sum(
                m.value for m in prepared.modifiers
            )
        return prepared


class AfterPreflightDice(RecordedDice):
    def __init__(self, engine: AuditedEngine) -> None:
        super().__init__((1, 1, 1))
        self.engine = engine

    def randbelow(self, exclusive_upper_bound: int, /) -> int:
        assert self.engine.checked == {"a": 2, "b": 3}, (
            "All due targets must be checked before the first action die"
        )
        return super().randbelow(exclusive_upper_bound)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("seeded", [False, True])
async def test_valid_first_invalid_second_flush_checks_all_before_dice_and_retains_legal_work(
    tmp_path: Path, backend: str, seeded: bool
) -> None:
    initial, original, _ = await magic_fixture(
        tmp_path,
        backend,
        enroll=False,
        grouped=True,
        retain_source_spells=True,
        equipment_modifier=1,
        check_modifier=-14,
    )
    engine = AuditedEngine(
        original.engine.reviewer, original.engine.resources, original.engine.rules
    )
    engine.checked = {}
    play = build_play(tmp_path / "audited", engine, store=original.store, seeds=lambda: f"{1:064x}")
    cid = initial["id"]
    service = PartyService(play)
    await service.execute(
        cid,
        PartyCommand(
            id="split", actor_id="a", expected_revision=0, kind="split_party", target_id="scouts"
        ),
        principal_id="a",
    )
    await service.execute(
        cid,
        PartyCommand(
            id="queue-a",
            actor_id="a",
            expected_revision=await revision(play, cid),
            kind="queue_activity",
            activity_json=Inspect(
                id="a-look",
                actor_id="a",
                expected_revision=await revision(play, cid),
                target_id="forge",
            ).model_dump_json(),
        ),
        principal_id="a",
    )
    # A was legal at4 when accepted. Real toxin exposure now makes its current
    # Per-based check2. B is still legal at3 and sorts before A in the flush.
    play.seeds = lambda: f"{5:064x}"
    await produce(play, cid)
    play.seeds = lambda: f"{1:064x}"
    engine.checked = {}
    dice = AfterPreflightDice(engine)
    if not seeded:
        play.rng = dice
    command = PartyCommand(
        id="queue-b",
        actor_id="b",
        expected_revision=await revision(play, cid),
        kind="queue_activity",
        activity_json=Inspect(
            id="b-look",
            actor_id="b",
            expected_revision=await revision(play, cid),
            target_id="forge",
        ).model_dump_json(),
    )
    result = await service.execute(cid, command, principal_id="b")
    assert [(r.actor_id, r.status) for r in result.party.receipts] == [
        ("b", "committed"),
        ("a", "rejected"),
    ]
    assert engine.checked == {"a": 2, "b": 3} and result.resources.game_time == 10
    assert "night-clue" not in {f.id for f in result.world.perspective("a").facts}
    assert ("night-clue" in {f.id for f in result.world.perspective("b").facts}) == (not seeded)
    assert all(p.current == 10 for p in result.resources.pools if p.id in ("fp:a", "fp:b"))
    if seeded:
        await reexecute(initial, play, tmp_path / "reexecute")
    else:
        assert dice.exhausted()
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await PartyService(restarted).execute(cid, command, principal_id="b") == result


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("target", [2, 3])
@pytest.mark.parametrize("current", [False, True])
async def test_ruling_floor_is_independent_and_includes_real_condition_and_selected_alternative(
    tmp_path: Path, backend: str, target: int, current: bool
) -> None:
    initial, play, _ = await background_fixture(
        tmp_path, backend, trained=True, deferred=True, authored_modifier=-8 if target == 2 else -7
    )
    cid = initial["id"]
    play.seeds = lambda: f"{5:064x}"
    await produce(play, cid)
    play.seeds = lambda: f"{1:064x}"
    service = AdjudicationService(play)
    current_revision = await revision(play, cid)
    request = RequestRuling(
        id="request",
        actor_id="a",
        expected_revision=current_revision,
        action=Social(
            id="appeal",
            actor_id="a",
            expected_revision=current_revision,
            target_id="b",
            approach="intimidation",
        ),
    )
    if current:
        requested = await service.submit(cid, request, principal_id="a")
    else:
        requested = await submit(
            play, cid, service.plan(request, correct_skill_context=True), principal_id="a"
        )
    assert isinstance(requested, Ruling)
    await service.submit(
        cid,
        DecideRuling(
            id="approve",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            ruling_id=request.id,
            approve=True,
            alternative_id="measured",
            reason="Approve the authored measured appeal",
        ),
        principal_id="gm",
    )
    before = await play.store.read(cid)
    state = play._load(before)
    assert context_enabled(state.resources, "ruling", request.id, "a")
    assert enabled(state.resources, "ruling", request.id, "a") == current
    command = ExecuteRuling(
        id="execute", actor_id="a", expected_revision=state.revision, ruling_id=request.id
    )
    if current and target == 2:
        play.rng = RecordedDice(())
        with pytest.raises(ValidationError, match="effective skill must be at least 3"):
            await service.submit(cid, command, principal_id="a")
        assert await play.store.read(cid) == before
    else:
        result = await service.submit(cid, command, principal_id="a")
        assert isinstance(result, ActionResult) and result.check is not None
        assert result.check.effective_target == target and [
            m.value for m in result.check.modifiers
        ] == [-8 if target == 2 else -7, 2, -2]
        assert play._load(await play.store.read(cid)).resources.game_time == 3
        restarted = build_play(
            tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
        )
        assert await AdjudicationService(restarted).submit(cid, command, principal_id="a") == result
    await reexecute(initial, play, tmp_path / "reexecute")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_positive_ruling_modifier_admits_three_even_when_unmodified_task_would_be_one(
    tmp_path: Path, backend: str
) -> None:
    initial, play, _ = await background_fixture(
        tmp_path, backend, trained=True, deferred=True, authored_modifier=-9
    )
    cid = initial["id"]
    service = AdjudicationService(play)
    at = await revision(play, cid)
    request = RequestRuling(
        id="request",
        actor_id="a",
        expected_revision=at,
        action=Social(
            id="appeal", actor_id="a", expected_revision=at, target_id="b", approach="intimidation"
        ),
    )
    await service.submit(cid, request, principal_id="a")
    await service.submit(
        cid,
        DecideRuling(
            id="approve",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            ruling_id=request.id,
            approve=True,
            alternative_id="measured",
            reason="Approve the selected positive modifier",
        ),
        principal_id="gm",
    )
    play.rng = RecordedDice((1, 1, 1))
    result = await service.submit(
        cid,
        ExecuteRuling(
            id="execute",
            actor_id="a",
            expected_revision=await revision(play, cid),
            ruling_id=request.id,
        ),
        principal_id="a",
    )
    assert isinstance(result, ActionResult) and result.check is not None
    assert (
        result.check.effective_target == result.check.total == 3
        and result.revealed_fact_ids == ("promise",)
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_context_only_old_queue_is_not_upgraded_by_fresh_flushing_command(
    tmp_path: Path, backend: str
) -> None:
    initial, play, _ = await magic_fixture(
        tmp_path, backend, enroll=False, grouped=True, retain_source_spells=True, check_modifier=-15
    )
    cid = initial["id"]
    service = PartyService(play)
    await service.execute(
        cid,
        PartyCommand(
            id="split", actor_id="a", expected_revision=0, kind="split_party", target_id="scouts"
        ),
        principal_id="a",
    )
    queue = PartyCommand(
        id="old-floor-absent",
        actor_id="a",
        expected_revision=await revision(play, cid),
        kind="queue_activity",
        activity_json=Inspect(
            id="look", actor_id="a", expected_revision=await revision(play, cid), target_id="forge"
        ).model_dump_json(),
    )
    accepted = await submit(
        play, cid, service.plan(queue, correct_skill_context=True), principal_id="a"
    )
    assert context_enabled(accepted.resources, "queued-activity", queue.id, "a")
    assert not enabled(accepted.resources, "queued-activity", queue.id, "a")
    command = PartyCommand(
        id="fresh-flusher",
        actor_id="b",
        expected_revision=await revision(play, cid),
        kind="queue_activity",
        activity_json=Wait(
            id="wait", actor_id="b", expected_revision=await revision(play, cid), ticks=10
        ).model_dump_json(),
    )
    result = await service.execute(cid, command, principal_id="b")
    assert next(r for r in result.party.receipts if r.id == queue.id).status == "committed"
    assert result.last_result is not None and result.last_result.check is not None
    assert result.last_result.check.effective_target == 2 and result.last_result.check.total == 9
    assert await service.execute(cid, queue, principal_id="a") == accepted
    await reexecute(initial, play, tmp_path / "reexecute")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("target", [2, 3])
async def test_negative_selected_ruling_modifier_participates_in_final_admission(
    tmp_path: Path, backend: str, target: int
) -> None:
    initial, play, _ = await background_fixture(
        tmp_path,
        backend,
        trained=True,
        deferred=True,
        authored_modifier=-7 if target == 2 else -6,
        ruling_modifier=-1,
    )
    cid = initial["id"]
    service = AdjudicationService(play)
    at = await revision(play, cid)
    request = RequestRuling(
        id="negative-request",
        actor_id="a",
        expected_revision=at,
        action=Social(
            id="appeal", actor_id="a", expected_revision=at, target_id="b", approach="intimidation"
        ),
    )
    await service.submit(cid, request, principal_id="a")
    await service.submit(
        cid,
        DecideRuling(
            id="approve",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            ruling_id=request.id,
            approve=True,
            alternative_id="measured",
            reason="Approve the authored difficult alternative",
        ),
        principal_id="gm",
    )
    before = await play.store.read(cid)
    command = ExecuteRuling(
        id="execute",
        actor_id="a",
        expected_revision=await revision(play, cid),
        ruling_id=request.id,
    )
    dice = RecordedDice(()) if target == 2 else RecordedDice((1, 1, 1))
    play.rng = dice
    if target == 2:
        with pytest.raises(ValidationError, match="effective skill must be at least 3"):
            await service.submit(cid, command, principal_id="a")
        assert await play.store.read(cid) == before
    else:
        result = await service.submit(cid, command, principal_id="a")
        assert isinstance(result, ActionResult) and result.check is not None
        assert result.check.effective_target == result.check.total == 3
        assert [m.value for m in result.check.modifiers] == [-6, -1]
    assert dice.exhausted()


async def test_private_minimum_marker_is_not_authored_genesis_authority(tmp_path: Path) -> None:
    initial, play, genesis = await magic_fixture(
        tmp_path, enroll=False, grouped=True, retain_source_spells=True
    )
    with pytest.raises(ValidationError, match="cannot seed"):
        play.initial_state(
            initial,
            genesis.world,
            ResourceState(
                events=(ResourceEvent(id=PREFIX + "forged", at=0, target_id="a", kind="{}"),)
            ),
            tuple(
                ActorSetup.model_validate(a.model_dump(exclude={"approval"}))
                for a in genesis.actors
            ),
        )
