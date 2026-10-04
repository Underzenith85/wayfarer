# Wither Limb arm consequences

The private arm adapter composes Characters B244–245 with Campaigns B420–422:
successful resisted contact inflicts a separate 1d HP injury and permanent
functional arm crippling. Because this effect actually injures and cripples,
its magical packet requires one major-wound HT check even below the ordinary
limb injury threshold. This is a composed interpretation, rather than a printed
Wither-specific exception. It differs from Paralyze's zero-HP functional effect.

The ordinary physical blow retains its own damage and injury checks. The spell
packet receives neither physical critical multipliers nor weapon training,
Strong or location multipliers. First admission is an ordinary living human arm
with maximum HP at least ten and no relevant injury transformations. Thus the
largest magical die does not exceed the ordinary six-point arm threshold for
the smallest admitted body. Armor does not protect against this packet. Its
locationless x1 arithmetic carrier does not establish a general damage type or
Injury Tolerance exemption.

The canonical injury reducer owns HP, shock, consciousness, death and the
single magical major-wound check. A failed check drops held items before any
remaining arm retention check. Permanent arm crippling then drops a held
non-shield item or canonical hand-held buckler, with current DX retention for a multi-hand object that remains
held. A shield remains attached after arm crippling, but cannot Block and loses
one Defense Bonus; B287 standard shields are strapped, so magical knockdown also preserves them.
Canonical bucklers remain freely droppable on knockdown and on crippling
the hand that holds them. If knockdown already dropped one, arm settlement
does not drop it twice or draw a redundant retention check. Hand occupancy does not override
the profile discriminator. The ordinary historical injury reducer is unchanged. Equipment and
control grips are reconciled after both consequences. A permanent lasting fact
records zero additional injury because the separate wound already accounts for
the HP loss. It never enters the ordinary crippling-duration lottery.

`tests/test_wither_arm_consequences.py` is explicitly a reducer diagnostic on
genuinely manufactured Staff fixtures, not a Wither casting acceptance claim.
Its six SQLite/PostgreSQL cases prove a one-point packet causes permanent
crippling and exactly one major-wound trace, successful/failed two-hand retention,
and knockdown dropping without an extra DX draw. Canonical full HP recovery and
a year of elapsed diagnostic time retain the permanent fact. Duplicate effect
identity refuses before dice. `tests/test_wither_arm_host_consequences.py` separately proves actual registered
Wither casting, manufactured Staff arm contact and the independent spell/HT
contest on both stores. Physical injury leaves HP at nine; magical injury
leaves eight with one major-wound trace and permanent crippling. It exercises
retained/dropped two-hand weapons, strapped shield preservation on successful
and failed magical HT, and actual buckler drop on both successful and failed HT
with one drop and no redundant DX draw. It also proves shield DB/Block loss and
pre-RNG refusal of
a subsequent unusable weapon attack. A reconstructed service retries the exact
response without dice or state change, and store fold equals the checkpoint.
Full original-genesis seeded replay belongs to the separate family host suites.

Ordinary HP recovery does not restore the limb. A source-qualified magical
limb-restoration consumer is absent; this adapter does not invent one. Lower-HP
bodies, transformed anatomy, legs and unidentified defensive contact limbs
remain unsupported. Neither this adapter nor its diagnostics completes the
whole Body Control issue.
