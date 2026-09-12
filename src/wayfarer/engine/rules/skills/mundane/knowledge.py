"""Knowledge, academic and investigation skill procedures (#341)."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.gurps_checks import RepeatedAttemptPolicy as P
from wayfarer.engine.rules.skills.mundane.procedures import Resolution as R
from wayfarer.engine.rules.skills.mundane.procedures import Task, bind

OWNER: Final = 341
KNOWLEDGE: Final = "campaign.knowledge"
ADMINISTRATION: Final = "campaign.administration"
LAW: Final = "campaign.law"
INVESTIGATION: Final = "noncombat.investigation"
SOCIAL: Final = "social.skill-procedure"
MOVEMENT: Final = "movement.approach"
NONCOMBAT: Final = "noncombat.approach"


def task(
    dispatch: str,
    effect: str,
    unit: str,
    *,
    resolution: R = R.SUCCESS_ROLL,
    policy: P = P.RETRY_UNTIL_SUCCESS,
    context: tuple[str, ...] = (),
    modifiers: frozenset[str] = frozenset(),
    margin: bool = False,
    cap: int = 0,
) -> Task:
    return Task(
        dispatch,
        effect,
        policy,
        unit,
        resolution,
        context,
        modifiers,
        int(margin),
        cap,
    )


STUDY = frozenset({"reference-quality", "time-spent", "familiarity", "complexity"})
ANALYSIS = frozenset({"records-quality", "time-spent", "complexity", "market-volatility"})
SENSE = frozenset({"distance", "visibility", "distraction", "concealment"})
TAIL = frozenset({"crowd", "distance", "visibility", "familiarity"})
DECEPTION = frozenset({"familiarity", "evidence", "demeanor", "distraction"})


def recall(effect: str, *context: str) -> Task:
    return task(
        KNOWLEDGE,
        effect,
        "fact",
        policy=P.UNKNOWN_UNTIL_LATER,
        context=context or ("question-present",),
        modifiers=STUDY,
        margin=True,
        cap=5,
    )


def analyze(effect: str, *context: str) -> Task:
    return task(
        ADMINISTRATION,
        effect,
        "analysis-step",
        context=context or ("records-present",),
        modifiers=ANALYSIS,
        margin=True,
        cap=6,
    )


def contest(
    dispatch: str, effect: str, unit: str, modifiers: frozenset[str], *context: str
) -> Task:
    return task(
        dispatch,
        effect,
        unit,
        resolution=R.QUICK_CONTEST,
        policy=P.UNKNOWN_UNTIL_LATER,
        context=context,
        modifiers=modifiers,
        margin=True,
    )


_ROWS: Final = (
    ("skill:accounting", "Accounting", analyze("audit-accounts")),
    (
        "skill:administration",
        "Administration",
        analyze("organize-institution", "organization-present"),
    ),
    ("skill:anthropology", "Anthropology", recall("interpret-culture", "culture-evidence")),
    ("skill:archaeology", "Archaeology", recall("interpret-archaeological-site", "site-evidence")),
    ("skill:area-knowledge", "Area Knowledge", recall("recall-local-fact", "location-question")),
    (
        "skill:body-language",
        "Body Language",
        contest(SOCIAL, "read-nonverbal-intent", "insight-margin", DECEPTION, "visible-subject"),
    ),
    (
        "skill:detect-lies",
        "Detect Lies",
        contest(SOCIAL, "detect-deception", "truth-margin", DECEPTION, "statement-present"),
    ),
    ("skill:economics", "Economics", analyze("model-economy", "economic-data")),
    ("skill:expert-skill", "Expert Skill", recall("apply-cross-disciplinary-expertise")),
    ("skill:finance", "Finance", analyze("structure-financial-plan", "financial-case")),
    (
        "skill:gambling",
        "Gambling",
        contest(SOCIAL, "win-game-of-chance", "gambling-margin", DECEPTION, "game-and-stakes"),
    ),
    ("skill:hidden-lore", "Hidden Lore", recall("recall-secret-lore", "hidden-lore-question")),
    ("skill:history", "History", recall("interpret-historical-evidence", "historical-question")),
    (
        "skill:law",
        "Law",
        task(
            LAW,
            "interpret-applicable-law",
            "legal-finding",
            context=("jurisdiction", "legal-question"),
            modifiers=frozenset({"precedent-quality", "time-spent", "jurisdiction-familiarity"}),
            margin=True,
            cap=5,
        ),
    ),
    ("skill:linguistics", "Linguistics", recall("analyze-language", "language-sample")),
    ("skill:literature", "Literature", recall("interpret-literary-work", "work-or-question")),
    ("skill:market-analysis", "Market Analysis", analyze("forecast-market", "market-data")),
    (
        "skill:observation",
        "Observation",
        contest(
            INVESTIGATION, "notice-deliberate-detail", "detection-margin", SENSE, "scene-present"
        ),
    ),
    ("skill:occultism", "Occultism", recall("identify-occult-practice", "occult-evidence")),
    (
        "skill:philosophy",
        "Philosophy",
        recall("apply-philosophical-school", "philosophical-question"),
    ),
    (
        "skill:religious-ritual",
        "Religious Ritual",
        recall("conduct-religious-ritual", "ritual-occasion"),
    ),
    (
        "skill:search",
        "Search",
        task(
            INVESTIGATION,
            "find-concealed-object",
            "found-clue",
            policy=P.UNKNOWN_UNTIL_LATER,
            context=("search-area",),
            modifiers=SENSE,
            margin=True,
            cap=5,
        ),
    ),
    (
        "skill:shadowing",
        "Shadowing",
        contest(MOVEMENT, "follow-without-detection", "shadowing-margin", TAIL, "moving-subject"),
    ),
    ("skill:sociology", "Sociology", recall("interpret-social-system", "society-evidence")),
    (
        "skill:speed-reading",
        "Speed-Reading",
        task(
            NONCOMBAT,
            "read-text-rapidly",
            "reading-progress",
            context=("text-present",),
            modifiers=frozenset({"text-complexity", "distraction", "time-pressure"}),
            margin=True,
        ),
    ),
    (
        "skill:stealth",
        "Stealth",
        contest(MOVEMENT, "move-without-detection", "stealth-margin", TAIL, "concealment-route"),
    ),
    ("skill:theology", "Theology", recall("interpret-doctrine", "doctrinal-question")),
)

PROCEDURES: Final = MappingProxyType(
    {identifier: bind(identifier, name, OWNER, selected) for identifier, name, selected in _ROWS}
)


def definitions() -> tuple[RuleDefinition, ...]:
    """Return concrete definitions; subject-selected family rows stay contextual."""
    return tuple(
        procedure.definition()
        for procedure in PROCEDURES.values()
        if procedure.dispatchable and procedure.spec() is not None
    )
