# Basic Set mundane skill inventory (#112)

`rules/mundane_skills` accounts for the Characters skill chapter without making
unimplemented procedures playable. The candidate package is `0.3.0`; no saved
campaign pin or live representative definition changes.

Accounting for an entry never makes a skill playable. A row becomes executable
only when a runtime module binds it to a service that already resolves it and a
**new** package pin carries that definition; the candidate package here stays
separate, immutable and hookless. See
[Ranged combat procedures](#ranged-combat-procedures-344) and
[Social procedures](#social-procedures-345) and
[Technology, science and vehicle procedures](#technology-science-and-vehicle-procedures-346)
and [Discipline-keyed specialties](#discipline-keyed-specialties-356)
for the groups bound this way.

## Source boundary and completeness

The supplied **Characters, Fourth Edition, third printing (2008), ISBN
978-1-55634-729-0** is the observed source. `source_index.json` records its SHA-256
and independently indexes B301–B304, with explicit chapter expansions at
B168–B233. This observation is not verification of the selected first-printing
plus 2007-01-26 errata baseline. That reconciliation remains #336, using #191's
audit machinery. No rulebook prose is bundled.

| Source accounting | Entries |
| --- | ---: |
| Indexed skill listings, B301–B304 | 275 |
| Named technique listings, B304 | 27 |
| Explicit chapter examples and parent-specific expansions | 154 |
| **Source index total** | **456** |

The combined Combat Art or Sport listing maps to two candidate records. Thus
456 source entries map to **457 records: 429 mundane and 28 transferred** to
#119's inventory. The expansions include the seven concrete Thrown Weapon
specialties #344 expands from the B226 family, the 39 concrete Boating, Driving,
Piloting, Shiphandling, Submarine and Explosives specialties #346 expands from
theirs, and the 75 concrete science, electronics, engineering and Mechanic
specialties #356 expands from the discipline-keyed families. Specialty families
remain explicitly blocked where context or expansion is incomplete. These counts
do not claim enumeration of every possible player-defined specialty.

Index reconciliation rejects missing records, unindexed additions, overlapping
transfers, invalid expansion parents and page drift. It runs when consumers load
the inventory, including the source-certification report. The exclusion transfer
also verifies names, pages and owners against the supernatural catalog.

## Accounting matrix

`python -m scripts.audit_mundane_skills` generates the report from the typed data.

| Accounting group | Rows | Decision |
| --- | ---: | --- |
| Structured candidate definitions | 179 | Unsupported; source/runtime blockers remain. |
| Bound runtime procedures | 222 | Implemented and dispatched by #344 (12), #345 (16), #346 (83), #356 (83) and the TL-indexed and crew-served ranged rows (#354, #355, #357); still blocked by the printing delta, so still unavailable here. |
| Contextual records | 28 | 23 B230-233 technique templates and five open families (#336). Not rollable skills, so they record a shape rather than a definition. No row is left recording nothing at all. |
| Transferred cinematic/supernatural skills | 28 | Owned by #242/#243 and source audit #191. |
| **Total accounted records** | **457** | **Zero available mundane candidates.** |

This revision fills the previously empty Aerobatics, Aquabatics, crewman, suit
and weapon entries; records Weather Sense as a TL-dependent Meteorology alias;
and accounts for Brain Hacking, Melee Weapon and every named technique. B208–B209
weapon defaults include category cross-defaults. The scope of Force Sword's
"any sword" default remains explicitly blocked. Brain Hacking's cross-package
prerequisite, variable families and context-sensitive definitions remain blocked.

The candidate package now owns its numeric data instead of inheriting it from
live representative definitions. B230 Arm Lock (Judo) and B231 Kicking (Karate)
are typed parent-relative techniques. A named template such as ST-based Neck Snap
is not converted into an ordinary DX skill.

| Structural class | Rows |
| --- | ---: |
| `attribute-default` | 315 |
| `skill-default` | 44 |
| `no-default` | 62 |
| `technology-level` | 223 |
| `required-specialty` | 158 |
| `unexpanded-specialty` | 59 |
| `listing-only` | 28 |
| `technique-template` | 24 |
| `variable-family` | 9 |
| `alternative-prerequisite` | 1 |
| `technique` | 6 |
| `prerequisite` | 4 |
| `optional-specialty` | 1 |
| `alias` | 1 |

Classes overlap, and a class describes what a row records structurally while
`implementation` describes its certification state: the 28 `listing-only` rows
record no rollable definition, and 28 of them are `contextual` because they do
record a technique template or an open family. Four of the nine `variable-family`
rows do carry a definition: B180 Biology is IQ/VH whichever planet type it
covers, so those rows record the numbers and leave only the subject open. `no-default` means no default is
recorded, not a claim that conditional defaults have been exhaustively verified. Fixtures sample every
class with independently stated source expectations.

## Remaining ownership

Every mundane row retains #112 for accounting, #336 for source/context work,
and a named procedure owner. The report maps each blocker to its owning issue;
`runtime_owner_unassigned` is zero. Historical mechanics issue references are
retained where previously recorded, but they do not replace the active owners.

| Owner | Remaining scope |
| --- | --- |
| #336 | Complete. The contextual shapes landed; everything it could not settle without the artifact or campaign state names one of the four children below. |
| #382 | Verifying the frozen first-printing baseline against the source artifact. |
| #383 | Conditional skill defaults and the remaining alternative prerequisites. |
| #384 | Technology-level context for TL-tagged skills, and optional-rule selection. |
| #385 | The remaining required and optional specialty families, and the one unexpanded technique template. |
| #338 | Arts, crafts and trade procedures. |
| #339 | Melee, defense and tactical skill procedures. |
| #340 | Combat technique procedures and parent-specific dispatch. |
| #341 | Knowledge, investigation and professional information procedures. |
| #342 | Medicine and mental procedures. |
| #343 | Physical, outdoor and animal procedures. |
| #344 | Ranged combat skill procedures; see below for what it bound and what it transferred. |
| #360 | The Spear Thrower launcher procedure. |
| #361 | Innate Attack specialties beyond Projectile. |
| #362 | Cross-specialty and conditional defaults for ranged combat skills. |
| #345 | Social skill procedures; see below for what it bound and what it transferred. |
| #346 | Technology, science and vehicle procedures; see below for what it bound and what it transferred. |
| #353 | Conditional and alternative mundane skill defaults and prerequisites. |
| #356 | Complete. The discipline-keyed families are expanded and bound; the four whose axis is a world, a planet type or a species name #390 below. |
| #390 | Procedures for the open-subject families, once a campaign names a subject. |
| #358 | Vehicle movement and combat capability verification for the bound vehicle rows. |
| #366 | Fortune-Telling and Savoir-Faire specialties. |
| #367 | The Propaganda technology-level media context. |
| #368 | Interrogation coercion and its reaction consequences. |
| #369 | Teaching and Leadership advancement and group-activity bindings. |
| #370 | Social skill audience reactions, income and material outcomes. |

Each procedure follow-up lists its exact candidate IDs and must reuse existing
authoritative services. Accounting completion does not certify those procedures.

## Ranged combat procedures (#344)

`rules/mundane_skills/ranged.py` is the only place a listed ranged combat row
becomes executable. A row is implemented when the module binds it to the ranged
dispatch that already resolves it (`orchestration/gurps_ranged`), declares the
exact weapon modes it governs and names a registered capability
(`gurps.combat.ranged_weapon_skills`, #344). Naming a procedure never implements
one, and neither does a generic target calculation: a weapon whose mode falls
outside its skill's class is refused before dice by
`simulation.gurps_equipment.require_skill_procedure`, which runs when an
equipment catalog is built and again when a mode is selected in play.

| Row | Reference | State |
| --- | --- | --- |
| `skill:bow` | B182, DX/A, DX-5 | Implemented. Two-handed launcher, pinned missile, one shot, no recoil ladder, and the only skill that may carry a B270 bow rating. |
| `skill:crossbow` | B186, DX/E, DX-4 | Implemented. Launcher with a pinned missile and the B270 crossbow rating. |
| `skill:sling` | B221, DX/H, DX-6 | Implemented. Launcher with a pinned missile. |
| `skill:blowpipe` | B180, DX/H, DX-6 | Implemented. Launcher with a pinned missile. Poisoned ammunition is an ammunition mechanic, not part of this skill. |
| `skill:thrown-weapon` | B226, DX/E, DX-4 | Family expanded into seven concrete specialties (Axe/Mace, Dart, Harpoon, Knife, Shuriken, Spear, Stick) and never dispatched itself. |
| `skill:thrown-weapon-*` | B226, DX/E, DX-4 | Implemented. The projectile is the item; it leaves active inventory and is retained in `expended_items`. |
| `skill:bolas` | B181, DX/A | Implemented. A landed throw binds the target; the binding, not the damage, is the outcome. |
| `skill:net` | B211, DX/H | Implemented. Cross-skill defaults remain with #362. |
| `skill:spear-thrower` | B222, DX/A, DX-5 | Transferred to #360 and #362; a launcher that modifies a projectile it does not consume. |
| `skill:guns` | B198, DX/E, DX-4 | Family expanded into eight concrete specialties (Pistol, Rifle, Shotgun, Submachine Gun, Light Machine Gun, Musket, Grenade Launcher, Light Anti-Armor Weapon); never dispatched itself. |
| `skill:beam-weapons` | B179, DX/E, DX-4 | Family expanded into three concrete specialties (Pistol, Rifle, Projector); never dispatched itself. |
| `skill:guns-*`, `skill:beam-weapons-*` | B198, B179, DX/E, DX-4 | Implemented. TL-indexed: each dispatches a weapon of the campaign's own era. Cross-specialty defaults remain with #362. |
| `skill:artillery` | B178, IQ/A, IQ-5 | Family expanded into six concrete specialties (Beams, Bombs, Cannon, Catapult, Guided Missile, Torpedoes); never dispatched itself. |
| `skill:gunner` | B198, DX/E, DX-4 | Family expanded into five concrete specialties (Beams, Cannon, Machine Gun, Rockets, Torpedoes); never dispatched itself. |
| `skill:artillery-*`, `skill:gunner-*` | B178, B198 | Implemented. Fired from a served mount rather than a grip. Cross-specialty defaults remain with #362. |
| `skill:liquid-projector` | B205, DX/E, DX-4 | Family expanded into four concrete specialties (Flamethrower, Sprayer, Squirt Gun, Water Cannon); never dispatched itself. |
| `skill:liquid-projector-*` | B205, DX/E, DX-4 | Implemented. Holds a stream second by second. Lingering fire and simultaneous area coverage are published scope owned by #398. |
| `skill:innate-attack` | B201, DX/E, DX-4 | Transferred to #361. The Projectile specialty is already dispatched by the opt-in spell adapter under its own pin; reconciling it here is an explicit migration. |

### Bindings (#354)

An entangling weapon mode carries pinned `EntangleSpec` facts: the binding's own
ST, the penalties it imposes on the victim's attacks and active defenses,
whether it pins the legs, whether it stays on the target, and an optional
trained escape skill. Nothing is inferred from a skill name, damage type or
weight, and a binding is Basic-only and must be a single thrown weapon.

A landed, undefended throw binds the target through the ordinary ranged
dispatch: the projectile still resolves its damage, and the binding is recorded
on the combatant in the encounter checkpoint. Breaking free is a Ready maneuver
(`escape_entanglement`) resolved as a quick contest of the victim's ST — or the
binding's pinned escape skill when the victim actually has a level in it —
against the binding's ST. A win frees the victim; a loss records the attempt, so
a replayed encounter reproduces the struggle instead of restarting it. While the
binding holds it applies its attack and defense penalties through the shared
melee, ranged and defense services, and a binding that pins the legs reduces
Move to zero. Evidence is in `tests/test_entangling_attacks.py`.

### Technology level (#355)

Guns (B198) and Beam Weapons (B179) are TL-indexed families. Their concrete
specialties come from the B301-B304 index and each is a distinct row with no
cross-specialty inference. A TL-indexed skill dispatches only when the campaign
policy pins a `technology_level` and the weapon's own equipment technology
level matches it: a weapon from another era fails closed rather than resolving
with an invented familiarity penalty, and an unpinned campaign era refuses the
skill outright. Tight-beam damage is resolvable only by the beam rows, and the
conventional firearm metadata #372 pinned is refused on a beam weapon.
Evidence is in `tests/test_tl_indexed_ranged_skills.py`.

### Mounts and crews (#357)

A mounted weapon mode carries pinned `MountSpec` facts: the crew it needs, the
seconds of laying an indirect shot takes, whether it is laid indirectly, and
whether it travels with a vehicle. Mounts are Basic-only, and a mount is never
thrown or entangling.

Serving a mount is a Ready maneuver by the gunner (`mount_crew`), and the
assignment is durable inventory state, so a restart finds the same crew. Firing
requires the gunner to be in the crew, the crew to be at full complement, and
every member still to be serving it — an unserved mount is refused, and the
mount is not a weapon the gunner simply holds. Because the mount bears the
weapon, the grip and hand-binding rules and the minimum-ST penalty do not apply
to it. A shot laid indirectly arrives without warning: the target gets no
active defense against it. Evidence is in
`tests/test_mounted_ranged_skills.py`.

### Streams (#359)

A liquid projector mode carries pinned `SprayerSpec` facts: how long a single
stream can run, the rounds every second of it costs, and whether it ignites its
target. Streams are Basic-only, and a stream is never thrown, entangling or
rapid-fired.

Attacking with a projector opens a stream on the target and records it on the
combatant. Every further second is a separate attack that pays its rounds again
and rolls again, and the stream may be walked to a new target without reopening
it. It ends when its holder does anything other than keep pouring the same
weapon, and it cannot run past its own sustained-seconds ceiling.

Two named parts of the entry are not carried here and are published as
transferred procedure scope under #398 rather than folded into a blocker:
lingering fire after the stream stops, and one second of stream covering
several combatants at once. Evidence is in
`tests/test_liquid_projector_streams.py`.

A binding may only resolve or keep the blockers the inventory recorded, and its
numbers must be the recorded ones: `inventory()` raises on either drift, and on
a kept blocker that names no owner. A blocker a procedure owner splits keeps
naming the child that owns it in `blocker_owners`, so a transfer stays visible
instead of resolving into silence. Every row still carries
`first-printing-delta-audit`, so a bound procedure reports as `implemented` and
remains unavailable.

The definitions live in a new pin, package `0.7.0` with profile version 7
(`rules/profiles.py`). Existing v2–v6 campaign pins resolve byte-for-byte
unchanged; switching a campaign still uses the existing explicit migration.
Evidence is in `tests/test_ranged_skills.py`.

## Social procedures (#345)

A social row is implemented only when `rules/mundane_skills/social.py` binds it to
a service that already resolves it — `rules.gurps_checks` for success rolls and
contests, `rules.gurps_social.influence_roll` for the six B359 influence skills —
declares the shape that decides it, and names a registered capability
(`gurps.social.skill_procedures`, #345). Naming a procedure never implements it.

Each bound procedure declares the contextual conditions it cannot proceed without,
the modifiers it derives itself, and a named effect for every verdict its shape can
reach. Conditions are facts about the situation, never numbers: an authored trigger
asserts `credible-threat` and the procedure owns what it is worth. A missing
required condition rejects before dice.

| Row | Recorded | Bound resolution |
| --- | --- | --- |
| `skill:acting` | B174, IQ/A | Quick Contest against the observer. |
| `skill:carousing` | B183, HT/E | Success roll; B183 goodwill of +2, or -2 and 1 FP on a critical failure. |
| `skill:diplomacy` | B187, IQ/H | B359 influence roll, keeping the better ordinary reaction. |
| `skill:fast-talk` | B195, IQ/A | B359 influence roll; the subject reacts at -3 once he realizes. |
| `skill:gesture` | B198, IQ/E | Success roll at the less fluent party's level. |
| `skill:interrogation` | B202, IQ/A | Regular Contest against the subject's Will. |
| `skill:intimidation` | B202, Will/A | B359 influence roll. |
| `skill:leadership` | B204, IQ/A | Success roll. |
| `skill:lip-reading` | B205, Per/A | Success roll. |
| `skill:panhandling` | B212, IQ/E | Success roll. |
| `skill:performance` | B212, IQ/A | Success roll. |
| `skill:politics` | B215, IQ/A | Quick Contest. |
| `skill:public-speaking` | B216, IQ/A | Success roll. |
| `skill:sex-appeal` | B219, HT/A | B359 influence roll; a win is Very Good. |
| `skill:streetwise` | B223, IQ/A | B359 influence roll. |
| `skill:teaching` | B224, IQ/A | Success roll. |

Three rows keep `runtime-procedure` because they cannot resolve at all yet:
`skill:fortune-telling` and `skill:savoir-faire` are not learnable without their
required specialties (#366), and `skill:propaganda` has no medium, reach or
duration without a technology level (#367). Those rows are absent from the pin.

A bound row can still leave a named part of its entry elsewhere. That is not a
blocker — the roll runs — so it is published as `transferred_procedure_scope`
rather than folded into the blocker list: coercion for Interrogation (#368),
advancement and group activity for Teaching and Leadership (#369), and audience
reactions, income and outlay for Carousing, Panhandling, Performance and Public
Speaking (#370). Conditional and alternative defaults stay with #353.

The B97 Voice bonus reaches Diplomacy, Fast-Talk, Leadership, Performance,
Politics, Public Speaking and Sex Appeal through
`character.social_traits.skill_conditions`, which reads approved purchases only,
so the build asserts the condition and the procedure owns the +2.

The definitions live in a new pin, package `0.8.0` with profile version 8
(`rules/profiles.py`). Existing v2–v7 campaign pins resolve byte-for-byte
unchanged; switching a campaign still uses the existing explicit migration.
Evidence is in `tests/test_social_skills.py`, with the declared table and every
expected result pinned by hand in `tests/fixtures/gurps/social_skills.json`.
Runtime behaviour is described in [the social runtime](gurps-social-runtime.md).

## Technology, science and vehicle procedures (#346)

`rules/mundane_skills/technology.py` is the only place a listed technology row
becomes executable. A row is implemented when the module binds it to a service
that already resolves it, declares the exact task it governs, and produces a
quantity that service consumes. Scoring stays in `rules/gurps_checks`, so no
second engine exists. Naming a procedure never implements one, and neither does
a generic target calculation: a family row, a transferred row and a skill outside
this group are all refused before dice by `technology.require_task`.

| Dispatch | Service | What it owns |
| --- | --- | --- |
| `transport.vehicle-control` | `simulation/transport` | Loss of control, skid, collision and occupant injury. |
| `hazard.exposure` | `simulation/hazards` | Scheduled exposure for a broken seal or placed ordnance. |
| `object.repair` | `simulation/object_repairs` | Recorded repair work and restored HP. |
| `noncombat.approach` | `simulation/noncombat` | Progress and revealed facts for an information task. |

| Row | Reference | State |
| --- | --- | --- |
| `skill:boating` `skill:driving` `skill:piloting` `skill:shiphandling` `skill:submarine` | B180, B188, B214, B220, B223 | Families expanded into 34 concrete specialties and never dispatched themselves. |
| `skill:boating-*` `skill:driving-*` `skill:piloting-*` `skill:shiphandling-*` `skill:submarine-*` | as their family | Implemented. Operator control: skill plus Handling, which no other procedure accepts. |
| `skill:crewman` | B185 | Family expanded into Airshipman, Seamanship, Spacer and Submariner. |
| `skill:airshipman` `skill:seamanship` `skill:spacer` `skill:submariner` | B185, IQ/E, IQ-4 | Implemented. A rated station is held, not steered, so Handling is refused. |
| `skill:environment-suit` | B192 | Family expanded into the four concrete suits, with their recorded cross-defaults. |
| `skill:battlesuit` `skill:diving-suit` `skill:nbc-suit` `skill:vacc-suit` | B192, DX/A, DX-5 | Implemented. Failure hands the scheduled exposure to the hazard service. |
| `skill:explosives` | B194 | Family expanded into five concrete specialties. |
| `skill:explosives-*` `skill:traps` `skill:set-trap` `skill:work-by-touch` | B194, B226, B233 | Implemented. Emplacement; failure is hazardous. |
| `skill:mathematics` | B207 | Family completed by the six recorded specialties. |
| `skill:mathematics-*` `skill:physics` `skill:physics-acoustics` and the other information rows | B176–B217 | Implemented. The margin decides how much is learned, capped where the task is a single object. |
| `skill:electrician` | B189, IQ/A, IQ-5 | Implemented. Repair progress scales with the margin. |
| `skill:no-landing-extraction` | B233 | Implemented. Bought against the concrete Piloting specialty flown, so it carries that control dispatch. |
| `skill:motion-picture-camera` | B233 | Transferred to #338; its parent Photography belongs to that group, and a parent with no dispatch cannot lend one. |
| `skill:bioengineering` `skill:biology` `skill:current-affairs` `skill:disguise` `skill:electronics-operation` `skill:electronics-repair` `skill:engineer` `skill:geography` `skill:geology` `skill:hazardous-materials` `skill:mechanic` `skill:paleontology` | B180–B212 | Transferred to #356; their specialty axis is a discipline, not a vehicle class, so expanding them here would be a guess. See below for what #356 then bound. |

Two modifiers belong to the procedure: the B168 technology-level difference (one
point of effective skill per level, either direction) and the B169 familiarity
penalty. A caller's situational ruling stays a separate typed modifier in the
receipt. Conditional defaults and alternative prerequisites are not implemented
and keep naming #336.

Every bound vehicle row also records `gurps.vehicles.movement`, which is still
`partial`; #358 must verify it before live play may offer those rows. The
capability registry, not a typed task, is what says so, and
`technology.unsupported_scope` publishes the gap to the scenario, character and
LLM validators as `unverified_activation_scope` rather than leaving live play to
discover it. #358's per-mode audit says exactly what each mode still owes and to
which of #392-#397; see [the vehicle operation audit](gurps-vehicles.md).

These definitions are **not** yet carried by a package pin. Two things must be
settled first, and `tests/test_technology_skills.py` pins both so neither is
discovered by a broken build: `skill:physics` and `skill:physics-acoustics`
already exist in the pinned package as representative definitions on the
`check.target` hook, so binding them changes what those ids mean; and the
recorded Diving Suit default reaches `skill:scuba`, which another group owns, so
these definitions do not resolve as a standalone catalog. Evidence for the
bindings themselves is in `tests/test_technology_skills.py` and
`tests/fixtures/gurps/technology_skills.json`.

## Discipline-keyed specialties (#356)

The twelve rows #346 transferred are keyed to a science discipline, an
electronics family, an engineering discipline, a machine type, a subject area or
a species. They are expanded and bound in the same
`rules/mundane_skills/technology.py` module and through the same four dispatches;
no second engine exists, and a family row is still refused before dice.

| Row | Reference | State |
| --- | --- | --- |
| `skill:bioengineering` `skill:engineer` | B180, B190 | Families expanded into 3 and 10 concrete disciplines. Implemented as design tasks: the margin buys further design steps. |
| `skill:current-affairs` | B186 | Family expanded into eight topics. A recall roll is capped: a margin of four still yields the three items the topic holds. |
| `skill:electronics-operation` `skill:electronics-repair` | B189, B190 | Both keyed to the same nine equipment families. Only the repair row adds Computers, because B184 Computer Operation is the skill that uses one. Operation produces a reading; repair produces restored HP. |
| `skill:hazardous-materials` | B199 | Family expanded into Biological, Chemical and Nuclear/Radiological. Containment is held rather than learned, so failure is hazardous and reaches the exposure service. |
| `skill:paleontology` | B212 | Family expanded into three concrete disciplines; the margin decides how much is found. |
| `skill:mechanic` | B207 | Family expanded into 29 machine types **derived** from the vehicle specialties #346 recorded, never authored a second time. Shiphandling is a command skill and contributes none; an unpowered hull carries no machinery. Repairing a machine needs no vehicle-movement capability of its own. |
| `skill:biology` `skill:disguise` `skill:geography` `skill:geology` | B180, B187, B198 | Open families. Their axis is a planet type, a species or a region, so the row records the axis the player names instead of an invented list, and keeps its own fixed numbers. Transferred to #390, which must instantiate a named subject before anything dispatches. |

The mechanics of a specialty are its family's: same attribute, same difficulty,
same recorded defaults, same page. A specialty that rolled against different
numbers would be a different skill. Cross-specialty defaults are not implemented
and keep naming #383, and the Engineer prerequisite — a related science or shop
skill, which one depending on the specialty — stays with it too.

Evidence is in `tests/test_technology_specialties.py`, with every effective
target, margin, outcome and unit count pinned by hand in
`tests/fixtures/gurps/technology_specialties.json`.

## Contextual catalog metadata (#336)

Three shapes let the source be recorded as it is stated instead of flattened into
something the compiler happens to support. All three are catalog metadata: none
of them makes a skill playable, and every row still carries the printing delta.

**Alternative prerequisites.** B168 states several prerequisites as "A or B".
`SkillSpec.prerequisite_groups` records each alternative set, and the skill
compiler requires every firm prerequisite plus one satisfied member of each set.
A set of one is rejected, because that is a firm prerequisite in disguise. B223
Surgery — First Aid, Physician or Veterinary — is the case the audit already
recorded as unflattenable, and it is the one this issue states; the other rows
that need an alternative set keep their blocker under #383 rather than a guess.

**Cross-package prerequisites.** B182 Brain Hacking requires Computer Hacking,
which the #119 supernatural catalog carries. `cross_package_prerequisites()`
resolves the reference against that catalog and fails if the owner ever drops it,
so a real source reference is neither dropped for being out of scope nor invented
locally. Such a target is excluded from this inventory's own cycle graph.

**Technique templates and open families.** A B230-233 technique bought against
Judo and against Karate is two distinct skills, so a template records the parents
the source permits and expands to a concrete parent-relative technique only for
one of them; expanding against any other parent raises. Where the source permits
a whole class rather than a list ("any melee weapon skill"), the template names
the open family instead. B232 Neck Snap keeps its own ST attribute rather than
inheriting its parent's. The five open families — Combat Art, Combat Sport, Hobby
Skill, Melee Weapon, Professional Skill — record that the player names the
specialty and how its mechanics are then determined, because an enumeration would
be an invention rather than a reconciliation.

An unused alternative set is pruned from a package's canonical JSON exactly as
absent skill and trait metadata already is, so adding the shape moves no digest of
a package pinned before it existed. `tests/test_contextual_metadata.py` pins that,
along with the compiler behaviour and every recorded template and family.

What this issue could not settle is split into four children, each owning specific
blockers rather than a share of a general one: **#382** the frozen first-printing
verification every row waits on, **#383** conditional defaults and the remaining
alternative prerequisites, **#384** technology-level context and optional-rule
selection, **#385** the remaining specialty families. `blocker_owners` names them
per row, so #336 itself keeps nothing.

## Validation and runtime contract

Candidates in this package have unsupported status and no runtime hooks. `require_available`
rejects unknown IDs, blocked rows and unsupported definitions even if their
blocker list is mistakenly cleared. Scenario/character/LLM validation therefore
cannot turn catalog presence into playable mechanics.

Reference checks cover defaults, prerequisites, specialty/technique parents,
aliases and source-index targets. Prerequisite, technique and alias cycles fail;
valid reciprocal defaults remain source data. Tests independently assert numeric
suit/crewman/weapon defaults, technique caps and pages, the complete source-index
classes, deletion detection, owner propagation and exclusion transfer integrity.
