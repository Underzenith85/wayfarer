# Deathtouch through the Haste prerequisite alternative

Characters B244 allows Hinder to be learned through Clumsiness or Haste. Its
Paralyze Limb prerequisite requires five other Body Control spells, including Pain.
Haste belongs to Movement and cannot supply that Body Control count. In the
second supported route, Itch, Spasm, Pain, Hinder and Rooted Feet provide five
distinct lawful Body Control purchases; purchased Haste qualifies Hinder.
Paralyze Limb, Wither Limb and Deathtouch then follow their printed chain, with
the compiler checking the required Magery and preventing circular witnesses.

The selected-hand and manufactured-Staff Deathtouch hosts now admit this exact
approved route as well as the existing Clumsiness route. Both use one private
qualification function that checks every route definition against the pinned
catalog and requires actual purchases. Knowing Haste through this prerequisite
route does not activate Haste or change the host's restrictions on active spells.

`test_deathtouch_haste_learning_route.py` starts from original approved builds
without Clumsiness, completes actual selected-hand casting and punch contact
with an independent magical HP packet, and manufactures a Staff before actual
Staff casting. Restarted completion retries retain their original receipt even
after hand discharge. Original-genesis seeded reexecution verifies the accepted
commands for both carriers. Removing Haste, Rooted Feet, Pain, Paralyze Limb or
Wither Limb prevents approved genesis. Tests retain SQLite and PostgreSQL
parameters; PostgreSQL requires the existing test DSN configuration.

No command schema or persisted generation changes. The existing accepted route
keeps its qualification and mechanics. This bounded addition does not establish
other prerequisite combinations, combat casting, broader anatomy, exceptional
mana or complete Body Control certification.
