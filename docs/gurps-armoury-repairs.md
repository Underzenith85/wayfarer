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

The selected printings have now been reopened; the current numerical review and
per-criterion evidence are in [Armoury source review](gurps-armoury-source-review.md).
The same two consumers now use existing authored toolkit modifiers, preserve
exact fractional-price parts costs, and cover B178 shields and thrown weapons.
Trusted-GM performer/model/specialty familiarity observations also affect the
actual repair skill and restored HP. The written #818 baseline checklist is
covered by resulting-state, eligibility, conservation and replay evidence.
The explicit [recorded parts assessment](gurps-armoury-parts.md) admits
source-sufficient rolled stock for major repairs. The private [selected-time](gurps-armoury-selected-time.md) and
[verified-default](gurps-armoury-defaults.md) paths now bind their source-defined
choices to real repairs. Alternative/operating toolkit variants remain unverified
in #977, as recorded in the source review.
Familiarity acquisition remains separate unimplemented work.
No source/certification status or frozen request contract is promoted.
