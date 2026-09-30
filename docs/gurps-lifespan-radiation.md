# Lifespan and radiation trait consumers (#753)


The lifespan projection now preserves exact rational Short Lifespan factors.
The authoritative aging reducer accepts the current approved physiology
projection and replaces the purchased Extended Lifespan, Short Lifespan, and
Unaging facts in its scenario rules. Technology level and explicitly authored
Longevity remain independent. First checks use the scaled fiftieth birthday;
annual, half-year, and quarter-year intervals use scaled ages 70 and 90.
A changed projection recomputes the next deadline from that birthday or the
last settled check. Unaging suspends the deadline and freezes biological aging time. Removing it
resumes eligibility at the frozen age; time spent Unaging does not create
retroactive aging checks. Retries return the saved result without reapplying a transformation.

The radiation service activates the current approved character for every new
command. Effective incoming dose divides by both equipment PF and purchased
Radiation Tolerance, once. The same effective dose feeds the HT-table target,
retained dose, and original-dose decay ledger. Removing tolerance affects new
exposures; previously absorbed dose is not multiplied or reinterpreted.

`tests/test_lifespan_radiation_traits.py` supplies independent numerical
expectations, trait removal, suspension/resumption, CAS, authority, serialization,
and real SQLite radiation-service restart cases. Aging currently has no gameplay
service caller; its integration is the authoritative pure reducer boundary.
The existing whole-rad ledger rounds incoming dose down; fractional-rad
accumulation remains outside this change. Other physiology variants remain
outside this evidence.

Source locators are B53/B79/B95/B444 and B435-436. This change uses the existing
repository source-review context; the supplied PDF printings could not be
reopened during implementation because the execution environment was offline.
This evidence does not by itself promote source-verification or certification
status.
