"""Registered Basic Set purchases and persistent attribute/secondary advancement.

The literal prices/defaults below exercise the already reviewed B14-17 rules. Native advancement uses trusted injury/fatigue reducer genesis, not an
additional public creation/combat journey. It retains authentic accepted inputs.
"""

import json
from dataclasses import replace
from decimal import ROUND_DOWN, ROUND_UP, Decimal, localcontext
from pathlib import Path

import pytest
from pydantic import ValidationError as SchemaError
from support.runtime import build_play, seed_campaign
from test_actions import campaign, world
from test_statistics import gurps_draft

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.character import statistics
from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.character.statistics import Encumbrance, PrimaryAttributes
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.profiles import DEFAULT_REGISTRY, GURPS_PROPAGANDA_PROFILE
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup
from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Owner, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.advancement import (
    AdvanceCharacter,
    AdvancementService,
    GrantPoints,
)
from wayfarer.orchestration.clock import CommandInstant
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.replay import execute_recorded
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.events import payload_digest
from wayfarer.persistence.replay import verify_commands

BASIC = "gurps-basic-set-4e-2004"
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("precision", [1, 2, 3])
@pytest.mark.parametrize("rounding", [ROUND_DOWN, ROUND_UP])
def test_reviewed_b017_load_rows_keep_exact_values_under_ambient_decimal_context(
    precision: int, rounding: str
) -> None:
    data = json.loads((ROOT / "tests/fixtures/gurps/conformance.json").read_text())
    rows = [c for c in data["cases"] if c["id"].startswith("basic-b017-load-table-st-")]
    assert len(rows) == 20
    with localcontext() as context:
        context.prec = precision
        context.rounding = rounding
        context.Emax = 1
        for case in rows:
            lift = statistics.basic_lift(BASIC, case["input"]["ST"])
            assert str(lift) == case["expected"]["basic_lift"]
            thresholds = statistics.encumbrance_thresholds(BASIC, lift)
            assert thresholds == tuple(Decimal(v) for v in case["expected"]["thresholds"])
        assert (context.prec, context.rounding, context.Emax) == (precision, rounding, 1)


@pytest.mark.parametrize("precision", [1, 2, 3])
@pytest.mark.parametrize("rounding", [ROUND_DOWN, ROUND_UP])
def test_source_quarters_and_floored_move_do_not_inherit_decimal_precision(
    precision: int, rounding: str
) -> None:
    with localcontext() as context:
        context.prec = precision
        context.rounding = rounding
        context.Emax = 1
        result = statistics.compile_statistics(
            BASIC, PrimaryAttributes(st=10, dx=12, iq=10, ht=13), revision=2
        )
        assert str(result.basic_speed) == "6.25"
        assert result.basic_move == 6 and result.dodge == 9 and result.costs.total == 70
        assert statistics.encumbered_move(BASIC, 12, Encumbrance.LIGHT) == 9
        assert statistics.encumbered_move(BASIC, 12, Encumbrance.HEAVY) == 4
        assert (context.prec, context.rounding, context.Emax) == (precision, rounding, 1)


# Source prices: ST/HT10, DX/IQ20 per level from10. Secondary defaults are
# independent source expectations rather than results read from the compiler.
PRIMARY_ROWS = (
    ("st", 9, -10, 9, 10, 10, "5", 5, 8),
    ("st", 11, 10, 11, 10, 10, "5", 5, 8),
    ("dx", 9, -20, 10, 10, 10, "4.75", 4, 7),
    ("dx", 11, 20, 10, 10, 10, "5.25", 5, 8),
    ("iq", 9, -20, 10, 9, 10, "5", 5, 8),
    ("iq", 11, 20, 10, 11, 10, "5", 5, 8),
    ("ht", 9, -10, 10, 10, 9, "4.75", 4, 7),
    ("ht", 11, 10, 10, 10, 11, "5.25", 5, 8),
)


def primary_cases() -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "id": f"basic-primary-registered-{attribute}-{level}",
            "capability_id": "gurps.character.primary_attributes",
            "profile": BASIC,
            "source_id": "sjg:basic-set-characters-4e-2004",
            "reference": "B14-B17, Basic Attributes and Secondary Characteristics",
            "operation": "registered_primary_compilation",
            "input": {"attribute": attribute, "level": level},
            "expected": {
                "point_cost": cost,
                "hp": hp,
                "will": mental,
                "per": mental,
                "fp": fp,
                "basic_speed": speed,
                "basic_move": move,
                "dodge": dodge,
            },
            "rounding": "floor",
            "prototype_divergence": None,
            "provenance": "Literal registered-compiler prices and defaults from the existing reviewed B14-17 rules; no fresh PDF review claimed.",
        }
        for attribute, level, cost, hp, mental, fp, speed, move, dodge in PRIMARY_ROWS
    )


def compiler() -> CharacterCompiler:
    selected = GURPS_PROPAGANDA_PROFILE
    assert selected.conformance_profile_id == BASIC
    assert DEFAULT_REGISTRY.resolve(selected.reference).digest == selected.digest
    return CharacterCompiler(
        selected.catalog,
        selected.rules,
        selected.policy,
        statistics_profile=selected.conformance_profile_id,
    )


@pytest.mark.parametrize("case", primary_cases(), ids=lambda c: str(c["id"]))
def test_registered_basic_primary_prices_and_dependents(
    case: dict[str, object],
) -> None:
    raw, wanted = case["input"], case["expected"]
    assert isinstance(raw, dict) and isinstance(wanted, dict)
    identifier = "attribute:" + str(raw["attribute"])
    draft = gurps_draft()
    draft = draft.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": raw["level"]})
                if p.definition_id == identifier
                else p
                for p in draft.purchases
            )
        }
    )
    engine = compiler()
    result = engine.compile(draft)
    assert result.legal and result.build is not None, result.diagnostics
    build = result.build
    assert build.statistics is not None
    assert build.spent == wanted["point_cost"]
    assert (
        next(p.cost for p in build.purchases if p.definition_id == identifier)
        == wanted["point_cost"]
    )
    for target in ("hp", "will", "per", "fp", "basic_move", "dodge"):
        assert getattr(build.statistics, target) == wanted[target]
    assert str(build.statistics.basic_speed) == wanted["basic_speed"]
    assert engine.compile(draft).build == build


def test_registered_basic_independent_secondaries_do_not_reprice_primary_attributes() -> None:
    draft = gurps_draft(
        Purchase(definition_id="secondary:hp", amount=12),
        Purchase(definition_id="secondary:will", amount=10),
        Purchase(definition_id="secondary:per", amount=12),
        Purchase(definition_id="secondary:fp", amount=10),
        Purchase(definition_id="secondary:basic-speed", amount=25),
        Purchase(definition_id="secondary:basic-move", amount=8),
        st_level=11,
    )
    primary = {"attribute:dx": 11, "attribute:iq": 11, "attribute:ht": 11}
    draft = draft.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": primary[p.definition_id]})
                if p.definition_id in primary
                else p
                for p in draft.purchases
            )
        }
    )
    result = compiler().compile(draft)
    assert result.legal and result.build is not None, result.diagnostics
    build = result.build
    assert build.spent == 84
    assert {p.definition_id: p.cost for p in build.purchases} == {
        "attribute:st": 10,
        "attribute:dx": 20,
        "attribute:iq": 20,
        "attribute:ht": 10,
        "secondary:hp": 2,
        "secondary:will": -5,
        "secondary:per": 5,
        "secondary:fp": -3,
        "secondary:basic-speed": 15,
        "secondary:basic-move": 10,
    }
    stats = build.statistics
    assert stats is not None and stats.advisories == ()
    assert (stats.hp, stats.will, stats.per, stats.fp) == (12, 10, 12, 10)
    assert str(stats.basic_speed) == "6.25" and stats.basic_move == 8 and stats.dodge == 9
    assert str(stats.basic_lift) == "24" and (str(stats.thrust), str(stats.swing)) == (
        "1d-1",
        "1d+1",
    )


def registered_engine() -> ActionEngine:
    selected = GURPS_PROPAGANDA_PROFILE
    return ActionEngine(
        PowerReviewer(
            compiler(), PowerPolicy(id="basic-attribute-advancement", version=1), frozenset({"gm"})
        ),
        ResourceEngine(world(), selected.catalog, selected.rules, selected.policy, ()),
        ActionRules(id="basic-attribute-advancement", version=1),
    )


async def wounded_play(path: Path, backend: str) -> tuple[str, PlayService]:
    engine = registered_engine()
    play = build_play(path, engine, backend=backend)
    initial = campaign(engine)
    state = play.initial_state(
        initial,
        world(),
        ResourceState(owners=(Owner(actor_id="a", capacity=100000),)),
        (ActorSetup(actor_id="a", proposal=CharacterProposal(draft=gurps_draft())),),
    )
    injured, _ = apply_injury(
        state.resources,
        Wound(
            id="source-wound",
            actor_id="a",
            expected_revision=0,
            basic_damage=4,
            resistance=0,
            damage_type="cr",
        ),
        ht=10,
        rng=RecordedDice(()),
        system=True,
    )
    depleted, _ = apply_fatigue(
        injured,
        FatigueCost(id="source-fatigue", actor_id="a", expected_revision=1, amount=3),
        ht=10,
        rng=RecordedDice(()),
        system=True,
    )
    state = state.model_copy(update={"revision": depleted.revision, "resources": depleted})
    play.commit(initial, state)
    await seed_campaign(play.store, initial)
    return initial["id"], play


async def verify_recorded_advancement(play: PlayService, cid: str, path: Path) -> None:
    history = await play.store.history(cid)
    initial, commands = history[0].state_after, history[1:]
    ids = {c.command_id for c in commands}

    def no_seed() -> str:
        raise AssertionError("Original-seed replay must not capture new entropy")

    def no_instant() -> CommandInstant:
        raise AssertionError("Original-seed replay must not capture a new instant")

    actual, checks = await verify_commands(
        initial,
        commands,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, path, instants=no_instant, seeds=no_seed),
    )
    assert len(checks) == len(commands) == 2
    assert all(c.folded and c.reexecuted and c.reason is None for c in checks)
    assert actual == await play.store.read(cid) == await play.store.replay(cid)


async def replay_refusals(play: PlayService, cid: str, path: Path) -> None:
    """Counterfactual inputs use verification copies, never authored gameplay state."""
    history = await play.store.history(cid)
    mutations = (
        "principal",
        "request-id",
        "revision",
        "input-digest",
        "raw-kind",
        "unknown-family",
        "non-object",
        "extra-command-field",
        "string-command",
    )

    def refuse_seed() -> str:
        raise AssertionError("Rejected replay must not request fresh entropy")

    def refuse_instant() -> CommandInstant:
        raise AssertionError("Rejected replay must not request a fresh clock")

    for original, before_row in zip(history[1:], history, strict=False):
        assert original.command_input is not None
        payload = json.loads(original.command_input)
        for mutation in mutations:
            forged = original
            if mutation == "principal":
                forged = replace(original, actor_id="a" if original.actor_id == "gm" else "gm")
            elif mutation == "request-id":
                forged = replace(original, command_id="changed")
            elif mutation == "revision":
                forged = replace(original, expected_revision=original.expected_revision + 1)
            elif mutation == "input-digest":
                forged = replace(original, command_input=original.command_input + " ")
            else:
                changed = json.loads(original.command_input)
                if mutation == "raw-kind":
                    changed = {"kind": payload["operation"], **payload["command"]}
                elif mutation == "unknown-family":
                    changed["operation"] = "unsupported-advancement-family"
                elif mutation == "non-object":
                    changed = ["not-an-operation"]
                elif mutation == "extra-command-field":
                    changed["command"]["invented_authority"] = True
                elif mutation == "string-command":
                    changed["command"] = json.dumps(changed["command"])
                text = json.dumps(changed, sort_keys=True, separators=(",", ":"))
                # A matching checksum alone never makes an unsupported envelope lawful.
                forged = replace(
                    original, command_input=text, payload_hash=payload_digest({"input": text})
                )
            store = AsyncSQLiteStore(path / f"{original.command_id}-{mutation}.sqlite")
            await seed_campaign(store, before_row.state_after)
            clone = PlayService(store, play.engine, seeds=refuse_seed, instants=refuse_instant)
            before = await store.read(cid)
            previous_history, previous_stream = await store.history(cid), await store.stream(cid)
            with pytest.raises((ValidationError, SchemaError)):
                await execute_recorded(clone, forged)
            assert await store.read(cid) == before
            assert await store.history(cid) == previous_history
            assert await store.stream(cid) == previous_stream
            assert await store.command_input(cid, forged.command_id) is None


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_registered_attribute_advancement_retains_injury_fatigue_and_exact_retries(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await wounded_play(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    old = compiler().compile(state.actors[0].proposal.draft).build
    assert old is not None
    assert {p.id: (p.current, p.maximum) for p in state.resources.pools} == {
        "hp:a": (6, 10),
        "fp:a": (7, 10),
    }
    service = AdvancementService(play)
    grant = GrantPoints(
        id="earned",
        actor_id="gm",
        target_actor_id="a",
        expected_revision=state.revision,
        points=42,
        reason="Source-backed attributes and independent HP purchase",
    )
    earned = await service.grant(cid, grant, principal_id="gm")
    draft = gurps_draft(Purchase(definition_id="secondary:hp", amount=13), st_level=12)
    draft = draft.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": 12}) if p.definition_id == "attribute:ht" else p
                for p in draft.purchases
            )
        }
    )
    before = await play.store.read(cid)
    purchase = AdvanceCharacter(
        id="attribute-purchase",
        actor_id="a",
        expected_revision=before["revision"],
        expected_build_revision=old.revision,
        draft=draft,
        reason="Increase ST, HT and HP",
    )
    preview = await service.preview(cid, purchase, principal_id="a")
    assert preview.points_delta == preview.points_available == 42
    accepted = await service.advance(cid, purchase, principal_id="a")
    assert accepted.points == -42
    saved = await play.store.read(cid)
    final = play._load(saved)
    assert {p.id: (p.current, p.maximum) for p in final.resources.pools} == {
        "hp:a": (9, 13),
        "fp:a": (9, 12),
    }
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    retained = {c.id: await play.store.command_input(cid, c.id) for c in (grant, purchase)}

    def refuse_seed() -> str:
        raise AssertionError("Accepted advancement retry must not request new entropy")

    def refuse_instant() -> CommandInstant:
        raise AssertionError("Accepted advancement retry must not request a new clock instant")

    restarted = build_play(
        tmp_path, play.engine, backend=backend, seeds=refuse_seed, instants=refuse_instant
    )
    again = AdvancementService(restarted)
    assert await again.grant(cid, grant, principal_id="gm") == earned
    assert await again.advance(cid, purchase, principal_id="a") == accepted
    with pytest.raises(ConflictError):
        await again.advance(
            cid, purchase.model_copy(update={"reason": "changed"}), principal_id="a"
        )
    with pytest.raises(ValidationError):
        await again.grant(
            cid, grant.model_copy(update={"id": "forged", "actor_id": "a"}), principal_id="a"
        )
    assert await play.store.read(cid) == saved
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    assert {c.id: await play.store.command_input(cid, c.id) for c in (grant, purchase)} == retained
    assert await play.store.command_input(cid, "forged") is None
    await verify_recorded_advancement(play, cid, tmp_path / "original-seed")
    await replay_refusals(play, cid, tmp_path / "replay-refusals")


async def test_existing_prototype_point_grant_and_purchase_keep_original_seed_replay(
    tmp_path: Path,
) -> None:
    from test_advancement import setup, upgraded

    cid, play, service = await setup(tmp_path)
    state = play._load(await play.store.read(cid))
    build = play.engine.reviewer.review(state.actors[0].proposal).compilation.build
    assert build is not None and build.statistics is None
    await service.grant(
        cid,
        GrantPoints(
            id="award",
            actor_id="gm",
            target_actor_id="a",
            expected_revision=0,
            points=4,
            reason="Existing prototype advancement",
        ),
        principal_id="gm",
    )
    accepted = await service.advance(cid, upgraded(1, build.revision), principal_id="a")
    assert accepted.points == -4
    await verify_recorded_advancement(play, cid, tmp_path / "prototype-original-seed")
