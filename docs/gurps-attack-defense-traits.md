# GURPS Basic Set attack and defense traits

Issue #237 supplies a pinned 18-entry construction package for natural attacks, innate
attacks, active restraints, personal defenses, injury tolerances, survival traits, and
their disadvantages. Required variants and variable costs are trusted catalog data;
unknown or incomplete selections fail before build approval.

Approved builds project personal and eye DR, Striking ST, injury tolerance, vulnerability,
natural attack damage types, and extraordinary death thresholds. Runtime trait attacks use
GM-authored attacker, target, location, damage, resistance, duration, and armor-divisor
facts. Damage enters the existing injury reducer with target-build DR and vulnerability;
Affliction and Binding enter the shared active-effect clock. Vampiric Bite healing uses the
attacker's canonical HP pool. All paths enforce actor authority, compare-and-set revisions,
stable receipts, and restart-safe replay.

The selected-printing review and #237 implementation evidence are reconciled.
Issue #688 completes and verifies the final Injury Tolerance row: all structural
and additive forms have exact construction costs and inclusions, and their
anatomy, hit-location, wounding, choking, crippling, recovery and replay effects
run through the shared injury services.

Issue #760 adds the modifier-free Fatigue Innate Attack baseline at 10 points
per level (B61). Penetrating damage enters the canonical signed FP reducer
(B426), including the existing below-one-third ST/Move/Dodge penalties, Will
checks for continued exertion, HP consequences below zero FP, and
unconsciousness at negative maximum FP. Damage absorbed by the remaining positive FP never
enters the HP injury reducer. Very Fit does not halve attack damage; that loss
retains ordinary recovery accounting rather than being classified as a power
activation cost.

Machine immunity is an explicit persisted Basic Set physiology fact on the
target's canonical HP injury state. Unliving Injury Tolerance alone does not
imply that fact. A nonmachine target still requires explicit matching FP and HP
pools. Attacker authority, revision checks, missed attacks, exact-command retry
and checkpoint replay are covered by `tests/test_fatigue_innate_attack.py`.

These regression expectations use issue #760 and the repository's existing
selected-source fatigue review. The supplied printings could not be reopened
in the implementation session because the execution service disconnected.
This change does not promote any certification row; Fatigue attack modifiers,
non-torso targeting and broader combat-channel integration remain unverified.
