# Coughing during Acrobatic Dodge

The existing selected Campaigns fourth-printing B428 condition consumer records
coughing as a −3 penalty to DX rolls and DX skills. B375's preceding Acrobatics
or airborne Aerobatics roll is a DX skill check. B421's exemption for temporary
attribute loss does not exempt this direct skill penalty.

The private `acrobatic-coughing-conditions` generation applies one −3 coughing
penalty to that reaction check. It reads active Characters third-printing B109
Symptoms, timed hazard afflictions, active authored environmental coughing, and
timed toxin conditions. Finished exposures retain the penalty until their
condition expires. Multiple carriers of coughing supply one condition penalty.
Other actors' conditions and expired conditions do not affect the defender.
The adapter leaves attribute loss, other afflictions, and the actual Dodge score
to their existing consumers.

With purchased Acrobatics 8 and coughing, a reaction roll of 8 fails against 5,
providing Dodge −2 instead of the previous +2. The matching Aerobatics example
uses the same rule. Direct combat and typed task commands capture the policy
privately; recorded absence preserves the previous result and dice sequence.

`tests/test_acrobatic_coughing_conditions.py` and
`tests/test_acrobatic_coughing_replay.py` cover actual defense and injury,
expiration and actor isolation, overlapping carriers, preview entropy, receipts,
and seeded direct/typed reexecution. This bounded consumer does not certify all
B428 conditions or complete issue #878.
