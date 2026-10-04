# Paralyze Limb arm consequences

The private Paralyze arm effect represents an actual contacted arm as crippled
for one minute, with zero additional HP injury. This composes Characters B244
with the functional arm consequences in Campaigns B421. It does not invent a
damage-caused major-wound check for the zero-HP spell; an ordinary physical blow
continues to apply all its normal injury checks. This is a composed source
interpretation, rather than a printed exception to the injury rules.

The source effect uses a timed `crippled` lasting injury, independently of current
HP. The arm's hand cannot wield a weapon or maintain a grip. A held non-shield
item drops; a two-handed item has the ordinary current DX retention check. A
shield remains attached, loses one Defense Bonus and cannot Block with the
affected arm. A retained two-handed weapon still needs a usable grip to attack
or parry. The effect removes an affected actual grip without recreating it at
expiry. At the exact deadline the timed fact becomes inactive, while another
lasting injury continues to prevent use. Recovery does not automatically ready
dropped equipment, stand the actor or recreate a removed unarmed control grip.
A never-dropped two-handed weapon retains its original hand bindings; expiry
restores its eligibility without an inventory grant or an invented new Ready action.

`tests/test_paralyze_arm_consequences.py` proves these canonical consequence
reducers on SQLite and PostgreSQL using genuinely manufactured Staff campaign
fixtures. Its tests are explicitly diagnostics: they do not substitute for the
registered Paralyze casting, actual limb contact and independent HT resistance
tests. The spell family records the lasting fact, deadline, actual dropped items
and any grip checks in its own immutable contact result. Duplicate effect
identity is refused before dice; command-level exact retry belongs to that
family's durable receipt.

`tests/test_paralyze_arm_host_consequences.py` separately proves real registered
Paralyze casting through a manufactured Staff, actual left-arm attack and the
independent contact skill/HT contest on both stores. Its ordinary one-point
physical blow leaves HP at nine; the spell adds no HP loss or major-wound dice.
The matrix demonstrates real two-handed retention/drop, shield DB/Block
consequences and pre-RNG refusal of a subsequent unusable weapon attack. A
reconstructed service retries the exact accepted response without dice or
state change. Actual GM disengagement then lawful waiting proves eligibility
immediately before and at the deadline without recreating dropped equipment;
no natural crippling-duration roll is introduced. The separate family suites
own broader casting/resistance and original-genesis seeded replay evidence.

The adapter does not change B557's existing timed `disabled` shoulder result or
ordinary wound duration/HP payloads. Wither, leg consequences, broader anatomy
and magical restoration are separate consumers. This bounded arm adapter alone
does not complete the Body Control issue.
