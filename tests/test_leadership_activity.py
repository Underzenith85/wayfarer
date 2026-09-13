"""B204 Leadership bindings to authoritative subgroup activity."""

import pytest

from wayfarer.engine.simulation.campaign.party import (
    LeadershipActivityRule,
    PartyRules,
    PartyState,
    Subgroup,
    bind_leadership_outcome,
)
from wayfarer.errors import ValidationError


def test_followed_npc_group_activity_records_actual_group_size() -> None:
    rules = PartyRules(
        id="party",
        version=1,
        leadership=(
            LeadershipActivityRule(
                id="advance",
                trigger_id="advance-trigger",
                group_id="group:road",
                activity_id="advance-down-road",
                follower_actor_ids=("npc-a", "npc-b"),
            ),
        ),
    )
    party = PartyState(
        groups=(
            Subgroup(
                id="group:road",
                scene_id="road",
                actor_ids=("leader", "npc-a", "npc-b"),
                ready_through=10,
            ),
        )
    )
    updated = bind_leadership_outcome(
        rules,
        party,
        command_id="leadership-roll",
        trigger_id="advance-trigger",
        leader_actor_id="leader",
        subject_id="npc-a",
        outcome="leadership-followed",
        player_actor_ids=frozenset({"leader"}),
    )
    result = updated.leadership[0]
    assert result.status == "followed"
    assert result.follower_actor_ids == ("npc-a", "npc-b")
    assert result.group_size == 3 and result.group_size_modifier == 0
    assert updated.receipts[0].status == "committed"


def test_leadership_never_selects_a_player_followers_action() -> None:
    rules = PartyRules(
        id="party",
        version=1,
        leadership=(
            LeadershipActivityRule(
                id="advance",
                trigger_id="advance-trigger",
                group_id="group:road",
                activity_id="advance-down-road",
                follower_actor_ids=("player-follower",),
            ),
        ),
    )
    party = PartyState(
        groups=(
            Subgroup(
                id="group:road",
                scene_id="road",
                actor_ids=("leader", "player-follower"),
                ready_through=0,
            ),
        )
    )
    with pytest.raises(ValidationError, match="player character"):
        bind_leadership_outcome(
            rules,
            party,
            command_id="leadership-roll",
            trigger_id="advance-trigger",
            leader_actor_id="leader",
            subject_id="player-follower",
            outcome="leadership-followed",
            player_actor_ids=frozenset({"leader", "player-follower"}),
        )
