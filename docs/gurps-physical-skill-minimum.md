# Voluntary physical skill minimum (#1013 follow-up)

Campaigns fourth printing B345 does not permit attempting a voluntary skill check
with an effective skill below 3. The native physical host previously clamped low
scores to 1 and could allow a natural 3 or 4 to succeed. Fresh source-bound
physical commands now check the applicable voluntary skill's exact effective
score before injury-turn or continued-exertion checks can draw entropy, interrupt
rest, spend resources or advance time.

The check uses the same target helpers as the actual native roll: Climbing's
approved skill/default, surface, encumbrance and shock; purchased Lifting;
individual Hiking's uncached daily skill/default; and intentional Swimming's
initial or check-due skill/default, entry adjustment and encumbrance. The same
condition-modifier helper supplies the active command generation's penalties to
both admission and the resulting check. A target of 3 remains legal. This does
not reinterpret a daily hiking result already recorded, or certify the separate
hourly-mileage convention.

The private `physical_skill_minimum_generation` is independent of enchanting
rest admission. New relevant commands capture its version 1 and their trusted
physical route. The route remains a server-side fact; the public command still
names only a route ID. Unknown versions, including booleans, refuse. A recorded
rest-generation-1 command without the new minimum retains its old score,
payload, retry and reexecution. Adding a minimum check does not give Climbing or
Hiking a new nighttime enchanting admission.

The scope excludes involuntary Swimming, group Hiking, optional controlled falls,
and mandatory minute-end Swimming fatigue, drowning, injury, consciousness,
continued-exertion, Fright and defense checks. Those retain their own rules and
low-attribute behavior. No global success-roll function changes. The dedicated
Swimming rest path continues to validate its whole interval and hazard boundary
before this voluntary check admission.

`tests/test_physical_skill_minimum.py` observes effective skills 2 and 3 in actual
Climbing, Lifting, individual Hiking and intentional Swimming. Skill 2 refuses
with HP and FP already at zero, before either the mandatory health checks or the
chosen skill can consume dice; persisted state remains unchanged. Skill 3 uses
the actual natural 4, physical progress, native duration and appropriate fatigue
cost, then folds and seed-reexecutes from authored genesis on SQLite and
PostgreSQL. Historical low-skill Lifting retains its earlier result through
restart and seeded replay. An involuntary Swim at HT 1 still performs both the
survival roll and minute-end fatigue roll at their native low targets.

An actual source-produced drowsy toxin also distinguishes condition generations:
the historical DX behavior leaves the selected Climbing target at 4, whereas
current B428 drowsiness reduces it to 2 and refuses before dice. Neither the
condition modifier nor the skill formula is duplicated in the admission path.
This bounded repair changes no source-ledger or certification status.

## Explicit unassisted lifting

B353's ordinary two-handed lift permits 8×Basic Lift in four seconds without a
skill bonus. Owning Lifting does not remove that base action. The trusted route
now supports `use_lifting=False` to choose it explicitly even when Lifting is
purchased. The shared native/admission target helper then selects no optional
skill roll, while ordinary strength, fatigue, health and duration rules remain.
The default remains `True`, retaining skill assistance and its minimum-3 guard.
This is a server-authored route fact; the public command gains no new field.

The common route encoder omits default `True` everywhere, including private
captures and physical result bindings. Older captures omit that field and decode
to `True`, preserving their bytes and digests. Explicit `False` is retained;
nonboolean values and unknown fields refuse. A source-bound HT 3 actor with
purchased Lifting 2 now completes the unassisted 160 lb base lift in four seconds
with an empty dice source, unchanged HP/FP and no skill check. The assisted choice
still refuses before dice. Both stores cover restart, original-genesis seeded
reexecution and the historical rest-generation-1 capture without the new field.
The separate Will-based extra-effort rules are not introduced by this choice.
