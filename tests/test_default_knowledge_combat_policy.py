"""B173 actor-scoped policy cannot be replaced by the opponent or later GM adoption."""

from pathlib import Path

import pytest
from support.runtime import build_play, open_store, seed_campaign
from test_default_knowledge_hosts import adopt
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.campaign.default_knowledge import DeclareDefaultKnowledge
from wayfarer.errors import ValidationError
from wayfarer.orchestration import default_knowledge_generations as generations
from wayfarer.orchestration.default_knowledge import DefaultKnowledgeService
from wayfarer.orchestration.play import PlayService


async def fixture(path: Path, backend: str) -> tuple[str, PlayService]:
    cid, play = await setup(path, "gurps-basic-set-4e-2004", human=True, trained=False)
    if backend == "postgres":
        store = open_store(path, backend=backend)
        await seed_campaign(store, await play.store.read(cid))
        play = build_play(path, play.engine, store=store)
    return cid, play


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("known", [False, True])
@pytest.mark.parametrize("version", [0, 1])
async def test_legacy_attacker_policy_cannot_disable_defenders_current_default_eligibility(
    tmp_path: Path, backend: str, known: bool, version: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await fixture(tmp_path, backend)
    await adopt(play, cid, ("skill:broadsword",) if known else (), actor_id="b")
    # The attack genuinely originates under the retained version, not a fresh unknown grant.
    with monkeypatch.context() as origin:
        origin.setattr(generations, "ACTIVE", version > 0)
        origin.setattr(generations, "CURRENT_VERSION", version)
        await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    before = await play.store.read(cid)
    play.rng = RecordedDice((1, 2, 2, 1, 2, 2) if known else ())
    if known:
        result = await defend(cid, play, "b", "parry")
        assert result.injury and result.injury.attack.effective_target == 5
        assert result.injury.defense and result.injury.defense.effective_target == 6
        assert result.injury.injury == 0 and play.rng.exhausted()
    else:
        with pytest.raises(ValidationError, match="unavailable"):
            await defend(cid, play, "b", "parry")
        assert await play.store.read(cid) == before and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("adopted_at_wait", [False, True])
@pytest.mark.parametrize("version", [1, 2])
async def test_wait_pins_actor_adoption_while_unrolled_current_facts_remain_current(
    tmp_path: Path,
    backend: str,
    adopted_at_wait: bool,
    version: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid, play = await fixture(tmp_path, backend)
    await adopt(play, cid, ("skill:broadsword",), actor_id="b", command_id="defender-society")
    monkeypatch.setattr(generations, "CURRENT_VERSION", version)
    if adopted_at_wait:
        await adopt(play, cid, ("skill:broadsword",))
    await turn(
        cid,
        play,
        "a",
        "wait",
        wait_trigger={
            "actor_id": "b",
            "action": "attack",
            "reaction": "attack",
            "item_id": "sword-a",
            "reaction_target_id": "b",
            "mode_id": "swing",
        },
    )
    if adopted_at_wait:
        state = play._load(await play.store.read(cid))
        await DefaultKnowledgeService(play).execute(
            cid,
            DeclareDefaultKnowledge(
                id="withdraw",
                actor_id="gm",
                expected_revision=state.revision,
                performer_id="a",
                skill_ids=("skill:broadsword",),
                society_known=False,
                reason="The GM corrects the actor's society before the original is drawn",
            ),
            principal_id="gm",
        )
    else:
        await adopt(play, cid)
    monkeypatch.setattr(generations, "CURRENT_VERSION", 2)
    paused = await turn(cid, play, "b", "attack", item_id="sword-b", target_id="a", mode_id="swing")
    assert paused.code == "combat.wait_triggered"
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    if adopted_at_wait or version == 2:
        with pytest.raises(ValidationError, match="no trained or legal default"):
            await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
        assert await play.store.read(cid) == before and play.rng.exhausted()
    else:
        await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
        play.rng = RecordedDice((1, 2, 2, 1))
        result = await defend(cid, play, "b", "none")
        assert result.injury and result.injury.attack.effective_target == 5
