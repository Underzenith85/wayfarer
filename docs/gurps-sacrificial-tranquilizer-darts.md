# Sacrificial Dodge against tranquilizer darts (#878)

Source: Basic Set Campaigns fourth printing B375/B377 and Characters third
printing B279 note 2/B105. Sacrificial Dodge moves a protector within a Step
into an ordinary attack against the friend. Success makes the protector the
struck victim; failure preserves the friend's defense. B377 adds the combined
Drop outcome: both become prone, and a margin of at least 3 avoids the hit.
B279's tranquilizer dart delivers its payload only after the carrier penetrates;
the victim resists at HT-3, with unconsciousness lasting minutes equal to the
failure margin. B105 binds a follow-up's recipient to its carrier's struck target.

Eligibility now admits the existing single-projectile carrier branch for a
small-piercing dart with divisor 0.2 and the explicit penetrating drug payload:
HT-3, unconsciousness, one minute per failure margin. All other linked payloads
retain the specialized-consumer refusal. Existing restrictions still exclude
bursts, areas, spells, selected objects, targeted anatomy, multiple-projectile
weapons, sprayers and single-use explosive launchers. This changes only a
previously rejected command subset; existing accepted historical inputs and
public schemas are unchanged.

The existing ranged reducer resolves the protector's current HP, DR and HT,
then applies the follow-up to that same victim. No task, turn, settlement or
replay reducer was added. An unsuccessful interception retains the original
attack and leaves the friend their own defense and payload resistance; the
round is expended only when the carrier settles. Critical attacks retain the
existing uninterrupted hit on the friend. Drop margin 3 avoids carrier damage
and drug resistance entirely.

Host fixtures use a protector with HP20/HT8 and a friend with HP10/HT10. A
current armor change after attack declaration supplies protector torso DR1:
damage6 faces effective DR5, gives injury1, and HT-3 is 5. Resistance roll12
causes seven minutes (420 seconds) of unconsciousness. The friend's HT-3 would
instead be 7, causing five minutes (300 seconds). Damage4 stopped by that armor
never rolls payload resistance. Without armor, B379 treats bare skin as DR1
against the fractional divisor; small-piercing damage6 gives injury2.

Both SQLite and PostgreSQL regressions cover actual damage and conditions,
failed interception, critical attacks, both Drop margins, ammunition, private
player projection, unauthorized/stale refusal, rollback after carrier damage,
exact command receipts and event folding plus seed-only reexecution. Folded
and executed checkpoints compare as complete validated states; serialized
checkpoint property ordering is not treated as a gameplay difference.

This completes this bounded carrier/payload consumer, not all of #878. Aerial
or swimming concealment, other linked effects, burst interception and other
specialized interpositions retain separate source/choice/acceptance requirements.
No profile certification or issue closure follows from these tests.
