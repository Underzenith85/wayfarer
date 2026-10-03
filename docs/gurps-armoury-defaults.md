# Armoury defaults in actual repairs

Basic Set B168, B173, B178 and B190 define the training/default rules; B169
model familiarity, B345 tools, B346 selected work time and B483–485 actual
object restoration remain separate inputs. This private opt-in path binds those
rules to the existing repair transaction. It adds no public gameplay schema.

A trusted current director records `DeclareArmouryTraining`: the performer's
current approved-build revision, personal TL and the concrete Armoury
specialties known in their society. The command cannot author an effective
skill. The performer then selects `SelectRepairDefault` for an owned damaged
item and one future repair-start ID. Selection and start both resolve the current
approved build, canonical skill definitions, custody, full object condition,
equipment profile, tools and ability to work. A default choice is immutable for
that start ID; selection itself rolls no dice and spends no parts.

Supported source edges are IQ−5 (IQ capped at 20), a genuinely purchased
same-specialty Engineer−4, and another genuinely purchased Armoury specialty−4.
Armor/weapon cross-specialty defaults are unavailable above training TL4. A
source known only by default cannot supply another default. B173 credit in a
skill with actual purchased points remains legitimate learned skill; the
resolver retains that approved level. Target-specific effects are retained only
against the exact canonical target definitions, whose recorded default is IQ−5.
Wrong specialties and missing current build, society or TL evidence fail closed.

The default supplies its training TL. The current item's concrete TL produces
the existing B168 IQ penalty exactly once. An explicit director familiarity
observation supplies its independent −2 for an unfamiliar model. For an
unpurchased target, that observation must name the already selected future
repair start and pass its current proof; it cannot infer a default or accept a
skill label. Existing purchased-specialty familiarity commands retain their
historical path.

`SelectRepairDefault.repair_time_method`, when supplied, declares the default
and one canonical B346 time choice together. Both private ledgers commit in one
transaction. This supports otherwise unusable ordinary targets rescued by extra
time without a parts roll or a partially committed intent. A later standalone
time choice can use a selected default too. Effective targets below three refuse
before parts RNG or consumption. `AssessRepairParts.repair_start_command_id`
explicitly connects a parts eligibility check to the same selected default/time
proof; its own command ID, durable die, cost and retry identity do not change.

Actual accepted starts capture the resolved skill (including familiarity and work
method), training TL, item TL, penalty, parts requirement and deadline in the existing task.
Finish retains that accepted numeric snapshot even when later approved training
changes. Current item condition/profile, custody, tool availability and the
existing assessed-parts/time linkage still must match. Failure restores zero HP;
success restores margin HP, minimum one, bounded by missing HP. Parts are consumed
once at start and are not returned on failed work.

## Source-bound Small Arms carrier

Canonical firearm rows have no implicit repair-profile migration. A director may
explicitly configure an otherwise unchanged canonical Small Arms firearm row
with `firearm_repair_profile.profile`: B483 unliving HP from its actual printed
weight, DR4, HT10, the portable Armoury toolkit and the selected spare-parts
inventory definition. Admission requires that exact derived profile and retains
the entire canonical row, including price, TL, mode, ammunition and reload rules.
Only the new private selected-default path binds Small Arms to B484. Existing
unselected generic Small Arms tasks and B407 malfunction servicing are unchanged.

The approved canonical Engineer (Small Arms) provides the lawful matching edge.
There is no canonical Engineer (Body Armor) here, and Engineer (Materials) is not
relabelled as it. Those unavailable/wrong edges refuse. Alternative toolkit
operating rates, unsupported object/material profiles and unimplemented firearm
mechanics remain unverified; neither a catalog label nor this profile asserts
that those consumers have been completed.

## Acceptance evidence

`test_armoury_defaults.py` covers real repair outcomes:
Body Armor IQ/cross-Armoury and canonical firearm Engineer defaults, success and
failure, actual parts/time, separate familiarity, selected extra time and refusal.
`test_armoury_default_consumers.py` proves a disabled canonical flintlock cannot
Ready, then repair restores its actual HP, allows the printed reload and fires a
real round that injures the target. Repaired Body Armor also resumes real DR6
protection against a five-point melee hit. The TL cases pin the penalty once.

`test_armoury_default_provenance.py` uses an approved IQ21 construction to prove
the Rule of 20 in an actual task, and a genuinely purchased Engineer construction
with default credit to distinguish learned skill from default-only chaining.
`test_armoury_default_guards.py` covers authority, stale/current physical setup,
atomic rollback, exact retry, private projection, authored-genesis rejection,
historical omitted-field bytes, pre-start build refusal and accepted-snapshot
retention. `test_armoury_default_replay.py` independently folds and reexecutes
recorded whole-command campaigns from their original genesis with deterministic
entropy. Every persistence scenario is parameterized for SQLite and PostgreSQL.
