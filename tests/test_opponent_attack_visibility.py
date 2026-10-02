"""Source privacy survives cancellation and later ordinary combat commands."""

import json
from pathlib import Path

import pytest
from support.runtime import build_play, build_runtime
from test_combat_sensory_authority import change
from test_composed_attack_host import declare
from test_composed_attacks import pick
from test_gurps_maneuvers import turn
from test_opponent_attack_host import fixture
from test_opponent_attack_inventory import fixture as inventory_fixture
from test_opponent_attack_secret import choose_secret

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.traits.composed_records import ResistComposedAttack
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.composed_attacks import ComposedAttackService
from wayfarer.orchestration.opponent_attack_records import BeginOpponentAttack
from wayfarer.orchestration.task_records import snapshot
from wayfarer.orchestration.tasks import TaskService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "route,gm_first",
    [
        ("composed", False),
        ("composed", True),
        ("malediction", False),
        ("malediction", True),
        ("melee", False),
        ("ranged", False),
    ],
)
async def test_cancelled_secret_attack_keeps_ordinary_response_and_retry_private(
    tmp_path: Path, backend: str, route: str, gm_first: bool
) -> None:
    if route in ("melee", "ranged"):
        cid, play = await inventory_fixture(tmp_path, backend, ranged=route == "ranged")
        await turn(
            cid,
            play,
            "a",
            "attack",
            item_id="sword-a",
            target_id="b",
            mode_id="ranged" if route == "ranged" else "swing",
        )
    else:
        cid, play, source = await fixture(
            tmp_path,
            backend,
            modifiers=(
                (pick("enhancement:malediction", option="1"),) if route == "malediction" else ()
            ),
        )
        await declare(play, cid, source)
    state = play._load(await play.store.read(cid))
    attack = state.encounters[0].pending_defense
    assert attack
    play.rng = RecordedDice(())
    command = BeginOpponentAttack(
        id="private",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        attack_id=attack.id,
        visibility="secret",
        resist=True if route == "malediction" else None,
    )
    begun = await TaskService(play).execute(cid, command, principal_id="gm")
    await choose_secret(play, cid, begun.pending_id, choice="cancel", principal="gm")
    state = play._load(await play.store.read(cid))
    assert snapshot(state).pending is None and state.encounters[0].pending_defense == attack
    with pytest.raises(ValidationError, match="cannot become public"):
        await TaskService(play).execute(
            cid,
            command.model_copy(
                update={
                    "id": "downgrade",
                    "expected_revision": state.revision,
                    "visibility": "public",
                }
            ),
            principal_id="gm",
        )
    assert play.rng.exhausted()
    # No Luck decision remains; the ordinary, authorized defense/resistance proceeds.
    restarted = build_play(
        tmp_path,
        play.engine,
        backend=backend,
        rng=RecordedDice(
            (2, 2, 2, 4, 4, 4, 2, 2)
            if route == "malediction"
            else (3, 3, 3, 2, 2)
            if route == "composed"
            else (3, 3, 3, 2)
        ),
    )
    principal = "gm" if gm_first else "b"
    response: ResistComposedAttack | ChooseDefense
    if route == "malediction":
        response = ResistComposedAttack(
            id="ordinary",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            pending_id=attack.id,
            resist=True,
        )
        result = await ComposedAttackService(restarted).execute(
            cid, response, principal_id=principal
        )
        assert result.combat
        assert bool(result.attack) == gm_first and bool(result.combat.injury) == gm_first
    else:
        response = ChooseDefense(
            id="ordinary",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        )
        combat_result = await CombatService(restarted).execute(
            cid, response, principal_id=principal
        )
        assert bool(combat_result.injury) == gm_first
    after = restarted._load(await restarted.store.read(cid))
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") < 10
    assert after.encounters[0].pending_defense is None
    assert all(
        not t.totals and not t.targets
        for t in after.encounters[0].tactical_traces
        if t.command_id == response.id
    )
    assert isinstance(restarted.rng, RecordedDice) and restarted.rng.exhausted()
    if gm_first:
        await change(
            restarted,
            cid,
            lambda s: s.model_copy(
                update={
                    "members": tuple(
                        CampaignMember(principal_id="gm", role="player", actor_ids=("b",))
                        if m.principal_id == "gm"
                        else m
                        for m in s.members
                    )
                }
            ),
        )
    fresh = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    if isinstance(response, ResistComposedAttack):
        again = await ComposedAttackService(fresh).execute(cid, response, principal_id=principal)
        assert again.attack is None and again.combat and again.combat.injury is None
    else:
        again_combat = await CombatService(fresh).execute(cid, response, principal_id=principal)
        assert again_combat.injury is None and again_combat.unarmed is None
    stream = json.dumps(
        [
            entry.model_dump(mode="json")
            for entry in await build_runtime(fresh).events(cid, principal_id=principal)
        ]
    )
    for private in (
        "effective_target",
        "damage_dice",
        "fragment_attack",
        "opponent-secret-source:",
        "opponent-secret-result:",
    ):
        assert private not in stream
    assert await fresh.store.read(cid) == await fresh.store.replay(cid)
