# Major Armoury repairs with rolled parts

Campaigns B484–485 requires parts worth 1d × 10% of an artifact's original
price before a major repair, followed by the ordinary half-hour repair at an
additional −2. Stock sufficient for the actual rolled requirement is valid even
when it cannot cover the maximum possible roll.

The private authenticated `AssessRepairParts` transaction records the actual die,
whole-part quantity sufficient for that exact price, current actor/item/damage,
and pinned equipment and parts-price digests. It validates the same current
approved skill, TL, tool, custody, fatigue and worksite eligibility as repair.
Assessment changes neither material nor durability. The requirement survives
insufficient stock, failed starts, reload and retries; a new command ID cannot
reroll an unchanged requirement. A changed definition, profile, price or unexplained condition fails closed
before dice. A currently qualified new owner can rebind the same immutable
requirement without rerolling; the original assessor provenance is retained. A new requirement is available
only after the canonical completed repair proves the exact changed condition.

An assessed start consumes precisely the recorded quantity from current owned,
accessible, unequipped stock. That stock can span several stacks: both assessed
and historical starts admit their combined quantity and consume only the exact
requirement, in inventory order. Unavailable and other actors' stacks do not
contribute. Success and failure retain the existing deadline,
check, restored HP and material conservation. Failed stock admission consumes
nothing and cannot erase the assessment. Cancelled work does not refund spent
parts or reroll the requirement. Public repair commands, results and task records
retain their schemas. Unassessed starts preserve the historical maximum-stock
preflight and original random stream; the assessment is an explicit opt-in.

The private `armoury-parts:` ledger is excluded from authored genesis and public
player projections. The existing registered Armoury replay family admits the new
private command; it does not add a public event or a player-authored die.

`test_armoury_parts_assessment.py` exercises actual melee weapon and body armor
consumers on SQLite and PostgreSQL: five parts suffice for a one-die requirement
on a $500 item with $10 parts, successful/failed completed work, exact retry after
restart, insufficient rolled stock without rerolls, stale and unauthorized
refusal, changed custody/damage/tools, private projection, settled repair renewal,
and full seeded assessment/start/finish reexecution. Existing Armoury tests
retain the historical unassessed cost streams. This closes only the rolled-parts
contract; alternative time, toolkit and defaulted-skill contracts remain separate.
