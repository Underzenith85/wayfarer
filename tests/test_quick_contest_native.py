"""B348 ordinary Quick Contests through a real B371 escape attempt.

Initial grips are GM-authored world facts, not claimed successful attack rolls.
The public command stores both actual checks; the pure adapter additionally
checks their B348 winner/margin without manufacturing a stored contest receipt.
"""

import json
import secrets
from collections.abc import Callable
from dataclasses import asdict, dataclass
from functools import cache
from pathlib import Path
from typing import Final

import pytest
from support.quick_contest_evidence import record_quick_contest
from support.runtime import build_play, played, seed_campaign
from test_actions import campaign
from test_gurps_melee import setup as melee_setup

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import ModifierKind, Outcome, RecordedDice
from wayfarer.engine.rules.fright import fright_effect
from wayfarer.engine.rules.gurps_checks import Contestant, resolve_quick_contest
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.simulation.actions import ActorSetup
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.commands import TakeUnarmedTurn
from wayfarer.engine.simulation.combat.spatial import SquareSpatialContext
from wayfarer.engine.simulation.combat.unarmed.records import Grip
from wayfarer.engine.simulation.health.fright import apply_effect, recover
from wayfarer.engine.simulation.health.fright_state import effects
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands

PROFILE: Final = "gurps-basic-set-4e-2004"


@dataclass(frozen=True)
class Witness:
    name: str
    first: int
    second: int
    first_roll: int
    second_roll: int
    winner: str | None
    victory: int
    decision: str
    aftermath: bool = False


WITNESSES = (
    Witness("Q1-lower-score-success", 12, 15, 9, 16, "a", 4, "success-over-failure"),
    Witness("Q1-reversed-success", 15, 12, 16, 9, "b", 4, "success-over-failure"),
    # Actual native modifiers are raw14/13 minus2 each, not the illustrative
    # positive10+2/10+1 decomposition. Both yield literal margins2/1.
    Witness("Q2-canonical-aftermath", 14, 13, 10, 10, "a", 1, "margin-of-success", True),
    Witness("Q3-both-fail", 10, 12, 12, 17, "a", 3, "margin-of-failure"),
    Witness("Q4-success-tie", 12, 15, 9, 12, None, 0, "tie"),
    Witness("Q4-failure-tie", 10, 8, 13, 11, None, 0, "tie"),
    Witness("Q5-success-plus-failure", 15, 10, 10, 15, "a", 10, "success-over-failure"),
    Witness("Q6-critical-does-not-override", 16, 20, 6, 7, "b", 3, "margin-of-success"),
)


CASE_IDS: Final = {
    "Q1-lower-score-success": "basic-b348-q1-break-free-first-success",
    "Q1-reversed-success": "basic-b348-q1-break-free-second-success",
    "Q2-canonical-aftermath": "basic-b348-q2-break-free-fright-aftermath",
    "Q3-both-fail": "basic-b348-q3-break-free-both-fail",
    "Q4-success-tie": "basic-b348-q4-break-free-success-tie",
    "Q4-failure-tie": "basic-b348-q4-break-free-failure-tie",
    "Q5-success-plus-failure": "basic-b348-q5-break-free-mixed-victory",
    "Q6-critical-does-not-override": "basic-b348-q6-break-free-critical-margin",
}


class NoDraw:
    calls = 0

    def randbelow(self, upper: int) -> int:
        self.calls += 1
        raise AssertionError("Refusal or exact retry attempted entropy")


@cache
def seed_for(first: int, second: int) -> str:
    """Choose real command seeds for literal totals, never force a stored roll."""
    for index in range(100000):
        seed = f"{index:064x}"
        rng = SeededRandom(seed)
        if (
            sum(rng.randbelow(6) + 1 for _ in range(3)) == first
            and sum(rng.randbelow(6) + 1 for _ in range(3)) == second
        ):
            return seed
    raise AssertionError("Literal Quick Contest totals need a deterministic seed")


async def prepare(path: Path, backend: str, witness: Witness) -> tuple[str, PlayService, Campaign]:
    source_id, source_play = await melee_setup(path / "source", PROFILE, human=True, trained=False)
    source = source_play._load(await source_play.store.read(source_id))
    play = build_play(path / "actual", source_play.engine, backend=backend)
    initial = campaign(play.engine)
    actors = []
    for actor in source.actors:
        # B371: holder's two hands supply +5; all remaining ST is purchased.
        st = witness.first if actor.actor_id == "a" else witness.second - 5
        purchases = tuple(
            p.model_copy(update={"amount": st}) if p.definition_id == "attribute:st" else p
            for p in actor.proposal.draft.purchases
        )
        proposal = actor.proposal.model_copy(
            update={"draft": actor.proposal.draft.model_copy(update={"purchases": purchases})}
        )
        actors.append(ActorSetup(actor_id=actor.actor_id, proposal=proposal, body=actor.body))
    resources = source.resources.model_copy(
        update={
            "revision": 0,
            "game_time": 0,
            "events": (),
            "items": tuple(
                item.model_copy(update={"ready": False, "equipped": False})
                for item in source.resources.items
            ),
        }
    )
    state = play.initial_state(
        initial, source.world, resources, tuple(actors), members=source.members
    )
    spatial = source.encounters[0].spatial
    assert isinstance(spatial, SquareSpatialContext)
    encounter = source.encounters[0].model_copy(
        update={
            "spatial_context": spatial.model_copy(
                update={
                    "placements": tuple(
                        p.model_copy(update={"position": GridPoint(x=1, y=1)})
                        for p in spatial.placements
                    )
                }
            ),
            "participants": tuple(
                p.model_copy(
                    update={
                        "position": GridPoint(x=1, y=1),
                        "ready_item_ids": (),
                        "hand_bindings": (),
                        "grappled": p.actor_id == "a",
                    }
                )
                for p in source.encounters[0].participants
            ),
            "close_pairs": (("a", "b"),),
            "grips": (
                Grip(
                    id="gm-initial-grip",
                    holder_id="b",
                    target_id="a",
                    hands=("left-hand", "right-hand"),
                ),
            ),
        }
    )
    if witness.aftermath:
        # These are genuine system-produced health records, not synthetic
        # TimedFright or successful public Fright/Clock command claims.
        resources = state.resources
        for actor_id in ("a", "b"):
            effect = fright_effect(28, 10, rng=RecordedDice(()))
            resources = apply_effect(
                resources,
                effect,
                actor_id=actor_id,
                trigger_id="prior-fear-" + actor_id,
                command_id="native-fright-" + actor_id,
                ht=10,
                will=10,
                modified_will=10,
                rng=RecordedDice(()),
            )
        resources = resources.model_copy(update={"game_time": 1800})
        for actor_id in ("a", "b"):
            dice = RecordedDice((3, 3, 3))
            resources, passed = recover(
                resources,
                actor_id=actor_id,
                trigger_id="prior-fear-" + actor_id,
                command_id="native-recover-" + actor_id,
                rng=dice,
            )
            assert passed and dice.exhausted()
        assert len(effects(resources)) == 2
        assert all(not e.active and e.aftermath_until == 23400 for e in effects(resources))
        state = state.model_copy(update={"resources": resources})
    state = state.model_copy(update={"encounters": (encounter,)})
    play.engine.validate(state)
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    return initial["id"], play, initial


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("witness", WITNESSES, ids=lambda w: w.name)
async def test_native_break_free_literal_quick_contest_and_original_seed(
    tmp_path: Path,
    backend: str,
    witness: Witness,
    record_property: Callable[[str, object], None],
) -> None:
    cid, play, initial = await prepare(tmp_path, backend, witness)
    state = play._load(initial)
    command = TakeUnarmedTurn(
        id=witness.name,
        actor_id="a",
        expected_revision=state.revision,
        encounter_id="fight",
        action="break_free",
        target_id="b",
        grip_id="gm-initial-grip",
    )
    no_draw = NoDraw()
    play.rng = no_draw
    with pytest.raises((AuthorizationError, ValidationError)):
        await CombatService(play).execute(cid, command, principal_id="b")
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid,
            command.model_copy(update={"expected_revision": state.revision + 1}),
            principal_id="a",
        )
    assert no_draw.calls == 0 and await play.store.read(cid) == initial

    seed = seed_for(witness.first_roll, witness.second_roll)
    play.rng, play.seeds = secrets, lambda: seed
    result = await CombatService(play).execute(cid, command, principal_id="a")
    final = await play.store.read(cid)
    after = play._load(final)
    native = after.encounters[0].unarmed_history[-1]
    first, second = native.checks
    assert (first.base_target, second.base_target) == (witness.first, witness.second)
    if witness.aftermath:
        for check in (first, second):
            assert len(check.modifiers) == 1
            modifier = check.modifiers[0]
            assert modifier.value == -2 and modifier.kind is ModifierKind.SITUATIONAL
            assert modifier.reason == "Fright aftermath"
            assert modifier.source_version == "Basic Set Campaigns 4e B361"
    else:
        assert first.modifiers == second.modifiers == ()
    penalty = 2 if witness.aftermath else 0
    assert (first.effective_target, second.effective_target) == (
        witness.first - penalty,
        witness.second - penalty,
    )
    assert (first.total, second.total) == (witness.first_roll, witness.second_roll)
    assert (first.margin, second.margin) == (
        witness.first - penalty - witness.first_roll,
        witness.second - penalty - witness.second_roll,
    )
    adapted = resolve_quick_contest(
        PROFILE,
        Contestant("a", first.base_target, first.modifiers),
        Contestant("b", second.base_target, second.modifiers),
        first_dice=first.dice,
        second_dice=second.dice,
    )
    assert (adapted.first, adapted.second) == (first, second)
    assert (adapted.winner, adapted.victory_margin, adapted.decision) == (
        witness.winner,
        witness.victory,
        witness.decision,
    )
    assert native.won == (witness.winner == "a")
    assert bool(after.encounters[0].grips) == (witness.winner != "a")
    assert next(p for p in after.encounters[0].participants if p.actor_id == "a").grappled == (
        witness.winner != "a"
    )
    assert tuple((p.id, p.current, p.maximum) for p in after.resources.pools) == tuple(
        (p.id, p.current, p.maximum) for p in state.resources.pools
    )
    if witness.name == "Q6-critical-does-not-override":
        assert first.outcome is Outcome.CRITICAL_SUCCESS and second.outcome is Outcome.SUCCESS
    restarted = build_play(tmp_path / "restart", play.engine, store=play.store, rng=no_draw)
    assert await CombatService(restarted).execute(cid, command, principal_id="a") == result
    assert no_draw.calls == 0 and await play.store.read(cid) == final
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "original-seed"),
    )
    assert len(checks) == 1 and all(c.folded and c.reexecuted for c in checks)
    assert replayed == final == await play.store.replay(cid)

    # These context observations precede provenance; expected fixture values are
    # never used to populate the actual input or outcome dictionaries.
    assert play.engine.reviewer.compiler.statistics_profile == PROFILE
    raw_strength = []
    for actor_id in (command.actor_id, command.target_id):
        actor = next(a for a in state.actors if a.actor_id == actor_id)
        approved, _ = play.engine.reviewer.activate(
            actor.proposal, actor.approval, campaign_id=cid, actor_id=actor_id
        )
        assert approved.statistics is not None
        raw_strength.append(approved.statistics.st)
    grip = state.encounters[0].grips[0]
    assert grip.holder_id == command.target_id and grip.target_id == command.actor_id
    assert grip.hands == ("left-hand", "right-hand")
    assert not grip.pinned and not grip.arm_lock and grip.escape_penalty == 0
    assert raw_strength == [witness.first, witness.second - 5]
    assert after.revision == state.revision + 1
    assert after.resources.game_time == state.resources.game_time
    assert after.encounters[0].round == state.encounters[0].round
    assert state.encounters[0].current_actor_id == command.actor_id
    assert after.encounters[0].current_actor_id == command.target_id
    assert (len(first.dice), len(second.dice)) == (3, 3)
    if witness.aftermath:
        episodes = effects(state.resources)
        assert {e.actor_id for e in episodes} == {command.actor_id, command.target_id}
        assert all(
            not e.active
            and e.effect.table_total == 28
            and e.effect.aftermath_penalty == -2
            and e.aftermath_until == 23400
            for e in episodes
        )
    record_property("b348_runtime_profile", play.engine.reviewer.compiler.statistics_profile)
    record_property(
        "b348_runtime_rules",
        json.dumps(asdict(play.engine.reviewer.compiler.rules), sort_keys=True),
    )
    record_quick_contest(
        CASE_IDS[witness.name],
        {
            "approved_raw_ST": raw_strength,
            "holder_hands": len(grip.hands),
            "holder_bonus": 5 if len(grip.hands) == 2 else 0,
            "native_trace_bases": [first.base_target, second.base_target],
            "native_trace_modifier_values": [
                [modifier.value for modifier in first.modifiers],
                [modifier.value for modifier in second.modifiers],
            ],
            "roll_totals": [first.total, second.total],
            "dice_per_attempt": len(first.dice),
        },
        {
            "effective_targets": [first.effective_target, second.effective_target],
            "signed_margins": [first.margin, second.margin],
            "winner": adapted.winner,
            "victory_margin": adapted.victory_margin,
            "grip_retained": bool(after.encounters[0].grips),
            "pools_unchanged": tuple((p.id, p.current, p.maximum) for p in after.resources.pools)
            == tuple((p.id, p.current, p.maximum) for p in state.resources.pools),
        },
        record_property,
    )
