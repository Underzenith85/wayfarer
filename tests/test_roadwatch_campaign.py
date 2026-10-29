"""Independent selected-printing oracles through the shipped HTTP composition.

This does not use test worlds, synthetic SkillSpecs, patched success results or
preseeded wounds. Every campaign starts with authenticated public lobby commands.
Expected values below are entered from Characters B14-17/B170/B195/B208/B211/B222,
B271/B283/B289 and Campaigns B344-345/B419-425. Component coverage is not a claim
that every Basic Set capability or an untested graphical client is supported.
"""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import TypedDict, cast

import pytest
from support.roadwatch_campaign import (
    GM_TOKEN,
    OUTSIDER_TOKEN,
    RoadwatchHTTP,
    actual_guard_wound,
    array_value,
    begin_care,
    campaign_http,
    finish_care,
    object_value,
    recovery_choice,
    start_encounter,
    start_guard_attack,
    travel_both,
    verify_postactivation_replay,
)

from wayfarer.contracts import Campaign
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.criticals.limbs import CriticalLimbResult
from wayfarer.engine.simulation.combat.encounter import CombatResult
from wayfarer.engine.simulation.combat.objects.combat import BreakageResult
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.events import CommandRecord


class ExpectedBuild(TypedDict):
    spent: int
    attributes: dict[str, int]
    skills: dict[str, int]
    hp: int
    fp: int
    speed: str
    move: int
    dodge: int
    lift: str
    load: int


# Independent published costs/progression, not compiled values copied into expected.
EXPECTED_BUILDS: dict[str, ExpectedBuild] = {
    "a": {
        "spent": 100,
        "attributes": {
            "attribute:st": 11,
            "attribute:dx": 12,
            "attribute:iq": 11,
            "attribute:ht": 11,
        },
        "skills": {"skill:broadsword": 14, "skill:stealth": 13, "skill:observation": 13},
        "hp": 11,
        "fp": 11,
        "speed": "5.75",
        "move": 5,
        "dodge": 8,
        "lift": "24",  # B17/B15: ST^2/5 is rounded for Basic Lift above 10.
        "load": 13,
    },
    "b": {
        "spent": 100,
        "attributes": {
            "attribute:st": 10,
            "attribute:dx": 11,
            "attribute:iq": 12,
            "attribute:ht": 11,
        },
        "skills": {
            "skill:first-aid": 13,
            "skill:broadsword": 12,
            "skill:observation": 15,
            "skill:stealth": 14,
        },
        "hp": 10,
        "fp": 11,
        "speed": "5.5",
        "move": 5,
        "dodge": 8,
        "lift": "20",
        "load": 15,
    },
}


def statistic_map(raw: object) -> dict[str, object]:
    return {str(object_value(row)["id"]): object_value(row)["value"] for row in array_value(raw)}


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_public_setup_admits_exact_source_builds_anatomy_and_real_gear(
    tmp_path: Path, backend: str
) -> None:
    async with campaign_http(tmp_path, backend) as host:
        await host.create_and_activate()
        party = await host.view()
        assert party["actors"] == ["a", "b"]
        assert "world" not in party
        state = await host.runtime.checkpoint(host.campaign_id)
        assert {member.principal_id: member.role for member in state.members} == {
            "party": "player",
            "gm": "gm",
        }
        # Read-only inspection of the state produced by public activation is a
        # verification backstop, never a way to construct a favorable campaign.
        for actor_id in ("a", "b"):
            expected = EXPECTED_BUILDS[actor_id]
            character = await host.character(actor_id)
            assert statistic_map(character["attributes"]) == expected["attributes"]
            values = statistic_map(character["skills"])
            for name, level in expected["skills"].items():
                assert values[name] == level
            assert character["hp"] == {"current": expected["hp"], "maximum": expected["hp"]}
            assert character["fp"] == {"current": expected["fp"], "maximum": expected["fp"]}
            actor = next(a for a in state.actors if a.actor_id == actor_id)
            assert actor.body is not None and actor.body.anatomy == "human"
            assert actor.held_item_hands == ((f"sword-{actor_id}", "right-hand"),)
            assert actor.approval is not None
            build = host.runtime.play.engine.reviewer.review(actor.proposal).compilation.build
            assert build is not None and build.statistics is not None
            assert build.spent == expected["spent"]
            assert str(build.statistics.basic_speed) == expected["speed"]
            assert build.statistics.basic_move == expected["move"]
            assert build.statistics.dodge == expected["dodge"]
            assert str(build.statistics.basic_lift) == expected["lift"]
            assert (
                host.runtime.play.engine.resources.carried_weight(state.resources, actor_id)
                == expected["load"] * 1000  # canonical equipment specs use millipounds
            )
        npc = next(a for a in state.actors if a.actor_id == "brigand")
        assert npc.body is not None and npc.body.anatomy == "human"
        assert npc.approval is not None
        assert state.resources.game_time == 0
        assert not any(e.id.startswith("injury:") for e in state.resources.events)
        assert not state.resources.recovery_tasks
        assert await host.store.replay(host.campaign_id) == await host.store.read(host.campaign_id)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_nonmembers_and_unsupported_transports_cannot_mutate_or_draw(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with campaign_http(tmp_path, backend) as host:
        await host.create_and_activate()
        before = await host.store.read(host.campaign_id)
        history = await host.store.history(host.campaign_id)
        draws: list[int] = []
        actual = SeededRandom.randbelow

        def watched(rng: SeededRandom, bound: int, /) -> int:
            draws.append(bound)
            return actual(rng, bound)

        monkeypatch.setattr(SeededRandom, "randbelow", watched)
        await host.request("GET", f"/campaigns/{host.campaign_id}", token="invalid", status=401)
        await host.request(
            "GET", f"/campaigns/{host.campaign_id}", token=OUTSIDER_TOKEN, status=404
        )
        await host.request("GET", f"/roadwatch/campaigns/{host.campaign_id}/gm", status=403)
        await host.request(
            "GET", f"/roadwatch/campaigns/{host.campaign_id}/gm", token=OUTSIDER_TOKEN, status=404
        )
        await host.request(
            "POST",
            f"/campaigns/{host.campaign_id}/commands",
            value={
                "id": "outside-magic",
                "actor_id": "a",
                "expected_revision": 3,
                "kind": "start",
                "spell_id": "light",
                "cast_id": "fake",
            },
            status=400,
        )
        await host.request(
            "POST",
            f"/api/v1/campaigns/{host.campaign_id}/actions",
            value={"command_id": "outside-v1-mutation"},
            status=400,
        )
        await host.request("GET", "/api/v1/live", status=403)
        await host.request(
            "POST",
            f"/campaigns/{host.campaign_id}/commands",
            value={
                "kind": "wait",
                "id": "wrong-owned-actor",
                "actor_id": "brigand",
                "expected_revision": 3,
                "ticks": 1,
            },
            status=400,
        )
        assert draws == []
        assert await host.store.read(host.campaign_id) == before
        assert await host.store.history(host.campaign_id) == history
        encoded = json.dumps(await host.view())
        assert "entropy_seed" not in encoded and "rng_algorithm" not in encoded
        assert "pending_critical_misses" not in encoded
        gm = object_value(
            await host.request("GET", f"/roadwatch/campaigns/{host.campaign_id}/gm", token=GM_TOKEN)
        )
        assert gm["pending_critical_misses"] == []
        assert draws == []


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_public_setup_default_gm_receipt_discovery(tmp_path: Path, backend: str) -> None:
    """A real lobby has pre-activation records; default discovery must handle them."""
    async with campaign_http(tmp_path, backend) as host:
        await host.create_and_activate()
        before = await host.store.read(host.campaign_id)
        history = await host.store.history(host.campaign_id)
        seeds = host.seeds.captures
        projection = object_value(
            await host.request("GET", f"/roadwatch/campaigns/{host.campaign_id}/gm", token=GM_TOKEN)
        )
        assert projection["revision"] == 3 and projection["role"] == "gm"
        assert projection["pending_critical_misses"] == []
        await host.request("GET", f"/roadwatch/campaigns/{host.campaign_id}/gm", status=403)
        for query in ("after=bad", "limit=bad", "after=-1", "limit=101"):
            await host.request(
                "GET",
                f"/roadwatch/campaigns/{host.campaign_id}/gm?{query}",
                token=GM_TOKEN,
                status=400,
            )
        assert host.seeds.captures == seeds
        assert await host.store.read(host.campaign_id) == before
        assert await host.store.history(host.campaign_id) == history


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "corruption", ["non-lobby-before-play", "missing-after-play", "invalid-play"]
)
async def test_gm_history_bridge_rejects_corrupt_checkpoints(
    tmp_path: Path, backend: str, corruption: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read-only history override cannot turn corrupt play into skipped lobby rows."""
    async with campaign_http(tmp_path, backend) as host:
        await host.create_and_activate()
        await host.command("wait", "a", ticks=1)
        before = await host.store.read(host.campaign_id)
        history = await host.store.history(host.campaign_id)
        altered = list(history)
        if corruption == "non-lobby-before-play":
            # Cursor0 intentionally excludes genesisrev0; assignrev1 is visible.
            original = altered[1]
            altered[1] = replace(original, event={**original.event, "action": "typed-action"})
        elif corruption == "missing-after-play":
            original = altered[-1]
            checkpoint = {
                key: value for key, value in original.state_after.items() if key != "play_json"
            }
            checkpoint["setup_json"] = history[1].state_after["setup_json"]
            altered[-1] = replace(
                original,
                command_id="setup:forged-after-activation",
                event={**original.event, "action": "setup"},
                state_after=cast(Campaign, checkpoint),
            )
        else:
            original = altered[-1]
            altered[-1] = replace(original, state_after={**original.state_after, "play_json": "{}"})
        actual_history = type(host.store).history

        async def damaged_history(store: object, cid: str) -> list[CommandRecord]:
            if store is host.store and cid == host.campaign_id:
                return altered
            return await actual_history(store, cid)  # type: ignore[arg-type]

        seeds = host.seeds.captures
        with monkeypatch.context() as patch:
            patch.setattr(type(host.store), "history", damaged_history)
            await host.request(
                "GET", f"/roadwatch/campaigns/{host.campaign_id}/gm", token=GM_TOKEN, status=400
            )
        assert host.seeds.captures == seeds
        assert await host.store.read(host.campaign_id) == before
        assert await host.store.history(host.campaign_id) == history


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_accepted_medical_id_rejects_changed_opaque_intent(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful receipt cannot authorize a different same-ID care intention."""
    async with campaign_http(tmp_path, backend) as host:
        await host.create_and_activate()
        await actual_guard_wound(host)
        view = await host.view()
        choice = next(
            object_value(raw)
            for raw in array_value(view["gurps_recovery_choices"])
            if object_value(raw)["kind"] == "bandage"
            and object_value(raw)["actor_id"] == "b"
            and object_value(raw)["target_actor_id"] == "a"
        )
        original, _ = await host.command(
            "gurps_recovery",
            "b",
            choice_id=choice["id"],
            identifier="accepted-bandage",
        )
        before = await host.store.read(host.campaign_id)
        history = await host.store.history(host.campaign_id)
        draws: list[int] = []
        actual = SeededRandom.randbelow

        def watched(rng: SeededRandom, bound: int, /) -> int:
            draws.append(bound)
            return actual(rng, bound)

        monkeypatch.setattr(SeededRandom, "randbelow", watched)
        # An exact public retry remains lawful after its start choice disappeared.
        await host.request(
            "POST",
            f"/campaigns/{host.campaign_id}/commands",
            value=original,
        )
        assert await host.store.read(host.campaign_id) == before
        changed = {**original, "choice_id": "gurps:rest:b:b"}
        async with host.client.post(
            f"/campaigns/{host.campaign_id}/commands",
            headers={"Authorization": "Bearer roadwatch-party-test-token"},
            json=changed,
        ) as response:
            status, body = response.status, await response.json()
        assert draws == []
        assert await host.store.read(host.campaign_id) == before
        assert await host.store.history(host.campaign_id) == history
        assert status == 409, (status, body)
        await host.request(
            "POST",
            f"/campaigns/{host.campaign_id}/commands",
            value={**original, "expected_revision": 0},
            status=409,
        )
        # Both actors are controlled by this principal. Structural admission is
        # valid, so this reaches original native actor-identity validation.
        await host.request(
            "POST",
            f"/campaigns/{host.campaign_id}/commands",
            value={**original, "actor_id": "a", "choice_id": "gurps:rest:a:a"},
            status=409,
        )
        await host.request(
            "POST",
            f"/campaigns/{host.campaign_id}/commands",
            token=OUTSIDER_TOKEN,
            value=original,
            status=404,
        )
        assert draws == []
        assert await host.store.read(host.campaign_id) == before
        assert await host.store.history(host.campaign_id) == history


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    ("seed", "expected_hp"),
    [(7, 10), (9, 9), (270, 10)],
    ids=["table-total3", "table-total2", "critical-maximum-includes-bandage"],
)
async def test_real_wound_bandage_first_aid_and_shelter_outcome(
    tmp_path: Path, backend: str, seed: int, expected_hp: int
) -> None:
    """B420-424: TL3 table healing includes the1HP already restored by bandaging."""
    async with campaign_http(tmp_path, backend) as host:
        await host.create_and_activate()
        host.seeds.value = 1  # observation9 against Iven15, not a patched result
        await host.command("inspect", "b", target_id="road-view")
        assert "road-warning" in json.dumps(await host.view())
        await actual_guard_wound(host)
        await begin_care(host, "bandage")
        await finish_care(host, 60)
        assert (await host.character("a"))["hp"] == {"current": 8, "maximum": 11}
        await begin_care(host, "first-aid")
        # Seed7: check11/die6=>total3. Seed9: check9/die5=>total2.
        # Critical seed270: check3=>maximum3, without a healing die.
        await finish_care(host, 1800, seed=seed)
        assert (await host.character("a"))["hp"] == {"current": expected_hp, "maximum": 11}
        assert (await host.character("a"))["fp"] == {"current": 11, "maximum": 11}
        await host.command("evaluate_objectives", "a")
        state = await host.runtime.checkpoint(host.campaign_id)
        assert state.objectives.outcome == "success"
        assert all(task.settled for task in state.resources.recovery_tasks)
        assert await host.store.replay(host.campaign_id) == await host.store.read(host.campaign_id)
        if seed == 7:
            await verify_postactivation_replay(host)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    ("seed", "expected_hp", "draw_count"),
    [(6, 8, 3), (270, 10, 3), (540, 6, 3)],
    ids=["ordinary-failure", "critical-success", "critical-failure"],
)
async def test_first_aid_failure_and_critical_results_from_real_wound(
    tmp_path: Path,
    backend: str,
    seed: int,
    expected_hp: int,
    draw_count: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B424: failure0, critical success max3, critical failure loses2; no extra d6."""
    async with campaign_http(tmp_path, backend) as host:
        await host.create_and_activate()
        await actual_guard_wound(host)
        await begin_care(host, "bandage")
        await finish_care(host, 60)
        assert (await host.character("a"))["hp"] == {"current": 8, "maximum": 11}
        started = await begin_care(host, "first-aid")
        draws: list[int] = []
        actual = SeededRandom.randbelow

        def watched(rng: SeededRandom, bound: int, /) -> int:
            draws.append(bound)
            return actual(rng, bound)

        monkeypatch.setattr(SeededRandom, "randbelow", watched)
        await finish_care(host, 1800, seed=seed)
        assert (await host.character("a"))["hp"] == {"current": expected_hp, "maximum": 11}
        assert draws == [6] * draw_count
        choices = array_value((await host.view())["gurps_recovery_choices"])
        assert not any(object_value(raw)["id"] == started["choice_id"] for raw in choices)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_failed_survey_can_withdraw_and_reach_an_authored_ending(
    tmp_path: Path, backend: str
) -> None:
    """An actual check16 fails Observation15 and Stealth14 without forcing victory."""
    async with campaign_http(tmp_path, backend) as host:
        await host.create_and_activate()
        host.seeds.value = 48
        await host.command("inspect", "b", target_id="road-view")
        await host.command(
            "start_noncombat", "b", encounter_id="quiet-survey", selection_id="quiet-path"
        )
        host.seeds.value = 48
        await host.command(
            "approach_noncombat", "b", encounter_id="quiet-survey", selection_id="move-quietly"
        )
        state = await host.runtime.checkpoint(host.campaign_id)
        assert ("b", "road-warning") not in state.world.knowledge
        assert ("b", "quiet-approach") not in state.world.knowledge
        assert ("b", "noisy-approach") in state.world.knowledge
        assert state.objectives.outcome == "ongoing"
        await travel_both(host, "post-to-road")
        # Returning both PCs to shelter already wins automatically. Exercise a
        # genuine abandonment before completing that authored return goal.
        before = await host.store.read(host.campaign_id)
        history = await host.store.history(host.campaign_id)
        captures = host.seeds.captures
        await host.command("abandon_scenario", "a", status=400)
        assert host.seeds.captures == captures
        assert await host.store.read(host.campaign_id) == before
        assert await host.store.history(host.campaign_id) == history
        await host.command("abandon_scenario", "gm", token=GM_TOKEN)
        state = await host.runtime.checkpoint(host.campaign_id)
        assert state.objectives.outcome == "abandoned"
        for actor, hp in (("a", 11), ("b", 10)):
            assert (await host.character(actor))["hp"] == {"current": hp, "maximum": hp}
            assert (await host.character(actor))["fp"] == {"current": 11, "maximum": 11}


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_natural_critical_arm_strain_and_gm_receipts(tmp_path: Path, backend: str) -> None:
    """B556 row15 naturally resolves the known human wielding arm for30minutes."""
    async with campaign_http(tmp_path, backend) as host:
        await host.create_and_activate()
        await travel_both(host, "post-to-road")
        await start_encounter(host)
        await host.command(
            "take_combat_turn",
            "a",
            encounter_id="roadwatch-fight",
            maneuver="move",
            hex_path=[{"q": 2, "r": 0}],
            hex_facing=0,
        )
        await host.command(
            "take_combat_turn", "b", encounter_id="roadwatch-fight", maneuver="do_nothing"
        )
        await host.command(
            "take_combat_turn",
            "brigand",
            token=GM_TOKEN,
            encounter_id="roadwatch-fight",
            maneuver="attack",
            item_id="sword-brigand",
            mode_id="swing",
            target_id="a",
            hit_location="torso",
        )
        # Actual seed1275: attack6+6+6=18, table4+5+6=15.
        host.seeds.value = 1275
        await host.command("choose_defense", "a", encounter_id="roadwatch-fight", defense="none")
        gm = object_value(
            await host.request("GET", f"/roadwatch/campaigns/{host.campaign_id}/gm", token=GM_TOKEN)
        )
        assert gm["pending_critical_misses"] == []  # lawful native resolution, no forced migration
        assert array_value(gm["events"])
        await host.request("GET", f"/roadwatch/campaigns/{host.campaign_id}/gm", status=403)
        state = await host.runtime.checkpoint(host.campaign_id)
        encounter = next(enc for enc in state.encounters if enc.id == "roadwatch-fight")
        assert encounter.blocked_reason is None
        injury = next(pool.injury for pool in state.resources.pools if pool.id == "hp:brigand")
        assert injury is not None
        arm = next(effect for effect in injury.lasting_injuries if effect.location == "right-arm")
        assert arm.kind == "disabled" and arm.duration == "timed"
        assert arm.recovery_at == arm.inflicted_at + 1800
        assert (await host.character("a"))["hp"] == {"current": 11, "maximum": 11}
        await host.command(
            "end_encounter",
            "gm",
            token=GM_TOKEN,
            encounter_id="roadwatch-fight",
            reason="The brigand's sword arm is strained; both surveyors withdraw.",
        )
        await travel_both(host, "road-to-shelter")
        await host.command("evaluate_objectives", "a")
        assert (await host.runtime.checkpoint(host.campaign_id)).objectives.outcome == "success"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    ("moving_actor", "elapsed"),
    [("a", 1795), ("b", 900)],
    ids=["patient-crosses-due", "caregiver"],
)
async def test_scene_travel_cannot_preserve_uninterrupted_shock_treatment(
    tmp_path: Path, backend: str, moving_actor: str, elapsed: int
) -> None:
    """B424 requires patient stillness and continued caregiver care for30minutes."""
    async with campaign_http(tmp_path, backend) as host:
        await host.create_and_activate()
        await actual_guard_wound(host)
        await begin_care(host, "bandage")
        await finish_care(host, 60)
        await begin_care(host, "first-aid")
        # The patient's10sec departure crosses the due time. Interruption must
        # happen before that clock advance, not grant care completed in transit.
        await host.command("wait", "a", ticks=elapsed)
        before = await host.store.read(host.campaign_id)
        captures = host.seeds.captures
        async with host.client.post(
            f"/campaigns/{host.campaign_id}/commands",
            headers={"Authorization": "Bearer roadwatch-party-test-token"},
            json={
                "kind": "travel_scene",
                "id": "movement-during-care",
                "actor_id": moving_actor,
                "expected_revision": await host.revision(),
                "exit_id": "shelter-to-road",
            },
        ) as response:
            status, body = response.status, await response.json()
        assert status in {200, 400, 409}, (status, body)
        assert (await host.character("a"))["hp"] == {"current": 8, "maximum": 11}
        if status != 200:
            assert host.seeds.captures == captures
            assert await host.store.read(host.campaign_id) == before
            return
        tasks = array_value((await host.view())["gurps_recovery_tasks"])
        task = next(object_value(row) for row in tasks if object_value(row)["kind"] == "first-aid")
        assert task["status"] == "interrupted", task
        choice = await recovery_choice(host, "finish-recovery")
        await host.command("gurps_recovery", "b", choice_id=choice["id"])
        assert (await host.character("a"))["hp"] == {"current": 8, "maximum": 11}


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_fresh_application_reload_preserves_real_wound_and_retry(
    tmp_path: Path, backend: str
) -> None:
    async with campaign_http(tmp_path, backend) as host:
        await host.create_and_activate()
        await actual_guard_wound(host)
        accepted, _ = await host.command("wait", "a", ticks=1, identifier="before-reload")
        cid = host.campaign_id
        character = await host.character("a")
        before = await host.store.read(cid)
        history = await host.store.history(cid)
    # New app, runtime, sessions, store handle and auth client; no campaign reseed.
    async with campaign_http(tmp_path, backend) as reopened:
        reopened.campaign_id = cid
        assert await reopened.character("a") == character
        assert character["hp"] == {"current": 7, "maximum": 11}
        assert await reopened.store.read(cid) == before
        await reopened.request("POST", f"/campaigns/{cid}/commands", value=accepted)
        assert reopened.seeds.captures == 0
        assert await reopened.store.read(cid) == before
        assert await reopened.store.history(cid) == history
        state = await reopened.runtime.checkpoint(cid)
        assert next(actor for actor in state.actors if actor.actor_id == "a").body is not None
        assert await reopened.store.replay(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_independent_apps_compare_and_swap_and_same_id_once(
    tmp_path: Path, backend: str
) -> None:
    async with campaign_http(tmp_path, backend) as first:
        await first.create_and_activate()
        async with campaign_http(tmp_path, backend) as second:
            second.campaign_id = first.campaign_id
            revision = await first.revision()

            async def send(host: RoadwatchHTTP, identifier: str, expected: int) -> int:
                async with host.client.post(
                    f"/campaigns/{first.campaign_id}/commands",
                    headers={"Authorization": "Bearer roadwatch-party-test-token"},
                    json={
                        "kind": "wait",
                        "id": identifier,
                        "actor_id": "a",
                        "expected_revision": expected,
                        "ticks": 1,
                    },
                ) as response:
                    await response.read()
                    return response.status

            statuses = await asyncio.gather(
                send(first, "race-left", revision), send(second, "race-right", revision)
            )
            assert sorted(statuses) == [200, 409]
            state = await first.runtime.checkpoint(first.campaign_id)
            assert state.revision == revision + 1 and state.resources.game_time == 1
            statuses = await asyncio.gather(
                send(first, "race-same", state.revision), send(second, "race-same", state.revision)
            )
            assert tuple(statuses) == (200, 200)
            final = await first.runtime.checkpoint(first.campaign_id)
            assert final.revision == revision + 2 and final.resources.game_time == 2
            history = await first.store.history(first.campaign_id)
            assert sum(row.command_id == "race-same" for row in history) == 1
            assert sum(row.command_id in {"race-left", "race-right"} for row in history) == 1
            assert await first.store.replay(first.campaign_id) == await first.store.read(
                first.campaign_id
            )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_failed_commit_rolls_back_real_injury_then_lawful_retry(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with campaign_http(tmp_path, backend) as host:
        await host.create_and_activate()
        await start_guard_attack(host)
        before = await host.store.read(host.campaign_id)
        history = await host.store.history(host.campaign_id)
        command = {
            "kind": "choose_defense",
            "id": "rolled-back-wound",
            "actor_id": "a",
            "expected_revision": await host.revision(),
            "encounter_id": "roadwatch-fight",
            "defense": "none",
        }
        commit = PlayService.commit
        reduced_hp: list[int] = []

        def fail_after_candidate(play: PlayService, campaign: Campaign, state: PlayState) -> None:
            commit(play, campaign, state)
            reduced_hp.append(
                next(pool.current for pool in state.resources.pools if pool.id == "hp:a")
            )
            raise RuntimeError("injected failure after genuine candidate injury")

        host.seeds.value = 1
        with monkeypatch.context() as patch:
            patch.setattr(PlayService, "commit", fail_after_candidate)
            await host.request(
                "POST", f"/campaigns/{host.campaign_id}/commands", value=command, status=500
            )
        assert reduced_hp == [7]
        assert await host.store.read(host.campaign_id) == before
        assert await host.store.history(host.campaign_id) == history
        assert (await host.character("a"))["hp"] == {"current": 11, "maximum": 11}
        await host.request("POST", f"/campaigns/{host.campaign_id}/commands", value=command)
        assert (await host.character("a"))["hp"] == {"current": 7, "maximum": 11}
        assert (
            sum(
                row.command_id == command["id"]
                for row in await host.store.history(host.campaign_id)
            )
            == 1
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    ("defense", "retreat", "target", "hp"),
    [("dodge", False, 8, 7), ("parry", False, 10, 11), ("dodge", True, 11, 11)],
    ids=["dodge8-fails9", "parry10-stops9", "retreat-dodge11-stops9"],
)
async def test_source_active_defenses_use_real_rolls_and_source_bonuses(
    tmp_path: Path, backend: str, defense: str, retreat: bool, target: int, hp: int
) -> None:
    """B374-377: Dodge8, skill14 sword Parry10, retreating Dodge+3."""
    async with campaign_http(tmp_path, backend) as host:
        await host.create_and_activate()
        await start_guard_attack(host)
        host.seeds.value = 1  # attack1+5+3=9, defense4+4+1=9
        await host.tactical("a")
        if defense == "parry":
            # Missing the actual parry mode would leave future critical limb
            # consequences ambiguous. Both public adapters must refuse it now.
            before = await host.store.read(host.campaign_id)
            history = await host.store.history(host.campaign_id)
            captures = host.seeds.captures
            ambiguous = {
                "id": "missing-parry-mode",
                "kind": "choose_defense",
                "actor_id": "a",
                "expected_revision": await host.revision(),
                "encounter_id": "roadwatch-fight",
                "defense": "parry",
                "item_id": "sword-a",
            }
            await host.request(
                "POST", f"/campaigns/{host.campaign_id}/commands", value=ambiguous, status=400
            )
            await host.request(
                "POST",
                f"/api/tactical/v2/campaigns/{host.campaign_id}/commands",
                value={"command": ambiguous},
                status=400,
            )
            assert host.seeds.captures == captures
            assert await host.store.read(host.campaign_id) == before
            assert await host.store.history(host.campaign_id) == history
        accepted = {
            "id": "source-defense-v2",
            "kind": "choose_defense",
            "actor_id": "a",
            "expected_revision": await host.revision(),
            "encounter_id": "roadwatch-fight",
            "defense": defense,
            "item_id": "sword-a" if defense == "parry" else None,
            "parry_mode_id": "swing" if defense == "parry" else None,
            "retreat": {"q": 1, "r": 0} if retreat else None,
        }
        await host.request(
            "POST",
            f"/api/tactical/v2/campaigns/{host.campaign_id}/commands",
            value={"command": accepted},
        )
        assert (await host.character("a"))["hp"] == {"current": hp, "maximum": 11}
        record = next(
            row
            for row in await host.store.history(host.campaign_id)
            if row.command_id == accepted["id"]
        )
        result = CombatResult.model_validate_json(record.event["outcome"])
        assert result.injury is not None and result.injury.defense is not None
        assert result.injury.defense.effective_target == target
        assert result.injury.defense.dice == (4, 4, 1)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("partial", [False, True], ids=["full-eleventh", "partial-eleventh"])
async def test_real_long_battle_defense_cost_then_positive_fp_rest(
    tmp_path: Path, backend: str, partial: bool
) -> None:
    """B426-427: >10actual combat seconds + rolled defense costs1FP, rest600 restores1."""
    async with campaign_http(tmp_path, backend) as host:
        await host.create_and_activate()
        host.seeds.value = 1
        await host.command("inspect", "b", target_id="road-view")
        await start_guard_attack(host)
        host.seeds.value = 1
        await host.command("choose_defense", "a", encounter_id="roadwatch-fight", defense="dodge")
        assert (await host.character("a"))["hp"] == {"current": 7, "maximum": 11}
        # First real attack/defense completed cycle1. Continue genuine turns to
        # eleven completed cycles, or ten cycles plus actorA's eleventh turn.
        for _cycle in range(9 if partial else 10):
            for actor in ("a", "b", "brigand"):
                await host.command(
                    "take_combat_turn",
                    actor,
                    token=GM_TOKEN if actor == "brigand" else "roadwatch-party-test-token",
                    encounter_id="roadwatch-fight",
                    maneuver="do_nothing",
                )
        assert (await host.character("a"))["fp"] == {"current": 11, "maximum": 11}
        if partial:
            await host.command(
                "take_combat_turn", "a", encounter_id="roadwatch-fight", maneuver="do_nothing"
            )
        assert (await host.character("a"))["fp"] == {"current": 11, "maximum": 11}
        await host.command(
            "end_encounter",
            "gm",
            token=GM_TOKEN,
            encounter_id="roadwatch-fight",
            reason="Both sides break off after the real eleventh combat second.",
        )
        assert (await host.character("a"))["fp"] == {"current": 10, "maximum": 11}
        # Iven never rolled attack or defense, so mere presence costs no battleFP.
        assert (await host.character("b"))["fp"] == {"current": 11, "maximum": 11}
        await travel_both(host, "road-to-shelter")
        await begin_care(host, "rest", actor="a", target="a")
        await finish_care(host, 600, actor="a", target="a")
        assert (await host.character("a"))["fp"] == {"current": 11, "maximum": 11}
        assert (await host.character("a"))["hp"] == {"current": 7, "maximum": 11}
        await begin_care(host, "bandage")
        await finish_care(host, 60)
        assert (await host.character("a"))["hp"] == {"current": 8, "maximum": 11}
        await begin_care(host, "first-aid")
        await finish_care(host, 1800, seed=7)
        assert (await host.character("a"))["hp"] == {"current": 10, "maximum": 11}
        assert (await host.character("a"))["fp"] == {"current": 11, "maximum": 11}
        assert (await host.character("a"))["hp"] == {"current": 10, "maximum": 11}
        await host.command("evaluate_objectives", "a")
        assert (await host.runtime.checkpoint(host.campaign_id)).objectives.outcome == "success"
        await verify_postactivation_replay(host)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    ("seed", "table"),
    [(8235, (2, 1, 1)), (31506, (6, 5, 6))],
    ids=["ordinary-break4", "ordinary-break17"],
)
async def test_natural_ordinary_sword_breakage_does_not_strand_campaign(
    tmp_path: Path, backend: str, seed: int, table: tuple[int, int, int]
) -> None:
    """B274/B556: ordinary sword breaks on3/4 and17/18; no resistant confirmation."""
    async with campaign_http(tmp_path, backend) as host:
        await host.create_and_activate()
        await start_guard_attack(host)
        host.seeds.value = seed  # literal attack18, then the independent table vector
        await host.command("choose_defense", "a", encounter_id="roadwatch-fight", defense="none")
        assert (await host.character("a"))["hp"] == {"current": 11, "maximum": 11}
        gm = object_value(
            await host.request("GET", f"/roadwatch/campaigns/{host.campaign_id}/gm", token=GM_TOKEN)
        )
        assert gm["pending_critical_misses"] == []
        state = await host.runtime.checkpoint(host.campaign_id)
        record = BreakageResult.model_validate_json(
            next(
                event.kind
                for event in state.resources.events
                if event.id.startswith("critical-breakage:")
            )
        )
        assert record.table == table and record.broken and record.confirmation is None
        assert record.item_id == "sword-brigand"
        sword = next(item for item in state.resources.items if item.id == "sword-brigand")
        assert sword.condition is not None and sword.condition.disabled
        assert (
            next(enc for enc in state.encounters if enc.id == "roadwatch-fight").blocked_reason
            is None
        )
        await host.command(
            "end_encounter",
            "gm",
            token=GM_TOKEN,
            encounter_id="roadwatch-fight",
            reason="The brigand's ordinary sword broke and the surveyors withdraw.",
        )
        await travel_both(host, "road-to-shelter")
        await host.command("evaluate_objectives", "a")
        assert (await host.runtime.checkpoint(host.campaign_id)).objectives.outcome == "success"
        await verify_postactivation_replay(host)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    ("seed", "table", "hp", "location", "self_injury", "draw_count"),
    [(8365, (1, 1, 3), -1, "left-leg", 6, 19), (13171, (1, 1, 4), 1, "right-leg", 4, 16)],
    ids=["parry-critical5", "parry-critical6"],
)
async def test_explicit_parry_critical_self_hit_and_honest_loss_ending(
    tmp_path: Path,
    backend: str,
    seed: int,
    table: tuple[int, int, int],
    hp: int,
    location: str,
    self_injury: int,
    draw_count: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B421/B556 independent hand oracles are retained outside the test checkout."""
    async with campaign_http(tmp_path, backend) as host:
        await host.create_and_activate()
        await start_guard_attack(host)
        draws: list[int] = []
        actual = SeededRandom.randbelow

        def watched(rng: SeededRandom, bound: int, /) -> int:
            draws.append(bound)
            return actual(rng, bound)

        monkeypatch.setattr(SeededRandom, "randbelow", watched)
        host.seeds.value = seed
        command = {
            "id": "natural-parry-critical",
            "kind": "choose_defense",
            "actor_id": "a",
            "expected_revision": await host.revision(),
            "encounter_id": "roadwatch-fight",
            "defense": "parry",
            "item_id": "sword-a",
            "parry_mode_id": "swing",
        }
        path = f"/api/tactical/v2/campaigns/{host.campaign_id}/commands"
        await host.request("POST", path, value={"command": command})
        assert draws == [6] * draw_count
        assert (await host.character("a"))["hp"] == {"current": hp, "maximum": 11}
        state = await host.runtime.checkpoint(host.campaign_id)
        limb = CriticalLimbResult.model_validate_json(
            next(
                event.kind
                for event in state.resources.events
                if event.id.startswith("critical-limb:")
            )
        )
        assert limb.table_rolls == (table,) and limb.resolved
        assert limb.location == location and limb.injury == self_injury
        encounter = next(enc for enc in state.encounters if enc.id == "roadwatch-fight")
        assert encounter.blocked_reason is None
        assert (
            next(actor for actor in encounter.participants if actor.actor_id == "a").posture
            == "prone"
        )
        injury = next(pool.injury for pool in state.resources.pools if pool.id == "hp:a")
        assert injury is not None and injury.stunned and not injury.unconscious
        original = next(
            row
            for row in await host.store.history(host.campaign_id)
            if row.command_id == command["id"]
        )
        result = CombatResult.model_validate_json(original.event["outcome"])
        assert result.injury is not None and result.injury.injury == 6
        gm = object_value(
            await host.request("GET", f"/roadwatch/campaigns/{host.campaign_id}/gm", token=GM_TOKEN)
        )
        assert gm["pending_critical_misses"] == []
        before = await host.store.read(host.campaign_id)
        history = await host.store.history(host.campaign_id)
        await host.request("POST", path, value={"command": command})
        assert draws == [6] * draw_count
        assert await host.store.read(host.campaign_id) == before
        assert await host.store.history(host.campaign_id) == history
        await host.command("abandon_scenario", "gm", token=GM_TOKEN)
        assert (await host.runtime.checkpoint(host.campaign_id)).objectives.outcome == "abandoned"
        assert (await host.character("a"))["hp"] == {"current": hp, "maximum": 11}
        monkeypatch.setattr(SeededRandom, "randbelow", actual)
        await verify_postactivation_replay(host)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("action", ["observation", "stealth"])
@pytest.mark.parametrize(
    ("seed", "outcome", "die"),
    [(270, "critical-success", 1), (540, "critical-failure", 6)],
)
async def test_selected_skill_critical_flags_and_authored_effects_remain_replayable(
    tmp_path: Path, backend: str, action: str, seed: int, outcome: str, die: int
) -> None:
    """B211/B222/B347: record criticals; human GM fiction remains explicitly manual."""
    async with campaign_http(tmp_path, backend) as host:
        await host.create_and_activate()
        if action == "observation":
            host.seeds.value = seed
            command, _ = await host.command("inspect", "b", target_id="road-view")
        else:
            await host.command(
                "start_noncombat",
                "b",
                encounter_id="critical-quiet-path",
                selection_id="quiet-path",
            )
            host.seeds.value = seed
            command, _ = await host.command(
                "approach_noncombat",
                "b",
                encounter_id="critical-quiet-path",
                selection_id="move-quietly",
            )
        gm = object_value(
            await host.request("GET", f"/roadwatch/campaigns/{host.campaign_id}/gm", token=GM_TOKEN)
        )
        event = next(
            object_value(row)
            for row in array_value(gm["events"])
            if object_value(row)["command_id"] == command["id"]
        )
        assert isinstance(event["outcome"], str)
        receipt = object_value(json.loads(event["outcome"]))
        trace = object_value(
            receipt["check"] if action == "observation" else array_value(receipt["checks"])[-1]
        )
        assert trace["outcome"] == outcome and trace["dice"] == [die, die, die]
        assert trace["effective_target"] == (15 if action == "observation" else 14)
        state = await host.runtime.checkpoint(host.campaign_id)
        succeeded = outcome == "critical-success"
        if action == "observation":
            assert (("b", "road-warning") in state.world.knowledge) == succeeded
        else:
            assert (("b", "quiet-approach") in state.world.knowledge) == succeeded
            assert (("b", "noisy-approach") in state.world.knowledge) != succeeded
        assert not state.encounters  # no invented surprise/combat follows the footfall check
        assert (await host.character("a"))["hp"] == {"current": 11, "maximum": 11}
        assert (await host.character("b"))["fp"] == {"current": 11, "maximum": 11}
        await host.request("GET", f"/roadwatch/campaigns/{host.campaign_id}/gm", status=403)
        await travel_both(host, "post-to-road")
        await travel_both(host, "road-to-shelter")
        await host.command("evaluate_objectives", "a")
        assert (await host.runtime.checkpoint(host.campaign_id)).objectives.outcome == "success"
        await verify_postactivation_replay(host)
