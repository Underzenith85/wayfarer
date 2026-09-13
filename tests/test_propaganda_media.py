"""Independent B216 Propaganda/TL media-context cases for issue #367."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from test_actions import campaign

from wayfarer.engine.character.compiler import (
    CharacterCompiler,
    CharacterDraft,
    Purchase,
    ValidatedBuild,
)
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.profiles import (
    GURPS_INFINITE_WORLDS_BOUNDARY_PROFILE,
    GURPS_PROPAGANDA_PROFILE,
)
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.campaign.npcs import (
    NPCSocialAction,
    NPCSocialPlan,
    NPCSocialRules,
    NPCSocialTrigger,
)
from wayfarer.engine.simulation.campaign.party import PartyRules
from wayfarer.engine.simulation.campaign.propaganda import (
    PropagandaMedium,
    PropagandaRules,
    bind_media,
)
from wayfarer.engine.simulation.campaign.scenes import Scene, SceneRules
from wayfarer.engine.simulation.campaign.social_policy import SocialActionRules
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Item, Owner, ResourceState
from wayfarer.engine.simulation.social.social import (
    SocialCommand,
    SocialContext,
    apply_social,
)
from wayfarer.engine.world import Entity, EntityKind, World
from wayfarer.errors import ValidationError
from wayfarer.orchestration.npcs import social_occurrence
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore

PROFILE: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"


def media_rules(*, executable: bool = True) -> PropagandaRules:
    return PropagandaRules(
        id="campaign-media",
        version=1,
        profile_id=PROFILE,
        media=(
            PropagandaMedium(
                id="broadcast",
                capability_id="media.broadcast",
                minimum_technology_level=7,
                maximum_technology_level=9,
                audience_limit=500_000,
                attempt_seconds=3_600,
                persistence_seconds=604_800,
                required_definition_ids=("skill:propaganda",),
                required_item_definition_ids=("equipment:transmitter",),
            ),
            PropagandaMedium(
                id="speech",
                capability_id="media.speech",
                minimum_technology_level=0,
                maximum_technology_level=12,
                audience_limit=200,
                attempt_seconds=600,
            ),
        ),
        executable_capability_ids=("media.broadcast",) if executable else ("media.speech",),
    )


def approved_build() -> ValidatedBuild:
    profile = GURPS_PROPAGANDA_PROFILE
    compiler = CharacterCompiler(
        profile.catalog,
        profile.rules,
        profile.policy,
        statistics_profile=PROFILE,
    )
    result = compiler.compile(
        CharacterDraft(
            name="Broadcaster",
            purchases=tuple(
                Purchase(definition_id=f"attribute:{attribute}", amount=10)
                for attribute in ("st", "dx", "iq", "ht")
            )
            + (Purchase(definition_id="skill:propaganda", amount=4, technology_level=8),),
        )
    )
    assert result.build is not None and not result.diagnostics
    return result.build


def world() -> World:
    return World(
        entities=(
            Entity("studio", EntityKind.LOCATION, "Studio"),
            Entity("actor", EntityKind.ACTOR, "Broadcaster", location_id="studio"),
            Entity("audience", EntityKind.ACTOR, "Audience", location_id="studio"),
        )
    )


def command() -> SocialCommand:
    return SocialCommand(
        id="campaign-one",
        actor_id="actor",
        subject_id="audience",
        kind="skill",
        trigger_id="evening-news",
        expected_revision=0,
    )


def test_campaign_pin_binds_reach_attempt_time_and_persistence() -> None:
    context = bind_media(
        media_rules(),
        profile_id=PROFILE,
        campaign_technology_level=8,
        medium_id="broadcast",
        actor_id="actor",
        build=approved_build(),
        resources=ResourceState(
            game_time=1_000,
            items=(Item(id="tx", definition_id="equipment:transmitter", owner_id="actor"),),
        ),
    )
    assert context.model_dump() == {
        "medium_id": "broadcast",
        "technology_level": 8,
        "audience_limit": 500_000,
        "attempt_seconds": 3_600,
        "persistence_seconds": 604_800,
        "ends_at": 609_400,
        "scene_bound": False,
    }


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"rules": None}, "authored campaign media policy"),
        ({"campaign_technology_level": None}, "explicit campaign technology level"),
        ({"campaign_technology_level": 6}, "unavailable at the campaign technology level"),
        ({"medium_id": "invented"}, "Unknown Propaganda medium"),
        ({"rules": media_rules(executable=False)}, "Unsupported Propaganda media capability"),
        ({"resources": ResourceState()}, "requires available equipment"),
    ],
)
def test_media_context_fails_closed_before_a_roll(changes: dict[str, object], message: str) -> None:
    arguments: dict[str, object] = {
        "rules": media_rules(),
        "profile_id": PROFILE,
        "campaign_technology_level": 8,
        "medium_id": "broadcast",
        "actor_id": "actor",
        "build": approved_build(),
        "resources": ResourceState(
            items=(Item(id="tx", definition_id="equipment:transmitter", owner_id="actor"),)
        ),
    }
    arguments.update(changes)
    with pytest.raises(ValidationError, match=message):
        bind_media(**arguments)  # type: ignore[arg-type]


def test_propaganda_receipt_records_only_bounded_media_facts_and_replays() -> None:
    resources = ResourceState(
        game_time=1_000,
        items=(Item(id="tx", definition_id="equipment:transmitter", owner_id="actor"),),
    )
    media = bind_media(
        media_rules(),
        profile_id=PROFILE,
        campaign_technology_level=8,
        medium_id="broadcast",
        actor_id="actor",
        build=approved_build(),
        resources=resources,
    )
    context = SocialContext(
        PROFILE,
        0,
        procedure_id="skill:propaganda",
        skill_level=11,
        conditions=frozenset({"audience-perceptible"}),
        medium_id="broadcast",
        media=media,
    )
    updated, outcome = apply_social(
        resources,
        world(),
        command(),
        context,
        rng=RecordedDice([3, 3, 3]),
        system=True,
    )
    assert outcome.outcome == "propaganda-received"
    assert outcome.media == media
    payload = json.loads(updated.events[-1].kind)
    assert payload["private"]["reference"] == "B216"
    assert payload["private"]["media"] == media.model_dump(mode="json")
    assert json.loads(payload["public"])["media"]["audience_limit"] == 500_000
    replayed, projection = apply_social(
        updated,
        world(),
        command(),
        SocialContext(PROFILE, 0),
        rng=RecordedDice([]),
        system=True,
    )
    assert replayed is updated and projection == outcome


def test_propaganda_requires_media_before_randomness() -> None:
    dice = RecordedDice([3, 3, 3])
    with pytest.raises(ValidationError, match="bound campaign medium"):
        apply_social(
            ResourceState(),
            world(),
            command(),
            SocialContext(
                PROFILE,
                0,
                procedure_id="skill:propaganda",
                skill_level=11,
                conditions=frozenset({"audience-perceptible"}),
            ),
            rng=dice,
            system=True,
        )
    assert not dice.exhausted()


def test_trigger_and_policy_validation_expose_unsupported_authoring() -> None:
    trigger = NPCSocialTrigger(
        kind="skill",
        subject_id="audience",
        skill_id="skill:propaganda",
        conditions=("audience-perceptible",),
        medium_id="broadcast",
    )
    assert trigger.medium_id == "broadcast"
    with pytest.raises(ValueError, match="must select one"):
        NPCSocialTrigger(kind="skill", subject_id="audience", skill_id="skill:propaganda")
    with pytest.raises(ValueError, match="Only Propaganda"):
        NPCSocialTrigger(
            kind="skill",
            subject_id="audience",
            skill_id="skill:public-speaking",
            medium_id="broadcast",
        )
    with pytest.raises(ValueError, match="Unsupported authored Propaganda media capability"):
        NPCSocialRules(
            id="npc-policy",
            version=2,
            plans=(
                NPCSocialPlan(
                    id="press-office",
                    actor_id="actor",
                    goal="reach the audience",
                    first_due=0,
                    interval=600,
                    actions=(NPCSocialAction(id="broadcast", kind="communicate", social=trigger),),
                ),
            ),
            propaganda=media_rules(executable=False),
        )


def test_new_package_pin_preserves_old_profile_and_requires_a_tl_policy() -> None:
    old_ids = {
        definition.id
        for package in GURPS_INFINITE_WORLDS_BOUNDARY_PROFILE.packages
        for definition in package.definitions
    }
    new_ids = {
        definition.id
        for package in GURPS_PROPAGANDA_PROFILE.packages
        for definition in package.definitions
    }
    assert "skill:propaganda" not in old_ids
    assert "skill:propaganda" in new_ids
    assert GURPS_PROPAGANDA_PROFILE.version == 11
    assert GURPS_PROPAGANDA_PROFILE.policy.technology_level == 8
    with pytest.raises(ValidationError, match="explicit campaign technology level"):
        CharacterCompiler(
            GURPS_PROPAGANDA_PROFILE.catalog,
            GURPS_PROPAGANDA_PROFILE.rules,
            replace(GURPS_PROPAGANDA_PROFILE.policy, technology_level=None),
            statistics_profile=PROFILE,
        )


async def test_live_dispatch_derives_policy_tl_and_advances_the_shared_clock(
    tmp_path: Path,
) -> None:
    profile = GURPS_PROPAGANDA_PROFILE
    compiler = CharacterCompiler(
        profile.catalog, profile.rules, profile.policy, statistics_profile=PROFILE
    )
    engine = ActionEngine(
        PowerReviewer(
            compiler,
            PowerPolicy(id="propaganda-review", version=1),
            frozenset({"gm"}),
        ),
        ResourceEngine(world(), profile.catalog, profile.rules, profile.policy, ()),
        SocialActionRules(
            id="propaganda-live",
            version=1,
            npcs=NPCSocialRules(
                id="social-policy",
                version=2,
                plans=(),
                propaganda=media_rules().model_copy(
                    update={
                        "executable_capability_ids": (
                            "media.broadcast",
                            "media.speech",
                        )
                    }
                ),
            ),
            party=PartyRules(id="propaganda-party", version=1),
            scenes=SceneRules(
                id="propaganda-scenes",
                version=1,
                scenes=(Scene(id="studio-scene", version=1, location_id="studio", title="Studio"),),
            ),
        ),
    )
    play = PlayService(
        AsyncSQLiteStore(tmp_path / "propaganda.sqlite", 10),
        engine,
        rng=RecordedDice([3, 3, 3]),
    )
    initial = campaign(engine)
    proposal = CharacterProposal(
        draft=CharacterDraft(
            name="Broadcaster",
            purchases=tuple(
                Purchase(definition_id=f"attribute:{attribute}", amount=10)
                for attribute in ("st", "dx", "iq", "ht")
            )
            + (Purchase(definition_id="skill:propaganda", amount=4, technology_level=8),),
        )
    )
    await play.create(
        initial,
        world(),
        ResourceState(owners=(Owner(actor_id="actor", capacity=100),)),
        (ActorSetup(actor_id="actor", proposal=proposal, aware_of=("audience",)),),
        members=(
            CampaignMember(principal_id="actor", role="player", actor_ids=("actor",)),
            CampaignMember(principal_id="gm", role="gm"),
        ),
    )
    before = play._load(await play.store.read(initial["id"]))
    updated = social_occurrence(
        play,
        before,
        "actor",
        NPCSocialTrigger(
            kind="skill",
            subject_id="audience",
            skill_id="skill:propaganda",
            conditions=("audience-perceptible",),
            medium_id="speech",
        ),
        "town-square",
    )
    assert updated.resources.game_time == 600
    event = next(value for value in updated.resources.events if value.id.startswith("social"))
    projection = json.loads(json.loads(event.kind)["public"])
    assert projection["media"] == {
        "medium_id": "speech",
        "technology_level": 8,
        "audience_limit": 200,
        "attempt_seconds": 600,
        "persistence_seconds": 0,
        "ends_at": None,
        "scene_bound": True,
    }
    receipts = {receipt.command_id for receipt in updated.resources.receipts}
    assert len(receipts) == 2
    social_id = next(value for value in receipts if not value.endswith(":propaganda-time"))
    assert social_id.startswith("social-occurrence:")
    assert receipts == {social_id, f"{social_id}:propaganda-time"}
