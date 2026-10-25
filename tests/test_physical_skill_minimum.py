"""B345's voluntary minimum skill3 precedes physical skill and health entropy."""

import json
import secrets
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, played, seed_campaign
from test_actions import campaign, world
from test_condition_attribute_sources import produced
from test_statistics import gurps_draft, profile_compiler, profile_package

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.skills.mundane.physical_outdoors import definitions
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup
from wayfarer.engine.simulation.campaign.default_knowledge import AdoptDefaultKnowledge
from wayfarer.engine.simulation.health.condition_generations import condition_generation
from wayfarer.engine.simulation.movement.physical import PhysicalContext, reduce_physical
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Owner, ResourceState
from wayfarer.errors import ValidationError
from wayfarer.orchestration.default_knowledge import DefaultKnowledgeService
from wayfarer.orchestration.physical import PhysicalCommand, PhysicalRoute, PhysicalService
from wayfarer.orchestration.physical_generations import decode, encode
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.replay_inputs import replay_inputs
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.events import payload_digest
from wayfarer.persistence.replay import verify_commands

Kind = Literal["climb", "lift", "hike", "swim"]
PROFILE = "gurps-basic-set-4e-2004"


async def fixture(
    path: Path,
    backend: str = "sqlite",
    *,
    dx: int = 10,
    ht: int = 10,
    lifting: bool = False,
    exhausted: bool = False,
    known_defaults: tuple[str, ...] = (),
) -> tuple[Campaign, PlayService]:
    pinned = {"skill:lifting", *known_defaults}
    package = profile_package(PROFILE, *(d for d in definitions() if d.id in pinned))
    compiler = profile_compiler(PROFILE, package=package)
    environment = world()
    engine = ActionEngine(
        PowerReviewer(compiler, PowerPolicy(id="test", version=1), frozenset({"gm"})),
        ResourceEngine(environment, RulesCatalog((package,)), compiler.rules, compiler.policy, ()),
        ActionRules(id="physical-minimum", version=1, maximum_wait=10000, fatigue_cost=0),
    )
    purchases = (Purchase(definition_id="skill:lifting", amount=1),) if lifting else ()
    draft = gurps_draft(*purchases)
    draft = draft.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": dx if p.definition_id == "attribute:dx" else ht})
                if p.definition_id in ("attribute:dx", "attribute:ht")
                else p
                for p in draft.purchases
            )
        }
    )
    play = build_play(path, engine, backend=backend, seeds=lambda: f"{14:064x}")
    initial = campaign(engine)
    genesis = play.initial_state(
        initial,
        environment,
        ResourceState(owners=(Owner(actor_id="a", capacity=100),)),
        (ActorSetup(actor_id="a", proposal=CharacterProposal(draft=draft)),),
    )
    if exhausted:
        genesis = genesis.model_copy(
            update={
                "resources": genesis.resources.model_copy(
                    update={
                        "pools": tuple(
                            p.model_copy(update={"current": 0}) for p in genesis.resources.pools
                        )
                    }
                )
            }
        )
        engine.validate(genesis)
    initial["play_json"] = genesis.model_dump_json()
    await seed_campaign(play.store, initial)
    if known_defaults:
        await DefaultKnowledgeService(play).execute(
            initial["id"],
            AdoptDefaultKnowledge(
                id="physical-society-knowledge",
                actor_id="gm",
                expected_revision=0,
                performer_id="a",
                known_skill_ids=known_defaults,
                reason="The GM confirms the explicitly selected physical skills are known in this society",
            ),
            principal_id="gm",
        )
    return initial, play


def route(kind: Kind, *, intentional: bool = True) -> PhysicalRoute:
    return PhysicalRoute(
        id="route",
        scene_id="dock",
        kind=kind,
        surface="wall",
        distance=Decimal(3 if kind == "climb" else 1),
        pounds=Decimal(160),
        seconds=3600 if kind == "hike" else 1,
        intentional=intentional,
    )


def command(kind: Kind, *, revision: int = 0) -> PhysicalCommand:
    return PhysicalCommand(
        id="physical", actor_id="a", expected_revision=revision, kind=kind, route_id="route"
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "kind,dx,ht,lifting",
    [
        ("climb", 10, 10, False),
        ("lift", 10, 3, True),
        ("hike", 10, 7, False),
        ("swim", 10, 3, False),
    ],
)
async def test_voluntary_effective_two_refuses_before_health_or_skill_dice(
    tmp_path: Path, backend: str, kind: Kind, dx: int, ht: int, lifting: bool
) -> None:
    known_defaults = {
        "climb": ("skill:climbing",),
        "hike": ("skill:hiking",),
        "swim": ("skill:swimming",),
    }.get(kind, ())
    initial, play = await fixture(
        tmp_path,
        backend,
        dx=dx,
        ht=ht,
        lifting=lifting,
        exhausted=True,
        known_defaults=known_defaults,
    )
    before = await play.store.read(initial["id"])
    play.rng = dice = RecordedDice(())
    with pytest.raises(ValidationError, match="effective skill at least 3"):
        await PhysicalService(play, lambda *_: route(kind)).execute(
            initial["id"],
            command(kind, revision=play._load(await play.store.read(initial["id"])).revision),
            principal_id="a",
        )
    assert dice.exhausted() and before == await play.store.read(initial["id"])


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "kind,dx,ht,lifting,seconds",
    [
        ("climb", 11, 10, False, 45),
        ("lift", 10, 4, True, 4),
        ("hike", 10, 8, False, 3600),
        ("swim", 10, 4, False, 1),
    ],
)
async def test_exact_skill_three_changes_durable_physical_outcome_and_replays(
    tmp_path: Path, backend: str, kind: Kind, dx: int, ht: int, lifting: bool, seconds: int
) -> None:
    known_defaults = {
        "climb": ("skill:climbing",),
        "hike": ("skill:hiking",),
        "swim": ("skill:swimming",),
    }.get(kind, ())
    initial, play = await fixture(
        tmp_path, backend, dx=dx, ht=ht, lifting=lifting, known_defaults=known_defaults
    )
    cid = initial["id"]
    first = await PhysicalService(play, lambda *_: route(kind)).execute(
        cid,
        command(kind, revision=play._load(await play.store.read(initial["id"])).revision),
        principal_id="a",
    )
    assert first.checks and first.checks[0].effective_target == 3 and first.checks[0].total == 4
    assert first.succeeded and first.seconds == seconds and Decimal(first.progress) > 0
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == seconds
    assert first.fp_lost == (1 if kind == "hike" else 0)
    stored = await play.store.command_input(cid, "physical")
    assert stored and stored.text
    payload = replay_payload(stored.text)
    assert isinstance(payload, dict) and payload["physical_skill_minimum_generation"] == 1
    final, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert final == await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_historical_rest_generation_without_minimum_keeps_old_lifting_result(
    tmp_path: Path, backend: str
) -> None:
    initial, play = await fixture(tmp_path, backend, ht=3, lifting=True)
    cid = initial["id"]
    lift = command("lift")
    service = PhysicalService(play, lambda *_: route("lift"))
    old = await submit(
        play, cid, service.plan(play, lift, principal_id="a", route=route("lift")), principal_id="a"
    )
    assert old.succeeded and old.checks[0].effective_target == 2
    stored = await play.store.command_input(cid, lift.id)
    assert stored and stored.text
    payload = replay_payload(stored.text)
    assert isinstance(payload, dict) and payload["enchanting_rest_generation"] == 1
    assert "physical_skill_minimum_generation" not in payload
    assert "use_lifting" not in json.loads(payload["physical_route"])
    assert "use_lifting" not in json.loads(old.route_binding)

    def unavailable(*_: object) -> PhysicalRoute:
        raise AssertionError("historical retry must retain its captured route")

    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await PhysicalService(restarted, unavailable).execute(cid, lift, principal_id="a") == old
    final, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert final == await play.store.read(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_involuntary_swimming_and_mandatory_fatigue_keep_low_ht_rolls(
    tmp_path: Path, backend: str
) -> None:
    initial, play = await fixture(tmp_path, backend, ht=1, known_defaults=("skill:swimming",))
    authored = replace(route("swim", intentional=False), seconds=60, distance=Decimal(60))
    play.rng = RecordedDice((1, 1, 1, 1, 1, 1))
    result = await PhysicalService(play, lambda *_: authored).execute(
        initial["id"],
        command("swim", revision=play._load(await play.store.read(initial["id"])).revision),
        principal_id="a",
    )
    assert result.succeeded and result.completed and result.seconds == 60
    assert [c.effective_target for c in result.checks] == [1, 1]
    assert result.fp_lost == 0


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("current", [False, True])
async def test_drowsy_condition_generation_controls_exact_native_minimum(
    tmp_path: Path, backend: str, current: bool
) -> None:
    initial, play = await fixture(tmp_path, backend, dx=12, known_defaults=("skill:climbing",))
    before = play._load(await play.store.read(initial["id"]))
    toxin_resources, _ = produced("toxin-drowsy")
    before = before.model_copy(
        update={"resources": before.resources.model_copy(update={"toxins": toxin_resources.toxins})}
    )
    play.rng = dice = RecordedDice(()) if current else RecordedDice((1, 1, 1))
    context = PhysicalContext(
        play.rules_context, lambda *_: route("climb"), dice, skill_minimum=True
    )
    with condition_generation(current):
        if current:
            with pytest.raises(ValidationError, match="at least 3"):
                reduce_physical(before, command("climb", revision=before.revision), context)
            assert dice.exhausted()
        else:
            updated, result = reduce_physical(
                before, command("climb", revision=before.revision), context
            )
            assert result.checks[0].effective_target == 4 and result.succeeded
            assert updated.resources.game_time == 45


@pytest.mark.parametrize("generation", [2, 0, True, False])
async def test_recorded_minimum_generation_is_strict(
    tmp_path: Path, generation: int | bool
) -> None:
    initial, play = await fixture(tmp_path, ht=4, lifting=True)
    cid = initial["id"]
    await PhysicalService(play, lambda *_: route("lift")).execute(
        cid, command("lift"), principal_id="a"
    )
    record = next(r for r in await play.store.history(cid) if r.command_id == "physical")
    assert record.command_input
    payload = replay_payload(record.command_input)
    assert isinstance(payload, dict)
    payload["physical_skill_minimum_generation"] = generation
    encoded = json.dumps(payload)
    before = await play.store.read(cid)
    with replay_inputs(
        replace(record, command_input=encoded, payload_hash=payload_digest({"input": encoded}))
    ):
        with pytest.raises(ValidationError, match="minimum-skill generation"):
            await PhysicalService(play, lambda *_: route("lift")).execute(
                cid, command("lift"), principal_id="a"
            )
    assert before == await play.store.read(cid)


async def test_minimum_plan_requires_a_versioned_route_capture(tmp_path: Path) -> None:
    _, play = await fixture(tmp_path)
    service = PhysicalService(play, lambda *_: route("lift"))
    with pytest.raises(ValidationError, match="captured route and generation"):
        service.plan(play, command("lift"), principal_id="a", skill_minimum=True)
    with pytest.raises(ValidationError, match="captured route and generation"):
        service.plan(
            play, command("lift"), principal_id="a", route=route("lift"), enchanting_rest=False
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_private_minimum_plan_retry_retains_its_actual_rest_scope(
    tmp_path: Path, backend: str
) -> None:
    initial, play = await fixture(tmp_path, backend, dx=11, known_defaults=("skill:climbing",))
    cid = initial["id"]
    value = command("climb", revision=play._load(await play.store.read(cid)).revision)
    service = PhysicalService(play, lambda *_: route("climb"))
    plan = service.plan(play, value, principal_id="a", route=route("climb"), skill_minimum=True)
    payload = json.loads(plan.payload)
    assert payload["physical_skill_minimum_generation"] == 1
    assert "enchanting_rest_generation" not in payload
    result = await submit(play, cid, plan, principal_id="a")
    play.rng = RecordedDice(())
    assert await service.execute(cid, value, principal_id="a") == result


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_trained_low_skill_actor_can_choose_unassisted_base_lift_and_replay(
    tmp_path: Path, backend: str
) -> None:
    initial, play = await fixture(tmp_path, backend, ht=3, lifting=True)
    cid = initial["id"]
    base_route = replace(route("lift"), id="base-lift", use_lifting=False)
    base_command = command("lift").model_copy(update={"id": "base", "route_id": base_route.id})
    before = play._load(initial)
    play.rng = no_dice = RecordedDice(())
    _, unassisted = reduce_physical(
        before,
        base_command,
        PhysicalContext(play.rules_context, lambda *_: base_route, no_dice, skill_minimum=True),
    )
    assert no_dice.exhausted() and not unassisted.checks
    play.rng = secrets
    # The committed seed is available for full replay, but neither the
    # optional Lifting check nor any other dice are needed by this healthy actor.
    result = await PhysicalService(play, lambda *_: base_route).execute(
        cid, base_command, principal_id="a"
    )
    assert result == unassisted
    assert result.succeeded and result.completed and not result.checks
    assert result.capacity == "160" and result.seconds == 4 and result.progress == "1"
    assert result.fp_lost == 0 and result.injury == 0
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == 4
    assert [(p.id, p.current) for p in state.resources.pools] == [
        (p.id, p.current) for p in before.resources.pools
    ]
    stored = await play.store.command_input(cid, base_command.id)
    assert stored and stored.text
    payload = replay_payload(stored.text)
    assert isinstance(payload, dict)
    assert json.loads(payload["physical_route"])["use_lifting"] is False
    assert json.loads(result.route_binding)["use_lifting"] is False
    # The same actor still cannot make the illegal optional skill2 attempt.
    assisted_route = replace(route("lift"), id="assisted-lift")
    assisted_command = command("lift").model_copy(
        update={
            "id": "assisted",
            "expected_revision": state.revision,
            "route_id": assisted_route.id,
        }
    )
    before_assisted = await play.store.read(cid)
    play.rng = dice = RecordedDice(())
    with pytest.raises(ValidationError, match="at least 3"):
        await PhysicalService(play, lambda *_: assisted_route).execute(
            cid, assisted_command, principal_id="a"
        )
    assert dice.exhausted() and before_assisted == await play.store.read(cid)

    def unavailable(*_: object) -> PhysicalRoute:
        raise AssertionError("base-lift retry must retain its trusted unassisted choice")

    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert (
        await PhysicalService(restarted, unavailable).execute(cid, base_command, principal_id="a")
        == result
    )
    final, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert final == await play.store.read(cid) == await play.store.replay(cid)


def test_default_lifting_choice_preserves_legacy_route_bytes() -> None:
    authored = route("lift")
    legacy = asdict(authored)
    legacy.pop("use_lifting")
    legacy_json = json.dumps(legacy, sort_keys=True, default=str)
    assert encode(authored) == legacy_json
    assert decode(legacy_json) == authored and decode(legacy_json).use_lifting is True
    assert decode(encode(replace(authored, use_lifting=False))).use_lifting is False


@pytest.mark.parametrize("invalid", [0, 1, "false", None])
def test_captured_lifting_choice_requires_a_strict_boolean(invalid: object) -> None:
    value = asdict(route("lift"))
    value["use_lifting"] = invalid
    with pytest.raises(ValidationError, match="captured physical route"):
        decode(json.dumps(value, default=str))
