# Detect Magic: bounded Regular spell execution

The private `detect-magic` command family executes Detect Magic from an approved
purchased IQ/Hard definition with Magery 1. Basic Set Characters B249 calls this
spell **Regular**. Its position under Knowledge spells does not make it an
Information spell.

The supported caster has purchased skill 10–14 and authenticated normal mana.
The B237 quiet speech and gesture requirements are checked against current
canonical capabilities. Five consecutive seconds of explicit casting work use
the canonical clock; ordinary waiting does not finish a cast. Start and further
work refuse active shock because this adapter does not advance injury turns.
Current shock can reduce the final ready-state check after the separate B236
Will−3 distraction check. Existing caster spells on, combat, split or paused
party state, timed hazards and NPC work are outside this bounded adapter.

The trusted GM records current physical touch, familiarity and absence of
concealment, together with an explicit improvised backfire policy. Neither the
observation nor the player command supplies whether an object is magical.
Supported subjects are one intact, accessible carried item with its pinned
equipment profile, or a placed admitted physical lock. Magic comes from a real
completed Haste enchantment project or a real active Magelock effect on that
lock. A real unenchanted carried item provides the negative case. Multiple,
unknown and unmodeled magic carriers fail closed. Configured legacy bindings
and historically lost enchantments also refuse admission; ordinary physical
repair does not restore their magic (B480).

A first successful cast reveals whether the object is magical. A second
successful cast against the same current physical and magical identity reveals
temporary or permanent magic. Critical success on either cast fully identifies
the familiar supported spell; Haste identification includes the actual item
Power (Campaigns B481). Knowledge belongs to the observer and exact item,
binding, project and rating. Custody transfer does not transfer that knowledge.
Public findings contain only the discoveries, without private identity hashes
or binding/project identifiers.

B235 costs are applied as Regular spell costs: success 2 FP, ordinary failure
1 FP, critical failure 2 FP, and critical success 0 FP. There is no Information
spell daily limit, full-cost ordinary failure, secret GM false-information
receipt, or default critical-failure lie. For this subset the authenticated GM
must explicitly select a one-HP injury improvisation before casting. B235–236
permits GM improvisation instead of the critical-failure table. The actual
canonical injury and its shock settle with the full 2 FP cost in the same
transaction; admission requires sufficient current HP so this improvisation
cannot kill the caster outright. This is not represented as a random table roll.

SQLite and PostgreSQL host tests cover real enchantment manufacture, real
temporary Magelock casting, mundane item transfer, all four outcomes,
first/second discoveries, current control and custody, deadline, interruption,
rollback, exact retry, reload, actor-private projections, and complete recorded
command folding and reexecution from the original genesis. Explicit diagnostic
injury/healing checkpoint tests establish the shock phase guards and current
final-roll modifier; those diagnostics are not a claim of a registered health
command or original-genesis health-service replay.

Distance casting, other mana levels, skill outside 10–14, HP energy, ceremonial
casting, unfamiliar or concealed magic, multiple spells, other item spell
carriers, and alternative/table backfires remain unverified. Public spell IDs
and schemas remain unchanged; the private family adds no Detect/Identify
runtime claim beyond this described Detect Magic subset.
