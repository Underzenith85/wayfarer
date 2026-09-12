"""Atomic NPC disclosure and private, restart-safe social dispatch."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_actions import campaign

from wayfarer.engine.character.compiler import CharacterCompiler, CharacterDraft, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.profiles import DEFAULT_REGISTRY
from wayfarer.engine.rules.social.gurps_social import ReactionModifier, influence_roll
from wayfarer.engine.simulation.action_engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup, PlayState
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.campaign.npcs import NPCSocialRules
from wayfarer.engine.simulation.campaign.party import PartyRules
from wayfarer.engine.simulation.campaign.scenes import Scene, SceneRules
from wayfarer.engine.simulation.campaign.social_policy import SocialActionRules
from wayfarer.engine.simulation.resources import Owner, ResourceEngine, ResourceState
from wayfarer.engine.simulation.social.social import (
    SocialCommand,
    SocialContext,
    SocialDisclosure,
    apply_interaction,
    apply_social,
)
from wayfarer.engine.world import Entity, EntityKind, Fact, World
from wayfarer.errors import ConflictError, NotFoundError, ValidationError
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.social import ResolvedInteraction, SocialService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore

PROFILE = "gurps-basic-set-4e-2004"


def world() -> World:
    return World(
        entities=(
            Entity("dock", EntityKind.LOCATION, "Dock"),
            Entity("a", EntityKind.ACTOR, "Player", location_id="dock"),
            Entity("npc", EntityKind.ACTOR, "Guard", location_id="dock"),
            Entity("npc:branch", EntityKind.ACTOR, "Other guard", location_id="dock"),
        ),
        facts=(Fact("disclosure", "dock", "route", "north"), Fact("secret", "npc", "fear", "fire")),
        knowledge=(("npc", "disclosure"), ("npc", "secret")),
    )


def command() -> SocialCommand:
    return SocialCommand(
        id="greeting",
        actor_id="a",
        subject_id="npc",
        kind="reaction",
        trigger_id="meeting",
        expected_revision=0,
    )


def test_replay_ignores_changed_knowledge_and_legacy_colon_ids() -> None:
    value = command().model_copy(update={"trigger_id": "meeting:one"})
    context = SocialContext(PROFILE, 10, required_fact_ids=("secret",))
    state, outcome = apply_social(
        ResourceState(),
        world(),
        value,
        context,
        rng=RecordedDice([5, 5, 5]),
        system=True,
    )
    legacy = state.model_copy(
        update={
            "events": (
                state.events[0].model_copy(update={"id": "social:reaction:npc:meeting:one"}),
            )
        }
    )
    assert apply_social(
        legacy,
        replace(world(), knowledge=()),
        value,
        context,
        rng=RecordedDice([]),
        system=True,
    ) == (legacy, outcome)
    with pytest.raises(ConflictError, match="already resolved"):
        apply_social(
            legacy,
            world(),
            value.model_copy(update={"id": "reroll", "expected_revision": 1}),
            context,
            rng=RecordedDice([]),
            system=True,
        )


def test_trigger_components_cannot_alias_and_nonactors_reject() -> None:
    context = SocialContext(PROFILE, 10)
    first = command().model_copy(update={"trigger_id": "branch:meeting"})
    state, _ = apply_social(
        ResourceState(), world(), first, context, rng=RecordedDice([4] * 3), system=True
    )
    second = command().model_copy(
        update={"id": "second", "subject_id": "npc:branch", "expected_revision": 1}
    )
    updated, _ = apply_social(
        state, world(), second, context, rng=RecordedDice([4] * 3), system=True
    )
    assert len({e.id for e in updated.events}) == 2
    with pytest.raises(ValidationError, match="world actors"):
        apply_social(
            ResourceState(),
            world(),
            command().model_copy(update={"subject_id": "dock"}),
            context,
            rng=RecordedDice([]),
            system=True,
        )


def test_disclosure_knows_only_configured_facts_and_never_repeats() -> None:
    context = SocialContext(PROFILE, 10)
    configured = SocialDisclosure(("disclosure",))
    state, learned, outcome = apply_interaction(
        ResourceState(),
        world(),
        command(),
        context,
        configured,
        rng=RecordedDice([5] * 3),
        system=True,
    )
    assert [f.id for f in learned.perspective("a").facts] == ["disclosure"]
    assert outcome.outcome == "good"
    assert apply_interaction(
        state,
        learned,
        command(),
        context,
        SocialDisclosure(("secret",)),
        rng=RecordedDice([]),
        system=True,
    ) == (state, learned, outcome)
    with pytest.raises(ValidationError, match="unknown facts"):
        apply_interaction(
            ResourceState(),
            world(),
            command(),
            context,
            SocialDisclosure(("invented",)),
            rng=RecordedDice([]),
            system=True,
        )
    _, unchanged, bad = apply_interaction(
        ResourceState(),
        world(),
        command(),
        context,
        configured,
        rng=RecordedDice([1] * 3),
        system=True,
    )
    assert bad.outcome == "very-bad" and unchanged == world()


def test_influence_rejects_duplicate_sources_before_dice() -> None:
    modifier = ReactionModifier("reputation", 1, "same")
    with pytest.raises(ValidationError, match="Duplicate"):
        influence_roll(
            PROFILE, "diplomacy", "a", "npc", 10, 10, (modifier, modifier), rng=RecordedDice([])
        )


async def prepare(path: Path, npcs: NPCSocialRules | None = None) -> tuple[str, PlayService]:
    selected = DEFAULT_REGISTRY.get("profile:gurps-basic-set-4e-2004", 3)
    compiler = CharacterCompiler(
        selected.catalog, selected.rules, selected.policy, statistics_profile=PROFILE
    )
    reviewer = PowerReviewer(compiler, PowerPolicy(id="social-test", version=1), frozenset({"gm"}))
    engine = ActionEngine(
        reviewer,
        ResourceEngine(world(), selected.catalog, selected.rules, selected.policy, ()),
        (SocialActionRules if npcs else ActionRules)(
            id="social-test",
            version=1,
            npcs=npcs,
            party=PartyRules(id="social-party", version=1) if npcs else None,
            scenes=SceneRules(
                id="social-scenes",
                version=1,
                scenes=(Scene(id="dock-scene", version=1, location_id="dock", title="Dock"),),
            )
            if npcs
            else None,
        ),
    )
    play = PlayService(
        AsyncSQLiteStore(path / "social.sqlite", 10), engine, rng=RecordedDice([5] * 3)
    )
    initial = campaign(engine)
    proposal = CharacterProposal(
        draft=CharacterDraft(
            name="Player",
            purchases=tuple(
                Purchase(definition_id="attribute:" + key, amount=10)
                for key in ("st", "dx", "iq", "ht")
            ),
        )
    )
    await play.create(
        initial,
        world(),
        ResourceState(owners=(Owner(actor_id="a", capacity=100),)),
        (ActorSetup(actor_id="a", proposal=proposal, aware_of=("npc",)),),
        members=(
            CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),
            CampaignMember(principal_id="gm", role="gm"),
        ),
    )
    return initial["id"], play


def resolve(play: PlayService, state: PlayState, value: SocialCommand) -> ResolvedInteraction:
    if value.trigger_id != "meeting":
        raise ValidationError("Unknown scenario trigger")
    return ResolvedInteraction(
        SocialContext(
            PROFILE,
            10,
            modifiers=(ReactionModifier("reputation", 0, "secret", True),),
            required_fact_ids=("secret",),
        ),
        SocialDisclosure(("disclosure",)),
    )


async def test_durable_dispatch_replays_without_resolver_and_keeps_secrets_private(
    tmp_path: Path,
) -> None:
    cid, play = await prepare(tmp_path)
    service = SocialService(play, resolve)
    result = await service.execute(cid, command(), authenticated_gm_id="gm")
    saved = await play.store.read(cid)
    assert saved == await play.store.replay(cid)

    def forbidden(play: PlayService, state: PlayState, value: SocialCommand) -> ResolvedInteraction:
        raise AssertionError("Committed retries cannot resolve or draw again")

    restarted = PlayService(
        AsyncSQLiteStore(tmp_path / "social.sqlite", 10), play.engine, rng=RecordedDice([])
    )
    assert (
        await SocialService(restarted, forbidden).execute(cid, command(), authenticated_gm_id="gm")
        == result
    )
    assert await restarted.store.read(cid) == saved
    projection = await CampaignAccess(restarted).read(cid, principal_id="alice")
    assert "secret" not in json.dumps(projection)
    assert "disclosure" in json.dumps(projection)
    events = await CampaignAccess(restarted).events(cid, principal_id="alice")
    assert all("secret" not in e.model_dump_json() for e in events)
    with pytest.raises(ConflictError):
        await service.execute(
            cid, command().model_copy(update={"id": "stale"}), authenticated_gm_id="gm"
        )


async def test_dispatch_authority_player_choice_and_failed_knowledge_are_atomic(
    tmp_path: Path,
) -> None:
    cid, play = await prepare(tmp_path)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="director authority"):
        await SocialService(play, resolve).execute(cid, command(), authenticated_gm_id="alice")
    with pytest.raises(NotFoundError):
        await SocialService(play, resolve).execute(cid, command(), authenticated_gm_id="outsider")
    with pytest.raises(ValidationError, match="player character"):
        await SocialService(play, resolve).execute(
            cid, command().model_copy(update={"subject_id": "a"}), authenticated_gm_id="gm"
        )
    assert await play.store.read(cid) == before


async def test_unknown_disclosure_foreign_profile_and_unavailable_fright_do_not_commit(
    tmp_path: Path,
) -> None:
    cid, play = await prepare(tmp_path)
    before = await play.store.read(cid)

    def unknown(play: PlayService, state: PlayState, value: SocialCommand) -> ResolvedInteraction:
        return ResolvedInteraction(SocialContext(PROFILE, 10), SocialDisclosure(("invented",)))

    def foreign(play: PlayService, state: PlayState, value: SocialCommand) -> ResolvedInteraction:
        return ResolvedInteraction(SocialContext("gurps-lite-4e-2004", 10))

    for resolver, error in ((unknown, "unknown facts"), (foreign, "campaign profile")):
        with pytest.raises(ValidationError, match=error):
            await SocialService(play, resolver).execute(cid, command(), authenticated_gm_id="gm")
    with pytest.raises(ValidationError, match="authoritative HP and FP"):
        await SocialService(play, resolve).execute(
            cid, command().model_copy(update={"kind": "fright"}), authenticated_gm_id="gm"
        )
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == []
