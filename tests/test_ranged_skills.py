"""Whole-entry ranged combat skill procedures (#344).

Independent expectations: Basic Set Characters, Fourth Edition, B168-233 skill
chapter with the B301-304 index (attribute, difficulty, recorded default and
specialty per row) and B170 for the point/level progression. Runtime targets
follow the existing ranged dispatch documented in `docs/gurps-ranged.md`
(Lite 27-29; B372-375, B550). Third-printing constructions are not a
first-printing certification; #191 still blocks every row.
"""

from decimal import Decimal
from pathlib import Path

import pytest
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup
from test_statistics import BASIC, gurps_draft, profile_compiler

from wayfarer.character.compiler import Purchase
from wayfarer.character.skills import SkillCompiler
from wayfarer.errors import ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.mundane_skills import inventory
from wayfarer.rules.mundane_skills.ranged import (
    PROCEDURES,
    definitions,
    require_capability,
    require_mode,
)
from wayfarer.simulation.combat import RangedSituation
from wayfarer.simulation.gurps_equipment import Damage, RangedMode

# The exact inventory scope #344 audits, transcribed from the issue.
LISTED = (
    "skill:artillery",
    "skill:beam-weapons",
    "skill:blowpipe",
    "skill:bolas",
    "skill:bow",
    "skill:crossbow",
    "skill:gunner",
    "skill:guns",
    "skill:innate-attack",
    "skill:liquid-projector",
    "skill:net",
    "skill:spear-thrower",
    "skill:thrown-weapon",
    "skill:sling",
)
# B168-233 with the B301-304 index: controlling attribute, difficulty, the
# recorded attribute default, and the level bought with four points (B170).
DISPATCHED = (
    ("skill:bow", "B182", "average", -5, 11),
    ("skill:crossbow", "B186", "easy", -4, 12),
    ("skill:sling", "B221", "hard", -6, 10),
    ("skill:blowpipe", "B180", "hard", -6, 10),
    ("skill:thrown-weapon-axe-mace", "B226", "easy", -4, 12),
    ("skill:thrown-weapon-dart", "B226", "easy", -4, 12),
    ("skill:thrown-weapon-harpoon", "B226", "easy", -4, 12),
    ("skill:thrown-weapon-knife", "B226", "easy", -4, 12),
    ("skill:thrown-weapon-shuriken", "B226", "easy", -4, 12),
    ("skill:thrown-weapon-spear", "B226", "easy", -4, 12),
    ("skill:thrown-weapon-stick", "B226", "easy", -4, 12),
)
THROWN = tuple(row for row in DISPATCHED if row[0].startswith("skill:thrown-weapon-"))
LAUNCHERS = tuple(row for row in DISPATCHED if row not in THROWN)


def mode(skill_id: str, *, thrown: bool, **changes: object) -> RangedMode:
    """Test-only weapon statistics; no catalog completeness claim."""
    fields: dict[str, object] = {
        "id": "shot",
        "skill_id": skill_id,
        "minimum_st": 10,
        "hands": 1,
        "damage": Damage(basis="fixed", dice=1, damage_type="cr"),
        "accuracy": 2,
        "range_basis": "yards",
        "maximum_range": 100,
        "half_damage_range": 10,
        "shots": 1,
        "rate_of_fire": 1,
        "recoil": 1,
        "reload_seconds": 2,
        "bulk": -4,
    }
    if thrown:
        fields |= {"thrown": True, "maximum_range": 10, "reload_seconds": 0}
    else:
        fields |= {"ammunition_id": "equipment:ammo"}
    if skill_id == "skill:bow":
        fields["hands"] = 2
    return RangedMode.model_validate(fields | changes)


def scene(distance: float = 2) -> tuple[RangedSituation, ...]:
    return (
        RangedSituation(attacker_id="a", defender_id="b", distance_yards=distance, size_modifier=0),
    )


async def dispatch(tmp_path: Path, skill_id: str, *, thrown: bool) -> tuple[str, PlayService]:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=mode(skill_id, thrown=thrown),
        ranged_scene=scene(),
        extra_definitions=definitions(),
        extra_purchases=(Purchase(definition_id=skill_id, amount=4),),
    )
    if not thrown:
        for _ in range(2):
            await turn(
                cid,
                play,
                "a",
                "ready",
                item_id="sword-a",
                mode_id="shot",
                reload_ammunition_id="ammo-a",
            )
            await turn(cid, play, "b", "do_nothing")
    return cid, play


def test_listed_scope_is_completely_accounted_for() -> None:
    """Every listed row is implemented, or transferred to a concrete open child."""
    entries = {e.id: e for e in inventory()}
    for identifier in LISTED:
        entry = entries[identifier]
        procedure = PROCEDURES[identifier]
        assert procedure.implemented or (procedure.blockers and procedure.owners), identifier
        # A transferred row keeps its recorded blocker and names its owner; it
        # never quietly loses the blocker or gains an unowned one.
        assert set(procedure.blockers) <= set(entry.blockers), identifier
        assert 344 in entry.followup_issues, identifier
        assert set(procedure.owners) <= set(entry.followup_issues), identifier
    assert set(LISTED) | {row[0] for row in THROWN} <= set(PROCEDURES)
    transferred = {
        identifier: PROCEDURES[identifier].owners
        for identifier in LISTED
        if not PROCEDURES[identifier].implemented
    }
    assert transferred == {
        "skill:innate-attack": (361,),
        "skill:spear-thrower": (360, 362),
    }


def test_thrown_weapon_family_is_expanded_into_concrete_specialties() -> None:
    """B226: a family is completed by distinct specialties, never dispatched itself."""
    family = PROCEDURES["skill:thrown-weapon"]
    assert family.implemented and not family.dispatchable
    assert family.specialties == tuple(row[0] for row in THROWN)
    for identifier in family.specialties:
        specialty = PROCEDURES[identifier].specialty
        assert specialty is not None
        assert specialty.family == "thrown-weapon" and specialty.optional_parent is None
    # The family itself is absent from the runtime pin; only its specialties bind.
    assert "skill:thrown-weapon" not in {d.id for d in definitions()}
    dispatched = {d.id for d in definitions()}
    assert {row[0] for row in DISPATCHED} | {"skill:bolas", "skill:net"} <= dispatched
    # The two families themselves stay out of the runtime pin.
    assert not {"skill:thrown-weapon", "skill:guns", "skill:beam-weapons"} & dispatched


@pytest.mark.parametrize(
    ("identifier", "reference", "difficulty", "default", "trained"), DISPATCHED
)
def test_recorded_mechanics_compile_to_source_levels(
    identifier: str, reference: str, difficulty: str, default: int, trained: int
) -> None:
    """B170: four points buy the difficulty's third level; the default is unpaid."""
    procedure = PROCEDURES[identifier]
    spec = procedure.spec()
    assert (spec.reference, spec.difficulty.value) == (reference, difficulty)
    assert spec.attribute.value == (
        "attribute:iq" if identifier == "skill:artillery" else "attribute:dx"
    )
    assert [(d.target, d.modifier) for d in spec.defaults] == [("attribute:dx", default)]
    skills = profile_compiler(BASIC, *definitions(), statistics_profile=BASIC).skills
    assert isinstance(skills, SkillCompiler)
    values = {str(k): Decimal(10) for k in skills.specs}
    attributes = {
        "attribute:st": Decimal(10),
        "attribute:dx": Decimal(10),
        "attribute:iq": Decimal(10),
        "attribute:ht": Decimal(10),
        "secondary:will": Decimal(10),
        "secondary:per": Decimal(10),
    }
    untrained = {r.target: r.level for r in skills.compile({}, values | attributes)}
    assert untrained[identifier] == 10 + default
    purchased = {r.target: r.level for r in skills.compile({identifier: 4}, values | attributes)}
    assert purchased[identifier] == trained


@pytest.mark.parametrize(("identifier", "reference", "difficulty", "default", "trained"), LAUNCHERS)
async def test_launcher_rows_dispatch_their_own_attack(
    tmp_path: Path,
    identifier: str,
    reference: str,
    difficulty: str,
    default: int,
    trained: int,
) -> None:
    """A pinned missile, one shot, the trained level and the shared range table."""
    cid, play = await dispatch(tmp_path, identifier, thrown=False)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="shot")
    play.rng = RecordedDice([3, 3, 4, 4])
    result = await defend(cid, play, "b")
    assert result.injury is not None
    # Two yards is inside the first range band, so the target is the skill itself.
    assert result.injury.attack.effective_target == trained
    assert (result.injury.hits, result.injury.per_hit_damage) == (1, (4,))
    assert (result.injury.injury, result.injury.hp_after) == (4, 6)
    state = play._load(await play.store.read(cid))
    # One declared shot consumes exactly one reserved round.
    assert next(i.quantity for i in state.resources.items if i.id == "ammo-a") == 9
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize(("identifier", "reference", "difficulty", "default", "trained"), THROWN)
async def test_thrown_specialties_dispatch_and_expend_the_item(
    tmp_path: Path,
    identifier: str,
    reference: str,
    difficulty: str,
    default: int,
    trained: int,
) -> None:
    """B226: the projectile is the item; it leaves inventory and is retained."""
    cid, play = await dispatch(tmp_path, identifier, thrown=True)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="shot")
    play.rng = RecordedDice([3, 3, 4, 4])
    result = await defend(cid, play, "b")
    assert result.injury is not None
    assert result.injury.attack.effective_target == trained
    assert (result.injury.hits, result.injury.per_hit_damage) == (1, (4,))
    assert (result.injury.injury, result.injury.hp_after) == (4, 6)
    state = play._load(await play.store.read(cid))
    assert "sword-a" not in {i.id for i in state.resources.items}
    assert state.resources.expended_items[0].id == "sword-a"
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("skill:spear-thrower", "#360"),
        ("skill:innate-attack", "#361"),
    ],
)
def test_transferred_rows_fail_closed_naming_their_owner(identifier: str, expected: str) -> None:
    with pytest.raises(ValidationError, match="unsupported") as error:
        require_mode(
            BASIC,
            identifier,
            ranged=True,
            thrown=False,
            ammunition=True,
            rate_of_fire=1,
            recoil=1,
            hands=1,
            tight_beam=False,
        )
    assert expected in str(error.value)
    assert "runtime-procedure" in str(error.value)


def test_family_and_out_of_class_weapons_are_refused_before_dice() -> None:
    def check(identifier: str, **changes: object) -> str:
        fields: dict[str, object] = {
            "ranged": True,
            "thrown": False,
            "ammunition": True,
            "rate_of_fire": 1,
            "recoil": 1,
            "hands": 2,
            "tight_beam": False,
            "rated_kind": None,
        }
        with pytest.raises(ValidationError) as error:
            require_mode(BASIC, identifier, **(fields | changes))  # type: ignore[arg-type]
        return str(error.value)

    assert "concrete specialty" in check("skill:thrown-weapon")
    # B182: a bow launches a pinned missile with two hands; it is never thrown.
    assert "outside the skill's class" in check("skill:bow", thrown=True, ammunition=False)
    assert "outside the skill's class" in check("skill:bow", hands=1)
    # Rapid fire, recoil and beams belong to the firearm rows #355 owns.
    assert "outside the skill's class" in check("skill:sling", hands=1, rate_of_fire=3)
    assert "outside the skill's class" in check("skill:sling", hands=1, recoil=2)
    assert "cannot resolve this weapon mode" in check("skill:crossbow", hands=1, tight_beam=True)
    assert "cannot resolve this weapon mode" in check("skill:crossbow", hands=1, ranged=False)
    # B226 specialties throw the item itself and never reserve ammunition.
    assert "outside the skill's class" in check("skill:thrown-weapon-knife", hands=1, thrown=True)
    # B270 rated weapon ST (#348) belongs to the launcher its own skill governs.
    assert "Rated weapon ST" in check("skill:bow", rated_kind="crossbow")
    assert "Rated weapon ST" in check("skill:crossbow", hands=2, rated_kind="bow")
    assert "Rated weapon ST" in check("skill:sling", hands=1, rated_kind="bow")
    require_mode(
        BASIC,
        "skill:bow",
        ranged=True,
        thrown=False,
        ammunition=True,
        rate_of_fire=1,
        recoil=1,
        hands=2,
        tight_beam=False,
        rated_kind="bow",
    )
    # These rows are pinned in the Basic Set package only.
    with pytest.raises(ValidationError, match="exact Basic Set profile"):
        require_mode(
            "gurps-lite-4e-2004",
            "skill:bow",
            ranged=True,
            thrown=False,
            ammunition=True,
            rate_of_fire=1,
            recoil=1,
            hands=2,
            tight_beam=False,
        )


def test_authored_catalogs_fail_closed_before_a_campaign_exists() -> None:
    """The validator gate is the catalog itself, not a later manual ruling."""
    from wayfarer.simulation.gurps_equipment import EquipmentCatalog, EquipmentProfile, Provenance

    def catalog(skill_id: str, **changes: object) -> EquipmentCatalog:
        return EquipmentCatalog(
            profile_id="gurps-basic-set-4e-2004",
            entries=(
                EquipmentProfile(
                    definition_id="equipment:test-weapon",
                    provenance=Provenance(
                        source_id="sjg:basic-set-characters-4e-2004",
                        edition="Fourth Edition, first printing (2004)",
                        pages=(198,),
                        errata="errata-2007-01-26",
                    ),
                    weight_millipounds=1000,
                    price=1,
                    technology_level=2,
                    slot="hand",
                    modes=(mode(skill_id, thrown=False, **changes),),
                ),
                EquipmentProfile(
                    definition_id="equipment:ammo",
                    provenance=Provenance(
                        source_id="sjg:basic-set-characters-4e-2004",
                        edition="Fourth Edition, first printing (2004)",
                        pages=(198,),
                        errata="errata-2007-01-26",
                    ),
                    weight_millipounds=10,
                    price=1,
                    technology_level=2,
                    ammunition=True,
                ),
            ),
        )

    assert catalog("skill:crossbow") is not None
    with pytest.raises(ValidationError, match="#361"):
        catalog("skill:innate-attack")
    for family in ("skill:thrown-weapon", "skill:guns", "skill:liquid-projector"):
        with pytest.raises(ValidationError, match="concrete specialty"):
            catalog(family)
    with pytest.raises(ValidationError, match="outside the skill's class"):
        catalog("skill:crossbow", rate_of_fire=3)


def test_skills_outside_this_audit_pass_through() -> None:
    assert (
        require_mode(
            BASIC,
            "skill:broadsword",
            ranged=True,
            thrown=True,
            ammunition=False,
            rate_of_fire=9,
            recoil=9,
            hands=1,
            tight_beam=True,
        )
        is None
    )


def test_capability_registry_is_the_only_authority() -> None:
    require_capability(BASIC, "gurps.combat.ranged_weapon_skills")
    with pytest.raises(ValidationError, match="Unknown rules capability"):
        require_capability(BASIC, "gurps.combat.invented")
    with pytest.raises(ValidationError, match="outside profile"):
        require_capability("gurps-lite-4e-2004", "gurps.combat.ranged_weapon_skills")


def test_new_pin_adds_the_skills_without_changing_existing_pins() -> None:
    from wayfarer.rules.profiles import (
        GURPS_RANGED_SKILLS_PACKAGE,
        GURPS_RANGED_SKILLS_PROFILE,
        GURPS_STATISTICS_PACKAGE,
        GURPS_STATISTICS_PROFILE,
        ProfileRegistry,
    )

    assert GURPS_STATISTICS_PACKAGE.version == "0.6.0"
    assert GURPS_RANGED_SKILLS_PACKAGE.version == "0.7.0"
    assert GURPS_RANGED_SKILLS_PACKAGE.digest != GURPS_STATISTICS_PACKAGE.digest
    old = {d.id: d for d in GURPS_STATISTICS_PACKAGE.definitions}
    added = {d.id: d for d in GURPS_RANGED_SKILLS_PACKAGE.definitions if d.id not in old}
    assert added == {d.id: d for d in definitions()}
    assert all(d == old[d.id] for d in GURPS_RANGED_SKILLS_PACKAGE.definitions if d.id in old)
    registry = ProfileRegistry((GURPS_STATISTICS_PROFILE, GURPS_RANGED_SKILLS_PROFILE))
    assert registry is not None
    assert GURPS_RANGED_SKILLS_PROFILE.version == 7


def test_registered_pin_compiles_a_ranged_character() -> None:
    from wayfarer.character.compiler import CharacterCompiler
    from wayfarer.rules.profiles import GURPS_RANGED_SKILLS_PROFILE as pinned

    engine = CharacterCompiler(
        pinned.catalog,
        pinned.rules,
        pinned.policy,
        statistics_profile=pinned.conformance_profile_id,
    )
    result = engine.compile(gurps_draft(Purchase(definition_id="skill:bow", amount=4), st_level=10))
    assert result.build is not None, result.diagnostics
    levels = {v.target: v.value for v in result.build.sheet.values}
    assert levels["skill:bow"] == Decimal(11)
    assert levels["skill:thrown-weapon-knife"] == Decimal(6)
