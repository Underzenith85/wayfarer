"""B349 caps an offered Malediction even when its subject waives resistance."""

import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, played, seed_campaign
from test_actions import campaign
from test_attack_defense_traits import tolerance_options
from test_basic_combat import start_basic
from test_composed_attack_host import declare
from test_composed_attacks import pick
from test_gurps_conformance import load_cases
from test_opponent_attack_host import fixture
from test_opponent_attack_malediction import change_will

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.certification.executable_evidence import provenance
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.location import InjuryTolerance
from wayfarer.engine.simulation.actions import ActorSetup
from wayfarer.engine.simulation.traits.composed_records import (
    BindComposedSource,
    ResistComposedAttack,
)
from wayfarer.engine.simulation.traits.composed_sources import source_history
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.composed_attacks import ComposedAttackService
from wayfarer.orchestration.opponent_attack_records import BeginOpponentAttack, ChooseOpponentAttack
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.replay import verify_commands


async def prepare(
    path: Path,
    backend: str,
    *,
    target_will: int = 10,
    target_iq: int = 10,
    target_structure: Literal["living", "unliving", "homogenous", "diffuse"] = "living",
    target_body: bool = True,
    partial_no_blood: bool = False,
    purchased_unliving: bool = False,
) -> tuple[str, PlayService, Campaign]:
    donor_id, donor, _ = await fixture(
        path / "donor", backend, modifiers=(pick("enhancement:malediction", option="1"),)
    )
    await change_will(donor, donor_id, "a", 20)
    if target_will != 10:
        await change_will(donor, donor_id, "b", target_will)
    source = donor._load(await donor.store.read(donor_id))
    play = build_play(path / "actual", donor.engine, backend=backend, rng=RecordedDice(()))
    initial = campaign(play.engine)
    actors_list = []
    for a in source.actors:
        proposal = a.proposal
        body = a.body
        if a.actor_id == "b":
            purchases = tuple(
                p.model_copy(update={"amount": target_iq})
                if p.definition_id == "attribute:iq"
                else p
                for p in proposal.draft.purchases
            )
            if target_iq != 10 and not any(p.definition_id == "secondary:will" for p in purchases):
                purchases += (Purchase(definition_id="secondary:will", amount=target_will),)
            if purchased_unliving:
                purchases += (
                    Purchase(
                        definition_id="advantage:injury-tolerance",
                        trait=tolerance_options("unliving"),
                    ),
                )
            if partial_no_blood:
                purchases += (
                    Purchase(
                        definition_id="advantage:injury-tolerance",
                        trait=tolerance_options(no_blood=True),
                    ),
                )
            proposal = proposal.model_copy(
                update={"draft": proposal.draft.model_copy(update={"purchases": purchases})}
            )
            assert body is not None
            body = (
                body.model_copy(update={"tolerance": InjuryTolerance(structure=target_structure)})
                if target_body
                else None
            )
        actors_list.append(ActorSetup(actor_id=a.actor_id, proposal=proposal, body=body))
    actors = tuple(actors_list)
    resources = source.resources.model_copy(update={"revision": 0, "events": ()})
    for actor in actors:
        review = play.engine.reviewer.review(actor.proposal)
        assert review.compilation.build is not None, review.compilation.diagnostics
    state = play.initial_state(initial, source.world, resources, actors, members=source.members)
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    cid = initial["id"]
    play.rng = secrets
    play.seeds = lambda: f"{2:064x}"
    await CombatService(play).execute(cid, start_basic(2, ranged=True), principal_id="gm")
    return cid, play, initial


async def bind(play: PlayService, cid: str) -> str:
    state = play._load(await play.store.read(cid))
    result = await ComposedAttackService(play).execute(
        cid,
        BindComposedSource(
            id="bind",
            actor_id="a",
            expected_revision=state.revision,
            description="An approved Malediction",
            specialty="beam",
        ),
        principal_id="gm",
    )
    assert result.source_id
    return result.source_id


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_waived_resistance_retains_offered_cap_and_reexecutes(
    tmp_path: Path, backend: str, record_property: Callable[[str, object], None]
) -> None:
    case = next(c for c in load_cases() if c["id"] == "basic-rule-of-16-live-waiver")
    assert case["input"] == {
        "attacker_will": 20,
        "distance_yards": 2,
        "resister_will": 10,
        "resist": False,
        "caster_roll": [4, 4, 4],
        "damage_roll": [2, 2],
    }
    assert case["expected"] == {
        "attacker_effective_target": 16,
        "attacker_margin": 4,
        "defender_rolls": 0,
        "injury": 4,
        "target_hp": 6,
    }
    cid, play, _ = await prepare(tmp_path / "numeric", backend)
    source = await bind(play, cid)
    assert source_history((play._load(await play.store.read(cid))).resources)[
        -1
    ].malediction_waiver_cap
    await declare(play, cid, source)
    before = await play.store.read(cid)
    state = play._load(before)
    pending = state.encounters[0].pending_defense
    assert pending
    command = ResistComposedAttack(
        id="waive",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        pending_id=pending.id,
        resist=False,
    )
    play.rng = RecordedDice(())
    with pytest.raises((AuthorizationError, ValidationError)):
        await ComposedAttackService(play).execute(cid, command, principal_id="alice")
    with pytest.raises(ConflictError):
        await ComposedAttackService(play).execute(
            cid, command.model_copy(update={"expected_revision": 0}), principal_id="bob"
        )
    assert await play.store.read(cid) == before
    play.rng = RecordedDice((4, 4, 4, 2, 2))
    result = await ComposedAttackService(play).execute(cid, command, principal_id="bob")
    assert result.attack and result.attack.checks
    checks = result.attack.checks
    assert len(checks) == 1 and checks[0].effective_target == 16 and checks[0].margin == 4
    assert [(m.reason, m.value) for m in checks[0].modifiers][-1] == ("rule-of-16", -2)
    after = play._load(await play.store.read(cid))
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 6
    assert play.rng.exhausted() and after.encounters[0].pending_defense is None
    final = await play.store.read(cid)
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert (
        await ComposedAttackService(restarted).execute(cid, command, principal_id="bob") == result
    )
    assert await play.store.read(cid) == final == await play.store.replay(cid)

    seeded_id, seeded_play, genesis = await prepare(tmp_path / "seeded", backend)
    seeded_play.rng = secrets
    seeded_play.seeds = lambda: f"{2:064x}"
    source = await bind(seeded_play, seeded_id)
    await declare(seeded_play, seeded_id, source)
    state = seeded_play._load(await seeded_play.store.read(seeded_id))
    pending = state.encounters[0].pending_defense
    assert pending
    await ComposedAttackService(seeded_play).execute(
        seeded_id,
        ResistComposedAttack(
            id="waive",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            pending_id=pending.id,
            resist=False,
        ),
        principal_id="bob",
    )
    replayed, replay_checks = await verify_commands(
        genesis,
        await played(seeded_play.store, seeded_id),
        await seeded_play.store.stream(seeded_id),
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(seeded_play.engine, tmp_path / "reexecute"),
    )
    assert replay_checks and all(c.folded and c.reexecuted for c in replay_checks)
    assert replayed == await seeded_play.store.read(seeded_id)
    for name, value in provenance(Path(__file__).resolve().parents[1], case).items():
        record_property(name, value)


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_delayed_waiver_keeps_original_caster_spec_across_current_target_changes(
    tmp_path: Path,
    backend: str,
    legacy: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wayfarer.orchestration import composed_attacks
    from wayfarer.orchestration.task_records import SetRealPlayClock, snapshot

    cid, play, _ = await prepare(tmp_path, backend)
    if legacy:

        async def historical(*args: object) -> bool:
            return False

        monkeypatch.setattr(composed_attacks, "capture_malediction", historical)
    source = await bind(play, cid)
    marker = source_history((play._load(await play.store.read(cid))).resources)[-1]
    assert marker.malediction_waiver_cap is not legacy
    assert ("malediction_waiver_cap" in marker.model_dump()) is not legacy
    state = play._load(await play.store.read(cid))
    await TaskService(play).execute(
        cid,
        SetRealPlayClock(
            id="clock",
            actor_id="gm",
            expected_revision=state.revision,
            running=True,
        ),
        principal_id="gm",
    )
    await declare(play, cid, source)
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending
    play.rng = RecordedDice((4, 4, 4))
    command = BeginOpponentAttack(
        id="caster",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        attack_id=pending.id,
        resist=False,
    )
    begun = await TaskService(play).execute(cid, command, principal_id="gm")
    assert begun.pending_id and begun.check
    assert begun.check.effective_target == (18 if legacy else 16)
    captured = snapshot(play._load(await play.store.read(cid))).pending
    assert captured is not None
    await change_will(play, cid, "b", 20)
    state = play._load(await play.store.read(cid))
    response = ChooseOpponentAttack(
        id="choice",
        actor_id="b",
        expected_revision=state.revision,
        pending_id=begun.pending_id,
        choice="use-luck",
        response=ResistComposedAttack(
            id="choice",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            pending_id=pending.id,
            resist=False,
        ),
    )
    play.rng = RecordedDice((2, 2, 2, 5, 5, 5, 2, 2))
    result = await TaskService(play).execute(cid, response, principal_id="bob")
    assert result.check and result.check.effective_target == (18 if legacy else 16)
    assert result.check.total == 15 and result.combat_json and result.luck
    assert play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    assert snapshot(state).pending is None
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 6
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await TaskService(restarted).execute(cid, response, principal_id="bob") == result
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("target_will,effective", [(17, 17), (20, 18)])
async def test_waived_resistance_uses_actual_higher_resistance_without_raising_attacker(
    tmp_path: Path,
    backend: str,
    target_will: int,
    effective: int,
) -> None:
    cid, play, _ = await prepare(tmp_path, backend, target_will=target_will)
    source = await bind(play, cid)
    await declare(play, cid, source)
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending
    play.rng = RecordedDice((4, 4, 4, 2, 2))
    result = await ComposedAttackService(play).execute(
        cid,
        ResistComposedAttack(
            id="waive",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            pending_id=pending.id,
            resist=False,
        ),
        principal_id="bob",
    )
    assert result.attack and len(result.attack.checks) == 1
    assert result.attack.checks[0].effective_target == effective
    assert play.rng.exhausted()


def test_waiver_source_generation_rejects_forged_versions_and_digest() -> None:
    import json

    from wayfarer.orchestration.malediction_generations import KEY, stamp, version
    from wayfarer.persistence.events import CommandInput, payload_digest

    payload = {"operation": "composed-attack", "command": {"kind": "bind"}}
    original = json.dumps(payload)
    assert stamp(original, False) == original
    valid = stamp(original, True)
    assert version(CommandInput(payload_digest({"input": valid}), valid))
    assert not version(CommandInput(payload_digest({"input": original}), original))
    for invalid in (True, "1", 0, 2):
        text = json.dumps(payload | {KEY: invalid})
        with pytest.raises(ValidationError, match="generation"):
            version(CommandInput(payload_digest({"input": text}), text))
    text = json.dumps(payload | {KEY: 1, "command": {"kind": "resist"}})
    with pytest.raises(ValidationError, match="binding"):
        version(CommandInput(payload_digest({"input": text}), text))
    with pytest.raises(ValidationError, match="digest"):
        version(CommandInput("0" * 64, valid))


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_historical_waiver_source_reexecutes_original_unmarked_target(
    tmp_path: Path,
    backend: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wayfarer.orchestration import composed_attacks

    cid, play, genesis = await prepare(tmp_path, backend)

    async def historical(*args: object) -> bool:
        return False

    with monkeypatch.context() as patch:
        patch.setattr(composed_attacks, "capture_malediction", historical)
        source = await bind(play, cid)
    saved = await play.store.command_input(cid, "bind")
    assert saved and saved.text and "malediction_waiver_generation" not in saved.text
    assert (
        "malediction_waiver_cap"
        not in source_history(play._load(await play.store.read(cid)).resources)[-1].model_dump()
    )
    await declare(play, cid, source)
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending
    result = await ComposedAttackService(play).execute(
        cid,
        ResistComposedAttack(
            id="waive",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            pending_id=pending.id,
            resist=False,
        ),
        principal_id="bob",
    )
    assert result.attack and result.attack.checks[0].effective_target == 18
    replayed, checks = await verify_commands(
        genesis,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == await play.store.read(cid)


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_seeded_delayed_waiver_reexecutes_from_original_genesis(
    tmp_path: Path,
    backend: str,
) -> None:
    from wayfarer.orchestration.task_records import SetRealPlayClock

    cid, play, genesis = await prepare(tmp_path, backend)
    source = await bind(play, cid)
    state = play._load(await play.store.read(cid))
    await TaskService(play).execute(
        cid,
        SetRealPlayClock(
            id="clock",
            actor_id="gm",
            expected_revision=state.revision,
            running=True,
        ),
        principal_id="gm",
    )
    await declare(play, cid, source)
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending
    begun = await TaskService(play).execute(
        cid,
        BeginOpponentAttack(
            id="caster",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            attack_id=pending.id,
            resist=False,
        ),
        principal_id="gm",
    )
    assert begun.pending_id and begun.check and begun.check.effective_target == 16
    state = play._load(await play.store.read(cid))
    result = await TaskService(play).execute(
        cid,
        ChooseOpponentAttack(
            id="choice",
            actor_id="b",
            expected_revision=state.revision,
            pending_id=begun.pending_id,
            choice="use-luck",
            response=ResistComposedAttack(
                id="choice",
                actor_id="b",
                expected_revision=state.revision,
                encounter_id="fight",
                pending_id=pending.id,
                resist=False,
            ),
        ),
        principal_id="bob",
    )
    assert result.check and result.check.effective_target == 16 and result.combat_json
    replayed, checks = await verify_commands(
        genesis,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == await play.store.read(cid)


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("resist", [False, True])
@pytest.mark.parametrize(
    "target_iq,structure,effective,target_hp",
    [
        (5, "living", 16, 6),
        (5, "unliving", 18, 6),
        (10, "unliving", 16, 6),
        (10, "homogenous", 16, 6),
        (10, "diffuse", 16, 8),
    ],
)
async def test_actual_target_living_or_sapient_scope_controls_waived_and_resisted_attacks(
    tmp_path: Path,
    backend: str,
    resist: bool,
    target_iq: int,
    structure: Literal["living", "unliving", "homogenous", "diffuse"],
    effective: int,
    target_hp: int,
) -> None:
    cid, play, _ = await prepare(tmp_path, backend, target_iq=target_iq, target_structure=structure)
    source = await bind(play, cid)
    await declare(play, cid, source)
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending
    play.rng = RecordedDice((4, 4, 4) + ((4, 4, 3) if resist else ()) + (2, 2))
    result = await ComposedAttackService(play).execute(
        cid,
        ResistComposedAttack(
            id="resolve",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            pending_id=pending.id,
            resist=resist,
        ),
        principal_id="bob",
    )
    assert result.attack and len(result.attack.checks) == (2 if resist else 1)
    assert result.attack.checks[0].effective_target == effective
    assert (any(m.reason == "rule-of-16" for m in result.attack.checks[0].modifiers)) == (
        effective == 16
    )
    assert (
        next(
            p.current
            for p in play._load(await play.store.read(cid)).resources.pools
            if p.id == "hp:b"
        )
        == target_hp
    )
    assert play.rng.exhausted()


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "structure,body", [("homogenous", True), ("diffuse", True), ("living", False)]
)
async def test_unknown_low_iq_target_livingness_refuses_before_entropy(
    tmp_path: Path,
    backend: str,
    structure: Literal["living", "unliving", "homogenous", "diffuse"],
    body: bool,
) -> None:
    cid, play, _ = await prepare(
        tmp_path, backend, target_iq=5, target_structure=structure, target_body=body
    )
    source = await bind(play, cid)
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="living or sapient"):
        await declare(play, cid, source)
    assert play.rng.exhausted() and await play.store.read(cid) == before


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("resist", [False, True])
async def test_partial_tolerance_living_damage_default_does_not_override_unliving_body(
    tmp_path: Path,
    backend: str,
    resist: bool,
) -> None:
    cid, play, _ = await prepare(
        tmp_path, backend, target_iq=5, target_structure="unliving", partial_no_blood=True
    )
    source = await bind(play, cid)
    await declare(play, cid, source)
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending
    play.rng = RecordedDice((4, 4, 4) + ((4, 4, 3) if resist else ()) + (2, 2))
    result = await ComposedAttackService(play).execute(
        cid,
        ResistComposedAttack(
            id="resolve",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            pending_id=pending.id,
            resist=resist,
        ),
        principal_id="bob",
    )
    assert result.attack and result.attack.checks[0].effective_target == 18
    assert not any(m.reason == "rule-of-16" for m in result.attack.checks[0].modifiers)
    assert play.rng.exhausted()


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_partial_tolerance_living_default_does_not_supply_missing_biology(
    tmp_path: Path,
    backend: str,
) -> None:
    cid, play, _ = await prepare(
        tmp_path, backend, target_iq=5, target_body=False, partial_no_blood=True
    )
    source = await bind(play, cid)
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="explicit target anatomy"):
        await declare(play, cid, source)
    assert play.rng.exhausted() and await play.store.read(cid) == before


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("iq,body,effective", [(5, True, 18), (10, True, 16)])
async def test_approved_unliving_purchase_retains_negative_scope_without_erasing_sapience(
    tmp_path: Path,
    backend: str,
    iq: int,
    body: bool,
    effective: int,
) -> None:
    cid, play, _ = await prepare(
        tmp_path, backend, target_iq=iq, target_body=body, purchased_unliving=True
    )
    source = await bind(play, cid)
    await declare(play, cid, source)
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending
    play.rng = RecordedDice((4, 4, 4, 2, 2))
    result = await ComposedAttackService(play).execute(
        cid,
        ResistComposedAttack(
            id="resolve",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            pending_id=pending.id,
            resist=False,
        ),
        principal_id="bob",
    )
    assert result.attack and result.attack.checks[0].effective_target == effective
    assert play.rng.exhausted()


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_purchased_unliving_without_injury_anatomy_refuses_before_entropy(
    tmp_path: Path,
    backend: str,
) -> None:
    cid, play, _ = await prepare(
        tmp_path, backend, target_iq=5, target_body=False, purchased_unliving=True
    )
    source = await bind(play, cid)
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="explicit target anatomy"):
        await declare(play, cid, source)
    assert play.rng.exhausted() and await play.store.read(cid) == before


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("stage", ["original", "luck"])
async def test_task_revalidates_target_injury_anatomy_before_original_or_luck_dice(
    tmp_path: Path,
    backend: str,
    stage: str,
) -> None:
    from test_combat_sensory_authority import change

    from wayfarer.orchestration.task_records import SetRealPlayClock

    cid, play, _ = await prepare(tmp_path, backend, partial_no_blood=True)
    source = await bind(play, cid)
    state = play._load(await play.store.read(cid))
    await TaskService(play).execute(
        cid,
        SetRealPlayClock(
            id="clock",
            actor_id="gm",
            expected_revision=state.revision,
            running=True,
        ),
        principal_id="gm",
    )
    await declare(play, cid, source)
    state = play._load(await play.store.read(cid))
    attack = state.encounters[0].pending_defense
    assert attack
    begun = None
    if stage == "luck":
        play.rng = RecordedDice((4, 4, 4))
        begun = await TaskService(play).execute(
            cid,
            BeginOpponentAttack(
                id="caster",
                actor_id="b",
                expected_revision=state.revision,
                encounter_id="fight",
                attack_id=attack.id,
                resist=False,
            ),
            principal_id="gm",
        )
        assert begun.pending_id and begun.check and begun.check.effective_target == 16
    await change(
        play,
        cid,
        lambda s: s.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(update={"body": None}) if a.actor_id == "b" else a
                    for a in s.actors
                )
            }
        ),
    )
    before = await play.store.read(cid)
    state = play._load(before)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="anatomy"):
        if begun is None:
            await TaskService(play).execute(
                cid,
                BeginOpponentAttack(
                    id="caster",
                    actor_id="b",
                    expected_revision=state.revision,
                    encounter_id="fight",
                    attack_id=attack.id,
                    resist=False,
                ),
                principal_id="gm",
            )
        else:
            assert begun.pending_id
            await TaskService(play).execute(
                cid,
                ChooseOpponentAttack(
                    id="choice",
                    actor_id="b",
                    expected_revision=state.revision,
                    pending_id=begun.pending_id,
                    choice="use-luck",
                    response=ResistComposedAttack(
                        id="choice",
                        actor_id="b",
                        expected_revision=state.revision,
                        encounter_id="fight",
                        pending_id=attack.id,
                        resist=False,
                    ),
                ),
                principal_id="bob",
            )
    assert play.rng.exhausted() and await play.store.read(cid) == before
