# B345 minimum for the existing HazardCare Diagnosis host

This bounded follow-up depends on the exact frozen Lifesaving/fixture candidate
`a22b6664fed6149775481dbef271f170cc8e9858b44dec17a49a2a8c2200cf23`.
It adds no medicine-family or capability certification.

B345 prohibits a voluntary success roll below final effective skill 3. Diagnosis
is a voluntary skill roll: B187 defines its lawful numerical defaults and B443
requires a successful diagnostic roll for an apparent disease. Previously, this
host clamped a base score to at least 1 and could roll a known Diagnosis default
of 2. A native roll of 3 would then succeed and permanently enable treatment.

Fresh Diagnosis now captures private hazard-care generation 2, with its exact
trusted context and retained public input. Generation 2 is valid only for
Diagnosis. An explicit independent diagnosis_minimum bit controls that rule;
existing generation 1 context capture does not imply the new floor. Generation
1 Lifesaving keeps its reviewed minimum, generation 1 Diagnosis keeps its
historical computation, and generation 1 antibiotics keeps its no-roll behavior.
No absent historical marker is interpreted as a current opt-in.

After the existing actor, patient, condition, capability, approval, knowledge and
attempt checks, the service prepares the canonical Diagnosis score and IQ
modifier tuple once. It checks their final sum before drawing the original or
writing a diagnostic attempt, benefit, cost or time change. The same score and
modifier tuple feed the actual roll. Corrected inputs receive no fabricated
minimum of 1 or synthetic failed check.

The existing trusted callback must identify the treatment kind and targets
before this context-dependent skill admission can occur. Authentication, stale
revision, boundary and exact Basic profile checks remain before that callback.
An exact retry uses the already captured context and consumes no new callback
or original dice. Byte-exact public identity, digest, strict versions, context
shape, duplicate private keys and whitespace-conflict rules remain intact.

Tests use actual approved TL8 actors and authenticated society-known Diagnosis:
IQ7/8/9 give lawful defaults 1/2/3. A paid point at IQ6 gives Diagnosis4; actual
DrinkingService tipsy/drunk states supply IQ penalties of -1/-2, producing legal3
and illegal2. The refused check leaves both selected drinking originals intact.
Actual stop, two-hour wait and sobriety make the same unconsumed command ID
lawful at a later revision. Failed legal originals still consume their native
once-per-symptom-cycle attempt; refusals do not.

A successful diagnosis enables the existing bound antibiotic dose. Antibiotics
continues to consume one real item, gives its existing bacterial-disease bonus,
and has no skill roll. The mandatory-patient control resolves a valid authored,
already-due native disease schedule at HT2 with a real roll3 after voluntary
Diagnosis2 refuses. It is evidence for the existing resolve host, not a claim
about a new public exposure producer. No global success-roll or health floor is
introduced.

Both stores exercise current and historical versions, exact retries, event fold,
current full original-seed replay, old captured generation1 original-seed replay,
privacy and actual state consequences. Uncaptured generation0 retains its exact
retry and event-fold contract; no missing historical trusted callback is invented
for full reexecution.

Equipment, physiology and rare-disease modifiers not represented by this host,
obvious-injury no-roll routing, broader Diagnosis coverage and other medical
consumers remain separate inventory. No public fields, broader antibiotic or
mandatory-HT behavior, source-audit disposition or capability status is changed.
