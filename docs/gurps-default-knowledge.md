# Explicit societal eligibility for ordinary skill defaults

Characters B173, “Who Gets a Default?”, permits a default only for someone from
a society where the skill is known. Package availability, purchased Unusual
Background, current equipment and campaign TL do not establish that fact.
Campaigns B344's ordinary success-roll, best-default, Rule of20 and NoDefault
rules still apply independently.

The native private `DefaultKnowledgeService` supplies two actual GM commands:

* `AdoptDefaultKnowledge` selects this policy for one approved actor and records
  an optional explicit list of skills known in that actor's society. An empty
  baseline means unknown, not permission to infer knowledge.
* `DeclareDefaultKnowledge` records positive or negative observations for named
  concrete skills. The authenticated principal must be both deployment-trusted
  and seated as the campaign GM. The performer is a separate identity. Commands
  cannot author a target, modifier, purchase or training level.

Historical generation 1 retains its explicit unadopted migration boundary.
[Current generation 2](gurps-default-knowledge-current.md) requires actual GM
eligibility even for an unadopted actor, without choosing their society or
creating adoption. Neither generation certifies all skill procedures or B344 by
itself.
Adoption remains sticky across configuration changes: old skill observations
stop authorizing changed definitions, but the actor does not silently return to
legacy mode. The GM can supply current observations or a new explicit baseline.

Facts are append-only, configuration-bound private receipts. Their reader checks
identity, owner, chronology, generation, duplicates and observation shape. Trusted
genesis paths reject forged receipts; player campaign and event projections do
not reveal private reasons or generation metadata. GM declarations spend no FP,
advance no game time and do not rewrite approved purchases, build revisions,
package pins or configuration digests.

The shared `DefaultContext.known_default_skills` projection gates only ordinary
skills without purchased points. Explicit known facts do not create a missing
default, bypass prerequisites or UB admission/payment, or grant trained benefits.
Paid skills retain learned levels and point-equivalent default credit. Techniques
retain their separate B229 parent-relative contract; this change does not infer
new technique restrictions from the ordinary skill-default paragraph.

Actual ordinary check recomputation and approved-sheet consumers use the same
GM decisions. Current player skill views omit unavailable unpaid defaults while
retaining the approved underlying build. Staff uses its explicitly corrected
B208 declaration package, and the handwritten B201 Innate Attack fallback checks
the target specialty's current permission before rebuilding any DX/specialty
fallback. A negative fact cannot be ignored simply because the skill row was
removed from the projected sheet.

An independent private producer generation preserves exact historical input and
principal bytes. Old omitted generation stays legacy even after adoption. Attack
and Wait markers pin the initiating actor's adoption; a fresh defender cannot
replace the attacker's policy, and a legacy attacker cannot disable the defender's
own current eligible defense skill. Unrolled attempts under adopted policy use
current facts. Already captured originals, paid missile originals, accepted
ordinary/secret originals and Luck choices retain their numerical context rather
than discarding entropy or recalculating after a fresh setting changes. Queued
checks and rulings retain their originating policy independently of their later
resolver commands. Existing task boundaries also prevent replacing a pending
original with an unrelated GM command.

The bounded medical join projects the shared recovery build. First Aid and
Physician cannot read an unauthorized unpaid sheet value; the direct treatments
can refuse before acquiring new environment facts. Already accepted CareTask
skills and existing hazard schedules remain captured. Physical, group hiking,
new drowning exposure and hazard care have additional explicit handwritten
fallback joins; see the companion physical inventory and its source-bound tests.
This does not certify medical treatment, lifesaving or group hiking's separate
minimum-skill rules.

## Evidence and remaining closure work

The actual GM producer, ordinary Inspect/Social, real Staff attacks, composed
Innate Attack, defender Parry, Wait, queued checks, rulings, secret/ordinary task
originals, opponent Luck and a paid Fireball original are exercised on SQLite and
PostgreSQL. Controlled seeds distinguish real hit/injury/discovery outcomes;
whole retained command sequences fold and reexecute from original genesis.
JSON object-key ordering is normalized only where existing replay contracts do
so; exact original command bytes and retry receipts remain separately checked.

`test_default_roll_source_examples.py` uses actual catalog definitions for the
literal B344 Lockpicking IQ10→5 and IQ25→15, Interrogation best default
max(7,11,9)=11, an uncapped skill-source default and Alchemy/Karate/Light with no
training. Native adopted ordinary checks bind the Lockpicking arithmetic to
actual success rolls, disclosure and seeded replay. They do not claim completion
of Lockpicking's tools, locks or timing, nor Interrogation's question lifecycle.

This slice legitimately supplies B173 societal eligibility evidence and B344
arithmetic/ordinary-roll evidence in explicitly adopted configurations. It does
not promote the whole skill_defaults capability, all skill descriptions, the
B173 chapter, or B344 source-section status.

Current generation 2 implements the mandatory eligibility step while preserving
captured legacy histories; its companion inventory records the native source
cases and entry-point boundaries for section review. Automatic society inference
is not implemented. The separate passive healing Physician threshold has only a
provisional Veterinary→Physician default witness, not a confirmed native host
finding; no speculative healing change or completion claim is included. Existing
specialized Armoury training/default proofs remain authoritative in their own
captured work lifecycle; the current companion documents their combined factual
chronology without claiming complete repair certification.
