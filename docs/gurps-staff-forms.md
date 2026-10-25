# Staff physical forms and held Deathtouch

The supplied Characters third printing (February 2008), B240 **Magic Staffs**,
distinguishes a nondamaging Reach C wand using Knife or Main-Gauche, a Reach 1
long wand/short staff functioning as a baton using Shortsword or Smallsword, and
a full-length staff using Staff or Two-Handed Sword. The latter uses the ordinary
quarterstaff modes, including their B273–274 Reach 1,2 rows. Campaigns fourth
printing (April 2008), B481 **Staff**, refers to that physical contract and prices
the enchantment at 30 energy. A generic magic-item rod need not possess Staff.

Fresh held Deathtouch contacts now bind the chosen pinned mode to the authenticated
Staff construction's skills and maximum reach. Only ordinary crushing thrust or
swing blows qualify. A quarterstaff mode cannot discharge a construction declared
as a short staff. A wand cannot borrow a damaging physical mode; its proper
nondamaging contact carrier remains unsupported. Physical construction alone does
not supply an enchantment, grant skill, change hands, or create an attack mode.
The existing current-custody, usable-enchantment, source-charge and target checks
still apply before dice and at physical settlement.

`tests/test_staff_forms.py` starts with mundane baton and short-staff items,
actually manufactures Staff through the 30-energy Slow and Sure procedure, casts
Deathtouch, and makes the ordinary one-handed Shortsword or Smallsword attack.
ST10 thrust with a physical roll of 4 causes 2 crushing injury; a separately rolled
3d Deathtouch packet of three ones causes another 3 injury. Those resulting HP,
spent charge, private pending identity, authority/stale refusal, exact restart
retry, fold and original-genesis seeded reexecution are checked in SQLite and
PostgreSQL. Mismatched forms refuse before dice and leave campaign/history intact.

The private `staff-form-contact` feature is captured at actual attack declaration,
and the contact retains `staff_form_generation: 1` for revalidation even if the
response command uses another generation. Historical feature absence omits that
field and preserves earlier contact validation and serialized records. No public
schema or source/certification status is changed.

This is a bounded #1015 Deathtouch consumer. Wand contact without a physical blow,
form admission for other held spells, broader Staff contact, and heavy barehanded
Parry knockback remain separate work. The full issue is not certified by these
tests.
