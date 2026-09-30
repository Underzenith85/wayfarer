# Armoury restoration integration (#818)

The source-bound `restore-melee-weapon` and `restore-body-armor` effects now
identify work in the existing equipment repair transaction. The equipment
profile pins the approved actor skill and tools; the consumer checks the actual
weapon/armor kind before starting work. Pending and completed repair tasks keep
the exact procedure/effect identity alongside the actual condition, skill check
and restored HP.

These two effects reject standalone arts skill rolls. A receipt without an owned
item, work deadline and resulting durability is not evidence of restored HP.
The transaction retains its existing B484 numerical oracle: 1,800 seconds,
margin HP on success with a minimum of one, capped by missing HP; failure restores
zero. The independent cases already in `test_object_combat.py` remain the baseline
for cost difficulty, major repair penalty and spare-parts consumption. The new
`test_issue_818_armoury_repairs.py` exercises actual resulting weapon and body
armor durability for margins three, zero and failure, using the existing live
transaction and its authority, revision and exact-retry controls.

This change does not promote source or certification evidence. The selected
printings could not be reopened in this execution session; the formula above
reuses the existing B483–485 equipment implementation and recorded source review.
Cross-TL repair/tool rules still require direct source review and bounded follow-up
where unsupported. The other Armoury variants remain outside this PR's scope.
