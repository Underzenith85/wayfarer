# One-gallon Create Water mist

Characters B253 specifies pure permanent water at two energy per gallon, with a
one-gallon mist extinguishing fires in a one-yard radius. This bounded host admits
exactly that quantity; it does not extrapolate mist coverage for larger volumes.
It uses the existing square-grid radius-one cell convention and rejects hex,
active combat, unknown placements, and multi-cell fire footprints.

The director records the complete current scene positions, complete single-cell
fire-source footprints, and complete single-cell burning-object footprints through
the authenticated canonical Water transaction. An object's owner position alone
does not establish its extent. Footprints must match the current item identity,
definition, owner, and individual quantity. Create Fire and its exposures must
match the actual canonical spell footprint. Unknown sources, held fireballs,
moving sprayer clothing fires, ambiguous ground/contained objects, and burning
spent-item snapshots fail closed. Historical spent records are preserved.

The private cast plan captures the selected scene admission. New scene admission,
changed fire inventory, changed placement, or unsettled exposure deadlines reject
the pending cast before dice. Successful approved casting atomically ends enclosed
Create Fire effects, disables enclosed fire exposures, clears enclosed objects'
burning conditions, and records one pure gallon of permanent mist separately from
liquid inventory. Fires outside the selected cell remain active. No collection,
evaporation, wet-object damage, dilution, or fluid rate is assumed. Ordinary failed
casts charge the existing failure cost and change neither fire nor material.

Host tests create a real approved Create Fire in combat and lawfully end the
encounter before casting mist. They prove successful and failed costs, live fire
and object consequences, source extent refusals, authority, superseded-scene
rollback, privacy, restart and exact retries, event-fold replay, seeded command
reexecution, and cessation of future fire exposure. A separate ordinary fire
exposure test settles its real hazard deadline before extinction and advances the
clock afterward without subsequent fire injury. The pre-change admission oracle
failed because the real Water host could not accept mist intent.

Remaining issue #804 scope includes other mist volumes/geometry, moving clothing
fires, arbitrary object footprints, physical collection and flows, midair globes,
fractional gallons, partial heterogeneous purification, accelerated large-ring
rates, and deep/refilling water. This bounded implementation does not close the
whole issue.
