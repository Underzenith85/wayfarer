"""B239 named known subjects, current sight/range and immutable private generation."""

import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from test_great_haste_combat_concentration import prepare_combat
from test_power_maintenance_lifecycle import change

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.hex_geometry import Cell, Hex, HexBattlefield
from wayfarer.engine.simulation.magic.great_haste_named import NamedCastGreatHaste, origin
from wayfarer.engine.simulation.magic.great_haste_state import CastGreatHaste
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.engine.world import Fact
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService, TakeCombatTurn
from wayfarer.orchestration.great_haste import GreatHasteService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands


def board(blocked: bool) -> HexBattlefield:
    return HexBattlefield(
        id="dock",
        location_id="dock",
        coordinate_system="hex-axial-v1",
        profile_id="gurps-basic-set-4e-2004",
        baseline_id="gurps-4e-characters-3p-2008+campaigns-4p-2008",
        cells=tuple(
            Cell(position=Hex(q=q, r=r), opaque_height=3 if blocked and q == 1 and r == 0 else 0)
            for q in range(3)
            for r in range(2)
        ),
    )


def command(revision: int, index: int, *, fact: str = "named-subject") -> NamedCastGreatHaste:
    return NamedCastGreatHaste(
        id="named-" + str(index),
        actor_id="a",
        expected_revision=revision,
        operation="start" if index == 0 else "concentrate",
        channel_id="great-haste",
        cast_id="named",
        known_fact_id=fact,
    )


async def prepare_named(tmp_path: Path, backend: str, *, blocked: bool) -> tuple[str, PlayService]:
    cid, play = await prepare_combat(tmp_path, backend, battlefield=board(blocked))

    def knowledge(state: PlayState) -> PlayState:
        target = next(e for e in state.world.entities if e.id == "b")
        world = replace(
            state.world,
            facts=state.world.facts + (Fact("named-subject", "b", "name", target.name),),
        )
        return state.model_copy(update={"world": world.learn("a", "named-subject")})

    await change(cid, play, "named-subject-knowledge", knowledge)
    return cid, play


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("blocked", [False, True])
async def test_named_subject_crosses_barrier_with_actual_unseen_penalty(
    tmp_path: Path, backend: str, blocked: bool
) -> None:
    cid, play = await prepare_named(tmp_path, backend, blocked=blocked)
    service = GreatHasteService(play)
    initial = await play.store.read(cid)
    before = play._load(initial)
    if blocked:
        with pytest.raises(ValidationError, match="currently visible"):
            await service.execute(
                cid,
                CastGreatHaste(
                    id="legacy",
                    actor_id="a",
                    expected_revision=before.revision,
                    operation="start",
                    channel_id="great-haste",
                    cast_id="old",
                ),
                principal_id="alice",
            )
        assert await play.store.read(cid) == initial
    for index in range(2):
        state = play._load(await play.store.read(cid))
        assert (
            await service.execute(cid, command(state.revision, index), principal_id="alice")
        ).outcome == "casting"
    for index in range(2):
        state = play._load(await play.store.read(cid))
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="other-" + str(index),
                actor_id="b",
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="do_nothing",
            ),
            principal_id="b",
        )
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((1, 1, 3))
    result = await service.execute(cid, command(state.revision, 2), principal_id="alice")
    assert result.outcome == "active" and result.energy_spent == 5
    final = play._load(await play.store.read(cid))
    effect = latest(final.resources)["named"]
    assert effect.skill == (5 if blocked else 10)
    assert effect.target_id == "b" and effect.concentration_seconds == 3
    assert final.resources.game_time == 1 and effect.expires_at == 11
    assert origin(final, "named") is not None
    assert final.revision == state.revision + 1


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("fact", ["missing", "promise"])
async def test_unknown_named_subject_rejects_without_writes(
    tmp_path: Path, backend: str, fact: str
) -> None:
    cid, play = await prepare_combat(
        tmp_path, backend, battlefield=board(True), known_subject=False
    )
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="explicit known subject"):
        await GreatHasteService(play).execute(
            cid, command(play._load(before).revision, 0, fact=fact), principal_id="alice"
        )
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_named_retry_restart_binding_and_seeded_reexecution(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare_named(tmp_path, backend, blocked=True)
    initial = await play.store.read(cid)
    play.rng = secrets
    play.seeds = lambda: "00" * 32
    first = command(play._load(initial).revision, 0)
    receipt = await GreatHasteService(play).execute(cid, first, principal_id="alice")
    after = await play.store.read(cid)
    restarted = PlayService(play.store, play.engine, seeds=play.seeds)
    assert await GreatHasteService(restarted).execute(cid, first, principal_id="alice") == receipt
    assert await play.store.read(cid) == after
    with pytest.raises(ConflictError):
        await GreatHasteService(restarted).execute(
            cid, first.model_copy(update={"known_fact_id": "different"}), principal_id="alice"
        )
    state = restarted._load(after)
    with pytest.raises(ConflictError, match="authenticated private carrier"):
        await GreatHasteService(restarted).execute(
            cid,
            CastGreatHaste(
                id="old-continuation",
                actor_id="a",
                expected_revision=state.revision,
                operation="concentrate",
                channel_id="great-haste",
                cast_id="named",
            ),
            principal_id="alice",
        )
    await GreatHasteService(restarted).execute(
        cid, command(state.revision, 1), principal_id="alice"
    )
    for index in range(2):
        state = restarted._load(await play.store.read(cid))
        await CombatService(restarted).execute(
            cid,
            TakeCombatTurn(
                id="other-" + str(index),
                actor_id="b",
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="do_nothing",
            ),
            principal_id="b",
        )
    state = restarted._load(await play.store.read(cid))
    await GreatHasteService(restarted).execute(
        cid, command(state.revision, 2), principal_id="alice"
    )
    records = [
        r
        for r in await play.store.history(cid)
        if r.expected_revision >= initial["revision"] and r.command_id != "setup:seed"
    ]
    identifiers = {r.command_id for r in records}
    final, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in identifiers],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert all(c.folded and c.reexecuted for c in checks)
    from wayfarer.engine.simulation.events import document

    assert document(final) == document(await play.store.read(cid))


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_current_legal_subject_move_refreshes_range_and_sight_at_roll(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare_named(tmp_path, backend, blocked=True)
    for index in range(2):
        state = play._load(await play.store.read(cid))
        await GreatHasteService(play).execute(
            cid, command(state.revision, index), principal_id="alice"
        )
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="move-subject",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="move",
            hex_path=(Hex(q=1, r=1), Hex(q=0, r=1)),
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="finish-subject",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((2, 2, 2))
    receipt = await GreatHasteService(play).execute(
        cid, command(state.revision, 2), principal_id="alice"
    )
    assert receipt.outcome == "active" and receipt.energy_spent == 5
    assert latest(play._load(await play.store.read(cid)).resources)["named"].skill == 11


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("mutation", ["name", "fact", "knowledge", "actor", "fact-selector"])
async def test_accepted_name_binding_rejects_changed_current_state_before_dice(
    tmp_path: Path, backend: str, mutation: str
) -> None:
    cid, play = await prepare_named(tmp_path, backend, blocked=True)
    state = play._load(await play.store.read(cid))
    await GreatHasteService(play).execute(cid, command(state.revision, 0), principal_id="alice")

    def mutate(state: PlayState) -> PlayState:
        world = state.world
        if mutation == "name":
            world = replace(
                world,
                entities=tuple(
                    replace(e, name="Changed") if e.id == "b" else e for e in world.entities
                ),
            )
        elif mutation == "fact":
            world = replace(
                world,
                facts=tuple(
                    replace(f, value="Changed") if f.id == "named-subject" else f
                    for f in world.facts
                ),
            )
        elif mutation == "knowledge":
            world = replace(
                world, knowledge=tuple(k for k in world.knowledge if k != ("a", "named-subject"))
            )
        return state.model_copy(update={"world": world})

    if mutation in ("name", "fact", "knowledge"):
        await change(cid, play, "changed-knowledge", mutate)
    before = await play.store.read(cid)
    cast = command(play._load(before).revision, 1)
    if mutation == "actor":
        cast = cast.model_copy(update={"actor_id": "b"})
    if mutation == "fact-selector":
        cast = cast.model_copy(update={"known_fact_id": "promise"})
    play.rng = RecordedDice(())
    with pytest.raises((ValidationError, ConflictError, AuthorizationError)):
        await GreatHasteService(play).execute(cid, cast, principal_id="alice")
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_arbitrary_known_fact_cannot_authorize_named_subject(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare_named(tmp_path, backend, blocked=True)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="matching subject name"):
        await GreatHasteService(play).execute(
            cid, command(play._load(before).revision, 0, fact="promise"), principal_id="alice"
        )
    assert await play.store.read(cid) == before


def test_named_generation_requires_exact_private_carrier_and_authenticated_envelope() -> None:
    import json

    from pydantic import ValidationError as SchemaError

    from wayfarer.engine.simulation.magic.great_haste_state import ADAPTER
    from wayfarer.orchestration.great_haste_generation import KEY, ORIGINAL, features
    from wayfarer.persistence.events import CommandInput, payload_digest

    named = command(0, 0).model_dump(mode="json")
    with pytest.raises(SchemaError):
        ADAPTER.validate_python(named)
    for generation in (1, 2, 4):
        source = {
            "operation": "great-haste",
            "generation": generation,
            "command": named,
            "principal_id": "alice",
        }
        text = json.dumps({**source, KEY: 1, ORIGINAL: json.dumps(source)})
        with pytest.raises(ValidationError, match="generation"):
            features(CommandInput(payload_digest({"input": text}), text))
    source = {
        "operation": "great-haste",
        "generation": 3,
        "command": named,
        "principal_id": "alice",
    }
    original = json.dumps(source, indent=2)
    text = json.dumps({**source, KEY: 1, ORIGINAL: original})
    assert features(CommandInput(payload_digest({"input": text}), text))
    source["command"] = CastGreatHaste(
        id="old",
        actor_id="a",
        expected_revision=0,
        operation="start",
        channel_id="great-haste",
        cast_id="great",
    ).model_dump(mode="json")
    text = json.dumps({**source, KEY: 1, ORIGINAL: json.dumps(source)})
    with pytest.raises(ValidationError, match="generation"):
        features(CommandInput(payload_digest({"input": text}), text))


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_named_origin_private_genesis_and_cancel_after_knowledge_loss(
    tmp_path: Path, backend: str
) -> None:
    import json

    from test_actions import campaign

    from wayfarer.engine.simulation.magic.great_haste_named import PREFIX
    from wayfarer.engine.simulation.resources import ResourceState
    from wayfarer.orchestration.views import campaign_view

    cid, play = await prepare_named(tmp_path, backend, blocked=True)
    state = play._load(await play.store.read(cid))
    await GreatHasteService(play).execute(cid, command(state.revision, 0), principal_id="alice")
    state = play._load(await play.store.read(cid))
    witness = next(e for e in state.resources.events if e.id.startswith(PREFIX))
    for member in state.members:
        assert PREFIX not in json.dumps(campaign_view(state, member, play.engine.rules.combat))
    with pytest.raises(ValidationError, match="supernatural execution receipts"):
        play.initial_state(campaign(play.engine), state.world, ResourceState(events=(witness,)), ())
    await change(
        cid,
        play,
        "lose-named-knowledge",
        lambda s: s.model_copy(
            update={
                "world": replace(
                    s.world,
                    knowledge=tuple(k for k in s.world.knowledge if k != ("a", "named-subject")),
                )
            }
        ),
    )
    state = play._load(await play.store.read(cid))
    cancel = command(state.revision, 1).model_copy(
        update={"id": "cancel-named", "operation": "cancel"}
    )
    receipt = await GreatHasteService(play).execute(cid, cancel, principal_id="alice")
    assert receipt.outcome == "cancelled"
    after = await play.store.read(cid)
    assert await GreatHasteService(play).execute(cid, cancel, principal_id="alice") == receipt
    assert await play.store.read(cid) == after


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("basic", [False, True])
@pytest.mark.parametrize("blind_caster", [False, True])
async def test_regular_named_spell_uses_unseen_not_attack_admission(
    tmp_path: Path, backend: str, basic: bool, blind_caster: bool
) -> None:
    from test_basic_combat import opening_facts, provenance
    from test_blindness_combat_consumers import blind

    from wayfarer.engine.simulation.combat.spatial import BasicSpatialContext, VisibilitySpatialFact
    from wayfarer.engine.simulation.combat.visibility import combat_visibility
    from wayfarer.orchestration.combat import DeclareBasicSpatialFacts

    cid, play = await prepare_named(tmp_path, backend, blocked=not blind_caster)
    if basic:

        def basic_fixture(state: PlayState) -> PlayState:
            encounter = state.encounters[0].model_copy(
                update={
                    "spatial_context": BasicSpatialContext(facts=opening_facts(2)),
                    "participants": tuple(
                        p.model_copy(update={"position": None, "hex_facing": None})
                        for p in state.encounters[0].participants
                    ),
                }
            )
            return state.model_copy(update={"encounters": (encounter,)})

        await change(cid, play, "accepted-basic-context", basic_fixture)
        state = play._load(await play.store.read(cid))
        if not blind_caster:
            await CombatService(play).execute(
                cid,
                DeclareBasicSpatialFacts(
                    id="gm-blocked-visibility",
                    actor_id="gm",
                    expected_revision=state.revision,
                    encounter_id="fight",
                    facts=(
                        VisibilitySpatialFact(
                            subject_id="a",
                            object_id="b",
                            visible=False,
                            provenance=provenance(
                                state.revision,
                                source="gm-adjudication",
                                source_id="gm-blocked-visibility",
                            ),
                        ),
                    ),
                ),
                principal_id="gm",
            )
        assert isinstance(
            play._load(await play.store.read(cid)).encounters[0].spatial, BasicSpatialContext
        )
    if blind_caster:
        await change(cid, play, "current-blindness", lambda state: blind(state, "a"))
    state = play._load(await play.store.read(cid))
    if basic or blind_caster:
        with pytest.raises(ValidationError, match="unavailable|nonvisual target location"):
            combat_visibility(state.encounters[0], "a", "b", state=state)
    for index in range(2):
        state = play._load(await play.store.read(cid))
        await GreatHasteService(play).execute(
            cid, command(state.revision, index), principal_id="alice"
        )
    for index in range(2):
        state = play._load(await play.store.read(cid))
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="other-" + str(index),
                actor_id="b",
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="do_nothing",
            ),
            principal_id="b",
        )
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((1, 1, 3))
    receipt = await GreatHasteService(play).execute(
        cid, command(state.revision, 2), principal_id="alice"
    )
    assert receipt.outcome == "active" and receipt.energy_spent == 5
    assert latest(play._load(await play.store.read(cid)).resources)["named"].skill == 5
