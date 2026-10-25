# Supported condition self-control and duplicate drowsiness

Campaigns fourth printing B428 gives Tipsy −2 and Drunk −4 to self-control,
except Cowardice, and Drowsy −2 without that exception. Self-control uses the
approved disadvantage's self-control number, independently of Will (Characters
third printing B121). Situational self-control modifiers still apply to Cowardice.

Fresh social generation 3 and later binds the private canonical trait identity
to an implemented, purchased disadvantage with its approved self-control options
and supported runtime hooks. An unknown or unpurchased identity refuses before
rolling. Trusted direct social dispatch and authored NPC occurrences use the same
binding, including occurrences during task time. The captured identity survives
retries and seeded command reexecution. Historical social generations 0–2 retain
their original condition-free self-control calculations.

Fresh Symptoms generation 3 treats simultaneous survival and toxin drowsiness as
one Drowsy condition. Characters B35's noncumulative same-effect rule supplies the
normalization; Campaigns B344's cumulative modifier default applies to distinct
intoxication and drowsiness effects. Thus Drunk plus Drowsy gives −6 to ordinary
Bad Temper self-control, while Cowardice gets only Drowsy's −2. The shared
ordinary DX/IQ/Per/Will consumers likewise count Drowsy once, retaining B421's
defensive exemptions. Expiry of one cause leaves another active cause effective.
Symptoms generations 0–2 keep their exact historical behavior, including the
generation-2 −4 from overlapping survival and toxin drowsiness.

`tests/test_condition_self_control.py` exercises SQLite and PostgreSQL with actual
drinking, sobering, short-lived toxin resistance and expiry, and supplied survival
settlements from healthy genesis. A real Observation check succeeds at 9 under
generation 3 instead of failing at historical 7. The survival/toxin and NPC
Wait/task histories reexecute all their recorded producers, supplied environments
and downstream commands. Fresh drinking and survival commands capture their
trusted environments privately. Older commands lacking that capture still need
their original resolver; generic offline reexecution reports them unavailable.

This does not admit additional Symptoms/Affliction names, provide the toxin
producer's broader inactivity/sleep contract, implement alcohol's Shyness
reduction, or establish every combination of conditions. No capability,
source-ledger or certification status changes.
