"""B349 eligibility at actual approved ability consumers, with historical replay."""

import secrets
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, played, seed_campaign
from test_abilities import command, spec
from test_ability_service import setup
from test_actions import campaign
from test_gurps_conformance import load_cases
from test_statistics import profile_package

from scripts.replay_fixtures import FixtureExecutor
from wayfarer import validation
from wayfarer.certification.executable_evidence import provenance
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import PowerReviewer
from wayfarer.engine.rules.catalog import RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.supernatural.abilities import PROFILE, definition
from wayfarer.engine.rules.types.location import HumanBody, InjuryTolerance
from wayfarer.engine.simulation.abilities import internal_id
from wayfarer.engine.simulation.ability_types import AbilityEvent
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, Wait
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.orchestration.abilities import AbilityService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands


async def prepare(
    path: Path,
    backend: str,
    kind: str,
    iq: int,
    structure: Literal["living", "unliving", "homogenous", "diffuse"] = "living",
    *,
    caster_iq: int = 20,
    caster_will: int = 20,
    distance: int = 2,
    principal: str | None = None,
) -> tuple[str, PlayService, Campaign]:
    ability = spec(kind, ("malediction-1",) if kind == "burning-malediction" else ())
    donor_id, donor = await setup(path / "donor", ability)
    source = donor._load(await donor.store.read(donor_id))
    original = donor.engine.reviewer.compiler
    catalog = RulesCatalog((profile_package(PROFILE, definition(ability)),))
    compiler = CharacterCompiler(
        catalog,
        original.rules,
        replace(original.policy, point_budget=1000),
        statistics_profile=original.statistics_profile,
        trait_runtime_hooks=original.trait_runtime_hooks,
    )
    rules = donor.engine.rules
    assert rules.abilities
    rules = rules.model_copy(
        update={
            "abilities": rules.abilities.model_copy(
                update={
                    "channels": tuple(
                        c.model_copy(update={"distance_yards": distance})
                        for c in rules.abilities.channels
                    )
                }
            )
        }
    )
    engine = ActionEngine(
        PowerReviewer(compiler, donor.engine.reviewer.policy, donor.engine.reviewer.gm_ids),
        ResourceEngine(source.world, catalog, compiler.rules, compiler.policy, ()),
        rules,
    )
    play = build_play(
        path / "actual", engine, backend=backend, rng=secrets, seeds=lambda: f"{2:064x}"
    )
    initial = campaign(engine)
    actors = []
    for actor in source.actors:
        purchases = tuple(
            p.model_copy(update={"amount": caster_iq if actor.actor_id == "a" else iq})
            if p.definition_id == "attribute:iq"
            else p
            for p in actor.proposal.draft.purchases
        )
        purchases += (
            Purchase(
                definition_id="secondary:will", amount=caster_will if actor.actor_id == "a" else 10
            ),
        )
        proposal = actor.proposal.model_copy(
            update={"draft": actor.proposal.draft.model_copy(update={"purchases": purchases})}
        )
        body = (
            HumanBody(anatomy="human", tolerance=InjuryTolerance(structure=structure))
            if actor.actor_id == "b"
            else HumanBody(anatomy="human")
        )
        actors.append(ActorSetup(actor_id=actor.actor_id, proposal=proposal, body=body))
    state = play.initial_state(
        initial,
        source.world,
        source.resources.model_copy(update={"revision": 0}),
        tuple(actors),
        members=source.members
        + (
            (CampaignMember(principal_id=principal, role="player", actor_ids=("a",)),)
            if principal is not None
            else ()
        ),
    )
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    return initial["id"], play, initial


CASES = [
    ("burning-living", "burning-malediction", 5, "living", 16),
    ("burning-neither", "burning-malediction", 5, "unliving", 18),
    ("burning-sapient", "burning-malediction", 10, "unliving", 16),
    ("mind-neither", "mind-reading", 5, "unliving", 20),
    ("mind-living", "mind-reading", 5, "living", 16),
]


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "case_id,kind,iq,structure,effective",
    CASES,
    ids=["basic-rule-of-16-ability-" + c[0] for c in CASES],
)
async def test_actual_ability_scope_and_original_genesis_replay(
    tmp_path: Path,
    backend: str,
    case_id: str,
    kind: str,
    iq: int,
    structure: Literal["living", "unliving"],
    effective: int,
    record_property: Callable[[str, object], None],
) -> None:
    case = next(c for c in load_cases() if c["id"] == "basic-rule-of-16-ability-" + case_id)
    source = validation.mapping(case["input"])
    expected = validation.mapping(case["expected"])
    assert source == {
        "ability_kind": kind,
        "caster_iq": 20,
        "caster_will": 20,
        "distance_yards": 2,
        "resister_iq": iq,
        "resister_will": 10,
        "target_structure": structure,
        "caster_roll": [4, 4, 4],
        "resister_roll": [5, 5, 5],
        "damage_roll": [4] if kind == "burning-malediction" else [],
    }
    assert expected == {
        "pre_cap_target": 18 if kind == "burning-malediction" else 20,
        "attacker_effective_target": effective,
        "defender_effective_target": 10,
        "defender_rolls": 1,
        "injury": 4 if kind == "burning-malediction" else 0,
        "target_hp": 6 if kind == "burning-malediction" else 10,
        "revealed_fact_ids": [] if kind == "burning-malediction" else ["thought"],
        **({"thought_value": "I am hungry"} if kind == "mind-reading" else {}),
    }
    selected_structure: Literal["living", "unliving"] = (
        "living" if source["target_structure"] == "living" else "unliving"
    )
    cid, play, _ = await prepare(
        tmp_path / "literal",
        backend,
        validation.string(source["ability_kind"]),
        validation.integer(source["resister_iq"]),
        selected_structure,
        caster_iq=validation.integer(source["caster_iq"]),
        caster_will=validation.integer(source["caster_will"]),
        distance=validation.integer(source["distance_yards"]),
    )
    await AbilityService(play).execute(cid, command(), principal_id="a")
    state = play._load(await play.store.read(cid))
    await play.execute(
        cid,
        Wait(id="wait", actor_id="a", expected_revision=state.revision, ticks=1),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    resolve = command(state.revision, "resolve")
    dice = (
        validation.sequence(source["caster_roll"])
        + validation.sequence(source["resister_roll"])
        + validation.sequence(source["damage_roll"])
    )
    play.rng = RecordedDice(tuple(validation.integer(die) for die in dice))
    result = await AbilityService(play).execute(cid, resolve, principal_id="a")
    state = play._load(await play.store.read(cid))
    event = next(e for e in state.resources.events if e.id == internal_id(resolve.id))
    traces = AbilityEvent.model_validate_json(event.kind).checks
    assert play.rng.exhausted() and len(traces) == 2
    assert traces[0].base_target == expected["pre_cap_target"]
    assert traces[0].effective_target == expected["attacker_effective_target"]
    assert traces[1].effective_target == 10
    assert traces[0].total == 12 and traces[1].total == 15
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == (
        6 if kind == "burning-malediction" else 10
    )
    assert list(result.revealed_fact_ids) == expected["revealed_fact_ids"]
    if kind == "mind-reading":
        assert (
            next(f.value for f in state.world.facts if f.id == "thought")
            == expected["thought_value"]
        )
    assert (("a", "thought") in state.world.knowledge) == (kind == "mind-reading")
    history = await played(play.store, cid)
    final = await play.store.read(cid)
    assert await AbilityService(play).execute(cid, resolve, principal_id="a") == result
    assert await played(play.store, cid) == history and await play.store.read(cid) == final

    sid, seeded, genesis = await prepare(tmp_path / "seeded", backend, kind, iq, structure)
    await AbilityService(seeded).execute(sid, command(), principal_id="a")
    current = seeded._load(await seeded.store.read(sid))
    await seeded.execute(
        sid,
        Wait(id="wait", actor_id="a", expected_revision=current.revision, ticks=1),
        principal_id="a",
    )
    current = seeded._load(await seeded.store.read(sid))
    await AbilityService(seeded).execute(
        sid, command(current.revision, "resolve"), principal_id="a"
    )
    replayed, checks = await verify_commands(
        genesis,
        await played(seeded.store, sid),
        await seeded.store.stream(sid),
        configuration_digest=current.configuration_digest,
        execute=FixtureExecutor(seeded.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert seeded._load(replayed) == seeded._load(await seeded.store.read(sid))
    for name, value in provenance(Path(__file__).resolve().parents[1], case).items():
        record_property(name, value)


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind", ["burning-malediction", "mind-reading"])
async def test_historical_raw_ability_producer_retains_cap_and_seeded_reexecution(
    tmp_path: Path, backend: str, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wayfarer.orchestration import abilities

    cid, play, genesis = await prepare(tmp_path, backend, kind, 5, "unliving")

    async def historical(*_args: object) -> bool:
        return False

    with monkeypatch.context() as patch:
        patch.setattr(abilities, "capture_scope", historical)
        await AbilityService(play).execute(cid, command(), principal_id="a")
        state = play._load(await play.store.read(cid))
        await play.execute(
            cid,
            Wait(id="wait", actor_id="a", expected_revision=state.revision, ticks=1),
            principal_id="a",
        )
        state = play._load(await play.store.read(cid))
        response = command(state.revision, "resolve")
        result = await AbilityService(play).execute(cid, response, principal_id="a")
    state = play._load(await play.store.read(cid))
    event = next(e for e in state.resources.events if e.id == internal_id(response.id))
    assert AbilityEvent.model_validate_json(event.kind).checks[0].effective_target == 16
    final = await play.store.read(cid)
    assert await AbilityService(play).execute(cid, response, principal_id="a") == result
    assert await play.store.read(cid) == final
    replayed, checks = await verify_commands(
        genesis,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert play._load(replayed) == state
    assert final == await play.store.replay(cid)


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("structure", ["homogenous", "diffuse"])
async def test_unknown_low_iq_ability_target_refuses_before_entropy_or_cost(
    tmp_path: Path, backend: str, structure: Literal["homogenous", "diffuse"]
) -> None:
    from wayfarer.errors import ValidationError

    cid, play, _ = await prepare(tmp_path, backend, "burning-malediction", 5, structure)
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="known living or sapient"):
        await AbilityService(play).execute(cid, command(), principal_id="a")
    assert await play.store.read(cid) == before and play.rng.exhausted()


def test_ability_scope_generation_rejects_tampered_or_unknown_inputs() -> None:
    import json

    from wayfarer.errors import ValidationError
    from wayfarer.orchestration.ability_rule16_generations import KEY, payload, version
    from wayfarer.persistence.events import CommandInput, payload_digest

    original = payload("a", command(), True)
    assert version(CommandInput(payload_digest({"input": original}), original))
    with pytest.raises(ValidationError, match="digest"):
        version(CommandInput("0" * 64, original))
    for invalid in (True, "1", 0, 2):
        raw = json.dumps(json.loads(original) | {KEY: invalid})
        with pytest.raises(ValidationError, match="generation"):
            version(CommandInput(payload_digest({"input": raw}), raw))


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("structure", ["homogenous", "diffuse"])
async def test_legacy_pending_ability_can_cancel_unknown_target_without_new_roll(
    tmp_path: Path,
    backend: str,
    structure: Literal["homogenous", "diffuse"],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wayfarer.orchestration import abilities

    cid, play, _ = await prepare(tmp_path, backend, "burning-malediction", 5, structure)

    async def historical(*_args: object) -> bool:
        return False

    with monkeypatch.context() as patch:
        patch.setattr(abilities, "capture_scope", historical)
        await AbilityService(play).execute(cid, command(), principal_id="a")
    state = play._load(await play.store.read(cid))
    pools = state.resources.pools
    play.rng = RecordedDice(())
    result = await AbilityService(play).execute(
        cid, command(state.revision, "cancel"), principal_id="a"
    )
    assert result.outcome == "cancelled" and play.rng.exhausted()
    assert play._load(await play.store.read(cid)).resources.pools == pools


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("principal", ["alice", "player:alice"])
async def test_delegated_principal_fresh_and_legacy_ability_reexecutes_exact_producer(
    tmp_path: Path, backend: str, legacy: bool, principal: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wayfarer.orchestration import abilities
    from wayfarer.orchestration.ability_rule16_generations import principal as recorded_principal

    cid, play, genesis = await prepare(
        tmp_path, backend, "burning-malediction", 5, "unliving", principal=principal
    )

    async def historical(*_args: object) -> bool:
        return False

    with monkeypatch.context() as patch:
        if legacy:
            patch.setattr(abilities, "capture_scope", historical)
        await AbilityService(play).execute(cid, command(), principal_id=principal)
        state = play._load(await play.store.read(cid))
        await play.execute(
            cid,
            Wait(id="wait", actor_id="a", expected_revision=state.revision, ticks=1),
            principal_id="a",
        )
        state = play._load(await play.store.read(cid))
        response = command(state.revision, "resolve")
        result = await AbilityService(play).execute(cid, response, principal_id=principal)
    records = await played(play.store, cid)
    ability_records = [r for r in records if r.event.get("outcome") == "ability"]
    assert len(ability_records) == 2
    assert all(r.actor_id == "a" and recorded_principal(r) == principal for r in ability_records)
    state = play._load(await play.store.read(cid))
    event = next(e for e in state.resources.events if e.id == internal_id(response.id))
    assert AbilityEvent.model_validate_json(event.kind).checks[0].effective_target == (
        16 if legacy else 18
    )
    final = await play.store.read(cid)
    assert await AbilityService(play).execute(cid, response, principal_id=principal) == result
    assert await play.store.read(cid) == final and await played(play.store, cid) == records
    replayed, checks = await verify_commands(
        genesis,
        records,
        await play.store.stream(cid),
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert play._load(replayed) == state and final == await play.store.replay(cid)
