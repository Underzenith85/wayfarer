"""Secret reaction authority, shared clock, zero-entropy refusals and atomicity."""

import asyncio
import os
import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play
from test_combat_sensory_authority import change
from test_gadgeteer_gizmos_persistence import FailingCommitPlay, RevokingStore
from test_reaction_task_host import Choice, choose, fixture, prepare, source
from test_secret_task_boundaries import RevokingPostgresStore, reapprove, revoke_owner, revoke_seat
from test_secret_task_host import choose_secret
from test_secret_task_host import prepare as prepare_secret

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState, Wait
from wayfarer.engine.simulation.campaign.npcs import NPCReputation, NPCSocialStanding
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.clock import CommandInstant
from wayfarer.orchestration.reaction_records import ChooseReaction, PrepareReaction
from wayfarer.orchestration.task_records import ChooseTaskCheck, snapshot
from wayfarer.orchestration.tasks import TaskService, real_play_clock


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_current_gm_and_owner_authority_and_pending_gate(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    command = PrepareReaction(
        id="prepare", actor_id="a", expected_revision=state.revision, source=source()
    )
    with pytest.raises(ValidationError, match="director"):
        await TaskService(play).execute(cid, command, principal_id="a")
    _, opened = await prepare(play, cid)
    before = await play.store.read(cid)
    for principal in ("gm", "b"):
        with pytest.raises(AuthorizationError):
            await choose(play, cid, opened.pending_id, principal=principal)
    for choice in ("resolve", "cancel"):
        with pytest.raises(ValidationError, match="director"):
            await choose(play, cid, opened.pending_id, choice, principal="a")
    with pytest.raises(ConflictError, match="pending task roll"):
        await play.execute(
            cid,
            Wait(
                id="blocked", actor_id="b", expected_revision=play._load(before).revision, ticks=1
            ),
            principal_id="b",
        )
    with pytest.raises(ConflictError, match="no longer"):
        await TaskService(play).execute(
            cid,
            ChooseTaskCheck(
                id="wrong-kind",
                actor_id="a",
                expected_revision=play._load(before).revision,
                pending_id=opened.pending_id or "",
                kind="accept-check",
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before
    await change(play, cid, revoke_seat)
    with pytest.raises(ValidationError, match="director"):
        await choose(play, cid, opened.pending_id, "cancel")
    play.rng = RecordedDice((5,) * 9)
    chosen, _ = await choose(play, cid, opened.pending_id)
    await change(play, cid, revoke_owner)
    with pytest.raises(AuthorizationError):
        await TaskService(play).execute(cid, chosen, principal_id="a")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("retry", [False, True])
async def test_control_rechecked_under_lock_and_on_exact_retry(
    tmp_path: Path, backend: str, retry: bool
) -> None:
    cid, original = await fixture(tmp_path, backend)
    _, opened = await prepare(original, cid)
    state = original._load(await original.store.read(cid))
    command = ChooseReaction(
        id="declare",
        actor_id="a",
        expected_revision=state.revision,
        pending_id=opened.pending_id or "",
        choice="use-luck",
    )
    if retry:
        original.rng = RecordedDice((5,) * 9)
        await TaskService(original).execute(cid, command, principal_id="a")
    store = (
        RevokingPostgresStore(os.environ["WAYFARER_TEST_DATABASE_URL"], 10)
        if backend == "postgres"
        else RevokingStore(tmp_path / "runtime.sqlite", 10)
    )
    play = build_play(
        tmp_path, original.engine, store=store, rng=RecordedDice(()), instants=original.instants
    )
    store.revoke = lambda: change(original, cid, revoke_owner)
    if not retry:
        command = command.model_copy(update={"expected_revision": state.revision + 1})
    with pytest.raises(AuthorizationError):
        await TaskService(play).execute(cid, command, principal_id="a")
    state = original._load(await original.store.read(cid))
    assert len(snapshot(state).luck.receipts) == int(retry)
    if not retry:
        await choose(original, cid, opened.pending_id, "cancel")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("change_kind", ["approval", "luck", "knowledge", "fact", "subject"])
async def test_changed_current_source_refuses_before_dice_and_allows_cancel(
    tmp_path: Path, backend: str, change_kind: str
) -> None:
    cid, play = await fixture(tmp_path, backend)
    _, opened = await prepare(play, cid)
    if change_kind == "luck":
        await reapprove(play, cid, luck=False)
    else:

        def update(state: PlayState) -> PlayState:
            if change_kind == "approval":
                return state.model_copy(
                    update={
                        "actors": tuple(
                            actor.model_copy(update={"approval": None})
                            if actor.actor_id == "a"
                            else actor
                            for actor in state.actors
                        )
                    }
                )
            world = state.world
            if change_kind == "knowledge":
                world = replace(
                    world,
                    knowledge=tuple(pair for pair in world.knowledge if pair != ("npc", "answer")),
                )
            elif change_kind == "fact":
                world = replace(
                    world,
                    facts=tuple(
                        replace(fact, value="south") if fact.id == "answer" else fact
                        for fact in world.facts
                    ),
                )
            else:
                world = replace(
                    world,
                    entities=tuple(
                        replace(entity, name="Replacement guard") if entity.id == "npc" else entity
                        for entity in world.entities
                    ),
                )
            return state.model_copy(update={"world": world})

        await change(play, cid, update)
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises((ValidationError, ConflictError)):
        await choose(play, cid, opened.pending_id)
    assert await play.store.read(cid) == before
    await choose(play, cid, opened.pending_id, "cancel")
    state = play._load(await play.store.read(cid))
    assert not snapshot(state).luck.receipts and snapshot(state).pending is None
    assert ("a", "answer") not in state.world.knowledge


@pytest.mark.parametrize(
    "modifiers,allowed",
    [
        (("active",), True),
        (("aspected-social",), True),
        (("aspected-job",), False),
        (("defensive",), False),
    ],
)
async def test_variant_refusal_precedes_recognition_and_target_entropy(
    tmp_path: Path, modifiers: tuple[str, ...], allowed: bool
) -> None:
    cid, play = await fixture(tmp_path, modifiers=modifiers)
    authored = source(
        standing=NPCSocialStanding(
            reputations=(NPCReputation(id="rep", level=1, recognition="sometimes"),)
        )
    )
    _, opened = await prepare(play, cid, authored=authored)
    if allowed:
        dice = RecordedDice((3,) * 12)
        play.rng = dice
        await choose(play, cid, opened.pending_id)
        assert dice.exhausted()
    else:
        before = await play.store.read(cid)
        play.rng = RecordedDice(())
        with pytest.raises(ValidationError, match="aspect|Defensive"):
            await choose(play, cid, opened.pending_id)
        assert await play.store.read(cid) == before
        await choose(play, cid, opened.pending_id, "cancel")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("choice", ["use-luck", "resolve"])
async def test_forced_commit_failure_rolls_back_all_reaction_effects(
    tmp_path: Path, backend: str, choice: Choice
) -> None:
    cid, play = await fixture(tmp_path, backend)
    _, opened = await prepare(play, cid)
    before = await play.store.read(cid)
    events, history = await play.store.stream(cid), await play.store.history(cid)
    failing = FailingCommitPlay(
        play.store, play.engine, rng=RecordedDice((5,) * 9), instants=play.instants
    )
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await choose(failing, cid, opened.pending_id, choice)
    assert await play.store.read(cid) == before == await play.store.replay(cid)
    assert await play.store.stream(cid) == events and await play.store.history(cid) == history
    play.rng = RecordedDice((5,) * 9)
    await choose(play, cid, opened.pending_id, choice)
    state = play._load(await play.store.read(cid))
    assert state.world.knowledge.count(("a", "answer")) == 1


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("opponent", ["use-luck", "resolve", "cancel"])
async def test_independent_store_competing_terminals_and_payload_identity(
    tmp_path: Path, backend: str, opponent: Choice
) -> None:
    cid, play = await fixture(tmp_path, backend)
    _, opened = await prepare(play, cid)
    before = play._load(await play.store.read(cid))
    play.rng = secrets
    other = build_play(tmp_path, play.engine, backend=backend, instants=play.instants)
    first = ChooseReaction(
        id="owner",
        actor_id="a",
        expected_revision=before.revision,
        pending_id=opened.pending_id or "",
        choice="use-luck",
    )
    second = first.model_copy(update={"id": "other", "choice": opponent})
    results = await asyncio.gather(
        TaskService(play).execute(cid, first, principal_id="a"),
        TaskService(other).execute(
            cid, second, principal_id="a" if opponent == "use-luck" else "gm"
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ConflictError) for result in results) == 1
    saved = await play.store.read(cid)
    state = play._load(saved)
    assert state.revision == before.revision + 1 and snapshot(state).pending is None
    assert state.world.knowledge.count(("a", "answer")) <= 1
    chosen = first if not isinstance(results[0], Exception) else second
    with pytest.raises(ConflictError):
        await TaskService(play).execute(
            cid,
            chosen.model_copy(update={"pending_id": "changed"}),
            principal_id="a" if chosen.choice == "use-luck" else "gm",
        )
    assert saved == await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("points,seconds", [(15, 3600), (30, 1800), (60, 600)])
@pytest.mark.parametrize("reaction_first", [True, False])
async def test_task_and_reaction_share_microsecond_deadline(
    tmp_path: Path, backend: str, points: int, seconds: int, reaction_first: bool
) -> None:
    cid, play = await fixture(tmp_path, backend, points=points)
    origin = real_play_clock(play._load(await play.store.read(cid))).observed_at_us
    assert origin is not None
    now = [origin + 900001]
    play.instants = lambda: CommandInstant(now[0])
    _, opened = await (prepare(play, cid) if reaction_first else prepare_secret(play, cid))
    play.rng = RecordedDice((5,) * 9)
    if reaction_first:
        await choose(play, cid, opened.pending_id)
    else:
        await choose_secret(play, cid, opened.pending_id)
    deadline = 900001 + seconds * 1_000_000
    assert (
        real_play_clock(play._load(await play.store.read(cid)))
        .cooldowns[0]
        .available_at_microseconds
        == deadline
    )
    play.rng = RecordedDice(())
    _, second = await (
        prepare_secret(play, cid, "second") if reaction_first else prepare(play, cid, "second")
    )
    now[0] = origin + deadline - 1
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="cooling down"):
        if reaction_first:
            await choose_secret(play, cid, second.pending_id, identifier="second-use")
        else:
            await choose(play, cid, second.pending_id, identifier="second-use")
    assert await play.store.read(cid) == before
    now[0] += 1
    play.rng = RecordedDice((5,) * 9)
    if reaction_first:
        await choose_secret(play, cid, second.pending_id, identifier="second-use")
    else:
        await choose(play, cid, second.pending_id, identifier="second-use")
    assert len(snapshot(play._load(await play.store.read(cid))).luck.receipts) == 2


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_historical_retry_redacts_current_gm_demoted_to_owner(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend)
    play.engine.reviewer.gm_ids = frozenset({"gm", "a"})

    def role(state: PlayState, gm: bool) -> PlayState:
        return state.model_copy(
            update={
                "members": tuple(
                    member.model_copy(
                        update={
                            "role": "gm" if gm else "player",
                            "actor_ids": () if gm else ("a",),
                        }
                    )
                    if member.principal_id == "a"
                    else member
                    for member in state.members
                )
            }
        )

    await change(play, cid, lambda state: role(state, True))
    _, opened = await prepare(play, cid)
    play.rng = RecordedDice((5,) * 9)
    command, result = await choose(play, cid, opened.pending_id)
    assert result.reaction_json and result.luck
    await change(play, cid, lambda state: role(state, False))
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    result = await TaskService(play).execute(cid, command, principal_id="a")
    assert result.reaction_json is None and result.luck is None
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_current_gm_trust_loss_blocks_prepare_retry_and_terminal(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend)
    command, opened = await prepare(play, cid)
    before = await play.store.read(cid)
    play.engine.reviewer.gm_ids = frozenset()
    with pytest.raises(ValidationError, match="GM authority"):
        await TaskService(play).execute(cid, command, principal_id="gm")
    with pytest.raises(ValidationError, match="GM authority"):
        await TaskService(play).pending(cid, principal_id="gm")
    for choice in ("resolve", "cancel"):
        with pytest.raises(ValidationError, match="GM authority"):
            await choose(play, cid, opened.pending_id, choice)
    assert await play.store.read(cid) == before
    play.rng = RecordedDice((5,) * 9)
    _, result = await choose(play, cid, opened.pending_id)
    assert result.status == "completed" and result.reaction_json is None


async def test_forged_trait_and_duplicate_purchased_standing_reject_before_dice(
    tmp_path: Path,
) -> None:
    from pydantic import ValidationError as SchemaError

    from wayfarer.engine.character.compiler import Purchase
    from wayfarer.engine.rules.social.gurps_social import ReactionModifier
    from wayfarer.orchestration.reaction_records import AuthoredSocialReaction

    with pytest.raises(SchemaError, match="approved builds"):
        AuthoredSocialReaction(
            active_interaction=True,
            sapient=True,
            trigger_id="forged",
            subject_id="npc",
            modifiers=(ReactionModifier("trait", 100, "caller"),),
        )
    cid, play = await fixture(
        tmp_path, social_traits=(Purchase(definition_id="trait:appearance-attractive", amount=1),)
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="Purchased appearance"):
        await prepare(play, cid, authored=source(standing=NPCSocialStanding(appearance="handsome")))
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_changed_approved_standing_is_rebound_before_recognition(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.character.compiler import Purchase

    cid, play = await fixture(
        tmp_path,
        backend,
        social_traits=(Purchase(definition_id="trait:appearance-attractive", amount=1),),
    )
    _, opened = await prepare(play, cid)

    def remove_appearance(state: PlayState) -> PlayState:
        actor = next(actor for actor in state.actors if actor.actor_id == "a")
        purchases = tuple(
            purchase
            for purchase in actor.proposal.draft.purchases
            if purchase.definition_id != "trait:appearance-attractive"
        )
        proposal = actor.proposal.model_copy(
            update={"draft": actor.proposal.draft.model_copy(update={"purchases": purchases})}
        )
        approval = play.engine.reviewer.approve(
            proposal,
            campaign_id=cid,
            actor_id="a",
            revision=state.revision,
            approver_id="gm",
            reason="Changed approved appearance",
        )
        compiled = play.engine.reviewer.compiler.compile(proposal.draft).build
        assert compiled
        return state.model_copy(
            update={
                "approvals": state.approvals + (approval,),
                "actors": tuple(
                    current.model_copy(update={"proposal": proposal, "approval": approval})
                    if current.actor_id == "a"
                    else current
                    for current in state.actors
                ),
                "resources": state.resources.model_copy(
                    update={
                        "owners": tuple(
                            owner.model_copy(
                                update={
                                    "definitions": tuple(
                                        purchase.definition_id for purchase in compiled.purchases
                                    )
                                }
                            )
                            if owner.actor_id == "a"
                            else owner
                            for owner in state.resources.owners
                        )
                    }
                ),
            }
        )

    await change(play, cid, remove_appearance)
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="context changed"):
        await choose(play, cid, opened.pending_id)
    assert await play.store.read(cid) == before
    await choose(play, cid, opened.pending_id, "cancel")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("pending_attack", [False, True])
async def test_safe_combat_reaction_and_unresolved_attack_boundary(
    tmp_path: Path, backend: str, pending_attack: bool
) -> None:
    from wayfarer.engine.simulation.combat.battlefield import GridPoint
    from wayfarer.engine.simulation.combat.commands import StartEncounter, TakeCombatTurn
    from wayfarer.engine.simulation.combat.spatial import Placement
    from wayfarer.orchestration.combat import CombatService

    cid, play = await fixture(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    assert play.engine.combat is not None
    await CombatService(play).execute(
        cid,
        StartEncounter(
            id="start",
            actor_id="gm",
            expected_revision=state.revision,
            encounter_id="fight",
            battlefield_id=next(iter(play.engine.combat.battlefields)),
            placements=(
                Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
                Placement(actor_id="b", position=GridPoint(x=1, y=0), facing="west"),
            ),
        ),
        principal_id="gm",
    )
    if pending_attack:
        state = play._load(await play.store.read(cid))
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="attack",
                actor_id="a",
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="attack",
                item_id="sword-a",
                mode_id="swing",
                target_id="b",
            ),
            principal_id="a",
        )
        state = play._load(await play.store.read(cid))
        assert state.encounters[0].pending_defense is not None
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    if pending_attack:
        with pytest.raises(ConflictError, match="pending attack"):
            await prepare(play, cid)
        assert await play.store.read(cid) == before
    else:
        _, opened = await prepare(play, cid)
        play.rng = RecordedDice((5,) * 9)
        await choose(play, cid, opened.pending_id)
        after = play._load(await play.store.read(cid))
        assert ("a", "answer") in after.world.knowledge
        assert after.encounters == play._load(before).encounters


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("retry", [False, True])
async def test_current_trust_is_rechecked_after_duplicate_lookup(
    tmp_path: Path, backend: str, retry: bool
) -> None:
    cid, original = await fixture(tmp_path, backend)
    _, opened = await prepare(original, cid)
    state = original._load(await original.store.read(cid))
    command = ChooseReaction(
        id="resolve",
        actor_id="a",
        expected_revision=state.revision,
        pending_id=opened.pending_id or "",
        choice="resolve",
    )
    if retry:
        original.rng = RecordedDice((5, 5, 5))
        await TaskService(original).execute(cid, command, principal_id="gm")
    before = await original.store.read(cid)
    store = (
        RevokingPostgresStore(os.environ["WAYFARER_TEST_DATABASE_URL"], 10)
        if backend == "postgres"
        else RevokingStore(tmp_path / "runtime.sqlite", 10)
    )
    play = build_play(
        tmp_path, original.engine, store=store, rng=RecordedDice(()), instants=original.instants
    )

    async def revoke() -> None:
        play.engine.reviewer.gm_ids = frozenset()

    store.revoke = revoke
    with pytest.raises(ValidationError, match="GM authority"):
        await TaskService(play).execute(cid, command, principal_id="gm")
    assert await original.store.read(cid) == before
