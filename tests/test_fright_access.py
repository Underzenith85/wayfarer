"""Campaign-visible fright requirements and director command dispatch."""

from pathlib import Path

import pytest
from test_social_dispatch import prepare

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState, Wait
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.health.fright import effects, public_id
from wayfarer.engine.simulation.social.social import SocialCommand, SocialContext
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.social import ResolvedInteraction, SocialService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


def resolve(play: PlayService, state: PlayState, command: SocialCommand) -> ResolvedInteraction:
    return ResolvedInteraction(SocialContext("gurps-basic-set-4e-2004", 10, will=10, ht=10))


async def fright(cid: str, play: PlayService, dice: list[int]) -> None:
    play.rng = RecordedDice(dice)
    await SocialService(play, resolve).execute(
        cid,
        SocialCommand(
            id="hidden-plan:occurrence",
            actor_id="a",
            subject_id="a",
            kind="fright",
            trigger_id="hidden-monster",
            expected_revision=0,
        ),
        authenticated_gm_id="gm",
    )


async def test_lasting_choice_visible_after_recovery_and_restart(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    before = play._load(await play.store.read(cid))
    # Failure by 4, table dice 12 -> row 16: one quirk and timed stun.
    await fright(cid, play, [4, 5, 5, 4, 4, 4, 1])
    access = CampaignAccess(play)
    initial = await access.read(cid, principal_id="alice")
    assert isinstance(initial["fright"], tuple)
    requirement = initial["fright"][0]
    assert requirement["choices"] == ({"kind": "quirk", "points": -1},)
    assert requirement["build_approval_required"] is True
    assert requirement["condition"] == "stunned"
    play.rng = RecordedDice([1, 1, 1])
    await play.execute(
        cid,
        Wait(id="recover", actor_id="a", expected_revision=1, ticks=1),
        authenticated_actor_id="a",
    )
    restarted = PlayService(
        AsyncSQLiteStore(tmp_path / "social.sqlite", 10), play.engine, rng=RecordedDice([])
    )
    access = CampaignAccess(restarted)
    current = await access.read(cid, principal_id="alice")
    assert isinstance(current["fright"], tuple)
    assert current["fright"][0]["choices"] == requirement["choices"]
    assert current["fright"][0]["condition"] == "none"
    assert current["fright"][0]["id"] == requirement["id"]
    state = play._load(await play.store.read(cid))
    assert state.actors == before.actors
    assert (
        CampaignAccess._projection(
            state, CampaignMember(principal_id="observer", role="spectator")
        )["fright"]
        == ()
    )
    events = await access.events(cid, principal_id="alice")
    assert events[-1].projection["fright"] == current["fright"]
    for secret in (
        "hidden-plan",
        "hidden-monster",
        "recovery_target",
        "recovery_checks",
        "table_total",
    ):
        assert secret not in str(current) and secret not in str(events)


async def test_campaign_panic_decision_authority_replay_and_player_privacy(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)

    # An authored situation penalty makes the effective target 1.
    def panic_context(
        play: PlayService, state: PlayState, command: SocialCommand
    ) -> ResolvedInteraction:
        return ResolvedInteraction(SocialContext("gurps-basic-set-4e-2004", 1, will=10, ht=10))

    play.rng = RecordedDice([6, 6, 6, 5, 5, 6, 3, 3, 3])
    await SocialService(play, panic_context).execute(
        cid,
        SocialCommand(
            id="hidden-plan",
            actor_id="a",
            subject_id="a",
            kind="fright",
            trigger_id="secret",
            expected_revision=0,
        ),
        authenticated_gm_id="gm",
    )
    access = CampaignAccess(play)
    state = play._load(await play.store.read(cid))
    command: dict[str, object] = {
        "id": "decision",
        "kind": "panic-response",
        "actor_id": "a",
        "expected_revision": 1,
        "fright_id": public_id(effects(state.resources)[0]),
        "response": "A privately adjudicated response",
    }
    saved = await play.store.read(cid)
    with pytest.raises(ValidationError, match="director authority"):
        await access.execute(cid, command, principal_id="alice")
    assert saved == await play.store.read(cid)
    play.rng = RecordedDice([1, 1, 1])
    result = await access.execute(cid, command, principal_id="gm")
    assert result["fright"] == ()
    saved = await play.store.read(cid)
    play.rng = RecordedDice([])
    assert await access.execute(cid, command, principal_id="gm") == result
    assert saved == await play.store.replay(cid)
    with pytest.raises(ConflictError):
        await access.execute(cid, command | {"response": "changed"}, principal_id="gm")
    visible = await access.read(cid, principal_id="alice")
    assert "privately adjudicated" not in str(visible)
    assert "panic_severity" not in str(visible)


async def test_care_through_authenticated_http(tmp_path: Path) -> None:
    import aiohttp
    from aiohttp import web

    from wayfarer.transport.campaign_api import create_campaign_app

    cid, play = await prepare(tmp_path)

    def context(play: PlayService, state: PlayState, command: SocialCommand) -> ResolvedInteraction:
        return ResolvedInteraction(SocialContext("gurps-basic-set-4e-2004", 1, will=10, ht=10))

    play.rng = RecordedDice([6, 6, 6, 4, 4, 5, 1])  # Row 30: one day of catatonia.
    await SocialService(play, context).execute(
        cid,
        SocialCommand(
            id="catatonia",
            actor_id="a",
            subject_id="a",
            kind="fright",
            trigger_id="private",
            expected_revision=0,
        ),
        authenticated_gm_id="gm",
    )
    runner = web.AppRunner(
        create_campaign_app(
            CampaignAccess(play),
            {"gm-token": "gm", "player-token": "alice"},
            legacy_routes=True,
        )
    )
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    base = f"http://127.0.0.1:{runner.addresses[0][1]}/campaigns/{cid}"
    try:
        async with aiohttp.ClientSession() as client:
            gm = {"Authorization": "Bearer gm-token"}
            async with client.get(base, headers=gm) as response:
                data = await response.json()
                assert response.status == 200
                assert data["fright"][0]["decision_kinds"] == ["care"]
            command = {
                "id": "care",
                "actor_id": "a",
                "expected_revision": 1,
                "kind": "care",
                "fright_id": data["fright"][0]["id"],
                "care": True,
            }
            saved = await play.store.read(cid)
            async with client.post(
                base + "/commands", json=command, headers={"Authorization": "Bearer player-token"}
            ) as response:
                assert response.status == 400
            assert saved == await play.store.read(cid)
            for _ in range(2):
                async with client.post(base + "/commands", json=command, headers=gm) as response:
                    result = await response.json()
                    assert response.status == 200, result
                    assert result["revision"] == 2
                    assert result["fright"][0]["care"] is True
            assert (await play.store.read(cid)) == await play.store.replay(cid)
    finally:
        await runner.cleanup()
