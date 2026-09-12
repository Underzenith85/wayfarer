"""Whole-entry social skill procedures, their fixtures and their fail-closed edges (#345)."""

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

import pytest

from wayfarer.errors import ValidationError
from wayfarer.rules.checks import ModifierKind, RecordedDice
from wayfarer.rules.conformance import CAPABILITIES
from wayfarer.rules.mundane_skills import inventory
from wayfarer.rules.mundane_skills.social import (
    CONDITIONS,
    DISPATCH,
    PROCEDURES,
    VOICE,
    Resolution,
    SocialProcedure,
    SocialSkillContext,
    Verdict,
    definitions,
    effect_ids,
    procedure,
    procedures,
    require_procedure,
    resolve,
    supported,
    unsupported_scope,
)
from wayfarer.simulation.npcs import NPCSocialTrigger
from wayfarer.simulation.resources import ResourceState
from wayfarer.simulation.social import (
    SocialCommand,
    SocialContext,
    SocialDisclosure,
    apply_interaction,
    apply_social,
)
from wayfarer.world import Entity, EntityKind, Fact, World

PROFILE = "gurps-basic-set-4e-2004"
FIXTURE = Path(__file__).parent / "fixtures/gurps/social_skills.json"

# The exact inventory scope of #345, transcribed from the issue rather than read
# back out of the registry the tests are checking.
SCOPE = (
    "acting",
    "carousing",
    "diplomacy",
    "fast-talk",
    "fortune-telling",
    "gesture",
    "interrogation",
    "intimidation",
    "leadership",
    "lip-reading",
    "panhandling",
    "performance",
    "politics",
    "propaganda",
    "public-speaking",
    "savoir-faire",
    "sex-appeal",
    "streetwise",
    "teaching",
)


@contextmanager
def monkeypatched(module: object, registry: Mapping[str, SocialProcedure]) -> Iterator[None]:
    """Swap the social binding the inventory reads, without touching the module."""
    original = module.BINDINGS  # type: ignore[attr-defined]
    module.BINDINGS = tuple(  # type: ignore[attr-defined]
        registry if group is module.SOCIAL_PROCEDURES else group  # type: ignore[attr-defined]
        for group in original
    )
    try:
        yield
    finally:
        module.BINDINGS = original  # type: ignore[attr-defined]


def fixture() -> dict[str, object]:
    data: dict[str, object] = json.loads(FIXTURE.read_text())
    return data


def rows() -> list[dict[str, object]]:
    declared: list[dict[str, object]] = json.loads(FIXTURE.read_text())["procedures"]
    return declared


def cases() -> list[dict[str, object]]:
    recorded: list[dict[str, object]] = json.loads(FIXTURE.read_text())["cases"]
    return recorded


def number(value: object) -> int:
    assert isinstance(value, int)
    return value


def names(value: object) -> list[str]:
    assert isinstance(value, list)
    return [str(item) for item in value]


def expectation(case: dict[str, object]) -> dict[str, object]:
    expected = case["expected"]
    assert isinstance(expected, dict)
    return expected


def context(case: dict[str, object]) -> SocialSkillContext:
    partner = case.get("partner_skill")
    return SocialSkillContext(
        number(case["skill"]),
        number(case.get("resistance", 10)),
        None if partner is None else number(partner),
        frozenset(names(case["conditions"])),
        "actor",
        "subject",
    )


# Rows this issue binds; the rest keep `runtime-procedure` for a named child.
BOUND = tuple(
    name for name in SCOPE if name not in ("fortune-telling", "propaganda", "savoir-faire")
)


def test_every_listed_row_is_accounted_for_and_only_bound_rows_dispatch() -> None:
    assert {entry.id for entry in procedures()} == {f"skill:{name}" for name in SCOPE}
    assert supported(PROFILE) == tuple(f"skill:{name}" for name in BOUND)
    assert {d.id for d in definitions()} == set(supported(PROFILE))
    for entry in procedures():
        assert entry.reference.startswith("B")
        assert 168 <= entry.page <= 233, entry.id
        assert set(entry.required_conditions) <= CONDITIONS
        for identifier in entry.capabilities:
            assert identifier in CAPABILITIES
        if entry.dispatchable:
            assert entry.definition().hooks == ("character.gurps-skill", DISPATCH)
        else:
            assert "runtime-procedure" in entry.blockers and entry.owners
            with pytest.raises(ValidationError, match="has no bound dispatch"):
                entry.definition()
    with pytest.raises(ValidationError, match="Basic Set profile"):
        supported("gurps-lite-4e-2004")
    with pytest.raises(ValidationError, match="Unknown social skill procedure"):
        procedure("skill:swimming")
    with pytest.raises(ValidationError, match="exact Basic Set profile"):
        require_procedure("gurps-lite-4e-2004", "skill:diplomacy")
    with pytest.raises(ValidationError, match="procedure is unsupported"):
        require_procedure(PROFILE, "skill:savoir-faire")


def test_declared_table_matches_the_independent_fixture() -> None:
    """The source-indexed table is transcribed by hand, not read from the module."""
    declared = {str(row["id"]): row for row in rows()}
    assert set(declared) == {f"skill:{name}" for name in SCOPE}
    for entry in procedures():
        row = declared[entry.id]
        assert entry.reference == row["reference"]
        assert entry.attribute.value == row["attribute"]
        assert entry.difficulty.value == row["difficulty"]
        assert entry.resolution.value == row["resolution"]
        assert list(entry.required_conditions) == names(row["required_conditions"])
        assert [m.condition for m in entry.modifiers] == names(row["modifiers"])
        assert sorted({s.owner_issue for s in entry.unsupported}) == row["unsupported"]
        assert entry.dispatchable == row["dispatched"]
        assert entry.complete == (row["dispatched"] and not row["unsupported"])
        # The procedure fixture predates the source-wide default transcription;
        # compare only procedure blockers and the original attribute anchor here.
        transferred_fixture = row["transferred"]
        assert isinstance(transferred_fixture, dict)
        expected_transferred = {
            b: o for b, o in transferred_fixture.items() if b != "conditional-or-skill-defaults"
        }
        actual_transferred = {
            b: list(o) for b, o in entry.transferred.items() if b != "contextual-default-procedure"
        }
        assert actual_transferred == expected_transferred
        recorded = row["defaults"]
        assert isinstance(recorded, list) and recorded
        assert [entry.defaults[0].target, entry.defaults[0].modifier] == recorded[0]


@pytest.mark.parametrize("case", cases(), ids=lambda case: str(case["name"]))
def test_expected_results_match_the_pinned_fixture(case: dict[str, object]) -> None:
    recorded = case["dice"]
    assert isinstance(recorded, list)
    dice = RecordedDice(number(die) for die in recorded)
    trace = resolve(PROFILE, str(case["procedure"]), context(case), rng=dice)
    expected = expectation(case)
    assert dice.exhausted(), case["name"]
    assert trace.verdict.value == expected["verdict"]
    assert trace.effect.id == expected["effect"]
    assert trace.effective_skill == expected["effective_skill"]
    assert trace.effect.requires_adjudication == expected["requires_adjudication"]
    assert trace.effect.reaction_modifier == expected.get("reaction_modifier", 0)
    assert trace.effect.aftermath_reaction == expected.get("aftermath_reaction", 0)
    assert trace.effect.fatigue_cost == expected.get("fatigue_cost", 0)
    assert trace.reaction == expected.get("reaction")
    assert trace.succeeded == (expected["verdict"] in ("success", "critical-success"))
    if trace.rounds is not None:
        assert len(trace.rounds.rounds) == expected["rounds"]


def test_the_fixture_covers_every_bound_row_and_every_resolution_shape() -> None:
    covered = {str(case["procedure"]) for case in cases()}
    assert covered == {f"skill:{name}" for name in BOUND}
    shapes = {procedure(str(case["procedure"])).resolution for case in cases()}
    assert shapes == set(Resolution)
    verdicts = {str(expectation(case)["verdict"]) for case in cases()}
    assert verdicts == {verdict.value for verdict in Verdict}
    assert {str(expectation(case)["effect"]) for case in cases()} <= effect_ids()


def test_a_missing_contextual_prerequisite_fails_closed() -> None:
    """A procedure never proceeds by assuming a circumstance it was not given."""
    with pytest.raises(ValidationError, match="prerequisite is not met"):
        resolve(
            PROFILE,
            "skill:sex-appeal",
            SocialSkillContext(12, conditions=frozenset({"audience-perceptible"})),
            rng=RecordedDice([3, 3, 3, 3, 3, 3]),
        )
    with pytest.raises(ValidationError, match="Undeclared social condition"):
        resolve(
            PROFILE,
            "skill:acting",
            SocialSkillContext(12, conditions=frozenset({"audience-is-drunk"})),
            rng=RecordedDice([3, 3, 3, 3, 3, 3]),
        )


def test_paired_and_unpaired_procedures_reject_the_wrong_second_party() -> None:
    visible = frozenset({"audience-visible"})
    with pytest.raises(ValidationError, match="the other party's level"):
        resolve(PROFILE, "skill:gesture", SocialSkillContext(12, conditions=visible), rng=None)  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="no second party"):
        resolve(
            PROFILE,
            "skill:acting",
            SocialSkillContext(
                12, partner_skill=10, conditions=frozenset({"audience-perceptible"})
            ),
            rng=RecordedDice([3, 3, 3, 3, 3, 3]),
        )


def test_reaction_modifiers_reach_influence_procedures_only() -> None:
    from wayfarer.rules.gurps_social import ReactionModifier

    modifiers = (ReactionModifier("status", 2, "trait:status"),)
    with pytest.raises(ValidationError, match="influence rolls only"):
        resolve(
            PROFILE,
            "skill:performance",
            SocialSkillContext(
                12, conditions=frozenset({"audience-perceptible"}), reaction_modifiers=modifiers
            ),
            rng=RecordedDice([3, 3, 3]),
        )
    trace = resolve(
        PROFILE,
        "skill:streetwise",
        SocialSkillContext(
            11,
            resistance=10,
            conditions=frozenset({"audience-perceptible", "criminal-milieu"}),
            reaction_modifiers=modifiers,
        ),
        rng=RecordedDice([4, 4, 4, 5, 5, 5]),
    )
    # B359: a reaction bonus also improves the influence roll it accompanies.
    assert trace.influence is not None and trace.influence.contest is not None
    assert trace.influence.contest.first.effective_target == 13
    assert trace.verdict is Verdict.SUCCESS


def test_voice_is_a_declared_rule_not_a_supplied_number() -> None:
    """B97: the condition comes from the build, the +2 belongs to the procedure."""
    assert VOICE.value == 2 and VOICE.reference == "B97"
    speaks = {entry.id for entry in procedures() if VOICE in entry.modifiers}
    assert speaks == {
        "skill:diplomacy",
        "skill:fast-talk",
        "skill:leadership",
        "skill:performance",
        "skill:politics",
        "skill:public-speaking",
        "skill:sex-appeal",
    }
    conditions = frozenset({"audience-perceptible", "audible-voice-trait"})
    trace = resolve(
        PROFILE,
        "skill:performance",
        SocialSkillContext(11, conditions=conditions),
        rng=RecordedDice([5, 4, 4]),
    )
    assert [modifier.value for modifier in trace.modifiers] == [2]
    assert trace.modifiers[0].kind is ModifierKind.SITUATIONAL
    assert trace.modifiers[0].source_id == "B97"
    assert trace.effective_skill == 13
    silent = resolve(
        PROFILE,
        "skill:performance",
        SocialSkillContext(11, conditions=frozenset({"audience-perceptible"})),
        rng=RecordedDice([5, 4, 4]),
    )
    assert silent.modifiers == () and silent.verdict is Verdict.FAILURE


def test_unsupported_scope_is_published_with_an_owner() -> None:
    """Transferred scope is visible to a validator, never silently missing."""
    scope = dict(unsupported_scope())
    # A bound row can still leave part of its entry elsewhere; a transferred row
    # keeps a blocker instead, so it never appears here.
    assert set(scope) == {
        "skill:carousing",
        "skill:interrogation",
        "skill:leadership",
        "skill:panhandling",
        "skill:performance",
        "skill:public-speaking",
        "skill:teaching",
    }
    assert all(entry.owner_issue > 0 and entry.detail for entry in scope.values())
    assert {entry.owner_issue for entry in scope.values()} == {368, 369, 370}
    assert all(procedure(identifier).dispatchable for identifier in scope)
    transferred = {
        identifier
        for identifier, entry in PROCEDURES.items()
        if "runtime-procedure" in entry.blockers
    }
    assert transferred == {"skill:fortune-telling", "skill:propaganda", "skill:savoir-faire"}


def test_inventory_rows_agree_with_the_procedure_registry() -> None:
    """The audit reconciles this registry: a resolved blocker needs a real dispatch."""
    rows = {entry.id: entry for entry in inventory()}
    for identifier, entry in PROCEDURES.items():
        row = rows[identifier]
        assert row.procedure_owner == 345
        recorded = set(row.blockers)
        assert set(entry.blockers) == recorded
        assert set(entry.owners) <= set(row.followup_issues), identifier
        assert row.bound is entry.dispatchable
        assert row.implementation == ("implemented" if entry.dispatchable else "unsupported")
        assert row.dispatch == (DISPATCH if entry.dispatchable else None)
        assert row.available is (entry.dispatchable and not row.blockers)
    # A transferred row keeps its blocker and every blocker still names an owner.
    assert "runtime-procedure" in rows["skill:savoir-faire"].blockers
    assert rows["skill:savoir-faire"].blocker_owners["runtime-procedure"] == (345, 366)
    assert "runtime-procedure" not in rows["skill:teaching"].blockers
    assert 369 in rows["skill:teaching"].followup_issues


def test_a_binding_cannot_disagree_with_the_recorded_inventory() -> None:
    """A procedure may not resolve a blocker the row never recorded, or restate its numbers."""
    from dataclasses import replace

    import wayfarer.rules.mundane_skills as module
    from wayfarer.rules.skill_types import SkillDefault

    entry = procedure("skill:acting")
    for broken, message in (
        (replace(entry, resolved=("runtime-procedure", "metadata-audit")), "disagrees"),
        (replace(entry, defaults=(SkillDefault("attribute:iq", -4),)), "changes recorded"),
        (
            replace(
                procedure("skill:savoir-faire"),
                transferred={"runtime-procedure": ()},
            ),
            "names no owner",
        ),
    ):
        registry = dict(PROCEDURES) | {broken.id: broken}
        with monkeypatched(module, registry):
            with pytest.raises(ValidationError, match=message):
                inventory()


def test_authored_triggers_cannot_invent_a_procedure_or_a_circumstance() -> None:
    trigger = NPCSocialTrigger(
        kind="skill",
        subject_id="npc",
        skill_id="skill:streetwise",
        conditions=("audience-perceptible", "criminal-milieu"),
    )
    assert trigger.skill_id in PROCEDURES
    for changes in (
        {"skill_id": "skill:swimming"},
        {"conditions": ("audience-perceptible", "audience-perceptible")},
        {"conditions": ("bribed-the-doorman",)},
    ):
        with pytest.raises(ValueError):
            NPCSocialTrigger.model_validate(trigger.model_dump() | changes)
    with pytest.raises(ValueError, match="belong to a social skill trigger"):
        NPCSocialTrigger(kind="reaction", subject_id="npc", conditions=("public-place",))


def social_world() -> World:
    return World(
        entities=(
            Entity("tavern", EntityKind.LOCATION, "Tavern"),
            Entity("a", EntityKind.ACTOR, "Player", location_id="tavern"),
            Entity("npc", EntityKind.ACTOR, "Fence", location_id="tavern"),
        ),
        facts=(Fact("cellar", "npc", "route", "cellar"),),
        knowledge=(("npc", "cellar"),),
    )


def skill_command(identifier: str = "ask-around") -> SocialCommand:
    return SocialCommand(
        id=identifier,
        actor_id="a",
        subject_id="npc",
        kind="skill",
        trigger_id="cellar-door",
        expected_revision=0,
    )


def skill_context() -> SocialContext:
    return SocialContext(
        PROFILE,
        0,
        will=10,
        procedure_id="skill:streetwise",
        skill_level=13,
        conditions=frozenset({"audience-perceptible", "criminal-milieu"}),
    )


def test_a_skill_procedure_commits_a_replayable_receipt() -> None:
    """The ledger records the whole trace privately and only the effect publicly."""
    state, outcome = apply_social(
        ResourceState(),
        social_world(),
        skill_command(),
        skill_context(),
        rng=RecordedDice([3, 4, 3, 4, 4, 4]),
        system=True,
    )
    assert outcome.kind == "skill"
    assert outcome.outcome == "streetwise-vouched"
    assert not outcome.requires_adjudication and outcome.adjudication == ()
    private = json.loads(state.events[0].kind)["private"]
    assert private["procedure_id"] == "skill:streetwise"
    assert private["reference"] == "B223"
    assert private["influence"]["contest"]["first"]["dice"] == [3, 4, 3]
    # Replaying the receipt returns the recorded projection without new dice.
    replayed_state, replayed = apply_social(
        state,
        social_world(),
        skill_command(),
        skill_context(),
        rng=RecordedDice([]),
        system=True,
    )
    assert replayed == outcome and replayed_state is state


def test_a_skill_command_without_a_procedure_fails_closed() -> None:
    with pytest.raises(ValidationError, match="declared procedure"):
        apply_social(
            ResourceState(),
            social_world(),
            skill_command(),
            SocialContext(PROFILE, 0),
            rng=RecordedDice([3, 3, 3, 3, 3, 3]),
            system=True,
        )


def test_a_procedure_effect_can_gate_an_authored_disclosure() -> None:
    world = social_world()
    disclosure = SocialDisclosure(("cellar",), ("streetwise-vouched",))
    _, updated, outcome = apply_interaction(
        ResourceState(),
        world,
        skill_command(),
        skill_context(),
        disclosure,
        rng=RecordedDice([3, 4, 3, 4, 4, 4]),
        system=True,
    )
    assert outcome.outcome == "streetwise-vouched"
    assert "cellar" in {fact.id for fact in updated.perspective("a").facts}
    with pytest.raises(ValidationError, match="Unsupported disclosure outcome"):
        apply_interaction(
            ResourceState(),
            world,
            skill_command("other"),
            skill_context(),
            SocialDisclosure(("cellar",), ("vouched-for",)),
            rng=RecordedDice([3, 4, 3, 4, 4, 4]),
            system=True,
        )


async def test_a_procedure_runs_in_a_live_authorized_transaction(tmp_path: Path) -> None:
    """Director dispatch commits once, replays without the resolver, and keeps dice private."""
    from test_social_dispatch import prepare

    from wayfarer.orchestration.access import CampaignAccess
    from wayfarer.orchestration.play import PlayService
    from wayfarer.orchestration.social import ResolvedInteraction, SocialService
    from wayfarer.simulation.actions import PlayState

    def resolver(play: PlayService, state: PlayState, value: SocialCommand) -> ResolvedInteraction:
        if value.trigger_id != "cellar-door":
            raise ValidationError("Unknown scenario trigger")
        return ResolvedInteraction(skill_context(), SocialDisclosure(("disclosure",)))

    cid, play = await prepare(tmp_path)
    play.rng = RecordedDice([3, 4, 3, 4, 4, 4])
    service = SocialService(play, resolver)
    outcome = await service.execute(cid, skill_command(), authenticated_gm_id="gm")
    assert outcome.kind == "skill" and outcome.outcome == "streetwise-vouched"
    with pytest.raises(ValidationError, match="trusted director authority"):
        await service.execute(cid, skill_command("again"), authenticated_gm_id="alice")
    projection = str(await CampaignAccess(play).read(cid, principal_id="alice"))
    for secret in ("criminal-milieu", "contest", "dice", "victory_margin"):
        assert secret not in projection


async def test_an_authored_trigger_dispatches_a_procedure_from_an_approved_level(
    tmp_path: Path,
) -> None:
    """The scenario names the procedure and the circumstances; the build sets the level."""
    import json as _json

    from test_social_dispatch import prepare

    from wayfarer.simulation.actions import Wait
    from wayfarer.simulation.npcs import NPCSocialAction, NPCSocialPlan, NPCSocialRules

    rules = NPCSocialRules(
        id="parley",
        version=2,
        plans=(
            NPCSocialPlan(
                id="envoy-parley",
                actor_id="a",
                goal="Open negotiations with the dock guard",
                first_due=1,
                interval=1,
                action_budget=1,
                actions=(
                    NPCSocialAction(
                        id="parley",
                        kind="communicate",
                        social=NPCSocialTrigger(
                            kind="skill",
                            subject_id="npc",
                            skill_id="skill:diplomacy",
                            conditions=("audience-audible", "shared-language"),
                        ),
                    ),
                ),
            ),
        ),
    )
    cid, play = await prepare(tmp_path, rules)
    # Diplomacy defaults to IQ-6 = 4 on this build, so the guard's Will of 10
    # wins the contest; the ordinary reaction is then kept when it is better.
    play.rng = RecordedDice([3, 3, 3, 4, 4, 4, 5, 5, 5])
    await play.execute(
        cid,
        Wait(id="first", actor_id="a", expected_revision=0, ticks=1),
        authenticated_actor_id="a",
    )
    state = play._load(await play.store.read(cid))
    recorded = _json.loads(state.resources.events[-1].kind)
    assert _json.loads(recorded["public"])["kind"] == "skill"
    assert _json.loads(recorded["public"])["outcome"] == "diplomacy-rebuffed"
    assert recorded["private"]["procedure_id"] == "skill:diplomacy"
    assert recorded["private"]["base_skill"] == 4


def test_an_approved_voice_purchase_asserts_the_condition_and_nothing_else() -> None:
    """B97: the build says the voice is heard; the procedure owns the +2."""
    from test_mundane_trait_runtime import approved

    from wayfarer.character.compiler import Purchase
    from wayfarer.character.social_traits import skill_conditions
    from wayfarer.rules.mundane_traits.runtime import Audience

    build, engine = approved(Purchase(definition_id="trait:voice"))
    assert skill_conditions(build, engine.definitions, "skill:diplomacy") == {"audible-voice-trait"}
    inaudible = skill_conditions(
        build, engine.definitions, "skill:diplomacy", Audience(audible=False)
    )
    assert inaudible == frozenset()
    # Streetwise is not one of the skills B97 improves, so nothing is asserted.
    assert skill_conditions(build, engine.definitions, "skill:streetwise") == frozenset()
    silent, other = approved(Purchase(definition_id="trait:charisma", amount=2))
    assert skill_conditions(silent, other.definitions, "skill:diplomacy") == frozenset()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"id": "acting"}, "identity and reference"),
        ({"page": 167}, "identity and reference"),
        ({"effects": ()}, "reachable verdict"),
        ({"transferred": {"runtime-procedure": (366,)}}, "either bound or transferred"),
        ({"paired": True}, "unopposed procedure can be paired"),
        ({"required_conditions": ("bribed-the-doorman",)}, "Undeclared social condition"),
        (
            {"required_conditions": ("audience-perceptible", "audience-perceptible")},
            "Duplicate social condition",
        ),
    ],
)
def test_the_registry_rejects_an_incoherent_procedure(
    changes: dict[str, object], message: str
) -> None:
    """The declared table is validated at import, not trusted because it is code."""
    from dataclasses import replace

    from wayfarer.rules.mundane_skills.social import _validate

    entry = procedure("skill:acting")
    with pytest.raises(ValidationError, match=message):
        _validate((replace(entry, **changes),))  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="Duplicate social procedure"):
        _validate((entry, entry))
    scope = replace(
        entry, unsupported=(replace(procedure("skill:teaching").unsupported[0], detail=""),)
    )
    with pytest.raises(ValidationError, match="owner and detail"):
        _validate((scope,))


async def test_an_unopposed_procedure_collects_no_reaction_modifiers(tmp_path: Path) -> None:
    """B359 modifiers reach influence rolls; an unopposed roll must not take them."""
    from test_social_dispatch import prepare

    from wayfarer.orchestration.play import PlayService
    from wayfarer.orchestration.social import ResolvedInteraction, SocialService
    from wayfarer.rules.social_hooks import Standing
    from wayfarer.simulation.actions import PlayState

    def watching(play: PlayService, state: PlayState, value: SocialCommand) -> ResolvedInteraction:
        return ResolvedInteraction(
            SocialContext(
                PROFILE,
                0,
                procedure_id="skill:lip-reading",
                skill_level=11,
                conditions=frozenset({"speaker-lips-visible", "shared-language"}),
                standing=Standing(appearance="handsome"),
            )
        )

    cid, play = await prepare(tmp_path)
    play.rng = RecordedDice([6, 6, 6])
    service = SocialService(play, watching)
    outcome = await service.execute(
        cid,
        skill_command("watch-the-window"),
        authenticated_gm_id="gm",
    )
    assert outcome.outcome == "lip-reading-misread"
    assert outcome.requires_adjudication and outcome.adjudication == ("lip-reading-misread",)
    state = play._load(await play.store.read(cid))
    private = json.loads(state.resources.events[-1].kind)["private"]
    # Appearance never touched the roll and its recognition dice were not drawn.
    assert private["modifiers"] == [] and private["recognition"] == []
    assert private["check"]["effective_target"] == 11


async def test_an_initiator_without_an_approved_build_asserts_nothing(tmp_path: Path) -> None:
    from test_social_dispatch import prepare

    from wayfarer.orchestration.play import PlayService
    from wayfarer.orchestration.social import ResolvedInteraction, SocialService
    from wayfarer.simulation.actions import PlayState

    def resolver(play: PlayService, state: PlayState, value: SocialCommand) -> ResolvedInteraction:
        return ResolvedInteraction(skill_context())

    cid, play = await prepare(tmp_path)
    play.rng = RecordedDice([3, 4, 3, 4, 4, 4])
    service = SocialService(play, resolver)
    stranger = skill_command("stranger").model_copy(
        update={"actor_id": "npc", "subject_id": "npc:branch"}
    )
    outcome = await service.execute(cid, stranger, authenticated_gm_id="gm")
    assert outcome.outcome == "streetwise-vouched"


async def test_dispatch_rejects_a_skill_context_without_a_procedure(tmp_path: Path) -> None:
    from test_social_dispatch import prepare

    from wayfarer.orchestration.play import PlayService
    from wayfarer.orchestration.social import ResolvedInteraction, SocialService
    from wayfarer.simulation.actions import PlayState

    def bare(play: PlayService, state: PlayState, value: SocialCommand) -> ResolvedInteraction:
        return ResolvedInteraction(SocialContext(PROFILE, 0))

    cid, play = await prepare(tmp_path)
    service = SocialService(play, bare)
    with pytest.raises(ValidationError, match="requires a declared procedure"):
        await service.execute(cid, skill_command(), authenticated_gm_id="gm")


@pytest.mark.parametrize(
    ("actor_id", "skill_id", "message"),
    [
        ("npc", "skill:diplomacy", "initiator requires an approved build"),
        ("a", "skill:acting", "no approved level"),
    ],
)
async def test_an_authored_trigger_needs_an_approved_level_to_roll(
    tmp_path: Path, actor_id: str, skill_id: str, message: str
) -> None:
    """An unpinned or unpurchased skill cannot become a roll target."""
    from test_social_dispatch import prepare

    from wayfarer.orchestration.npcs import social_occurrence

    cid, play = await prepare(tmp_path)
    state = play._load(await play.store.read(cid))
    trigger = NPCSocialTrigger(
        kind="skill",
        subject_id="npc",
        skill_id=skill_id,
        conditions=("audience-audible", "shared-language", "audience-perceptible"),
    )
    with pytest.raises(ValidationError, match=message):
        social_occurrence(play, state, actor_id, trigger, "parley")


def test_an_authored_skill_trigger_cannot_supply_a_roll_modifier() -> None:
    """Authoring selects circumstances; the procedure owns what each is worth."""
    with pytest.raises(ValueError, match="conditions, not a modifier"):
        NPCSocialTrigger(
            kind="skill",
            subject_id="npc",
            skill_id="skill:leadership",
            modifier=-2,
            conditions=("followers-present", "audience-audible"),
        )
