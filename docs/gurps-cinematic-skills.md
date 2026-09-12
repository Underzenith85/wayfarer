# GURPS Basic Set cinematic skills

Issue #242 accounts for all 23 cinematic skills identified by the Basic Set
inventory. Their controlling attributes, difficulties, `/TL` marker, pages,
and explicit acquisition prerequisites are immutable package data. They use the
existing skill compiler, so point progression, skill ceilings, prerequisite
levels, profile selection, and approved-build revisions are not reimplemented.

The bounded runtime accepts only a skill present on the approved character
sheet, checks actor authority and current location, applies the source's shared
concentration ladder where applicable, spends catalog-owned FP costs, and uses
the common 3d6 check service. Commands use compare-and-set revisions and the
resource receipt ledger; retries and JSON restarts return the original outcome
without rerolling or spending FP again. Check history is visible only to the
acting character or the GM.

Free-form narrative consequences and procedures requiring broader combat,
invention, influence, or knowledge adjudication remain explicit boundaries.
Inventory rows stay `partial` pending source certification issue #191.
