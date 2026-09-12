"""Medical, psychological and mental discipline procedures (#342)."""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

from wayfarer.engine.rules.catalog import RuleDefinition
from wayfarer.engine.rules.gurps_checks import RepeatedAttemptPolicy as P
from wayfarer.engine.rules.skills.mundane.procedures import Resolution as R
from wayfarer.engine.rules.skills.mundane.procedures import Task, bind

OWNER: Final = 342
MEDICAL: Final = "recovery.medical-treatment"
MENTAL: Final = "noncombat.mental-procedure"
SOCIAL: Final = "social.skill-procedure"
HAZARD: Final = "hazard.exposure"
KNOWLEDGE: Final = "campaign.knowledge"


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


CARE = frozenset({"equipment-quality", "time-spent", "patient-condition", "facility"})
DIAGNOSIS = frozenset({"symptom-quality", "time-spent", "patient-history", "facility"})
MIND = frozenset({"distraction", "time-spent", "stress", "familiarity"})
INFLUENCE = frozenset({"rapport", "time-spent", "resistance", "environment"})
HAZARDS = frozenset({"equipment-quality", "visibility", "exposure", "time-pressure"})


def care(effect: str, *context: str, margin: bool = True) -> Task:
    return task(
        MEDICAL,
        effect,
        "treatment-step",
        context=context or ("patient-present",),
        modifiers=CARE,
        margin=margin,
        cap=6 if margin else 0,
    )


def mind(effect: str, *context: str) -> Task:
    return task(
        MENTAL,
        effect,
        "mental-state-step",
        context=context,
        modifiers=MIND,
        margin=True,
        cap=5,
    )


def influence(effect: str, *context: str, regular: bool = False) -> Task:
    return task(
        SOCIAL,
        effect,
        "influence-margin",
        resolution=R.REGULAR_CONTEST if regular else R.QUICK_CONTEST,
        policy=P.UNKNOWN_UNTIL_LATER,
        context=context,
        modifiers=INFLUENCE,
        margin=not regular,
    )


_ROWS: Final = (
    ("skill:autohypnosis", "Autohypnosis", mind("induce-self-trance", "quiet-focus")),
    (
        "skill:brain-hacking",
        "Brain Hacking",
        influence("restructure-conditioned-mind", "prepared-subject", regular=True),
    ),
    (
        "skill:brainwashing",
        "Brainwashing",
        influence("install-conditioning", "captive-subject", regular=True),
    ),
    (
        "skill:diagnosis",
        "Diagnosis",
        task(
            MEDICAL,
            "identify-condition",
            "diagnostic-finding",
            policy=P.UNKNOWN_UNTIL_LATER,
            context=("patient-and-symptoms",),
            modifiers=DIAGNOSIS,
            margin=True,
            cap=5,
        ),
    ),
    ("skill:dreaming", "Dreaming", mind("control-or-interpret-dream", "dream-state")),
    (
        "skill:erotic-art",
        "Erotic Art",
        influence("provide-intimate-performance", "consenting-partner"),
    ),
    ("skill:esoteric-medicine", "Esoteric Medicine", care("apply-esoteric-treatment")),
    (
        "skill:exorcism",
        "Exorcism",
        influence("expel-possessing-influence", "possessed-subject", regular=True),
    ),
    ("skill:first-aid", "First Aid", care("stabilize-injury", "injured-patient")),
    ("skill:hypnotism", "Hypnotism", influence("induce-hypnotic-trance", "attentive-subject")),
    ("skill:meditation", "Meditation", mind("restore-mental-equilibrium", "quiet-focus")),
    (
        "skill:mind-block",
        "Mind Block",
        task(
            MENTAL,
            "resist-mind-reading",
            "defense-margin",
            resolution=R.QUICK_CONTEST,
            context=("mental-intrusion",),
            modifiers=MIND,
            margin=True,
        ),
    ),
    ("skill:pharmacy", "Pharmacy", None),
    (
        "skill:pharmacy-herbal",
        "Pharmacy (Herbal)",
        care("prepare-herbal-medicine", "ingredients-and-patient"),
    ),
    (
        "skill:pharmacy-synthetic",
        "Pharmacy (Synthetic)",
        care("prepare-synthetic-medicine", "laboratory-and-patient"),
    ),
    ("skill:physician", "Physician", care("provide-physician-care")),
    (
        "skill:physiology",
        "Physiology",
        task(
            KNOWLEDGE,
            "analyze-species-physiology",
            "medical-fact",
            policy=P.UNKNOWN_UNTIL_LATER,
            context=("anatomical-question",),
            modifiers=DIAGNOSIS,
            margin=True,
            cap=5,
        ),
    ),
    (
        "skill:poisons",
        "Poisons",
        task(
            MEDICAL,
            "identify-prepare-or-treat-poison",
            "poison-procedure-step",
            context=("poison-task",),
            modifiers=CARE | DIAGNOSIS,
            margin=True,
            cap=6,
        ),
    ),
    (
        "skill:psychology",
        "Psychology",
        task(
            KNOWLEDGE,
            "assess-psychology",
            "psychological-finding",
            policy=P.UNKNOWN_UNTIL_LATER,
            context=("subject-behavior",),
            modifiers=MIND,
            margin=True,
            cap=5,
        ),
    ),
    (
        "skill:scuba",
        "Scuba",
        task(
            HAZARD,
            "operate-self-contained-breathing-gear",
            "safe-exposure-step",
            context=("diving-gear", "underwater"),
            modifiers=HAZARDS,
            margin=True,
        ),
    ),
    ("skill:surgery", "Surgery", care("perform-surgery", "surgical-patient-and-tools")),
    ("skill:veterinary", "Veterinary", care("treat-animal-patient", "animal-patient")),
)

PROCEDURES: Final = MappingProxyType(
    {identifier: bind(identifier, name, OWNER, selected) for identifier, name, selected in _ROWS}
)


def definitions() -> tuple[RuleDefinition, ...]:
    """Return concrete procedures; the Pharmacy family itself never dispatches."""
    return tuple(
        procedure.definition()
        for procedure in PROCEDURES.values()
        if procedure.dispatchable and procedure.spec() is not None
    )
