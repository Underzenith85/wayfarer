# Current conditions in Acrobatic Dodge (#878)

The uploaded Campaigns fourth printing matches the selected source digest. B375
requires an Acrobatics roll before Acrobatic Dodge, or Aerobatics while flying.
B421 exempts this maneuver-free defensive reaction from temporary attribute
reductions. B361 rows 28–30 separately impose −2 on **all skill rolls** after
coma or catatonia recovery. That direct skill penalty still applies to the
preceding reaction check; it does not directly reduce Dodge.

The private `acrobatic-condition-checks` feature reads the existing Fright
runtime's aftermath at the authoritative current game time and includes its
recorded modifiers once in the reaction trace. A purchased skill of 8 therefore
checks against 6 during the aftermath. A roll of 7 fails, changing the ensuing
Dodge from 11 to 7 in the acceptance fixture and causing actual 3 HP injury.
At the exact six-hour expiry, the same roll succeeds against 8 and the Dodge
prevents the injury. Ground Acrobatics and airborne Aerobatics use the same
condition policy. Approved purchases, derived base skill and base Dodge remain
unchanged; purchased task bonuses still compose through their existing feature.

Fresh ordinary combat and typed opponent-defense commands capture this feature.
Historical recorded absence keeps the previous omitted aftermath. Exact retries
and seeded reexecution retain their captured semantics after the fresh feature
set changes. No public command or payload schema changes.

`tests/test_acrobatic_condition_checks.py` runs the existing B361 row-28 coma
producer and HT recovery before testing real Dodge settlement. It checks current
and expired aftermath, ground/air reaction traces, resulting injury, authority,
stale revision, transactional rollback, restarted exact retry, event folding and
seeded reexecution of direct and typed defenses on SQLite and PostgreSQL. Seeded
reexecution starts from the persisted post-recovery campaign snapshot; it does
not claim whole-campaign manufacture from original genesis.

B428 coughing, intoxication and retching name attribute penalties and do not
become direct skill penalties by inference. The existing B421 reaction projection
retains its attribute exemption. B428 Euphoria, Nauseated and Pain explicitly
name skill penalties, but their missing lawful irritant producers and broader
consumers remain unsupported. This correction neither adds those conditions nor
closes #878. Aerial/swimming concealment and specialized interpositions retain
their existing unresolved boundaries; no certification status is promoted.
