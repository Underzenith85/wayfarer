# Timed noncombat encounters during enchanting rest (#1013)

The existing selected-source B481 calendar distinguishes an eight-hour Slow and
Sure shift from the remaining nightly rest. A freshly captured ordinary
`approach_noncombat` may now run during that rest, using its existing authored
`CheckRule.duration`. The full half-open interval is checked against the current
project schedule before target dice, fatigue, HP consequences, discovery or clock
advancement. An approach may finish exactly when the next shift starts; beginning
in work or continuing into a shift refuses without committing any consequence.

This is an ordinary noncombat consumer of the recorded calendar. It neither
interrupts the project implicitly nor awards another mage-day, changes committed
extra energy, moves the project's completion deadline, or establishes subgroup
clock authority. Current actor control, approved build, scene/check identity,
resources and noncombat eligibility still govern the approach. Enrolled mages
must resolve an existing supernatural concentration before starting this timed
activity. Other pending project/activity obligations retain their existing
checks.

New approach inputs carry the private `enchanting_rest_generation: 1` envelope
marker. Public command fields are unchanged. That same captured generation also
passes the compiler's existing approved skill context into live target
recomputation: purchased definitions and amounts, TLs, capability prerequisites
and campaign defaults retain the facts from character compilation. Current
equipment effects and the authored check modifier still apply. This repairs the
previous failure where recomputing an ordinary skill in an approved Enchant build
lost its Magery prerequisite context. No separate skill engine is introduced.

Unmarked historical inputs retain their earlier project-busy admission and live
target recomputation. Live exact retries read the stored generation; seeded
reexecution reads the captured command rather than today's defaults. Ordinary
noncombat commands now have a registered replay adapter. Choice creation and
withdrawal retain their previous zero-time behavior.

`tests/test_enchanting_noncombat_nighttime.py` begins actual 30-energy Staff work
with two approved mages. A ten-second authored Observation approach after the
first shift rolls against 17, spends one authored FP and reveals its bound fact;
the project and its 1,238,400-second deadline remain unchanged. The exact-boundary
case starts at 86,390 and finishes at 86,400. Starting one second later or during a
shift rejects before entropy or costs. Separate cases exercise authored failure
costs, an equipped +1 and authored -2 producing target 16, current pending casting,
unauthorized/stale commands, rollback, concurrent exact retries, restart, event
folding and original-genesis seeded reexecution on SQLite and PostgreSQL. A
historical unmarked command also retains its exact input on retry and reexecutes
its earlier behavior.

Grouped project clocks, active-work casting, scene travel, physical procedures,
combat, ceremonial casting and the other #1013 activity families remain separate
unverified consumers. This slice does not promote any source-ledger or whole
Basic Set certification status.
