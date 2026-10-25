"""B173 native physical/care defaults cannot be reconstructed after adoption."""

import secrets
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, seed_campaign
from test_actions import campaign, world
from test_default_knowledge_hosts import adopt
from test_hazard_variants import schedule
from test_live_skill_prerequisite_context import reexecute
from test_statistics import gurps_draft, profile_compiler, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import PackagePin, RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.skills.mundane.medicine import definitions as medicine
from wayfarer.engine.rules.skills.mundane.physical_outdoors import definitions
from wayfarer.engine.rules.skills.mundane.social.inventory import definitions as social
from wayfarer.engine.rules.types.hazard import HazardSchedule, HazardSpec
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup
from wayfarer.engine.simulation.campaign.default_knowledge import DeclareDefaultKnowledge
from wayfarer.engine.simulation.campaign.party import PartyRules
from wayfarer.engine.simulation.campaign.scenes import Scene, SceneRules
from wayfarer.engine.simulation.health.hazards import HazardCommand
from wayfarer.engine.simulation.movement.physical import PhysicalCommand, PhysicalRoute
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Owner, ResourceState
from wayfarer.errors import ValidationError
from wayfarer.orchestration import default_knowledge_generations as generations
from wayfarer.orchestration.default_knowledge import DefaultKnowledgeService
from wayfarer.orchestration.hazard_care import HazardCare, HazardCareCommand, HazardCareService
from wayfarer.orchestration.hazards import HazardContext, HazardService
from wayfarer.orchestration.physical import PhysicalService
from wayfarer.orchestration.play import PlayService


async def fixture(
    path: Path,
    backend: str,
    *,
    learned: str = "",
    no_edge: bool = False,
    hazard: str = "",
    exhausted: bool = False,
    group: bool = False,
    iq: int = 10,
) -> tuple[str, PlayService]:
    wanted = {
        "skill:climbing",
        "skill:hiking",
        "skill:swimming",
        "skill:acrobatics",
        "skill:jumping",
        "skill:diagnosis",
        "skill:leadership",
    }
    selected = tuple(d for d in definitions() + medicine() + social() if d.id in wanted)
    if no_edge:
        selected = tuple(
            replace(d, skill=replace(d.skill, defaults=()))
            if d.id == "skill:climbing" and d.skill
            else d
            for d in selected
        )
    package = profile_package("gurps-basic-set-4e-2004", *selected)
    base = profile_compiler("gurps-basic-set-4e-2004")
    compiler = CharacterCompiler(
        RulesCatalog((package,)),
        replace(base.rules, packages=(PackagePin(package.id, package.version, package.digest),)),
        replace(base.policy, technology_level=8),
        statistics_profile="gurps-basic-set-4e-2004",
    )
    environment = world()
    engine = ActionEngine(
        PowerReviewer(compiler, PowerPolicy(id="test", version=1), frozenset({"gm"})),
        ResourceEngine(environment, RulesCatalog((package,)), compiler.rules, compiler.policy, ()),
        ActionRules(
            id="default-physical",
            version=1,
            maximum_wait=10000,
            fatigue_cost=0,
            party=PartyRules(id="hikers", version=1) if group else None,
            scenes=SceneRules(
                id="route-scenes",
                version=1,
                scenes=(Scene(id="dock-scene", version=1, location_id="dock", title="Dock"),),
            ),
        ),
    )
    play = build_play(path, engine, backend=backend)
    initial = campaign(engine)
    hazards: tuple[HazardSchedule, ...] = ()
    if hazard:
        spec = HazardSpec(
            id="hazard",
            scene_id="dock",
            kind="drowning" if hazard == "lifesaving" else "disease",
            interval=5 if hazard == "lifesaving" else 86400,
            cycles=1000 if hazard == "lifesaving" else 4,
            reference="B354/B443",
        )
        h = schedule(spec, stage="struggling" if hazard == "lifesaving" else "cycles")
        hazards = (h.model_copy(update={"actor_id": "b", "due": 60, "symptoms": 1}),)
    state = play.initial_state(
        initial,
        environment,
        ResourceState(
            owners=(Owner(actor_id="a", capacity=100), Owner(actor_id="b", capacity=100)),
        ),
        tuple(
            ActorSetup(
                actor_id=actor,
                proposal=CharacterProposal(
                    draft=gurps_draft(
                        *(
                            (Purchase(definition_id=learned, amount=1),)
                            if learned and actor == "a"
                            else ()
                        )
                    )
                ),
            )
            for actor in ("a", "b")
        ),
    )
    if iq != 10:
        # An authored purchase, recompiled and approved at genesis.
        setups = tuple(
            ActorSetup(
                actor_id=a.actor_id,
                proposal=a.proposal.model_copy(
                    update={
                        "draft": a.proposal.draft.model_copy(
                            update={
                                "purchases": tuple(
                                    p.model_copy(update={"amount": iq})
                                    if p.definition_id == "attribute:iq"
                                    else p
                                    for p in a.proposal.draft.purchases
                                )
                            }
                        )
                    }
                ),
            )
            for a in state.actors
        )
        state = play.initial_state(
            initial,
            environment,
            ResourceState(
                owners=(Owner(actor_id="a", capacity=100), Owner(actor_id="b", capacity=100))
            ),
            setups,
        )
    if hazards:
        state = state.model_copy(
            update={
                "resources": ResourceState.model_validate(
                    state.resources.model_copy(update={"hazards": hazards})
                )
            }
        )
        engine.validate(state)
    if exhausted:
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "pools": tuple(
                            p.model_copy(update={"current": 0}) if p.id == "fp:a" else p
                            for p in state.resources.pools
                        )
                    }
                )
            }
        )
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    return initial["id"], play


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "kind,key", [("climb", "climbing"), ("hike", "hiking"), ("swim", "swimming")]
)
@pytest.mark.parametrize("knowledge", ["unadopted", "unknown", "known"])
async def test_actual_native_required_default_admission_precedes_entropy_and_fatigue(
    tmp_path: Path, backend: str, kind: Literal["climb", "hike", "swim"], key: str, knowledge: str
) -> None:
    cid, play = await fixture(tmp_path, backend)
    known = knowledge == "known"
    if knowledge != "unadopted":
        await adopt(play, cid, ("skill:" + key,) if known else ())
    state = play._load(await play.store.read(cid))
    route = PhysicalRoute(
        id="route",
        scene_id="dock",
        kind=kind,
        surface="tree",
        distance=Decimal(1),
        seconds=3600 if kind == "hike" else 1,
    )

    def resolver(*_args: object) -> PhysicalRoute:
        if not known:
            raise AssertionError("Unknown required skill must refuse before route lookup")
        return route

    command = PhysicalCommand(
        id="feat", actor_id="a", expected_revision=state.revision, kind=kind, route_id="route"
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice((2, 2, 2)) if known else RecordedDice(())
    if not known:
        with pytest.raises(ValidationError, match="society-known"):
            await PhysicalService(play, resolver).execute(cid, command, principal_id="a")
        assert await play.store.read(cid) == before
    else:
        result = await PhysicalService(play, resolver).execute(cid, command, principal_id="a")
        assert (
            result.checks
            and result.checks[0].effective_target == ({"climb": 10, "hike": 5, "swim": 9}[kind])
        )
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("learned", [False, True])
async def test_true_knowledge_never_invents_edge_but_learned_skill_remains_legal(
    tmp_path: Path, backend: str, learned: bool
) -> None:
    cid, play = await fixture(
        tmp_path, backend, learned="skill:climbing" if learned else "", no_edge=True
    )
    await adopt(play, cid, ("skill:climbing",))
    state = play._load(await play.store.read(cid))
    before = await play.store.read(cid)
    route = PhysicalRoute(
        id="route", scene_id="dock", kind="climb", surface="tree", distance=Decimal(1)
    )
    cmd = PhysicalCommand(
        id="feat", actor_id="a", expected_revision=state.revision, kind="climb", route_id="route"
    )
    play.rng = RecordedDice((2, 2, 2)) if learned else RecordedDice(())
    if learned:
        result = await PhysicalService(play, lambda *_: route).execute(cid, cmd, principal_id="a")
        assert result.checks[0].effective_target == 14
    else:
        with pytest.raises(ValidationError, match="society-known"):
            await PhysicalService(play, lambda *_: route).execute(cid, cmd, principal_id="a")
        assert await play.store.read(cid) == before
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind,key", [("lifesaving", "swimming"), ("diagnosis", "diagnosis")])
@pytest.mark.parametrize("knowledge", ["unadopted", "unknown", "known"])
async def test_actual_care_checks_default_before_dice_and_cost(
    tmp_path: Path, backend: str, kind: Literal["lifesaving", "diagnosis"], key: str, knowledge: str
) -> None:
    cid, play = await fixture(tmp_path, backend, hazard=kind)
    known = knowledge == "known"
    if knowledge != "unadopted":
        await adopt(play, cid, ("skill:" + key,) if known else ())
    state = play._load(await play.store.read(cid))
    before = await play.store.read(cid)
    command = HazardCareCommand(
        id="care", actor_id="a", expected_revision=state.revision, treatment_id="care"
    )
    service = HazardCareService(
        play, lambda *_: HazardCare(kind, "b", "exposure", safe_landing=True)
    )
    play.rng = RecordedDice((2, 2, 2)) if known and kind != "lifesaving" else RecordedDice(())
    if known and kind == "lifesaving":
        # B173 permits the default; B345 independently forbids its final1 rescue.
        with pytest.raises(ValidationError, match="effective skill at least 3"):
            await service.execute(cid, command, principal_id="a")
        assert await play.store.read(cid) == before
    elif known:
        result = await service.execute(cid, command, principal_id="a")
        assert result.check and result.check.effective_target == 4
    else:
        with pytest.raises(ValidationError, match="society-known"):
            await service.execute(cid, command, principal_id="a")
        assert await play.store.read(cid) == before
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("adoption", [False, True])
async def test_unknown_controlled_fall_keeps_real_injury_without_optional_acrobatics_roll(
    tmp_path: Path, backend: str, adoption: bool
) -> None:
    cid, play = await fixture(tmp_path, backend)
    if adoption:
        await adopt(play, cid)
    state = play._load(await play.store.read(cid))
    route = PhysicalRoute(
        id="fall", scene_id="dock", kind="fall", distance=Decimal(1), controlled=True
    )
    play.rng = RecordedDice((3,))
    result = await PhysicalService(play, lambda *_: route).execute(
        cid,
        PhysicalCommand(
            id="fall", actor_id="a", expected_revision=state.revision, kind="fall", route_id="fall"
        ),
        principal_id="a",
    )
    assert result.checks == () and result.injury > 0 and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("adoption", [False, True])
async def test_new_drowning_exposure_does_not_invent_unknown_swimming(
    tmp_path: Path, backend: str, adoption: bool
) -> None:
    cid, play = await fixture(tmp_path, backend)
    if adoption:
        await adopt(play, cid)
    state = play._load(await play.store.read(cid))
    before = await play.store.read(cid)
    spec = HazardSpec(
        id="water", scene_id="dock", kind="drowning", interval=5, cycles=100, reference="B354"
    )
    command = HazardCommand(
        id="water", actor_id="a", expected_revision=state.revision, kind="enter", hazard_id="water"
    )
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="society-known"):
        await HazardService(play, lambda *_: HazardContext(spec)).execute(
            cid, command, principal_id="a"
        )
    assert await play.store.read(cid) == before and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_unknown_later_hiker_refuses_before_any_member_draws(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend, group=True, exhausted=True)
    await adopt(play, cid, ("skill:hiking",))
    from wayfarer.engine.simulation.campaign.default_knowledge import AdoptDefaultKnowledge
    from wayfarer.orchestration.default_knowledge import DefaultKnowledgeService

    state = play._load(await play.store.read(cid))
    await DefaultKnowledgeService(play).execute(
        cid,
        AdoptDefaultKnowledge(
            id="adopt-b",
            actor_id="gm",
            expected_revision=state.revision,
            performer_id="b",
            reason="The second hiker has no known Hiking default",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    before = await play.store.read(cid)
    route = PhysicalRoute(
        id="hike",
        scene_id="dock",
        kind="hike",
        seconds=3600,
        distance=Decimal(100),
        group_hike=True,
    )
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="society-known"):
        await PhysicalService(play, lambda *_: route).execute(
            cid,
            PhysicalCommand(
                id="hike",
                actor_id="a",
                expected_revision=state.revision,
                kind="hike",
                route_id="hike",
            ),
            principal_id="a",
        )
    assert await play.store.read(cid) == before and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_unknown_optional_swimming_keeps_mandatory_ht_fatigue_on_continuation(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await fixture(tmp_path, backend)
    await adopt(play, cid, ("skill:swimming",))
    route = PhysicalRoute(
        id="water", scene_id="dock", kind="swim", seconds=59, distance=Decimal(1000)
    )
    service = PhysicalService(play, lambda *_: route)
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((2, 2, 2))
    first = await service.execute(
        cid,
        PhysicalCommand(
            id="first",
            actor_id="a",
            expected_revision=state.revision,
            kind="swim",
            route_id="water",
        ),
        principal_id="a",
    )
    assert first.elapsed == 59 and len(first.checks) == 1 and play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    play.rng = secrets
    await DefaultKnowledgeService(play).execute(
        cid,
        DeclareDefaultKnowledge(
            id="withdraw-swimming",
            actor_id="gm",
            expected_revision=state.revision,
            performer_id="a",
            skill_ids=("skill:swimming",),
            society_known=False,
            reason="The GM corrects current societal knowledge before a mandatory fatigue check",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((4, 4, 4))
    second = await service.execute(
        cid,
        PhysicalCommand(
            id="minute",
            actor_id="a",
            expected_revision=state.revision,
            kind="swim",
            route_id="water",
        ),
        principal_id="a",
    )
    assert second.elapsed == 60 and len(second.checks) == 1
    assert second.checks[0].effective_target == 10 and second.fp_lost == 1 and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_required_native_producer_seed_replays_and_legacy_absence_survives_retry(
    tmp_path: Path, backend: str, legacy: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await fixture(tmp_path, backend)
    initial = (await play.store.history(cid))[0].state_after
    play.rng = secrets
    play.seeds = lambda: f"{14:064x}"
    await adopt(play, cid, () if legacy else ("skill:climbing",))
    monkeypatch.setattr(generations, "ACTIVE", not legacy)
    state = play._load(await play.store.read(cid))
    route = PhysicalRoute(
        id="tree", scene_id="dock", kind="climb", surface="tree", distance=Decimal(1)
    )
    command = PhysicalCommand(
        id="climb", actor_id="a", expected_revision=state.revision, kind="climb", route_id="tree"
    )
    result = await PhysicalService(play, lambda *_: route).execute(cid, command, principal_id="a")
    assert result.checks[0].effective_target == 10
    monkeypatch.setattr(generations, "ACTIVE", legacy)
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert (
        await PhysicalService(restarted, lambda *_: route).execute(cid, command, principal_id="a")
        == result
    )
    await reexecute(initial, play, tmp_path / "reexecute")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_captured_daily_hiking_check_survives_later_adoption(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Retained generation 1 actually admitted this unadopted producer.
    monkeypatch.setattr(generations, "CURRENT_VERSION", 1)
    cid, play = await fixture(tmp_path, backend)
    route = PhysicalRoute(
        id="road", scene_id="dock", kind="hike", seconds=3600, distance=Decimal(1000)
    )
    service = PhysicalService(play, lambda *_: route)
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((2, 2, 2))
    await service.execute(
        cid,
        PhysicalCommand(
            id="first", actor_id="a", expected_revision=state.revision, kind="hike", route_id="road"
        ),
        principal_id="a",
    )
    monkeypatch.setattr(generations, "CURRENT_VERSION", 2)
    await adopt(play, cid)
    state = play._load(await play.store.read(cid))
    daily = next(e for e in state.resources.events if e.id.startswith("hiking-day:"))
    play.rng = RecordedDice(())
    result = await service.execute(
        cid,
        PhysicalCommand(
            id="continue",
            actor_id="a",
            expected_revision=state.revision,
            kind="hike",
            route_id="road",
        ),
        principal_id="a",
    )
    assert result.checks == () and play.rng.exhausted()
    assert daily in play._load(await play.store.read(cid)).resources.events


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_previously_captured_drowning_skill_still_resolves_after_adoption(
    tmp_path: Path, backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Retained generation 1 actually admitted this unadopted producer.
    monkeypatch.setattr(generations, "CURRENT_VERSION", 1)
    cid, play = await fixture(tmp_path, backend)
    spec = HazardSpec(
        id="water", scene_id="dock", kind="drowning", interval=5, cycles=1000, reference="B354"
    )
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice(())
    await HazardService(play, lambda *_: HazardContext(spec)).execute(
        cid,
        HazardCommand(
            id="water",
            actor_id="a",
            expected_revision=state.revision,
            kind="enter",
            hazard_id="water",
        ),
        principal_id="a",
    )
    captured = play._load(await play.store.read(cid)).resources.hazards[0]
    assert captured.swimming == 6
    monkeypatch.setattr(generations, "CURRENT_VERSION", 2)
    await adopt(play, cid)
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((2, 2, 2))
    result = await HazardService(play, lambda *_: HazardContext(spec)).execute(
        cid,
        HazardCommand(
            id="resolve",
            actor_id="a",
            expected_revision=state.revision,
            kind="resolve",
            hazard_id="water",
        ),
        principal_id="a",
    )
    assert result.check is not None and result.check.effective_target == 6
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_explicit_false_withdrawal_refuses_the_next_untrained_attempt(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.simulation.campaign.default_knowledge import DeclareDefaultKnowledge
    from wayfarer.orchestration.default_knowledge import DefaultKnowledgeService

    cid, play = await fixture(tmp_path, backend)
    await adopt(play, cid, ("skill:climbing",))
    state = play._load(await play.store.read(cid))
    await DefaultKnowledgeService(play).execute(
        cid,
        DeclareDefaultKnowledge(
            id="unknown",
            actor_id="gm",
            expected_revision=state.revision,
            performer_id="a",
            skill_ids=("skill:climbing",),
            society_known=False,
            reason="The GM corrects the societal fact",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    before = await play.store.read(cid)
    play.rng = RecordedDice(())

    def unavailable(*_args: object) -> PhysicalRoute:
        raise AssertionError("False knowledge must refuse before route resolution")

    with pytest.raises(ValidationError, match="society-known"):
        await PhysicalService(play, unavailable).execute(
            cid,
            PhysicalCommand(
                id="climb",
                actor_id="a",
                expected_revision=state.revision,
                kind="climb",
                route_id="tree",
            ),
            principal_id="a",
        )
    assert await play.store.read(cid) == before and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("leadership_known", [False, True])
async def test_optional_untrained_leadership_cannot_supply_unknown_group_roll(
    tmp_path: Path, backend: str, leadership_known: bool
) -> None:
    from wayfarer.engine.simulation.campaign.default_knowledge import AdoptDefaultKnowledge
    from wayfarer.orchestration.default_knowledge import DefaultKnowledgeService

    cid, play = await fixture(tmp_path, backend, group=True, iq=17)
    await adopt(
        play, cid, ("skill:hiking", "skill:leadership") if leadership_known else ("skill:hiking",)
    )
    state = play._load(await play.store.read(cid))
    await DefaultKnowledgeService(play).execute(
        cid,
        AdoptDefaultKnowledge(
            id="adopt-b",
            actor_id="gm",
            expected_revision=state.revision,
            performer_id="b",
            known_skill_ids=("skill:hiking",),
            reason="The follower knows Hiking",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    route = PhysicalRoute(
        id="road",
        scene_id="dock",
        kind="hike",
        seconds=3600,
        distance=Decimal(1000),
        group_hike=True,
    )
    play.rng = RecordedDice((2, 2, 2) * (1 if leadership_known else 2))
    result = await PhysicalService(play, lambda *_: route).execute(
        cid,
        PhysicalCommand(
            id="hike", actor_id="a", expected_revision=state.revision, kind="hike", route_id="road"
        ),
        principal_id="a",
    )
    assert len(result.checks) == (1 if leadership_known else 2) and play.rng.exhausted()


async def test_fresh_exposure_join_does_not_borrow_unrelated_legacy_attack_policy(
    tmp_path: Path,
) -> None:
    # A component policy seam, not a fabricated public hazard producer.
    from wayfarer.engine.simulation.campaign.default_knowledge_fallbacks import require_skill
    from wayfarer.engine.simulation.combat.battlefield import GridPoint
    from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter, PendingDefense

    cid, play = await fixture(tmp_path, "sqlite")
    await adopt(play, cid)
    state = play._load(await play.store.read(cid))
    encounter = Encounter(
        id="legacy-fight",
        legacy_battlefield_id="legacy-map",
        participants=tuple(
            Combatant(
                actor_id=a,
                initiative=5,
                position=GridPoint(x=i, y=0),
                reach=1,
                movement_allowance=5,
            )
            for i, a in enumerate(("a", "b"))
        ),
        turn_order=("a", "b"),
        pending_defense=PendingDefense(
            id="old-attack",
            attacker_id="a",
            defender_id="b",
            weapon_id="old",
            allowed=("none",),
            opened_round=1,
            opened_turn=0,
        ),
    )
    state = state.model_copy(update={"encounters": (encounter,)})
    build = play.rules_context.approved_build(state, "a")
    # The old attack still sees its native default; an independent fresh
    # exposure must honor sticky actor adoption and the fresh command policy.
    assert require_skill(play.rules_context, state, "a", build, "swimming", 6) == 6
    with pytest.raises(ValidationError, match="society-known"):
        require_skill(play.rules_context, state, "a", build, "swimming", 6, fresh=True)
