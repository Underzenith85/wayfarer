"""Actual GM retirement transactions from restored canonical lost-limb checkpoints."""

import asyncio
import secrets
from pathlib import Path

import pytest
from support.runtime import build_play, played
from test_cyclic_host import current, hp, prepare
from test_gadgeteer_gizmos_persistence import RevokingStore, revoke_gm

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.hazard import blocked_hp
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.location import HumanLocation
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.health.cyclic import save
from wayfarer.engine.simulation.health.cyclic_host_state import (
    AdjudicateCyclicLocationLoss,
    CyclicHostReceipt,
    CyclicPolicy,
    CyclicProcedure,
    bind_occurrence,
    binding,
    history,
)
from wayfarer.engine.simulation.health.cyclic_observations import resolve
from wayfarer.errors import ConflictError, NotFoundError, ValidationError
from wayfarer.orchestration.cyclic import CyclicService
from wayfarer.persistence.replay import verify_commands


def severed(*, location: str = "right-arm", kind: str = "severed") -> InjuryStatus:
    return InjuryStatus.model_validate(
        {
            "profile_id": "gurps-basic-set-4e-2004",
            "anatomy": "human",
            "lasting_injuries": (
                {
                    "id": "prior-limb-loss",
                    "location": location,
                    "kind": kind,
                    "duration": "permanent",
                    "inflicted_at": 0,
                    "injury": 6,
                },
            ),
        }
    )


def adjudicate(occurrence: str) -> AdjudicateCyclicLocationLoss:
    return AdjudicateCyclicLocationLoss(
        id="retire-lost-limb",
        actor_id="b",
        expected_revision=1,
        occurrence_id=occurrence,
        location_id="room",
        campaign_policy="Lost delivery limb ends its continuing local effect",
        reason="The recorded delivery limb is absent from the current canonical body",
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("changed_body", [False, True])
async def test_due_location_retirement_atomic_retry_restart_seed_replay_and_clock(
    tmp_path: Path, backend: str, changed_body: bool
) -> None:
    status = (
        InjuryStatus(profile_id="gurps-basic-set-4e-2004", anatomy="swarm")
        if changed_body
        else severed()
    )
    cid, play, occurrence = await prepare(
        tmp_path,
        backend,
        location="right-hand",  # The loss of an arm also removes its hand.
        target_status=status,
        restored_at=10,  # An already-due invalid location must have a real rescue path.
        policy=CyclicPolicy(condition="wash", procedure=CyclicProcedure(seconds=60)),
    )
    initial = await play.store.read(cid)
    before = play._load(initial)
    original_binding = binding(before.resources, occurrence)
    assert hp(before) == 8 and blocked_hp(before.resources.illnesses, "b", "natural") == 2
    cmd = adjudicate(occurrence)
    play.rng = secrets
    other = build_play(tmp_path, play.engine, backend=backend, rng=secrets)
    results = await asyncio.gather(
        CyclicService(play).execute(cid, cmd, principal_id="gm"),
        CyclicService(other).execute(cid, cmd, principal_id="gm"),
    )
    result = results[0]
    assert result == results[1] and result.kind == "retired" and result.at == 10
    assert result.adjudication is not None and original_binding is not None
    assert result.adjudication.authority == "gm-campaign-adjudication"
    assert result.adjudication.source_id == original_binding.source_id
    assert result.adjudication.source_revision == original_binding.source_revision
    assert result.adjudication.location == "right-hand"
    assert result.adjudication.campaign_policy == cmd.campaign_policy
    assert result.adjudication.injury_ids == (() if changed_body else ("prior-limb-loss",))
    assert result.adjudication.evidence == (
        "incompatible-body" if changed_body else "severed-location"
    )
    after = await current(play, cid)
    assert after.revision == after.resources.revision == 2
    assert binding(after.resources, occurrence) == original_binding
    attack = after.resources.cyclic_attacks[0]
    assert not attack.active and attack.hp_debt == 2 and attack.cycle == 1
    assert blocked_hp(after.resources.illnesses, "b", "natural") == 0
    assert hp(after) == 8 and after.resources.pools == before.resources.pools
    assert not any(e.id.startswith("cyclic-stop:") for e in after.resources.events)
    await play.execute(
        cid, Wait(id="continue", actor_id="b", expected_revision=2, ticks=20), principal_id="b"
    )
    saved = await play.store.read(cid)
    assert play._load(saved).resources.game_time == 30 and hp(play._load(saved)) == 8
    records = await played(play.store, cid)
    assert len(records) == 2 and len(history(play._load(saved).resources)) == 1
    replayed, checks = await verify_commands(
        initial,
        records,
        await play.store.stream(cid),
        configuration_digest=before.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "seed-only"),
    )
    assert all(check.folded and check.reexecuted for check in checks) and replayed == saved
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    assert await restarted.store.read(cid) == await restarted.store.replay(cid) == saved
    assert await CyclicService(restarted).execute(cid, cmd, principal_id="gm") == result
    with pytest.raises(ConflictError):
        await CyclicService(restarted).execute(
            cid, cmd.model_copy(update={"campaign_policy": "different"}), principal_id="gm"
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_retirement_authority_revision_closed_payload_and_competing_cas(
    tmp_path: Path, backend: str
) -> None:
    cid, play, occurrence = await prepare(
        tmp_path, backend, location="right-arm", target_status=severed()
    )
    original = await play.store.read(cid)
    cmd = adjudicate(occurrence)
    service = CyclicService(play)
    for principal in ("alice", "outsider", "untrusted", "unseated"):
        with pytest.raises((ValidationError, NotFoundError)):
            await service.execute(cid, cmd, principal_id=principal)
    for change in (
        {"expected_revision": 0},
        {"occurrence_id": "other"},
        {"actor_id": "c"},
        {"location_id": "elsewhere"},
    ):
        with pytest.raises((ValidationError, ConflictError)):
            await service.execute(cid, cmd.model_copy(update=change), principal_id="gm")
    for field in ("condition", "damage", "location", "anatomy", "source_id"):
        with pytest.raises(ValidationError, match="Invalid Cyclic"):
            await service.execute(cid, {**cmd.model_dump(), field: 10}, principal_id="gm")
    assert await play.store.read(cid) == original
    other = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    results = await asyncio.gather(
        service.execute(cid, cmd, principal_id="gm"),
        CyclicService(other).execute(
            cid, cmd.model_copy(update={"id": "competitor"}), principal_id="gm"
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(r, CyclicHostReceipt) for r in results) == 1
    assert sum(isinstance(r, ConflictError) for r in results) == 1
    assert len(await played(play.store, cid)) == 1


@pytest.mark.parametrize(
    "status",
    [
        InjuryStatus(profile_id="gurps-basic-set-4e-2004", anatomy="human"),
        InjuryStatus(profile_id="gurps-basic-set-4e-2004"),
        severed(location="left-arm"),
        severed(kind="crippled"),
        severed(kind="destroyed"),
    ],
)
async def test_retirement_requires_proven_inapplicable_limb(
    tmp_path: Path, status: InjuryStatus
) -> None:
    cid, play, occurrence = await prepare(tmp_path, location="right-arm", target_status=status)
    original = await play.store.read(cid)
    with pytest.raises(ValidationError, match="anatomy"):
        await CyclicService(play).execute(cid, adjudicate(occurrence), principal_id="gm")
    assert await play.store.read(cid) == original


@pytest.mark.parametrize("location", [None, "torso"])
async def test_retirement_cannot_replace_unbound_or_ordinary_delivery(
    tmp_path: Path, location: HumanLocation | None
) -> None:
    cid, play, occurrence = await prepare(tmp_path, location=location, target_status=severed())
    initial = await play.store.read(cid)
    with pytest.raises(ValidationError, match="bound delivery limb"):
        await CyclicService(play).execute(cid, adjudicate(occurrence), principal_id="gm")
    assert await play.store.read(cid) == initial


async def test_location_retirement_preserves_an_independent_active_recovery_debt(
    tmp_path: Path,
) -> None:
    cid, play, occurrence = await prepare(tmp_path, location="right-arm", target_status=severed())
    state = await current(play, cid)
    other = state.resources.cyclic_attacks[0].model_copy(update={"id": "independent", "hp_debt": 3})
    resources = save(state.resources, other)
    assert blocked_hp(resources.illnesses, "b", "natural") == 5
    after, _ = resolve(
        play.rules_context,
        state.model_copy(update={"resources": resources}),
        adjudicate(occurrence),
        principal_id="gm",
    )
    assert next(a for a in after.resources.cyclic_attacks if a.id == other.id) == other
    assert blocked_hp(after.resources.illnesses, "b", "natural") == 3


async def test_infectious_unpinned_delivery_cannot_use_limb_retirement(tmp_path: Path) -> None:
    cid, play, occurrence = await prepare(tmp_path, contagious=True, target_status=severed())
    initial = await play.store.read(cid)
    with pytest.raises(ValidationError, match="bound delivery limb"):
        await CyclicService(play).execute(cid, adjudicate(occurrence), principal_id="gm")
    assert await play.store.read(cid) == initial


async def test_multiple_due_lost_limb_occurrences_can_retire_independently(tmp_path: Path) -> None:
    cid, play, occurrence = await prepare(
        tmp_path, location="right-arm", target_status=severed(), restored_at=10
    )
    state = await current(play, cid)
    other = state.resources.cyclic_attacks[0].model_copy(update={"id": "other-due"})
    source = binding(state.resources, occurrence)
    assert source is not None
    resources = bind_occurrence(
        save(state.resources, other),
        other.id,
        source_id=source.source_id,
        source_revision=source.source_revision,
        policy=source.policy,
        location="right-arm",
    )
    after, _ = resolve(
        play.rules_context,
        state.model_copy(update={"resources": resources}),
        adjudicate(occurrence),
        principal_id="gm",
    )
    assert next(a for a in after.resources.cyclic_attacks if a.id == other.id) == other
    assert after.resources.game_time == 10 and hp(after) == 8
    assert blocked_hp(after.resources.illnesses, "b", "natural") == 2
    ended, _ = resolve(
        play.rules_context,
        after,
        adjudicate(other.id).model_copy(update={"id": "retire-second", "expected_revision": 2}),
        principal_id="gm",
    )
    assert not any(a.active for a in ended.resources.cyclic_attacks)
    assert blocked_hp(ended.resources.illnesses, "b", "natural") == 0
    assert ended.resources.game_time == 10 and hp(ended) == 8


@pytest.mark.parametrize("retry", [False, True])
async def test_retirement_rechecks_revoked_gm_under_lock(tmp_path: Path, retry: bool) -> None:
    cid, original, occurrence = await prepare(
        tmp_path, location="right-arm", target_status=severed()
    )
    cmd = adjudicate(occurrence)
    if retry:
        await CyclicService(original).execute(cid, cmd, principal_id="gm")
    revision = 2 if retry else 1
    store = RevokingStore(tmp_path / "runtime.sqlite", 10)
    play = build_play(tmp_path, original.engine, store=store, rng=RecordedDice(()))
    store.revoke = lambda: revoke_gm(original, cid, revision)
    if not retry:
        cmd = cmd.model_copy(update={"expected_revision": revision + 1})
    with pytest.raises(ValidationError, match="director authority"):
        await CyclicService(play).execute(cid, cmd, principal_id="gm")
    assert len(history((await current(original, cid)).resources)) == int(retry)
