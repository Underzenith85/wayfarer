# Disease, infection, and aging (#519)

This engine slice implements the selected Campaigns fourth-printing procedures
at B442-B444 and the related Characters third-printing age boundary at B20-B21.
It is confined to the exact Basic Set profile and does not change the prerelease
engine version.

## Disease and contagion

A `DiseaseProfile` is trusted scenario data: it owns vector, resistance,
incubation, cycle cadence, damage, symptom threshold, recovery, immunity, and
any lasting consequence. An `ExposeDisease` command cannot supply those values.
It names a `ContactExposure` authored by the scenario, including the exact
relationship, contact category, carrier (when applicable), and an understood
protective measure. Merely sharing narrative space creates no exposure.

The end-of-exposure resistance check uses the recorded contact modifier plus
authored protection. Those modifiers do not carry into later recovery checks.
Failed exposure enters a private incubation stage; cyclic HT checks then either
end the illness or apply toxic injury through the ordinary injury reducer.
Active illness debt blocks natural recovery. Natural and acquired immunity are
durable per named disease profile.

Runtime records, rolls, carrier identity, receipts, and deadlines survive JSON
checkpoint/replay. A reconnect may settle all overdue cycles in chronological
order, while the shared resource clock refuses to step over an unresolved
disease deadline. Player projections reveal neither an incubation nor its
carrier; symptoms become visible, and the authored disease name appears only
after diagnosis. Director projections retain the full authoritative record.

## Wound infection

`WoundInfectionRisk` must cite an existing injury event and records when the
wound opened, when its infection check becomes due, contamination, antibiotics,
and any critical treatment failure. The check cannot run early. Effective
antibiotics prevent ordinary infection; otherwise the recorded B444 modifier is
applied to HT+3. Failure creates a normal disease episode, so recurrence,
damage, recovery restriction, restart, and exact-once behavior do not fork into
a second illness engine. The older postoperative surgery hook remains compatible
with this same disease semantics.

## Aging and permanent changes

Chronological aging runs only when an exact Basic Set `AgingRules` selection is
enabled. Unaging actors are rejected from scheduling. The schedule applies the
human 50/70/90 thresholds, annual/half-year/quarter-year cadence, lifespan
scaling, medical TL and fitness modifiers, four ordered HT rolls, and the
Longevity exception from B444. Alternative disadvantage substitutions and
setting-specific artificial youth are not silently synthesized.

Failed rolls produce a `PermanentChange`; they do not directly rewrite a saved
character. `ApprovePermanentChange` accepts only the exact pending attribute
loss and a nonempty approved build revision supplied by the character mutation
boundary. Applied losses live in a permanent health ledger, separate from HP,
recovery tasks, and rebuildable derived values. Ordinary healing or a checkpoint
rebuild therefore cannot remove them. Disease profiles use the same boundary
for authored lasting consequences.

Independent fixtures are in `tests/test_disease_aging.py`. They cover authored
contact and protection, private incubation, large time jumps, JSON restart,
exact-once replay, wound elapsed-time and antibiotic branches, dirty-wound
infection, profile-gated chronological aging, four-roll losses, and exact
permanent-mutation approval.
