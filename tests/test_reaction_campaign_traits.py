"""B41 and B27 approved social sources reach actual campaign consequences."""

import json
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError as SchemaError
from test_reaction_campaign_host import fixture
from test_reaction_task_host import choose
from test_secret_task_host import private_result

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.mundane.runtime import Audience
from wayfarer.engine.simulation.campaign.reaction_context import CampaignReactionInteraction
from wayfarer.errors import ValidationError
from wayfarer.orchestration.reaction_records import AuthoredSocialReaction, PrepareReaction
from wayfarer.orchestration.tasks import TaskService

Mode = Literal["active", "passive", "absent", "proxy"]


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "mode,sapient,perceptible,expected",
    [
        ("active", True, True, 13),
        ("passive", True, True, 12),
        ("absent", True, True, 12),
        ("proxy", True, True, 12),
        ("active", False, True, 12),
        ("active", True, False, 12),
    ],
)
async def test_approved_charisma_applies_only_to_actual_active_sapient_interaction(
    tmp_path: Path,
    backend: str,
    mode: Mode,
    sapient: bool,
    perceptible: bool,
    expected: int,
) -> None:
    cid, play, source = await fixture(
        tmp_path,
        backend,
        "initial-loyalty",
        social_traits=(Purchase(definition_id="trait:charisma", amount=1),),
    )
    interaction = CampaignReactionInteraction(
        actor_id="a",
        mode=mode,
        sapient=sapient,
        audience=Audience(perceptible=perceptible),
        proxy_actor_id="b" if mode == "proxy" else None,
    )
    source = source.model_copy(update={"interaction": interaction})
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((3, 3, 3))
    opened = await TaskService(play).execute(
        cid,
        PrepareReaction(
            id="prepare", actor_id="a", expected_revision=state.revision, source=source
        ),
        principal_id="gm",
    )
    dice = RecordedDice((4, 4, 4))
    play.rng = dice
    await choose(play, cid, opened.pending_id, "resolve")
    assert dice.exhausted()
    state = play._load(await play.store.read(cid))
    assert state.economics.hirelings[0].loyalty == expected
    result = private_result(state)
    trace = json.loads(result.reaction_json or "{}")["reaction"]
    assert trace["total"] == expected
    assert any(modifier["source_id"] == "trait:charisma" for modifier in trace["modifiers"]) is (
        expected == 13
    )
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_approved_reputation_is_recognized_once_and_reused_across_reaction_roles(
    tmp_path: Path, backend: str
) -> None:
    cid, play, source = await fixture(
        tmp_path,
        backend,
        "initial-loyalty",
        social_traits=(
            Purchase(definition_id="trait:reputation-bravery-guild-sometimes", amount=1),
        ),
    )
    source = source.model_copy(
        update={
            "interaction": CampaignReactionInteraction(
                actor_id="a", mode="active", audience=Audience(classes=("guild",))
            )
        }
    )
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((3, 3, 3))
    opened = await TaskService(play).execute(
        cid,
        PrepareReaction(
            id="prepare", actor_id="a", expected_revision=state.revision, source=source
        ),
        principal_id="gm",
    )
    dice = RecordedDice((3, 3, 3, 3, 3, 3, 5, 5, 5, 4, 4, 4))
    play.rng = dice
    await choose(play, cid, opened.pending_id)
    assert dice.exhausted()
    state = play._load(await play.store.read(cid))
    assert state.economics.hirelings[0].loyalty == 17
    # The same actor-NPC reputation recognition is an existing decision now.
    play.rng = RecordedDice(())
    following = AuthoredSocialReaction(
        active_interaction=True,
        sapient=True,
        trigger_id="after-hiring",
        subject_id="b",
        audience=Audience(classes=("guild",)),
    )
    opened = await TaskService(play).execute(
        cid,
        PrepareReaction(
            id="following", actor_id="a", expected_revision=state.revision, source=following
        ),
        principal_id="gm",
    )
    dice = RecordedDice((4, 4, 4))
    play.rng = dice
    await choose(play, cid, opened.pending_id, "resolve", identifier="after-hire")
    assert dice.exhausted()
    result = private_result(play._load(await play.store.read(cid)), "after-hire")
    assert json.loads(result.reaction_json or "{}")["reaction"]["total"] == 14


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_campaign_cannot_borrow_other_actor_interaction_before_search(
    tmp_path: Path, backend: str
) -> None:
    cid, play, source = await fixture(
        tmp_path,
        backend,
        "initial-loyalty",
        social_traits=(Purchase(definition_id="trait:charisma", amount=1),),
    )
    state = play._load(await play.store.read(cid))
    invalid = source.model_copy(
        update={"interaction": CampaignReactionInteraction(actor_id="b", mode="active")}
    )
    play.rng = RecordedDice(())
    before = await play.store.read(cid)
    with pytest.raises((ValidationError, SchemaError), match="recipient"):
        await TaskService(play).execute(
            cid,
            PrepareReaction(
                id="borrow", actor_id="a", expected_revision=state.revision, source=invalid
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("order", [(4, 4, -4), (4, -4, 4)])
async def test_reputation_sum_then_cap_changes_actual_loyalty_and_seed_replays(
    tmp_path: Path, backend: str, order: tuple[int, ...]
) -> None:
    import secrets

    from scripts.replay_fixtures import FixtureExecutor
    from wayfarer.engine.rules.social.social_hooks import Reputation, Standing
    from wayfarer.persistence.replay import verify_commands

    cid, play, source = await fixture(tmp_path, backend, "initial-loyalty")
    standing = Standing(
        reputations=tuple(Reputation(str(index), value) for index, value in enumerate(order))
    )
    source = source.model_copy(
        update={
            "interaction": CampaignReactionInteraction(
                actor_id="a", mode="active", standing=standing
            )
        }
    )
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng = secrets
    play.seeds = lambda: "07" * 32  # First3d is 1,4,4: the independent raw9 oracle.
    opened = await TaskService(play).execute(
        cid,
        PrepareReaction(
            id="prepare",
            actor_id="a",
            expected_revision=play._load(initial).revision,
            source=source,
        ),
        principal_id="gm",
    )
    await choose(play, cid, opened.pending_id, "resolve")
    final = await play.store.read(cid)
    state = play._load(final)
    assert state.economics.hirelings[0].loyalty == 13
    trace = json.loads(private_result(state).reaction_json or "{}")["reaction"]
    assert trace["dice"] == [1, 4, 4] and trace["total"] == 13 and trace["outcome"] == "good"
    records = (await play.store.history(cid))[count:]
    ids = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert len(checks) == 2 and all(check.folded and check.reexecuted for check in checks)
    assert replayed == final == await play.store.replay(cid)
