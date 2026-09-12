"""Innate Attack specialties (#361): B201.

An innate attack comes from the creature, not from an item: there is no missile
to reserve, no magazine to reload and no grip that limits it. The four
specialties the B301-B304 index lists are distinct rows with explicit B201
cross-specialty defaults, and Projectile is reconciled with the projectile skill
the opt-in spell adapter already pins.
"""

import pytest

from wayfarer.engine.rules.catalog import ImplementationStatus
from wayfarer.engine.rules.mundane_skills import inventory
from wayfarer.engine.rules.mundane_skills.ranged import PROCEDURES, definitions, require_mode
from wayfarer.engine.rules.spell_catalog import projectile_definition
from wayfarer.errors import ValidationError

BASIC = "gurps-basic-set-4e-2004"
SPECIALTIES = tuple(
    f"skill:innate-attack-{key}" for key in ("beam", "breath", "gaze", "projectile")
)


def test_the_family_expands_into_its_indexed_specialties() -> None:
    entries = {e.id: e for e in inventory()}
    family = PROCEDURES["skill:innate-attack"]
    assert family.implemented and not family.dispatchable
    assert family.specialties == SPECIALTIES
    assert family.blockers == () and entries["skill:innate-attack"].owners == (344,)
    assert set(SPECIALTIES) <= {d.id for d in definitions()}
    for identifier in SPECIALTIES:
        entry = entries[identifier]
        assert entry.bound and entry.dispatch == "combat.ranged-attack"
        assert entry.definition is not None and entry.definition.skill is not None
        spec = entry.definition.skill
        # B201: DX/E, defaulting to DX-4, with no TL context of its own.
        assert (spec.reference, spec.difficulty.value) == ("B201", "easy")
        assert (spec.defaults[0].target, spec.defaults[0].modifier) == ("attribute:dx", -4)
        assert {(d.target, d.modifier) for d in spec.defaults[1:]} == {
            (other, -2) for other in SPECIALTIES if other != identifier
        }
        assert not entry.tl_required
        specialty = spec.specialty
        assert specialty is not None
        assert specialty.family == "innate-attack" and specialty.optional_parent is None
        assert "conditional-or-skill-defaults" not in entry.blockers


def test_the_projectile_specialty_extends_the_adapters_definition() -> None:
    """The downstream ranged package adds B201 specialty defaults and dispatch."""
    legacy = projectile_definition()
    reconciled = PROCEDURES["skill:innate-attack-projectile"].definition()
    assert legacy.id == reconciled.id == "skill:innate-attack-projectile"
    assert legacy.name == reconciled.name == "Innate Attack (Projectile)"
    assert legacy.status is reconciled.status is ImplementationStatus.IMPLEMENTED
    assert legacy.skill is not None and reconciled.skill is not None
    # The base mechanics agree; the ranged package adds exact cross-defaults,
    # specialty metadata, and the dispatch hook the adapter never carried.
    assert legacy.skill.attribute == reconciled.skill.attribute
    assert legacy.skill.difficulty == reconciled.skill.difficulty
    assert legacy.skill.reference == reconciled.skill.reference
    assert legacy.skill.defaults == reconciled.skill.defaults[:1]
    assert legacy.skill.specialty is None and reconciled.skill.specialty is not None
    assert "combat.ranged-attack" not in legacy.hooks
    assert "combat.ranged-attack" in reconciled.hooks


@pytest.mark.parametrize("identifier", SPECIALTIES)
def test_each_specialty_dispatches_an_itemless_attack(identifier: str) -> None:
    assert (
        require_mode(
            BASIC,
            identifier,
            ranged=True,
            thrown=False,
            ammunition=False,
            rate_of_fire=1,
            recoil=1,
            hands=1,
            tight_beam=True,
        )
        is not None
    )


def test_an_innate_attack_is_not_a_weapon_with_ammunition_or_a_mount() -> None:
    def check(skill_id: str, **changes: object) -> str:
        fields: dict[str, object] = {
            "ranged": True,
            "thrown": False,
            "ammunition": False,
            "rate_of_fire": 1,
            "recoil": 1,
            "hands": 1,
            "tight_beam": False,
        }
        with pytest.raises(ValidationError) as error:
            require_mode(BASIC, skill_id, **(fields | changes))  # type: ignore[arg-type]
        return str(error.value)

    assert "outside the skill's class" in check("skill:innate-attack-beam", ammunition=True)
    assert "outside the skill's class" in check("skill:innate-attack-breath", thrown=True)
    assert "Mount facts are outside" in check("skill:innate-attack-gaze", mounted=True)
    # The family itself is never dispatched.
    assert "concrete specialty" in check("skill:innate-attack")
    # A pinned missile weapon is not an innate attack.
    assert "outside the skill's class" in check("skill:bow", hands=2)
