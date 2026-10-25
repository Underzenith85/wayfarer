# Offered resistance and the Rule of 16

Campaigns B349 caps a supernatural attack that offers resistance against a living
or sapient subject, whether that subject elects to roll resistance or waives it.
Characters B106 permits the Malediction subject to make that choice. Characters
B241 likewise distinguishes willing subjects from the availability of resistance.

Fresh approved composed Malediction source bindings capture the private
`malediction_waiver_generation: 1` input and the source's omitted-when-false
`malediction_waiver_cap` policy. The caster uses current effective resistance to
calculate the cap even when the subject waives the roll. Waiving still draws no
resistance dice and performs no resistance contest. It does not invent damage or
change the existing injury reducer.

Direct resistance responses and delayed opponent-Luck preparation share the same
helper and approved source policy. Once a caster check is captured for a delayed
choice, its target and modifiers remain fixed. Later target changes cannot
rescore that check. Historical source bindings omit both new fields and retain
their original uncapped waiver targets during retries and command reexecution.

The source and input remain private under the existing composed-source event
and genesis policies. This correction establishes the offered-versus-elected
resistance distinction for the existing composed Malediction host; it does not
certify all supernatural abilities, creature bodies, or Magic Staffs.

`tests/test_rule_of_16_waiver.py` checks actual approved purchases and source
bindings, direct injury, zero defender dice, higher resistance, delayed Luck,
authority, retry, folding and independently seeded original-genesis reexecution
on SQLite and PostgreSQL. The source-derived numeric oracle is separate from the
seeded replay witness.

The fresh host proves eligibility independently of the resistance choice. Approved
baseline IQ6+ establishes sapience (B23), including sapient unliving subjects.
Otherwise it uses approved Injury Tolerance and trusted current anatomy: ordinary
living subjects remain eligible at low IQ; explicitly unliving nonsapient
subjects are exempt. Homogenous/diffuse damage structures do not establish
livingness, so nonsapient targets with those structures or missing body facts
refuse before attack entropy. Temporary IQ penalties do not change sapience.
The private context omits its default true eligibility field so historical task
captures retain their previous serialized form.

A partial Injury Tolerance purchase such as No Blood uses `living` as its damage
profile default. That default supplies no positive biological fact and cannot
override explicitly unliving trusted anatomy. Missing biology remains unsupported
for a nonsapient subject even when that partial purchase exists.

The separate engine-authority creature special-attack command now requires a
strict `target_living_or_sapient` fact for fresh calls. Compiled sapient targets
reject a contradictory false fact before dice. Older commands omit the field and
retain their exact digest and seeded outcome through the explicit historical
`replay_creature_combat` path; exact accepted retries settle before fresh scope
validation. The creature source case checks a failed attack's unmodified target
against an explicitly nonliving, nonsapient subject, without claiming toxic
injury or a live database host for that pure resource API.

A damaging composed target with purchased Injury Tolerance also needs explicit
injury anatomy. Fresh sources check that admission before direct or task-original
dice and again before a delayed Luck choice draws rerolls. This check uses the
target whose injury will be projected; it does not constrain a mental-only host's
unrelated anatomy or rescore a captured caster check.
