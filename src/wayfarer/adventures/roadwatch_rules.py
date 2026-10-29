"""Immutable, source-pinned rules and fixed builds for Roadside Watch.

This is a small campaign admission, not a Basic Set completeness declaration.
The skill suppliers retain their full source metadata, including defaults whose
other skills are deliberately absent from this campaign. Starting gear and a
common language/culture are explicit GM provisions, not wealth-workshop proof.
"""

from dataclasses import replace
from types import MappingProxyType
from typing import Final

from wayfarer.engine.character.compiler import CharacterCompiler, CharacterDraft, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules import gurps_characters
from wayfarer.engine.rules.catalog import (
    CampaignPolicy,
    CampaignRules,
    PackagePin,
    RulesCatalog,
    RulesPackage,
    reference,
)
from wayfarer.engine.rules.profiles import (
    GURPS_BASIC_EQUIPMENT_DEFINITIONS,
    GURPS_CAMPAIGNS_SOURCE,
    GURPS_CHARACTERS_SOURCE,
    GURPS_EDITION,
)
from wayfarer.engine.rules.skills import gurps_skills
from wayfarer.engine.rules.skills.mundane import medicine, melee
from wayfarer.engine.rules.types.battle_fatigue import BattleFatiguePolicy
from wayfarer.engine.simulation.equipment.basic.catalog import BASIC_EQUIPMENT
from wayfarer.engine.simulation.equipment.catalog import EquipmentCatalog, EquipmentProfile
from wayfarer.errors import ValidationError

PROFILE_ID: Final = "gurps-basic-set-4e-2004"
SOURCE_BASELINE: Final = "gurps-4e-characters-3p-2008+campaigns-4p-2008"
SOURCE_SHA256: Final = MappingProxyType(
    {
        GURPS_CHARACTERS_SOURCE.id: "872b5fece8f4013bf46825b397ef52b52c865fa2879f4544f055d9b6caecf47e",
        GURPS_CAMPAIGNS_SOURCE.id: "79cff8f75b91b4ba72e7947320bf98e184515e60108bda0f0891d379b3c96e80",
    }
)
PC_ACTOR_IDS: Final = ("a", "b")
NPC_ACTOR_ID: Final = "brigand"
GM_ID: Final = "gm"
PLAYER_ID: Final = "party"
SKILL_IDS: Final = frozenset(
    {"skill:broadsword", "skill:observation", "skill:stealth", "skill:first-aid"}
)
EQUIPMENT_IDS: Final = frozenset(
    {"equipment:broadsword", "equipment:leather-armor", "equipment:bandages"}
)

_representative = {definition.id: definition for definition in gurps_skills.definitions(PROFILE_ID)}
_melee = {definition.id: definition for definition in melee.definitions(default_revision=2)}
_medicine = {definition.id: definition for definition in medicine.definitions()}
_skills: Final = (
    _melee["skill:broadsword"],
    _representative["skill:observation"],
    _representative["skill:stealth"],
    _medicine["skill:first-aid"],
)

PACKAGE: Final = RulesPackage(
    id="package:roadside-watch-gurps",
    version="1.1.0",
    edition=GURPS_EDITION,
    sources=tuple(
        replace(source, citation=f"{source.citation}; SHA-256 {SOURCE_SHA256[source.id]}")
        for source in (GURPS_CHARACTERS_SOURCE, GURPS_CAMPAIGNS_SOURCE)
    ),
    # B426: this fixed, temperate scenario admits ordinary battles under 120s.
    battle_fatigue=BattleFatiguePolicy(maximum_seconds=119),
    definitions=gurps_characters.definitions(PROFILE_ID, revision=2)
    + _skills
    + tuple(
        definition
        for definition in GURPS_BASIC_EQUIPMENT_DEFINITIONS
        if definition.id in EQUIPMENT_IDS
    ),
)
POLICY: Final = CampaignPolicy(
    id="policy:roadside-watch-gurps",
    version=1,
    point_budget=100,
    disadvantage_limit=0,
    attribute_ceiling=12,
    skill_ceiling=15,
    permitted_sources=frozenset(SOURCE_SHA256),
    technology_level=3,
    allow_supernatural=False,
    allowed_equipment=EQUIPMENT_IDS,
)
RULES: Final = CampaignRules(
    edition=GURPS_EDITION,
    packages=(PackagePin(PACKAGE.id, PACKAGE.version, PACKAGE.digest),),
    policy_id=POLICY.id,
    policy_version=POLICY.version,
)
CATALOG: Final = RulesCatalog((PACKAGE,))
RULES_REFERENCE: Final = reference(RULES)


def _campaign_equipment(entry: EquipmentProfile) -> EquipmentProfile:
    if entry.definition_id != "equipment:broadsword":
        return entry
    # Characters B274: at TL3 the listed B271 price buys good quality.
    # Campaigns B556: an ordinary sword breaks on critical-miss 3/4/17/18.
    # Author this fact locally; the historical canonical catalog stays unchanged.
    return entry.model_copy(
        update={
            "critical_breakage": "ordinary",
            "provenance": entry.provenance.model_copy(
                update={"pages": (*entry.provenance.pages, 274)}
            ),
        }
    )


EQUIPMENT: Final = EquipmentCatalog(
    profile_id=PROFILE_ID,
    entries=tuple(
        _campaign_equipment(entry)
        for entry in BASIC_EQUIPMENT.entries
        if entry.definition_id in EQUIPMENT_IDS
    ),
)
EQUIPMENT_SPECS: Final = EQUIPMENT.bind((PACKAGE,))


def compiler() -> CharacterCompiler:
    """Use the actual Basic Set compiler against this exact scoped package."""
    return CharacterCompiler(CATALOG, RULES, POLICY, statistics_profile=PROFILE_ID)


def reviewer(*, gm_ids: frozenset[str] = frozenset({GM_ID})) -> PowerReviewer:
    return PowerReviewer(
        compiler(),
        PowerPolicy(id="power-policy:roadside-watch", version=1, automatic_approval=True),
        gm_ids,
    )


def proposal(actor_id: str) -> CharacterProposal:
    """Return the campaign's fixed lawful proposal; clients never choose levels."""
    skills: tuple[tuple[str, int], ...]
    if actor_id == "a":
        name, attributes = "Mira, road guard", (11, 12, 11, 11)
        skills = (("broadsword", 8), ("stealth", 4), ("observation", 8))
    elif actor_id == "b":
        name, attributes = "Iven, scout and first-aider", (10, 11, 12, 11)
        skills = (("first-aid", 2), ("broadsword", 4), ("observation", 12), ("stealth", 12))
    elif actor_id == NPC_ACTOR_ID:
        name, attributes = "Road brigand", (10, 10, 10, 10)
        skills = (("broadsword", 2),)
    else:
        raise ValidationError("Unknown Roadside Watch character")
    return CharacterProposal(
        draft=CharacterDraft(
            name=name,
            purchases=tuple(
                Purchase(definition_id="attribute:" + key, amount=amount)
                for key, amount in zip(("st", "dx", "iq", "ht"), attributes, strict=True)
            )
            + tuple(
                Purchase(
                    definition_id="skill:" + key,
                    amount=points,
                    technology_level=3 if key == "first-aid" else None,
                )
                for key, points in skills
            ),
        )
    )


def require_pregen(actor_id: str, candidate: CharacterProposal) -> None:
    """Separate fixed campaign admission from general point-build legality."""
    if candidate != proposal(actor_id):
        raise ValidationError("Roadside Watch admits only its fixed character proposals")
